"""Silver tables for MTGO events, from bronze/mtgo.

Tables (``silver/mtgo/``)
-------------------------
events          one row per event: site_name, kind, type, day, entrants, lists published
decklists       one row per published list, with final rank and record
decklist_cards  (decklist_id, section, qty, card) — same shape as Melee's, so the
                archetype classifier and card tools read both sources alike

``decklist_id`` is ``mtgo:<site_name>:<loginid>``. Players are keyed by MTGO
username (``player``), which is unrelated to Melee usernames.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from mtgmeta.sources.mtgo.fetch import bronze_root
from mtgmeta.sources.mtgo.parse import IncompleteEventError, parse_event

log = logging.getLogger(__name__)
TABLES = ("events", "decklists", "decklist_cards")


def silver_root(data_root: Path) -> Path:
    return data_root / "silver" / "mtgo"


@dataclass
class MtgoSilver:
    events: list[dict]
    decklists: list[dict]
    decklist_cards: list[dict]

    def counts(self) -> dict[str, int]:
        return {t: len(getattr(self, t)) for t in TABLES}


def build(data_root: Path) -> MtgoSilver:
    events, decks, cards = [], [], []
    for mf in sorted((bronze_root(data_root) / "events").glob("*/manifest.json")):
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        site = mf.parent.name
        if manifest.get("status") != "complete":
            events.append({"site_name": site, "event_id": None, "name": None,
                           "type": None, "kind": manifest.get("kind"), "day": manifest.get("day"),
                           "player_count": None, "decklists": 0, "status": manifest.get("status"),
                           "fetched_at": manifest.get("fetched_at")})
            continue
        try:
            ev = parse_event((mf.parent / "page.html").read_text(encoding="utf-8"), site)
        except (OSError, IncompleteEventError) as exc:
            log.warning("%s: %s", site, exc)
            continue
        events.append({"site_name": site, "event_id": ev.event_id, "name": ev.name,
                       "type": ev.kind, "kind": manifest.get("kind"), "day": ev.day.isoformat(),
                       "player_count": ev.player_count, "decklists": len(ev.decklists),
                       "status": "complete", "fetched_at": manifest.get("fetched_at")})
        for d in ev.decklists:
            did = f"mtgo:{site}:{d.loginid}"
            decks.append({"decklist_id": did, "site_name": site, "day": ev.day.isoformat(),
                          "loginid": d.loginid, "player": d.player, "rank": d.rank,
                          "wins": d.wins, "losses": d.losses, "format": "Modern",
                          "usable": d.main_count >= 60, "main_count": d.main_count,
                          "side_count": d.side_count})
            for section, lines in (("main", d.main), ("side", d.side)):
                for qty, card in lines:
                    cards.append({"decklist_id": did, "section": section, "qty": qty,
                                  "card": card})
    return MtgoSilver(events, decks, cards)


def write_parquet(build_: MtgoSilver, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for table in TABLES:
        rows = getattr(build_, table)
        if not rows:
            continue
        target = out_dir / f"{table}.parquet"
        tmp = out_dir / f"{table}.parquet.tmp"
        pq.write_table(pa.Table.from_pylist(rows), tmp)
        tmp.replace(target)
        written[table] = target
    return written


def report(build_: MtgoSilver) -> str:
    by_kind: dict[str, list[int]] = {}
    for e in build_.events:
        if e["status"] == "complete":
            by_kind.setdefault(e["kind"], []).append(e["decklists"])
    lines = ["MTGO events in bronze"]
    lines.append(f"  {'kind':<22}{'events':>7}{'lists':>7}")
    for kind, ns in sorted(by_kind.items()):
        lines.append(f"  {kind:<22}{len(ns):>7}{sum(ns):>7}")
    empty = [e for e in build_.events if e["status"] != "complete"]
    if empty:
        lines.append(f"  {len(empty)} events recorded without lists")
    days = sorted(e["day"] for e in build_.events if e.get("day"))
    if days:
        lines.append(f"  {days[0]} .. {days[-1]}")
    return "\n".join(lines)
