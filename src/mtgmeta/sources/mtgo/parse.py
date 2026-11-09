"""Read an MTGO decklist page.

The page is a shell; the event is embedded whole as JSON in
``window.MTGO.decklists.data``. Challenges/Preliminaries ("TOURNAMENT")
carry ``decklists``, ``standings`` (rank, record, tiebreakers), ``winloss``,
``final_rank``, ``brackets`` (top 8) and ``player_count``; leagues carry
``decklists`` only, each with ``wins`` (always 5 for published league lists).

A freshly created event is sometimes served before its lists land (brackets
only, or no data at all); that raises :class:`IncompleteEventError` so the
caller leaves it for the next run instead of recording an empty event.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

DATA_RE = re.compile(r"window\.MTGO\.decklists\.data\s*=\s*(\{.*?\});\s*\n", re.S)


class IncompleteEventError(ValueError):
    """The page has no decklists yet (or is not an event page at all)."""


@dataclass
class MtgoDecklist:
    loginid: str
    player: str
    main: list[tuple[int, str]]
    side: list[tuple[int, str]]
    rank: int | None = None       # final standing (tournaments)
    wins: int | None = None
    losses: int | None = None

    @property
    def main_count(self) -> int:
        return sum(q for q, _ in self.main)

    @property
    def side_count(self) -> int:
        return sum(q for q, _ in self.side)


@dataclass
class MtgoEvent:
    site_name: str
    event_id: str
    name: str
    kind: str                     # TOURNAMENT or LEAGUE
    day: date
    player_count: int | None      # entrants (tournaments); None for leagues
    decklists: list[MtgoDecklist] = field(default_factory=list)
    top8: list[dict] = field(default_factory=list)   # bracket matches, as published


def card_name(raw: str) -> str:
    """MTGO prints split cards as ``Wear/Tear``; Scryfall and Melee say ``Wear // Tear``."""
    name = raw.strip()
    if "/" in name and "//" not in name:
        name = " // ".join(part.strip() for part in name.split("/"))
    return name


def _cards(entries: list[dict]) -> list[tuple[int, str]]:
    out: dict[str, int] = {}
    for e in entries:
        name = card_name(e["card_attributes"]["card_name"])
        out[name] = out.get(name, 0) + int(e["qty"])
    return [(q, n) for n, q in sorted(out.items(), key=lambda kv: (-kv[1], kv[0]))]


def extract_data(html: str) -> dict:
    m = DATA_RE.search(html)
    if not m:
        raise IncompleteEventError("no window.MTGO.decklists.data on the page")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise IncompleteEventError(f"embedded data is not JSON: {exc}") from exc


def parse_event(html: str, site_name: str) -> MtgoEvent:
    data = extract_data(html)
    lists = data.get("decklists") or []
    if not lists:
        raise IncompleteEventError("event page has no decklists yet")

    if "tournamentid" in lists[0]:
        standings = {s["loginid"]: s for s in data.get("standings", [])}
        winloss = {w["loginid"]: w for w in data.get("winloss", [])}
        decks = []
        for d in lists:
            st, wl = standings.get(d["loginid"], {}), winloss.get(d["loginid"], {})
            decks.append(MtgoDecklist(
                loginid=str(d["loginid"]), player=d["player"],
                main=_cards(d.get("main_deck", [])), side=_cards(d.get("sideboard_deck", [])),
                rank=int(st["rank"]) if st.get("rank") else None,
                wins=int(wl["wins"]) if wl.get("wins") is not None else None,
                losses=int(wl["losses"]) if wl.get("losses") is not None else None,
            ))
        pc = data.get("player_count") or {}
        day = date.fromisoformat(str(data.get("starttime", ""))[:10])
        top8 = [m | {"bracket": b.get("index")}
                for b in data.get("brackets", []) for m in b.get("matches", [])]
        return MtgoEvent(site_name, str(data["event_id"]), data.get("description", ""),
                         "TOURNAMENT", day, int(pc["players"]) if pc.get("players") else None,
                         decks, top8)

    decks = []
    for d in lists:
        rec = d.get("wins") or {}          # {"wins": "5", "losses": "0"} on league lists
        rec = rec if isinstance(rec, dict) else {"wins": rec}
        decks.append(MtgoDecklist(
            loginid=str(d["loginid"]), player=d["player"],
            main=_cards(d.get("main_deck", [])), side=_cards(d.get("sideboard_deck", [])),
            wins=int(rec["wins"]) if rec.get("wins") is not None else None,
            losses=int(rec["losses"]) if rec.get("losses") is not None else None,
        ))
    day = date.fromisoformat(str(data.get("publish_date", ""))[:10])
    return MtgoEvent(site_name, str(data.get("instance_id") or data.get("playeventid")),
                     data.get("name", ""), "LEAGUE", day, None, decks)
