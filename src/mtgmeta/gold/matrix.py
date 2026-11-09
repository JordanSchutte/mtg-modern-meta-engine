"""Matchup matrix and archetype summary.

Inputs: silver ``matches`` (one row per match, competitors a/b) and
``decklist_archetypes`` (decklist -> archetype, with the rules file that
governs it, i.e. the format period), from both sources:

* paper (Melee): Modern, Swiss rounds, not a bye, both decklists known and
  both classified. These also define meta share.
* mtgo: the published top-8 bracket of each challenge, where both lists are on
  the same page. The Swiss rounds are not published, so MTGO adds matchup
  results but never meta share (a top 8 is not the field). Every row carries
  ``source`` and each pair reports how many of its matches came from MTGO.

``Unclassified`` is excluded from pairs but still counted in meta share. Draws
count half a win. A mirror is kept as a row (it's real meta share) but its win
rate is 50% by definition and it's flagged.

Shrinkage: each pair's win rate is pulled toward 50% by ``prior_n`` pseudo
matches, so 3-1 reads as about 56%, not 75%, and the pull fades as matches
accumulate. ``wr_lo``/``wr_hi`` is a 90% Wilson interval on the raw rate.
Game-level rates are also given; matches are coarse at these sample sizes.

Tables
------
matchups           one row per ordered pair (a, b) per period
archetype_summary  one row per archetype per period: share, record, position

``position`` is the share-weighted average of an archetype's shrunk win rate
against every other archetype — "how well placed is this deck against the
field as it actually is". It is the number the forecast will later replace
the current field with an expected one for.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from mtgmeta.archetypes.rules import UNCLASSIFIED
from mtgmeta.silver.melee import silver_root
from mtgmeta.silver.mtgo import silver_root as mtgo_silver_root

PRIOR_N = 10.0
Z90 = 1.6449


def gold_root(data_root: Path) -> Path:
    return data_root / "gold" / "melee"


@dataclass
class Pair:
    wins: float = 0.0   # draws add 0.5
    n: int = 0
    game_wins: int = 0
    game_losses: int = 0
    imputed: int = 0    # matches where at least one side's list was imputed
    mtgo: int = 0       # matches from MTGO top-8 brackets


def wilson(p: float, n: int, z: float = Z90) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def shrink(wins: float, n: int, prior_n: float = PRIOR_N) -> float:
    return (wins + prior_n / 2) / (n + prior_n)


def load_inputs(data_root: Path, impute: bool = True,
                ) -> tuple[list[dict], dict[str, tuple[str, str]]]:
    """Matches with both decks known (published, or imputed when ``impute``) and
    ``{decklist_id: (archetype, period)}``, over both sources.

    Imputed sides come from ``silver/melee/imputations.parquet`` (same player's
    nearby published list); each match row carries ``imputed_a`` / ``imputed_b``.
    A decklist that exists but is unusable counts as unknown unless imputed.
    MTGO bracket matches (``silver/mtgo/matches.parquet``) are appended with
    ``source = 'mtgo'``; paper rows carry ``source = 'paper'``.
    """
    root = silver_root(data_root)
    con = duckdb.connect()
    imp_path = root / "imputations.parquet"
    use_imp = impute and imp_path.exists()
    imp_join = f"""
        left join (select match_guid, decklist_id as imp_a
                   from '{imp_path.as_posix()}' where side = 'a') ia using (match_guid)
        left join (select match_guid, decklist_id as imp_b
                   from '{imp_path.as_posix()}' where side = 'b') ib using (match_guid)
    """ if use_imp else ""
    imp_cols = "imp_a, imp_b" if use_imp else "null as imp_a, null as imp_b"
    matches = con.execute(f"""
        with usable as (
            select decklist_id from '{(root / "decklists.parquet").as_posix()}' where usable
        )
        select m.tournament_id, m.round_number, m.result, m.winner_player_id, m.game_draws,
               m.player_a_id,
               case when m.decklist_a in (select decklist_id from usable) then m.decklist_a end
                   as decklist_a,
               m.game_wins_a, m.player_b_id,
               case when m.decklist_b in (select decklist_id from usable) then m.decklist_b end
                   as decklist_b,
               m.game_wins_b, {imp_cols}
        from '{(root / "matches.parquet").as_posix()}' m {imp_join}
        where m.is_swiss and m.format = 'Modern' and m.result <> 'bye'
    """).fetchall()
    cols = ["tournament_id", "round_number", "result", "winner_player_id", "game_draws",
            "player_a_id", "decklist_a", "game_wins_a", "player_b_id", "decklist_b",
            "game_wins_b", "imp_a", "imp_b"]
    rows = []
    for r in matches:
        d = dict(zip(cols, r, strict=True))
        d["imputed_a"] = d["decklist_a"] is None and d["imp_a"] is not None
        d["imputed_b"] = d["decklist_b"] is None and d["imp_b"] is not None
        d["decklist_a"] = d["decklist_a"] or d["imp_a"]
        d["decklist_b"] = d["decklist_b"] or d["imp_b"]
        d["source"] = "paper"
        if d["decklist_a"] and d["decklist_b"]:
            rows.append(d)
    arch = con.execute(f"""
        select decklist_id, archetype, rules_file
        from '{(root / "decklist_archetypes.parquet").as_posix()}'
    """).fetchall()
    archetypes = {d: (a, _period(f)) for d, a, f in arch}

    oroot = mtgo_silver_root(data_root)
    if (oroot / "matches.parquet").exists() and (oroot / "decklist_archetypes.parquet").exists():
        for r in con.execute(f"""
            select site_name, bracket, loginid_a, decklist_a, game_wins_a,
                   loginid_b, decklist_b, game_wins_b, winner_loginid
            from '{(oroot / "matches.parquet").as_posix()}'
        """).fetchall():
            site, bracket, la, da, ga, lb, db, gb, win = r
            rows.append({"tournament_id": site, "round_number": bracket, "result": "win",
                         "winner_player_id": win, "game_draws": 0,
                         "player_a_id": la, "decklist_a": da, "game_wins_a": ga,
                         "player_b_id": lb, "decklist_b": db, "game_wins_b": gb,
                         "imputed_a": False, "imputed_b": False, "source": "mtgo"})
        for d, a, f in con.execute(f"""
            select decklist_id, archetype, rules_file
            from '{(oroot / "decklist_archetypes.parquet").as_posix()}'
        """).fetchall():
            archetypes[d] = (a, _period(f))
    con.close()
    return rows, archetypes


def _period(rules_file: str) -> str:
    stem = rules_file.rsplit(".", 1)[0]
    return stem.split("-", 1)[1] if "-" in stem else stem


def build(matches: list[dict], archetypes: dict[str, tuple[str, str]],
          prior_n: float = PRIOR_N) -> tuple[list[dict], list[dict]]:
    pairs: dict[tuple[str, str, str], Pair] = defaultdict(Pair)     # (period, a, b)
    slots: Counter[tuple[str, str]] = Counter()                     # (period, archetype)
    slots_total: Counter[str] = Counter()
    for m in matches:
        a = archetypes.get(m["decklist_a"])
        b = archetypes.get(m["decklist_b"])
        if a is None or b is None or a[1] != b[1]:
            continue
        period = a[1]
        aa, bb = a[0], b[0]
        from_mtgo = m.get("source") == "mtgo"
        if not from_mtgo:                  # meta share is the paper Swiss field only
            slots_total[period] += 2
            slots[(period, aa)] += 1
            slots[(period, bb)] += 1
        if UNCLASSIFIED in (aa, bb):
            continue
        if m["result"] == "draw":
            wa = wb = 0.5
        elif m["result"] == "win":
            wa = 1.0 if m["winner_player_id"] == m["player_a_id"] else 0.0
            wb = 1.0 - wa
        else:
            continue  # unreported
        ga, gb = int(m["game_wins_a"] or 0), int(m["game_wins_b"] or 0)
        imputed = bool(m.get("imputed_a") or m.get("imputed_b"))
        for x, y, wx, gx, gy in ((aa, bb, wa, ga, gb), (bb, aa, wb, gb, ga)):
            p = pairs[(period, x, y)]
            p.wins += wx
            p.n += 1
            p.game_wins += gx
            p.game_losses += gy
            p.imputed += imputed
            p.mtgo += from_mtgo

    matchup_rows = []
    for (period, x, y), p in sorted(pairs.items()):
        raw = p.wins / p.n
        lo, hi = wilson(raw, p.n)
        games = p.game_wins + p.game_losses
        matchup_rows.append({
            "period": period, "archetype": x, "opponent": y, "is_mirror": x == y,
            "matches": p.n, "imputed_matches": p.imputed, "mtgo_matches": p.mtgo,
            "wins": p.wins, "win_rate": raw,
            "win_rate_shrunk": 0.5 if x == y else shrink(p.wins, p.n, prior_n),
            "wr_lo": lo, "wr_hi": hi,
            "games": games, "game_win_rate": (p.game_wins / games) if games else None,
        })

    summary_rows = []
    by_period: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in matchup_rows:
        by_period[r["period"]][r["archetype"]].append(r)
    for period, archs in by_period.items():
        total = slots_total[period]
        share = {a: (slots[(period, a)] / total if total else 0.0) for a in archs}
        unclassified_share = slots[(period, UNCLASSIFIED)] / total if total else 0.0
        for a, rows in archs.items():
            n = sum(r["matches"] for r in rows if not r["is_mirror"])
            w = sum(r["wins"] for r in rows if not r["is_mirror"])
            imp = sum(r["imputed_matches"] for r in rows if not r["is_mirror"])
            mtgo_n = sum(r["mtgo_matches"] for r in rows if not r["is_mirror"])
            # position: share-weighted shrunk win rate vs each other archetype
            weight = sum(share.get(r["opponent"], 0.0) for r in rows if not r["is_mirror"])
            position = (sum(share.get(r["opponent"], 0.0) * r["win_rate_shrunk"]
                            for r in rows if not r["is_mirror"]) / weight) if weight else None
            lo, hi = wilson(w / n, n) if n else (0.0, 1.0)
            summary_rows.append({
                "period": period, "archetype": a, "share": share[a],
                "matches": n, "imputed_matches": imp, "mtgo_matches": mtgo_n, "wins": w,
                "win_rate": (w / n) if n else None,
                "win_rate_shrunk": shrink(w, n, prior_n) if n else None,
                "wr_lo": lo, "wr_hi": hi,
                "position": position, "opponents": len(rows) - (1 if a in archs and any(
                    r["is_mirror"] for r in rows) else 0),
                "unclassified_share": unclassified_share,
            })
    summary_rows.sort(key=lambda r: (r["period"], -r["share"]))
    return matchup_rows, summary_rows


def write(matchups: list[dict], summary: list[dict], out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, rows in (("matchups", matchups), ("archetype_summary", summary)):
        target = out_dir / f"{name}.parquet"
        tmp = out_dir / f"{name}.parquet.tmp"
        pq.write_table(pa.Table.from_pylist(rows) if rows else pa.table({"empty": []}), tmp)
        tmp.replace(target)
        written[name] = target
    return written


def matrix_text(matchups: list[dict], summary: list[dict], period: str | None = None,
                top: int = 12, min_matches: int = 20) -> str:
    """A readable matrix of the top archetypes by share: shrunk win rates, row vs column."""
    if not summary:
        return "no matchups"
    period = period or max(r["period"] for r in summary)
    rows = [r for r in summary if r["period"] == period][:top]
    names = [r["archetype"] for r in rows]
    cell = {(m["archetype"], m["opponent"]): m for m in matchups if m["period"] == period}
    short = {n: (n[:9]) for n in names}
    lines = [f"Matchups, period {period}: shrunk match win rate of row vs column "
             f"(blank = under {min_matches} matches); share and position per row",
             " " * 22 + " ".join(f"{short[n]:>9}" for n in names)]
    for r in rows:
        cells = []
        for n in names:
            m = cell.get((r["archetype"], n))
            if n == r["archetype"]:
                cells.append(f"{'—':>9}")
            elif m is None or m["matches"] < min_matches:
                cells.append(f"{'':>9}")
            else:
                cells.append(f"{m['win_rate_shrunk']:>8.0%} ")
        pos = f"{r['position']:.0%}" if r["position"] is not None else "n/a"
        lines.append(f"{r['archetype'][:21]:<21} " + " ".join(cells)
                     + f"   share {r['share']:>5.1%}  pos {pos}  n={r['matches']}")
    return "\n".join(lines)


def summary_text(summary: list[dict], period: str | None = None, top: int = 30) -> str:
    if not summary:
        return "no archetypes"
    period = period or max(r["period"] for r in summary)
    rows = [r for r in summary if r["period"] == period][:top]
    lines = [f"Archetypes, period {period} (share of Swiss match slots; win rate vs the field, "
             f"shrunk; position = share-weighted matchup win rate)",
             f"{'archetype':<26}{'share':>7}{'matches':>9}{'win%':>7}{'90% CI':>14}{'pos':>6}"]
    for r in rows:
        wr = f"{r['win_rate_shrunk']:.1%}" if r["win_rate_shrunk"] is not None else "n/a"
        ci = f"{r['wr_lo']:.0%}–{r['wr_hi']:.0%}"
        pos = f"{r['position']:.1%}" if r["position"] is not None else "n/a"
        lines.append(f"{r['archetype'][:25]:<26}{r['share']:>7.1%}{r['matches']:>9}{wr:>7}"
                     f"{ci:>14}{pos:>6}")
    u = rows[0]["unclassified_share"] if rows else 0.0
    n_all = sum(r["matches"] for r in rows)
    imp_all = sum(r.get("imputed_matches", 0) for r in rows)
    mtgo_all = sum(r.get("mtgo_matches", 0) for r in rows)
    lines.append(f"\nunclassified share of match slots: {u:.1%}; "
                 f"matches using an imputed list: {imp_all} of {n_all}"
                 + (f" ({imp_all / n_all:.1%})" if n_all else "")
                 + f"; from MTGO top-8 brackets: {mtgo_all}"
                 + (f" ({mtgo_all / n_all:.1%})" if n_all else ""))
    return "\n".join(lines)
