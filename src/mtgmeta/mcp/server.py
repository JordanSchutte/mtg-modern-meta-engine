"""MCP server: the project's tables as tools for Claude Code / Claude Desktop.

Run over stdio::

    python -m mtgmeta.mcp.server            # DATA_ROOT from .env (default ./data)

Refresh the tables first (``rclone copy r2:mtgmeta-bronze/silver data/silver``
and ``.../gold data/gold``); the server reads local Parquet only. Register it
in ``.mcp.json`` (Claude Code) or Claude Desktop's config — see README.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from mtgmeta.config import Settings
from mtgmeta.mcp import queries as q

server = MCPServer(
    "mtg-modern-meta",
    instructions=(
        "Tables from the MTG Modern meta engine: Melee tournament matches, decklists, "
        "archetype classification and the matchup matrix, pooled per format period. "
        "Win rates are shrunk toward 50% by 10 pseudo-matches; always report sample "
        "sizes. Archetype names match loosely ('blink' -> 'Blink')."
    ),
)
_root = Settings.from_env().data_root


@server.tool()
def coverage(days: int = 30) -> dict:
    """Events ingested in the last N days: Modern Swiss matches, how many have both
    decklists known, empty lists, refresh checks. Use for 'how much data do we have'."""
    return q.coverage(_root, days)


@server.tool()
def failed_events() -> dict:
    """Watchlist events that were too small or had no rounds, and bronze events with no
    complete version. Use for 'what did we miss / what broke'."""
    return q.failed_events(_root)


@server.tool()
def meta_share(period: str | None = None, top: int = 25) -> list[dict]:
    """Archetypes by share of the field with win rate vs the field and position (share-
    weighted matchup win rate). period = rules period start like '2026-05-18'; default latest."""
    return q.meta_share(_root, period, top)


@server.tool()
def matchup(archetype: str, opponent: str, period: str | None = None) -> dict:
    """One matchup: shrunk and raw match win rate, 90% interval, game win rate, sample size."""
    try:
        return q.matchup(_root, archetype, opponent, period)
    except ValueError as exc:
        return {"error": str(exc)}


@server.tool()
def matrix(period: str | None = None, top: int = 12, min_matches: int = 20) -> dict:
    """The matchup grid for the top archetypes by share: grid[row][column] = row's shrunk
    win rate vs column, or null under min_matches."""
    return q.matrix(_root, period, top, min_matches)


@server.tool()
def archetype_cards(archetype: str, period: str | None = None, section: str = "main") -> dict:
    """MTGGoldfish-style card breakdown of an archetype with core / package / slot tags
    and the flex budget (60 minus core copies). section: main or side."""
    try:
        return q.archetype_cards(_root, archetype, period, section)
    except ValueError as exc:
        return {"error": str(exc)}


@server.tool()
def card_usage(card: str, period: str | None = None) -> list[dict]:
    """Which archetypes play a card, share of their lists and average copies."""
    return q.card_presence_by_archetype(_root, card, period)


@server.tool()
def archetypes(period: str | None = None) -> list[str]:
    """Archetype names in a period, by share."""
    return q.list_archetypes(_root, period)


@server.tool()
def run_sql(sql: str, limit: int = 200) -> dict:
    """Read-only SQL over: events, matches, decklists, decklist_cards, players,
    decklist_archetypes, cards, matchups, archetype_summary. Rows capped by limit."""
    return q.run_sql(_root, sql, limit)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
