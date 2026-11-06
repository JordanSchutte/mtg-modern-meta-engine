"""Matchup matrix and archetype summary.

Inputs: silver ``matches`` (one row per match, competitors a/b) and
``decklist_archetypes`` (decklist -> archetype, with the rules file that
governs it, i.e. the format period).

What counts: Modern, Swiss rounds, not a bye, both decklists known and both
classified (``Unclassified`` is excluded from pairs but still counted in meta
share). Draws count half a win. A mirror is kept as a row (it's real meta
share) but its win rate is 50% by definition and it's flagged.

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


def wilson(p: float, n: int, z: float = Z90) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def shrink(wins: float, n: int, prior_n: float = PRIOR_N) -> float:
    return (wins + prior_n / 2) / (n + prior_n)


def load_inputs(data_root: Path) -> tuple[list[dict], dict[str, tuple[str, str]]]:
    """Usable matches and ``{decklist_id: (archetype, period)}``."""
    root = silver_root(data_root)
    con = duckdb.connect()
    matches = con.execute(f"""
        select tournament_id, round_number, result, winner_player_id, game_draws,
               player_a_id, decklist_a, game_wins_a, player_b_id, decklist_b, game_wins_b
        from '{(root / "matches.parquet").as_posix()}'
        where is_swiss and format = 'Modern' and result <> 'bye' and both_decks_known
    """).fetchall()
    cols = ["tournament_id", "round_number", "result", "winner_player_id", "game_draws",
            "player_a_id", "decklist_a", "game_wins_a", "player_b_id", "decklist_b",
            "game_wins_b"]
    arch = con.execute(f"""
        select decklist_id, archetype, rules_file
        from '{(root / "decklist_archetypes.parquet").as_posix()}'
    """).fetchall()
    con.close()
    return ([dict(zip(cols, r, strict=True)) for r in matches],
            {d: (a, _period(f)) for d, a, f in arch})


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
        for x, y, wx, gx, gy in ((aa, bb, wa, ga, gb), (bb, aa, wb, gb, ga)):
            p = pairs[(period, x, y)]
            p.wins += wx
            p.n += 1
            p.game_wins += gx
            p.game_losses += gy

    matchup_rows = []
    for (period, x, y), p in sorted(pairs.items()):
        raw = p.wins / p.n
        lo, hi = wilson(raw, p.n)
        games = p.game_wins + p.game_losses
        matchup_rows.append({
            "period": period, "archetype": x, "opponent": y, "is_mirror": x == y,
            "matches": p.n, "wins": p.wins, "win_rate": raw,
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
        share = {a: slots[(period, a)] / total for a in archs}
        unclassified_share = slots[(period, UNCLASSIFIED)] / total if total else 0.0
        for a, rows in archs.items():
            n = sum(r["matches"] for r in rows if not r["is_mirror"])
            w = sum(r["wins"] for r in rows if not r["is_mirror"])
            # position: share-weighted shrunk win rate vs each other archetype
            weight = sum(share.get(r["opponent"], 0.0) for r in rows if not r["is_mirror"])
            position = (sum(share.get(r["opponent"], 0.0) * r["win_rate_shrunk"]
                            for r in rows if not r["is_mirror"]) / weight) if weight else None
            lo, hi = wilson(w / n, n) if n else (0.0, 1.0)
            summary_rows.append({
                "period": period, "archetype": a, "share": share[a],
                "matches": n, "wins": w, "win_rate": (w / n) if n else None,
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
    lines.append(f"\nunclassified share of match slots: {u:.1%}")
    return "\n".join(lines)
