"""Parsers for Melee's tournament page, round-matches JSON and decklist page.

Every function validates the shape it depends on and raises ``ContractError``
when that shape changes. Results come from structured fields (``GameWins``,
``GameDraws``, competitor count), never from the human-readable result string.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from mtgmeta.contracts import ContractError, require

from . import SOURCE

# --------------------------------------------------------------------------
# Tournament page
# --------------------------------------------------------------------------

_TITLE_RE = re.compile(r"<title>\s*(.*?)\s*\|\s*Melee\s*</title>", re.S)
_ROUND_RE = re.compile(
    r'class="[^"]*\bround-selector\b[^"]*"\s+data-id="(\d+)"\s+data-name="([^"]+)"'
    r'(?:\s+data-is-completed="(True|False)")?'
)
_ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


@dataclass(frozen=True)
class RoundRef:
    id: int
    name: str
    is_completed: bool | None

    @property
    def is_swiss(self) -> bool:
        """Swiss rounds are named "Round N"; playoffs pair only the top cut."""
        return re.fullmatch(r"Round \d+", self.name) is not None


@dataclass(frozen=True)
class TournamentMeta:
    tournament_id: int
    name: str
    start: str | None
    rounds: list[RoundRef]


def parse_tournament_page(page: str, tournament_id: int) -> TournamentMeta:
    title = _TITLE_RE.search(page)
    require(title is not None, SOURCE, "tournament page has no '<title>… | Melee</title>'",
            f"tournament {tournament_id}")

    # Round buttons appear twice (standings + pairings selectors); keep first by id.
    seen: dict[int, RoundRef] = {}
    for rid, name, completed in _ROUND_RE.findall(page):
        rid_int = int(rid)
        if rid_int not in seen:
            seen[rid_int] = RoundRef(
                id=rid_int,
                name=html.unescape(name),
                is_completed=None if completed == "" else completed == "True",
            )
    require(bool(seen), SOURCE, "tournament page has no round-selector buttons",
            f"tournament {tournament_id} may be private, unstarted, or the page changed")

    start = _ISO_RE.search(page)
    return TournamentMeta(
        tournament_id=tournament_id,
        name=html.unescape(title.group(1)).strip(),
        start=start.group(1) if start else None,
        rounds=list(seen.values()),
    )


# --------------------------------------------------------------------------
# Round matches (DataTables JSON)
# --------------------------------------------------------------------------

MATCH_COLUMNS = ["TableNumber", "PodNumber", "Teams", "Decklists", "ResultString"]

_MATCH_KEYS = ("Competitors", "RoundNumber", "RoundId", "TournamentId", "HasResult",
               "GameDraws", "Guid")
_COMPETITOR_KEYS = ("Team", "GameWins", "Decklists")


def datatables_form(columns: list[str], start: int, length: int) -> list[tuple[str, str]]:
    """The server-side DataTables payload Melee expects; a bare body returns 500/empty."""
    form: list[tuple[str, str]] = [("draw", "1")]
    for i, col in enumerate(columns):
        form += [
            (f"columns[{i}][data]", col),
            (f"columns[{i}][name]", col),
            (f"columns[{i}][searchable]", "true"),
            (f"columns[{i}][orderable]", "true"),
            (f"columns[{i}][search][value]", ""),
            (f"columns[{i}][search][regex]", "false"),
        ]
    form += [
        ("order[0][column]", "0"),
        ("order[0][dir]", "asc"),
        ("start", str(start)),
        ("length", str(length)),
        ("search[value]", ""),
        ("search[regex]", "false"),
    ]
    return form


@dataclass(frozen=True)
class CompetitorRecord:
    player_id: int
    display_name: str
    username: str | None
    decklist_id: str | None
    decklist_name: str | None
    game_wins: int


@dataclass(frozen=True)
class MatchRecord:
    match_guid: str
    tournament_id: int
    round_id: int
    round_number: int
    round_label: str | None
    format: str | None  # mixed-format events (e.g. a Pro Tour) also have draft rounds
    result: str  # "win" | "draw" | "bye" | "unreported"
    winner_player_id: int | None
    game_draws: int
    competitors: list[CompetitorRecord] = field(default_factory=list)

    @property
    def both_decks_known(self) -> bool:
        return len(self.competitors) == 2 and all(c.decklist_id for c in self.competitors)


def check_datatables_page(payload: object) -> tuple[int, list[dict]]:
    require(isinstance(payload, dict), SOURCE, "round matches response is not a JSON object")
    require("recordsTotal" in payload and "data" in payload, SOURCE,
            "round matches response lacks recordsTotal/data", str(list(payload)[:8]))
    require(isinstance(payload["data"], list), SOURCE, "round matches 'data' is not a list")
    return int(payload["recordsTotal"]), payload["data"]


def _competitor(raw: dict, where: str) -> CompetitorRecord:
    for key in _COMPETITOR_KEYS:
        require(key in raw, SOURCE, f"competitor missing '{key}'", where)
    players = (raw["Team"] or {}).get("Players") or []
    require(len(players) == 1, SOURCE, "expected exactly one player per competitor",
            f"{where}: got {len(players)}")
    player = players[0]
    require("ID" in player and "DisplayName" in player, SOURCE,
            "player missing ID/DisplayName", where)
    decklists = raw["Decklists"]
    require(isinstance(decklists, list), SOURCE, "competitor Decklists is not a list", where)
    deck = decklists[0] if decklists else None
    if deck is not None:
        require("DecklistId" in deck, SOURCE, "decklist entry missing DecklistId", where)
    return CompetitorRecord(
        player_id=int(player["ID"]),
        display_name=player["DisplayName"],
        username=player.get("Username"),
        decklist_id=deck["DecklistId"] if deck else None,
        decklist_name=deck.get("DecklistName") if deck else None,
        game_wins=int(raw["GameWins"] or 0),
    )


def parse_match(raw: dict) -> MatchRecord:
    where = f"match {raw.get('Guid', '?')}"
    for key in _MATCH_KEYS:
        require(key in raw, SOURCE, f"match missing '{key}'", where)
    competitors = [_competitor(c, where) for c in raw["Competitors"] or []]
    require(len(competitors) in (1, 2), SOURCE, "match must have 1 or 2 competitors",
            f"{where}: got {len(competitors)}")

    winner: int | None = None
    if len(competitors) == 1:
        result = "bye"
        winner = competitors[0].player_id
    elif not raw["HasResult"]:
        result = "unreported"
    else:
        a, b = competitors
        if a.game_wins == b.game_wins:
            result = "draw"
        else:
            result = "win"
            winner = a.player_id if a.game_wins > b.game_wins else b.player_id

    return MatchRecord(
        match_guid=raw["Guid"],
        tournament_id=int(raw["TournamentId"]),
        round_id=int(raw["RoundId"]),
        round_number=int(raw["RoundNumber"]),
        round_label=raw.get("RoundDescription"),
        format=raw.get("Format"),
        result=result,
        winner_player_id=winner,
        game_draws=int(raw["GameDraws"] or 0),
        competitors=competitors,
    )


def parse_round_matches(pages: list[dict]) -> list[MatchRecord]:
    """Parse every DataTables page of one round and check nothing was lost."""
    require(bool(pages), SOURCE, "no round-matches pages to parse")
    total, _ = check_datatables_page(pages[0])
    rows: list[dict] = []
    for page in pages:
        _, data = check_datatables_page(page)
        rows.extend(data)
    require(len(rows) == total, SOURCE, "round matches row count != recordsTotal",
            f"{len(rows)} vs {total}")
    return [parse_match(r) for r in rows]


# --------------------------------------------------------------------------
# Decklist page
# --------------------------------------------------------------------------

_DECK_PRE_RE = re.compile(r'<pre[^>]*\bid="decklist-text"[^>]*>(.*?)</pre>', re.S)
_CARD_LINE_RE = re.compile(r"^(\d+)\s+(.+)$")
_SECTIONS = {
    "maindeck": "main", "main deck": "main", "deck": "main", "main": "main",
    "sideboard": "side", "side board": "side",
    "companion": "companion", "commander": "companion",
}


@dataclass(frozen=True)
class Decklist:
    decklist_id: str
    main: list[tuple[int, str]]
    side: list[tuple[int, str]]
    companion: list[tuple[int, str]]

    @property
    def main_count(self) -> int:
        return sum(q for q, _ in self.main)


def parse_decklist_page(page: str, decklist_id: str) -> Decklist:
    block = _DECK_PRE_RE.search(page)
    require(block is not None, SOURCE, "decklist page has no <pre id=\"decklist-text\">",
            decklist_id)
    sections: dict[str, list[tuple[int, str]]] = {"main": [], "side": [], "companion": []}
    current = "main"
    for raw_line in html.unescape(block.group(1)).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header = _SECTIONS.get(line.lower())
        if header:
            current = header
            continue
        card = _CARD_LINE_RE.match(line)
        if card is None:
            raise ContractError(SOURCE, "unrecognised decklist line", f"{decklist_id}: {line!r}")
        sections[current].append((int(card.group(1)), card.group(2).strip()))

    deck = Decklist(decklist_id, sections["main"], sections["side"], sections["companion"])
    require(deck.main_count >= 40, SOURCE, "decklist main deck has fewer than 40 cards",
            f"{decklist_id}: {deck.main_count}")
    return deck
