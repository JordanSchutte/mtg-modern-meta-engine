"""Coverage flags: events whose lists never published, and how long since the
last big paper event.

Reads the silver Parquet tables (never bronze) so it can run after
classification and be answered by the MCP ``coverage`` tool from the same
code. Three questions:

unpublished Melee events
    Events whose start is ``GRACE_DAYS`` or more in the past with no usable
    decklist, or fewer usable lists than ``MIN_LIST_SHARE`` of the players.
    A list "belongs" to an event when a match of that event references it,
    so a 75 reused from an earlier event still counts here.
empty MTGO events
    Events in bronze whose manifest status is ``empty`` — the page was
    fetched but never carried decklists.
paper dark
    The most recent Melee event with at least ``MIN_CLASSIFIED`` classified
    lists (the forecast's threshold for a paper week it can score) and how
    many days ago it was. When this grows past a couple of weeks the forecast
    is coasting on MTGO alone.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb

from mtgmeta.archetypes.rules import UNCLASSIFIED
from mtgmeta.silver.melee import silver_root as melee_root
from mtgmeta.silver.mtgo import silver_root as mtgo_root

GRACE_DAYS = 3          # an event this old with no lists is flagged, not "pending"
MIN_LIST_SHARE = 0.10   # usable lists below this share of players counts as unpublished
MIN_CLASSIFIED = 150    # = gold.forecast.MIN_PAPER_LISTS, the bar for a scorable paper week
#                         (kept as a literal so silver doesn't import gold; a test pins them)


def _q(path: Path) -> str:
    return f"'{path.as_posix()}'"


def _rows(con, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _melee_event_lists(con, root: Path) -> str:
    """SQL for one row per Melee event with usable and classified list counts.

    ``decklist_archetypes`` may not exist yet (first run, or a session without
    classification); then ``classified`` is null for every event.
    """
    archetypes = root / "decklist_archetypes.parquet"
    classified = (f"count(distinct case when a.archetype is not null and a.archetype <> "
                  f"'{UNCLASSIFIED}' then l.decklist_id end)"
                  if archetypes.exists() else "null")
    arch_join = (f"left join {_q(archetypes)} a on a.decklist_id = l.decklist_id"
                 if archetypes.exists() else "")
    return f"""
        with refs as (
            select tournament_id, decklist_a as decklist_id from {_q(root / 'matches.parquet')}
            where decklist_a is not null
            union
            select tournament_id, decklist_b from {_q(root / 'matches.parquet')}
            where decklist_b is not null
        ), lists as (
            select r.tournament_id, r.decklist_id
            from refs r join {_q(root / 'decklists.parquet')} d using (decklist_id)
            where d.usable
        ), per_event as (
            select l.tournament_id, count(distinct l.decklist_id) as usable_lists,
                   {classified} as classified
            from lists l {arch_join}
            group by l.tournament_id
        )
        select substr(e.start, 1, 10) as start, e.tournament_id, e.name,
               e.players_estimate as players, e.decklist_ids as referenced_lists,
               coalesce(p.usable_lists, 0) as usable_lists, p.classified
        from {_q(root / 'events.parquet')} e
        left join per_event p using (tournament_id)
    """


def unpublished_melee(data_root: Path, today: date, grace_days: int = GRACE_DAYS,
                      min_share: float = MIN_LIST_SHARE) -> list[dict]:
    """Melee events at least ``grace_days`` old with no usable lists, or fewer
    than ``min_share`` of the players' worth. Most recent first."""
    root = melee_root(data_root)
    if not all((root / f"{t}.parquet").exists() for t in ("events", "matches", "decklists")):
        return []
    cutoff = (today - timedelta(days=grace_days)).isoformat()
    con = duckdb.connect()
    rows = _rows(con, f"""
        select * from ({_melee_event_lists(con, root)}) ev
        where start is not null and start <= ?
          and (usable_lists = 0
               or (players is not null and players > 0 and usable_lists < ? * players))
        order by start desc, tournament_id desc
    """, [cutoff, min_share])
    con.close()
    for r in rows:
        r["list_share"] = (round(r["usable_lists"] / r["players"], 3)
                           if r["players"] else None)
    return rows


def empty_mtgo(data_root: Path) -> list[dict]:
    """MTGO events whose manifest status is ``empty``. Most recent first."""
    path = mtgo_root(data_root) / "events.parquet"
    if not path.exists():
        return []
    con = duckdb.connect()
    rows = _rows(con, f"""
        select day, site_name, kind, status from {_q(path)}
        where status = 'empty' order by day desc nulls last, site_name
    """)
    con.close()
    return rows


def unusable_mtgo(data_root: Path) -> dict:
    """How many MTGO lists reached silver but are not usable (main deck under 60
    cards; ``silver/mtgo.py`` sets ``usable``) and how many events they sit in.
    The per-event detail is in the ``MTGO events in bronze`` block of the silver
    report; this is the stock the MCP ``coverage`` tool shows."""
    path = mtgo_root(data_root) / "decklists.parquet"
    if not path.exists():
        return {"lists": 0, "events": 0}
    con = duckdb.connect()
    n, events = con.execute(f"""
        select count(*), count(distinct site_name) from {_q(path)} where not usable
    """).fetchone()
    con.close()
    return {"lists": int(n), "events": int(events)}


def last_big_paper_event(data_root: Path, today: date,
                         min_classified: int = MIN_CLASSIFIED) -> dict | None:
    """The most recent Melee event with ``min_classified`` classified lists, with
    ``days_ago``; None when no event qualifies (or nothing is classified yet)."""
    root = melee_root(data_root)
    if not all((root / f"{t}.parquet").exists()
               for t in ("events", "matches", "decklists", "decklist_archetypes")):
        return None
    con = duckdb.connect()
    rows = _rows(con, f"""
        select start, tournament_id, name, players, usable_lists, classified
        from ({_melee_event_lists(con, root)}) ev
        where start is not null and classified >= ? order by start desc limit 1
    """, [min_classified])
    con.close()
    if not rows:
        return None
    r = rows[0]
    r["days_ago"] = (today - date.fromisoformat(r["start"])).days
    r["min_classified"] = min_classified
    return r


def flags(data_root: Path, today: date | None = None) -> dict:
    """Everything above as one dict, for the report and the MCP tool."""
    today = today or date.today()
    melee = unpublished_melee(data_root, today)
    mtgo = empty_mtgo(data_root)
    return {
        "as_of": today.isoformat(),
        "unpublished_melee": melee,
        "empty_mtgo": mtgo,
        "unpublished_total": len(melee) + len(mtgo),
        "last_big_paper_event": last_big_paper_event(data_root, today),
        "rules": {"grace_days": GRACE_DAYS, "min_list_share": MIN_LIST_SHARE,
                  "min_classified": MIN_CLASSIFIED},
    }


def report(data_root: Path, today: date | None = None) -> str:
    """The coverage.txt section: flagged events, a total, and the paper-dark line."""
    f = flags(data_root, today)
    lines = [f"Events whose lists never published (as of {f['as_of']}; Melee events "
             f"{GRACE_DAYS}+ days old with no usable lists or under {MIN_LIST_SHARE:.0%} "
             "of players; MTGO events fetched empty)"]
    if f["unpublished_melee"]:
        lines.append(f"  {'start':<11}{'id':>7}{'players':>8}{'lists':>6}  name")
        for e in f["unpublished_melee"]:
            lines.append(f"  {e['start']:<11}{e['tournament_id']:>7}{e['players'] or 0:>8}"
                         f"{e['usable_lists']:>6}  {(e['name'] or '')[:48]}")
    if f["empty_mtgo"]:
        lines.append(f"  {'day':<11}{'kind':<22}  mtgo event")
        for e in f["empty_mtgo"]:
            lines.append(f"  {e['day'] or '':<11}{e['kind'] or '':<22}  {e['site_name']}")
    lines.append(f"  {len(f['unpublished_melee'])} Melee + {len(f['empty_mtgo'])} MTGO = "
                 f"{f['unpublished_total']} events without published lists")
    big = f["last_big_paper_event"]
    if big is None:
        lines.append(f"Paper dark: no event with >= {MIN_CLASSIFIED} classified lists yet")
    else:
        lines.append(f"Last paper event with >= {MIN_CLASSIFIED} classified lists: "
                     f"{big['start']} {big['name']} ({big['classified']} lists), "
                     f"{big['days_ago']} days ago")
    return "\n".join(lines)
