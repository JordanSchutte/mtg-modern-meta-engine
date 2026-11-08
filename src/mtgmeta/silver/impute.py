"""Same-75 imputation: fill a player's missing decklist from their own nearby list.

Players at a convention often register the same deck for Friday's RCQ and
Saturday's RC, and publish it for one event but not the other. When a match
has a player with no usable list, we look for that player's nearest *usable,
published* Modern list in another event within ``window_days`` and use it,
flagged ``imputed_same_75``. Nothing in silver is overwritten: imputations are
their own table and the matrix joins them in (and can leave them out).

Before trusting the assumption, measure it: players who published usable lists
at two events inside the window tell us how often it really was the same deck
(same archetype, and exactly the same 75). ``validate`` reports that rate per
day-gap, which is what decides the window.

Tables
------
imputations   one row per (match, side) filled: the list used, its source event
              and the day gap
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .melee import silver_root

DEFAULT_WINDOW_DAYS = 14


@dataclass
class Imputation:
    match_guid: str
    tournament_id: int
    side: str
    player_id: int
    decklist_id: str
    from_tournament_id: int
    days_delta: int
    had_record: bool   # the event listed a decklist for them, but it was empty/unusable


def _day(text) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def load(data_root: Path):
    root = silver_root(data_root)
    con = duckdb.connect()
    events = {r[0]: _day(r[1]) for r in con.execute(
        f"select tournament_id, start from '{(root / 'events.parquet').as_posix()}'").fetchall()}
    usable = {r[0] for r in con.execute(
        f"select decklist_id from '{(root / 'decklists.parquet').as_posix()}' "
        "where usable and format = 'Modern'").fetchall()}
    matches = con.execute(f"""
        select match_guid, tournament_id, player_a_id, decklist_a, player_b_id, decklist_b
        from '{(root / 'matches.parquet').as_posix()}'
        where is_swiss and format = 'Modern' and result <> 'bye'
    """).fetchall()
    con.close()
    return events, usable, matches


def player_lists(matches, events, usable) -> dict[int, list[tuple[date, int, str]]]:
    """Each player's usable published lists: (event day, tournament, decklist)."""
    seen: dict[int, dict[tuple[int, str], date]] = defaultdict(dict)
    for _, tid, pa_, da, pb, db in matches:
        day = events.get(tid)
        if day is None:
            continue
        for pid, did in ((pa_, da), (pb, db)):
            if pid is not None and did in usable:
                seen[pid][(tid, did)] = day
    return {pid: sorted((d, t, lst) for (t, lst), d in v.items()) for pid, v in seen.items()}


def nearest(lists: list[tuple[date, int, str]], tid: int, day: date,
            window: int) -> tuple[date, int, str] | None:
    """The player's list from another event closest in time (earlier wins ties)."""
    best = None
    for d, t, lst in lists:
        if t == tid:
            continue
        gap = abs((d - day).days)
        if gap > window:
            continue
        key = (gap, d > day)  # same gap: prefer the earlier event
        if best is None or key < best[0]:
            best = (key, (d, t, lst))
    return best[1] if best else None


def impute(data_root: Path, window: int = DEFAULT_WINDOW_DAYS) -> list[Imputation]:
    events, usable, matches = load(data_root)
    lists = player_lists(matches, events, usable)
    out: list[Imputation] = []
    for guid, tid, pa_, da, pb, db in matches:
        day = events.get(tid)
        if day is None:
            continue
        for side, pid, did in (("a", pa_, da), ("b", pb, db)):
            if pid is None or did in usable:
                continue
            hit = nearest(lists.get(pid, []), tid, day, window)
            if hit is None:
                continue
            d, t, lst = hit
            out.append(Imputation(guid, tid, side, pid, lst, t, (d - day).days, did is not None))
    return out


def write(imputations: list[Imputation], data_root: Path) -> Path:
    out = silver_root(data_root) / "imputations.parquet"
    rows = [vars(i) for i in imputations]
    tmp = out.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pylist(rows) if rows else pa.table({
        "match_guid": pa.array([], pa.string()), "tournament_id": pa.array([], pa.int64()),
        "side": pa.array([], pa.string()), "player_id": pa.array([], pa.int64()),
        "decklist_id": pa.array([], pa.string()),
        "from_tournament_id": pa.array([], pa.int64()),
        "days_delta": pa.array([], pa.int64()), "had_record": pa.array([], pa.bool_()),
    }), tmp)
    tmp.replace(out)
    return out


def validate(data_root: Path, window: int = DEFAULT_WINDOW_DAYS) -> dict:
    """How often a player's lists at two nearby events were the same deck.

    For every player with usable lists at >= 2 events within the window, take
    each list and its nearest other list and compare: same archetype (from
    decklist_archetypes) and identical 75 (same card multiset, main + side).
    Reported per day-gap bucket so the window can be chosen from evidence.
    """
    root = silver_root(data_root)
    events, usable, matches = load(data_root)
    lists = player_lists(matches, events, usable)
    con = duckdb.connect()
    arch = {}
    arch_path = root / "decklist_archetypes.parquet"
    if arch_path.exists():
        arch = dict(con.execute(
            f"select decklist_id, archetype from '{arch_path.as_posix()}'").fetchall())
    cards: dict[str, frozenset] = defaultdict(set)
    cards_path = (root / "decklist_cards.parquet").as_posix()
    for did, sec, qty, card in con.execute(
            f"select decklist_id, section, qty, card from '{cards_path}'").fetchall():
        cards[did].add((sec, int(qty), card))
    con.close()

    buckets: dict[str, Counter] = defaultdict(Counter)
    for pid, pl in lists.items():
        if len(pl) < 2:
            continue
        for d, t, lst in pl:
            hit = nearest(pl, t, d, window)
            if hit is None:
                continue
            d2, _, lst2 = hit
            gap = abs((d2 - d).days)
            bucket = "0-1d" if gap <= 1 else "2-3d" if gap <= 3 else "4-7d" if gap <= 7 else "8-14d"
            b = buckets[bucket]
            b["pairs"] += 1
            if lst == lst2 or frozenset(cards[lst]) == frozenset(cards[lst2]):
                b["same_75"] += 1
            if arch and arch.get(lst) is not None and arch.get(lst) == arch.get(lst2):
                b["same_archetype"] += 1
    out = {}
    for bucket in ("0-1d", "2-3d", "4-7d", "8-14d"):
        b = buckets.get(bucket)
        if not b:
            continue
        out[bucket] = {"pairs": b["pairs"],
                       "same_75": round(b["same_75"] / b["pairs"], 3),
                       "same_archetype": round(b["same_archetype"] / b["pairs"], 3)}
    return out


def report(imputations: list[Imputation], validation: dict) -> str:
    lines = ["Same-75 imputation"]
    if validation:
        lines.append("  evidence (players with published lists at two nearby events):")
        lines.append(f"  {'gap':<7}{'pairs':>7}{'same 75':>9}{'same archetype':>16}")
        for bucket, v in validation.items():
            lines.append(f"  {bucket:<7}{v['pairs']:>7}{v['same_75']:>9.0%}"
                         f"{v['same_archetype']:>16.0%}")
    n = len(imputations)
    by_gap = Counter("0-1d" if abs(i.days_delta) <= 1 else "2-3d" if abs(i.days_delta) <= 3
                     else "4-7d" if abs(i.days_delta) <= 7 else "8-14d" for i in imputations)
    lines.append(f"  filled {n} match sides"
                 + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(by_gap.items())) + ")" if n else ""))
    lines.append(f"  of which {sum(1 for i in imputations if i.had_record)} had an empty list on record")
    return "\n".join(lines)
