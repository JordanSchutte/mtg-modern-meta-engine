"""Silver tables for Melee: events, matches, decklists, decklist cards, players.

Reads the **latest version** of every event in bronze (``events/<id>/<version>/``)
and the shared decklist pages, parses them with the same contract-checked
parsers fetch used, and writes one Parquet file per table under
``<data_root>/silver/melee/``.

Tables
------
events          one row per event (latest version): counts and coverage
matches         one row per match; competitors flattened to a/b (a bye has no b)
decklists       one row per decklist page: usable or not, sizes, name as submitted
decklist_cards  one row per card line: decklist_id, section, qty, card
players         one row per player id: latest names, events played

Unknown decks are kept, not dropped: ``deck_a_known`` / ``deck_b_known`` and
``both_decks_known`` flag what the matrix can use. ``format`` is kept per match
because mixed-format events (a Pro Tour) have draft rounds.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from mtgmeta.sources.melee.fetch import bronze_root, latest_version
from mtgmeta.sources.melee.parse import (
    MatchRecord,
    UnusableDecklistError,
    parse_decklist_page,
    parse_round_matches,
)

log = logging.getLogger(__name__)

TABLES = ("events", "matches", "decklists", "decklist_cards", "players")


def silver_root(data_root: Path) -> Path:
    return data_root / "silver" / "melee"


@dataclass
class SilverBuild:
    events: list[dict]
    matches: list[dict]
    decklists: list[dict]
    decklist_cards: list[dict]
    players: list[dict]

    def counts(self) -> dict[str, int]:
        return {t: len(getattr(self, t)) for t in TABLES}


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

def _match_row(m: MatchRecord, version: str, is_swiss: bool) -> dict:
    comps = sorted(m.competitors, key=lambda c: c.player_id)
    a = comps[0] if comps else None
    b = comps[1] if len(comps) > 1 else None
    a_known = bool(a and a.decklist_id)
    b_known = bool(b and b.decklist_id)
    return {
        "match_guid": m.match_guid,
        "tournament_id": m.tournament_id,
        "version": version,
        "round_id": m.round_id,
        "round_number": m.round_number,
        "round_label": m.round_label,
        "is_swiss": is_swiss,
        "format": m.format,
        "result": m.result,
        "winner_player_id": m.winner_player_id,
        "game_draws": m.game_draws,
        "player_a_id": a.player_id if a else None,
        "player_a_name": a.display_name if a else None,
        "decklist_a": a.decklist_id if a else None,
        "deck_a_known": a_known,
        "game_wins_a": a.game_wins if a else None,
        "player_b_id": b.player_id if b else None,
        "player_b_name": b.display_name if b else None,
        "decklist_b": b.decklist_id if b else None,
        "deck_b_known": b_known,
        "game_wins_b": b.game_wins if b else None,
        "both_decks_known": a_known and b_known,
    }


def _read_event(event_root: Path, version_dir: Path) -> tuple[dict, list[dict], dict[str, dict]]:
    """One event's manifest -> (event row, match rows, decklist facts seen in matches)."""
    manifest = json.loads((version_dir / "manifest.json").read_text(encoding="utf-8"))
    swiss_by_round = {r["round_id"]: bool(r["is_swiss"]) for r in manifest["rounds"]}
    version = version_dir.name

    match_rows: list[dict] = []
    deck_facts: dict[str, dict] = {}
    for f in sorted((version_dir / "rounds").glob("*.json")):
        pages = json.loads(f.read_text(encoding="utf-8"))
        for m in parse_round_matches(pages):
            is_swiss = swiss_by_round.get(m.round_id, False)
            match_rows.append(_match_row(m, version, is_swiss))
            for c in m.competitors:
                if c.decklist_id and c.decklist_id not in deck_facts:
                    deck_facts[c.decklist_id] = {
                        "decklist_name": c.decklist_name,
                        "format": m.format,
                        "player_id": c.player_id,
                        "first_tournament_id": m.tournament_id,
                    }

    swiss = [r for r in match_rows if r["is_swiss"]]
    # What the matrix can use: Modern Swiss rounds, byes excluded (a bye isn't a
    # match). A Pro Tour's draft rounds are Swiss too but carry no lists.
    usable = [r for r in swiss if r["format"] == "Modern" and r["result"] != "bye"]
    # Players, from round 1's rows when the manifest predates players_estimate.
    first_round = manifest["rounds"][0]["round_id"] if manifest["rounds"] else None
    players_from_rows = len({r[f"player_{side}_id"] for r in match_rows for side in ("a", "b")
                             if r["round_id"] == first_round
                             and r[f"player_{side}_id"] is not None}) or None
    refresh_log = event_root / "refreshes.json"
    refreshes = json.loads(refresh_log.read_text(encoding="utf-8")) if refresh_log.exists() else []
    event_row = {
        "tournament_id": manifest["tournament_id"],
        "name": manifest["name"],
        "start": manifest.get("start"),
        "version": version,
        "fetched_at": manifest["fetched_at"],
        "fetcher_version": manifest.get("fetcher_version"),
        "players_estimate": manifest.get("players_estimate") or players_from_rows,
        "rounds": len(manifest["rounds"]),
        "swiss_rounds": sum(1 for r in manifest["rounds"] if r["is_swiss"]),
        "matches": len(match_rows),
        "swiss_matches": len(swiss),
        "swiss_both_known": sum(1 for r in swiss if r["both_decks_known"]),
        "swiss_byes": sum(1 for r in swiss if r["result"] == "bye"),
        "modern_swiss_matches": len(usable),
        "modern_swiss_known": sum(1 for r in usable if r["both_decks_known"]),
        "formats": ",".join(sorted({r["format"] for r in match_rows if r["format"]})),
        "decklist_ids": len(manifest.get("decklist_ids") or []),
        "unusable_decklists": len(manifest.get("unusable_decklists") or {}),
        "versions": sum(1 for p in event_root.iterdir()
                        if p.is_dir() and (p / "manifest.json").exists()),
        "refresh_checks": len(refreshes),
        "refresh_changes": sum(1 for r in refreshes if r.get("changed")),
    }
    return event_row, match_rows, deck_facts


def _read_decklists(deck_dir: Path, facts: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Parse every current decklist page referenced by a match (superseded copies skipped)."""
    rows: list[dict] = []
    cards: list[dict] = []
    for did, fact in sorted(facts.items()):
        page = deck_dir / f"{did}.html"
        if not page.exists():
            rows.append({"decklist_id": did, **fact, "in_bronze": False, "usable": False,
                         "main_count": None, "side_count": None, "companion_count": None})
            continue
        try:
            deck = parse_decklist_page(page.read_text(encoding="utf-8"), did)
        except UnusableDecklistError as exc:
            rows.append({"decklist_id": did, **fact, "in_bronze": True, "usable": False,
                         "main_count": exc.main_count, "side_count": None,
                         "companion_count": None})
            continue
        rows.append({
            "decklist_id": did, **fact, "in_bronze": True, "usable": True,
            "main_count": deck.main_count,
            "side_count": sum(q for q, _ in deck.side),
            "companion_count": sum(q for q, _ in deck.companion),
        })
        for section, lines in (("main", deck.main), ("side", deck.side),
                               ("companion", deck.companion)):
            for qty, card in lines:
                cards.append({"decklist_id": did, "section": section, "qty": qty, "card": card})
    return rows, cards


def _players(match_rows: Iterable[dict]) -> list[dict]:
    seen: dict[int, dict] = {}
    for r in match_rows:
        for side in ("a", "b"):
            pid = r[f"player_{side}_id"]
            if pid is None:
                continue
            p = seen.setdefault(pid, {"player_id": pid, "display_name": None,
                                      "tournaments": set(), "matches": 0})
            p["display_name"] = r[f"player_{side}_name"] or p["display_name"]
            p["tournaments"].add(r["tournament_id"])
            p["matches"] += 1
    return [{**p, "tournaments": len(p["tournaments"])}
            for p in sorted(seen.values(), key=lambda p: p["player_id"])]


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def build(data_root: Path) -> SilverBuild:
    root = bronze_root(data_root)
    events_dir = root / "events"
    deck_dir = root / "decklists"
    events: list[dict] = []
    matches: list[dict] = []
    facts: dict[str, dict] = {}
    if events_dir.exists():
        for event_root in sorted(events_dir.iterdir(), key=lambda p: int(p.name)):
            version_dir = latest_version(event_root)
            if version_dir is None:
                log.warning("event %s has no complete version; skipped", event_root.name)
                continue
            ev, rows, deck_facts = _read_event(event_root, version_dir)
            events.append(ev)
            matches.extend(rows)
            for did, fact in deck_facts.items():
                facts.setdefault(did, fact)
    decklists, cards = _read_decklists(deck_dir, facts)
    return SilverBuild(events, matches, decklists, cards, _players(matches))


def write_parquet(build_: SilverBuild, out_dir: Path) -> dict[str, Path]:
    """One Parquet file per table, written atomically (tmp then rename)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    written: dict[str, Path] = {}
    for table in TABLES:
        rows = getattr(build_, table)
        target = out_dir / f"{table}.parquet"
        tmp = out_dir / f"{table}.parquet.tmp"
        if rows:
            con.register("rows", _frame(rows))
            con.execute(f"COPY (SELECT * FROM rows) TO '{tmp.as_posix()}' (FORMAT PARQUET)")
            con.unregister("rows")
        else:
            con.execute(f"COPY (SELECT 1 AS empty WHERE false) TO '{tmp.as_posix()}' "
                        "(FORMAT PARQUET)")
        tmp.replace(target)
        written[table] = target
    con.close()
    return written


def _frame(rows: list[dict]):
    """Rows -> a pyarrow table DuckDB can register (keeps None as NULL)."""
    import pyarrow as pa
    return pa.Table.from_pylist(rows)


def coverage_report(build_: SilverBuild) -> str:
    lines = [f"{'start':<11}{'id':>7}{'players':>8}{'modern':>7}{'known':>7}{'share':>7}"
             f"{'lists':>7}{'empty':>7}  name",
             "  (modern = Modern Swiss matches excluding byes; known = both decks published)"]
    for e in sorted(build_.events, key=lambda e: e["start"] or ""):
        n, k = e["modern_swiss_matches"], e["modern_swiss_known"]
        share = k / n if n else 0.0
        lines.append(
            f"{(e['start'] or '')[:10]:<11}{e['tournament_id']:>7}"
            f"{e['players_estimate'] or 0:>8}{n:>7}{k:>7}"
            f"{share:>7.0%}{e['decklist_ids']:>7}{e['unusable_decklists']:>7}  {e['name'][:48]}"
        )
    total_n = sum(e["modern_swiss_matches"] for e in build_.events)
    total_k = sum(e["modern_swiss_known"] for e in build_.events)
    lines.append(f"\n{len(build_.events)} events, {total_n} Modern Swiss matches, "
                 f"{total_k} with both decks known "
                 f"({total_k / total_n:.0%})" if total_n else "\nno events")
    usable = sum(1 for d in build_.decklists if d["usable"])
    lines.append(f"{len(build_.decklists)} decklists referenced, {usable} usable, "
                 f"{len(build_.decklist_cards)} card lines, {len(build_.players)} players")
    return "\n".join(lines)


def built_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
