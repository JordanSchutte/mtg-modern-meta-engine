"""Questions people ask of the data, as functions over the Parquet tables.

Every function takes ``data_root`` (where ``silver/`` and ``gold/`` live —
``rclone copy r2:mtgmeta-bronze/silver data/silver`` etc. to refresh) and
returns plain dicts/lists so the MCP layer can hand them straight back.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from mtgmeta.sources.melee.fetch import bronze_root

TABLES = {
    "events": "silver/melee/events.parquet",
    "matches": "silver/melee/matches.parquet",
    "decklists": "silver/melee/decklists.parquet",
    "decklist_cards": "silver/melee/decklist_cards.parquet",
    "players": "silver/melee/players.parquet",
    "decklist_archetypes": "silver/melee/decklist_archetypes.parquet",
    "cards": "silver/cards.parquet",
    "matchups": "gold/melee/matchups.parquet",
    "archetype_summary": "gold/melee/archetype_summary.parquet",
}


def connect(data_root: Path) -> duckdb.DuckDBPyConnection:
    """A connection with one read-only view per table that exists on disk."""
    con = duckdb.connect()
    for name, rel in TABLES.items():
        path = data_root / rel
        if path.exists():
            con.execute(f"create view {name} as select * from '{path.as_posix()}'").fetchall()
    return con


def _rows(con, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _latest_period(con) -> str | None:
    r = con.execute("select max(period) from archetype_summary").fetchone()
    return r[0] if r else None


# --------------------------------------------------------------------------

def coverage(data_root: Path, days: int = 30) -> dict:
    """Events in bronze over the last N days with how much of each the matrix can use."""
    con = connect(data_root)
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    events = _rows(con, """
        select substr(start, 1, 10) as start, tournament_id, name, players_estimate as players,
               modern_swiss_matches as modern_swiss, modern_swiss_known as both_known,
               round(100.0 * modern_swiss_known / nullif(modern_swiss_matches, 0), 1) as known_pct,
               decklist_ids as decklists, unusable_decklists as empty_lists,
               versions, refresh_checks, refresh_changes
        from events where start >= ? order by start desc
    """, [since])
    tot = sum(e["modern_swiss"] or 0 for e in events)
    known = sum(e["both_known"] or 0 for e in events)
    con.close()
    return {"since": since, "events": len(events), "modern_swiss_matches": tot,
            "both_decks_known": known,
            "known_share": round(known / tot, 3) if tot else None, "rows": events}


def failed_events(data_root: Path) -> dict:
    """Watchlist entries that didn't become events (too small / no rounds) and
    events in bronze with no complete version."""
    import json
    root = bronze_root(data_root)
    wl_path = root / "watchlist.json"
    out: dict = {"too_small": [], "no_rounds": [], "pending": 0, "incomplete_in_bronze": []}
    if wl_path.exists():
        for e in json.loads(wl_path.read_text(encoding="utf-8"))["events"].values():
            if e["status"] == "pending":
                out["pending"] += 1
            elif e["status"] in ("too_small", "no_rounds"):
                out[e["status"]].append({"tournament_id": e["tournament_id"],
                                         "title": e["title"], "start": e["start"][:10],
                                         "players_estimate": e.get("players_estimate")})
    events_dir = root / "events"
    if events_dir.exists():
        for d in events_dir.iterdir():
            if d.is_dir() and not any((v / "manifest.json").exists()
                                      for v in d.iterdir() if v.is_dir()):
                out["incomplete_in_bronze"].append(int(d.name))
    out["too_small"] = sorted(out["too_small"], key=lambda e: e["start"], reverse=True)[:50]
    return out


def meta_share(data_root: Path, period: str | None = None, top: int = 25) -> list[dict]:
    """Archetypes by share of Swiss match slots with record vs the field and position."""
    con = connect(data_root)
    period = period or _latest_period(con)
    rows = _rows(con, """
        select archetype, round(share, 4) as share, matches, round(win_rate_shrunk, 4) as win_rate,
               round(wr_lo, 3) as wr_lo, round(wr_hi, 3) as wr_hi, round(position, 4) as position
        from archetype_summary where period = ? order by share desc limit ?
    """, [period, top])
    con.close()
    return rows


def matchup(data_root: Path, archetype: str, opponent: str, period: str | None = None) -> dict:
    """One matchup both ways, with sample sizes and intervals; names are matched loosely."""
    con = connect(data_root)
    period = period or _latest_period(con)
    a = _resolve(con, archetype, period)
    b = _resolve(con, opponent, period)
    rows = _rows(con, """
        select archetype, opponent, matches, wins, round(win_rate, 4) as win_rate,
               round(win_rate_shrunk, 4) as win_rate_shrunk, round(wr_lo, 3) as wr_lo,
               round(wr_hi, 3) as wr_hi, games, round(game_win_rate, 4) as game_win_rate
        from matchups where period = ? and archetype = ? and opponent = ?
    """, [period, a, b])
    con.close()
    if not rows:
        return {"period": period, "archetype": a, "opponent": b, "matches": 0,
                "note": "no recorded matches between these archetypes in this period"}
    r = rows[0]
    r["period"] = period
    r["reading"] = (f"{a} vs {b}: {r['win_rate_shrunk']:.0%} shrunk match win rate over "
                    f"{r['matches']} matches (raw {r['win_rate']:.0%}, 90% CI "
                    f"{r['wr_lo']:.0%}–{r['wr_hi']:.0%}); games {r['game_win_rate']:.0%}"
                    if r["game_win_rate"] is not None else "")
    return r


def matrix(data_root: Path, period: str | None = None, top: int = 12,
           min_matches: int = 20) -> dict:
    """The top archetypes' matchup grid (shrunk win rate of row vs column)."""
    con = connect(data_root)
    period = period or _latest_period(con)
    names = [r["archetype"] for r in _rows(con, """
        select archetype from archetype_summary where period = ? order by share desc limit ?
    """, [period, top])]
    cells = _rows(con, """
        select archetype, opponent, matches, round(win_rate_shrunk, 3) as wr
        from matchups
        where period = ? and archetype in (select unnest(?)) and opponent in (select unnest(?))
    """, [period, names, names])
    con.close()
    grid: dict[str, dict[str, dict | None]] = {a: {} for a in names}
    for c in cells:
        grid[c["archetype"]][c["opponent"]] = (
            {"win_rate": c["wr"], "matches": c["matches"]} if c["matches"] >= min_matches else None)
    return {"period": period, "archetypes": names, "min_matches": min_matches, "grid": grid}


def archetype_cards(data_root: Path, archetype: str, period: str | None = None,
                    section: str = "main", variant: str | None = None) -> dict:
    """MTGGoldfish-style breakdown with core / package / slot tags and the flex budget.

    core    : in 90%+ of lists at a steady count — the deck
    package : in a minority of lists but nearly always as a 3-4-of when present —
              a decision ("which package"), not a slot
    slot    : everything else — the loose slots pilots tune

    ``variant`` narrows to one build (e.g. "Rakdos Phoenix"); without it an
    archetype that holds several builds shows their shells as packages.
    """
    con = connect(data_root)
    period = period or _latest_period(con)
    name = _resolve(con, archetype, period)
    vfilter = "and variant = ?" if variant else ""
    vparams = [variant] if variant else []
    rows = _rows(con, f"""
        with lists as (
            select decklist_id from decklist_archetypes
            where archetype = ? and rules_file like ? {vfilter}
        )
        select c.card, count(distinct c.decklist_id) as lists, avg(c.qty) as avg_qty,
               sum(case when c.qty >= 3 then 1 else 0 end) as three_plus
        from decklist_cards c join lists using (decklist_id)
        where c.section = ?
        group by c.card
    """, [name, f"%{period}%", *vparams, section])
    n = con.execute(f"""
        select count(*) from decklist_archetypes
        where archetype = ? and rules_file like ? {vfilter}
    """, [name, f"%{period}%", *vparams]).fetchone()[0]
    variants = [r["variant"] for r in _rows(con, """
        select variant, count(*) n from decklist_archetypes
        where archetype = ? and rules_file like ? group by variant order by n desc
    """, [name, f"%{period}%"])]
    land = {r["key"]: r["is_land"] for r in _rows(con, "select key, is_land from cards")} \
        if _has(con, "cards") else {}
    con.close()
    from mtgmeta.cards.scryfall import normalize
    cards = []
    core_copies = 0.0
    for r in rows:
        p = r["lists"] / n if n else 0.0
        is_land = bool(land.get(normalize(r["card"]), False))
        if p >= 0.9:
            tag = "core"
            core_copies += r["avg_qty"]
        elif p >= 0.15 and r["three_plus"] / r["lists"] >= 0.7:
            tag = "package"
        else:
            tag = "slot"
        cards.append({"card": r["card"], "share": round(p, 3), "avg_copies": round(r["avg_qty"], 2),
                      "tag": tag, "is_land": is_land})
    cards.sort(key=lambda c: (-c["share"], -c["avg_copies"]))
    return {"period": period, "archetype": name, "variant": variant, "variants": variants,
            "lists": n, "section": section, "core_copies": round(core_copies, 1),
            "flex_budget": round((60 if section == "main" else 15) - core_copies, 1),
            "cards": cards}


def run_sql(data_root: Path, sql: str, limit: int = 200) -> dict:
    """Read-only SQL over the tables (events, matches, decklists, decklist_cards,
    players, decklist_archetypes, cards, matchups, archetype_summary)."""
    if re.search(r"\b(insert|update|delete|drop|create|alter|copy|attach|pragma|install|load)\b",
                 sql, re.I):
        return {"error": "read-only: select statements only"}
    con = connect(data_root)
    try:
        rows = _rows(con, f"select * from ({sql.rstrip().rstrip(';')}) q limit {int(limit)}")
    except Exception as exc:  # noqa: BLE001 - surface the engine's message to the caller
        con.close()
        return {"error": str(exc)}
    con.close()
    return {"rows": rows, "count": len(rows), "truncated": len(rows) >= limit}


def list_archetypes(data_root: Path, period: str | None = None) -> list[str]:
    con = connect(data_root)
    period = period or _latest_period(con)
    names = [r["archetype"] for r in _rows(
        con, "select archetype from archetype_summary where period = ? order by share desc",
        [period])]
    con.close()
    return names


# --------------------------------------------------------------------------

def _has(con, table: str) -> bool:
    try:
        con.execute(f"select 1 from {table} limit 0").fetchall()
        return True
    except duckdb.Error:
        return False


def _resolve(con, name: str, period: str | None) -> str:
    """Exact, then case-insensitive, then substring match on archetype names."""
    names = [r["archetype"] for r in _rows(
        con, "select distinct archetype from archetype_summary where period = ?", [period])]
    if name in names:
        return name
    low = {n.lower(): n for n in names}
    if name.lower() in low:
        return low[name.lower()]
    hits = [n for n in names if name.lower() in n.lower()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise ValueError(f"'{name}' is ambiguous: {', '.join(hits)}")
    raise ValueError(f"no archetype named '{name}' in period {period}; "
                     f"known: {', '.join(names[:20])}...")


def card_presence_by_archetype(data_root: Path, card: str, period: str | None = None,
                               ) -> list[dict]:
    """Where a card is played: share of each archetype's lists that run it."""
    con = connect(data_root)
    period = period or _latest_period(con)
    rows = _rows(con, """
        with lists as (
            select decklist_id, archetype from decklist_archetypes where rules_file like ?
        ), per as (
            select archetype, count(*) as n from lists group by archetype
        ), hits as (
            select l.archetype, count(distinct c.decklist_id) as k, avg(c.qty) as avg_qty
            from decklist_cards c join lists l using (decklist_id)
            where lower(c.card) = lower(?) and c.section = 'main' group by l.archetype
        )
        select per.archetype, per.n as lists, coalesce(hits.k, 0) as with_card,
               round(coalesce(hits.k, 0) * 1.0 / per.n, 3) as share,
               round(hits.avg_qty, 2) as avg_copies
        from per left join hits using (archetype)
        where coalesce(hits.k, 0) > 0 order by share desc
    """, [f"%{period}%", card])
    con.close()
    return rows


def group_counts(rows: list[dict], key: str) -> Counter:
    c: Counter = Counter()
    for r in rows:
        c[r[key]] += 1
    return c


def _by(rows: list[dict], key: str) -> dict:
    d = defaultdict(list)
    for r in rows:
        d[r[key]].append(r)
    return d
