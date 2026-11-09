"""Forecast next weekend's paper field and position each archetype against it.

Two weekly series of archetype shares feed the forecast:

* **paper** — Melee Swiss decklists (published, usable, classified), by the
  Monday of the event's week;
* **mtgo** — MTGO Challenge / League / Prelim lists, by the Monday of the
  publish week. Online players move first, so this is the leading signal. It
  comes in two flavours: every list counting 1 (``lists``), and lists counting
  by finish (``results``: challenge top 8 = 3, 9-16 = 2, other challenge lists
  1, a league 5-0 = 0.5). The second encodes "people copy the list that keeps
  top-8ing challenges, not a random 5-0" — a deck that top-8s three
  challenges in a fortnight weighs nine lists.

Candidate models for the share vector of week T, using weeks < T only:

* ``paper_last``      last paper week (the naive baseline)
* ``paper_ewma(h)``   exponentially weighted paper counts, half-life h weeks
                      (paper memory is long: players test what won the last RC)
* ``mtgo_recent(k)``  MTGO lists from the last k weeks
* ``mtgo_results(k)`` the same, each list weighted by its finish
* ``blend(a,h,k)``    a * paper_ewma(h) + (1-a) * mtgo_recent(k)
* ``blend_r(a,h,k)``  a * paper_ewma(h) + (1-a) * mtgo_results(k)
* ``momentum(h,b)``   paper_ewma(h) nudged by b * (MTGO last week - MTGO weeks 2-4)

``backtest`` scores every candidate on every past week with enough paper
lists (multinomial log-loss per list, plus total-variation distance), and the
best mean log-loss picks the model. ``position`` then weights the current
period's shrunk matchup win rates by the forecast field: an archetype's
expected match win rate against the field it will actually meet.

Tables (``gold/melee/forecast/``)
--------------------------
backtest           one row per (model, week): lists, log_loss, tvd
field_forecast     archetype, share (chosen model), paper_recent, mtgo_recent
position_forecast  archetype, expected_wr, matchup_coverage, current share/position
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from mtgmeta.archetypes.rules import UNCLASSIFIED
from mtgmeta.gold.matrix import gold_root
from mtgmeta.silver.melee import silver_root as melee_root
from mtgmeta.silver.mtgo import silver_root as mtgo_root

MIN_PAPER_LISTS = 150   # a week needs this many paper lists to score a forecast
MIN_HISTORY_WEEKS = 3
EPS = 0.002             # smoothing so an unseen archetype is not an infinite loss

Series = dict[date, Counter]   # week Monday -> archetype -> lists (or weight)
RESULT_WEIGHTS = {"top8": 3.0, "top16": 2.0, "tournament": 1.0, "league": 0.5}


@dataclass
class MtgoSeries:
    lists: Series        # every published list counts 1
    results: Series      # each list counts RESULT_WEIGHTS[finish]


def _lists(mtgo) -> Series:
    return mtgo.lists if isinstance(mtgo, MtgoSeries) else mtgo


def _results(mtgo) -> Series:
    return mtgo.results if isinstance(mtgo, MtgoSeries) else mtgo


def result_weight(event_type: str | None, rank: int | None) -> float:
    if event_type == "LEAGUE":
        return RESULT_WEIGHTS["league"]
    if rank is not None and rank <= 8:
        return RESULT_WEIGHTS["top8"]
    if rank is not None and rank <= 16:
        return RESULT_WEIGHTS["top16"]
    return RESULT_WEIGHTS["tournament"]


def week_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def forecast_root(data_root: Path) -> Path:
    return gold_root(data_root) / "forecast"


# -- inputs ------------------------------------------------------------------

def load_series(data_root: Path) -> tuple[Series, MtgoSeries, str | None]:
    """(paper, mtgo, latest rules period). Unclassified lists are dropped."""
    con = duckdb.connect()
    paper: Series = defaultdict(Counter)
    mroot = melee_root(data_root)
    period = None
    if (mroot / "decklist_archetypes.parquet").exists():
        rows = con.execute(f"""
            select substr(e.start, 1, 10), a.archetype, a.rules_file
            from '{(mroot / "decklist_archetypes.parquet").as_posix()}' a
            join '{(mroot / "decklists.parquet").as_posix()}' d using (decklist_id)
            join '{(mroot / "events.parquet").as_posix()}' e
              on e.tournament_id = d.first_tournament_id
            where d.usable and d.format = 'Modern'
        """).fetchall()
        for day, arch, rules in rows:
            if arch == UNCLASSIFIED or not day:
                continue
            paper[week_of(date.fromisoformat(day))][arch] += 1
            period = max(period or "", rules)
    lists: Series = defaultdict(Counter)
    results: Series = defaultdict(Counter)
    oroot = mtgo_root(data_root)
    if (oroot / "decklist_archetypes.parquet").exists():
        rows = con.execute(f"""
            select d.day, a.archetype, e.type, d.rank
            from '{(oroot / "decklist_archetypes.parquet").as_posix()}' a
            join '{(oroot / "decklists.parquet").as_posix()}' d using (decklist_id)
            left join '{(oroot / "events.parquet").as_posix()}' e using (site_name)
            where d.usable
        """).fetchall()
        for day, arch, etype, rank in rows:
            if arch != UNCLASSIFIED and day:
                wk = week_of(date.fromisoformat(day))
                lists[wk][arch] += 1
                results[wk][arch] += result_weight(etype, rank)
    con.close()
    if period:
        stem = period.rsplit(".", 1)[0]
        period = stem.split("-", 1)[1] if "-" in stem else stem
    return dict(paper), MtgoSeries(dict(lists), dict(results)), period


# -- models ------------------------------------------------------------------

def _norm(c: Counter) -> dict[str, float]:
    tot = sum(c.values())
    return {a: n / tot for a, n in c.items()} if tot else {}


def _weighted(series: Series, before: date, weights) -> Counter:
    """Sum counts of weeks < before, each week scaled by weights(weeks_ago)."""
    out: Counter = Counter()
    for wk, c in series.items():
        if wk >= before:
            continue
        ago = (before - wk).days // 7     # 1 = the week just before
        w = weights(ago)
        if w:
            for a, n in c.items():
                out[a] += n * w
    return out


def paper_last(paper: Series, mtgo, t: date) -> dict[str, float]:
    prev = [wk for wk in paper if wk < t]
    return _norm(paper[max(prev)]) if prev else {}


def paper_ewma(h: float):
    def model(paper: Series, mtgo, t: date) -> dict[str, float]:
        return _norm(_weighted(paper, t, lambda ago: 0.5 ** ((ago - 1) / h)))
    model.__name__ = f"paper_ewma({h:g})"
    return model


def mtgo_recent(k: int):
    def model(paper: Series, mtgo, t: date) -> dict[str, float]:
        return _norm(_weighted(_lists(mtgo), t, lambda ago: 1.0 if ago <= k else 0.0))
    model.__name__ = f"mtgo_recent({k})"
    return model


def mtgo_results(k: int):
    def model(paper: Series, mtgo, t: date) -> dict[str, float]:
        return _norm(_weighted(_results(mtgo), t, lambda ago: 1.0 if ago <= k else 0.0))
    model.__name__ = f"mtgo_results({k})"
    return model


def _blend(a: float, pm, mm, name: str):
    def model(paper: Series, mtgo, t: date) -> dict[str, float]:
        p, m = pm(paper, mtgo, t), mm(paper, mtgo, t)
        if not m:
            return p
        if not p:
            return m
        return {x: a * p.get(x, 0) + (1 - a) * m.get(x, 0) for x in set(p) | set(m)}
    model.__name__ = name
    return model


def blend(a: float, h: float, k: int):
    return _blend(a, paper_ewma(h), mtgo_recent(k), f"blend({a:g},{h:g},{k})")


def blend_r(a: float, h: float, k: int):
    return _blend(a, paper_ewma(h), mtgo_results(k), f"blend_r({a:g},{h:g},{k})")


def momentum(h: float, b: float):
    pm = paper_ewma(h)

    def model(paper: Series, mtgo, t: date) -> dict[str, float]:
        p = pm(paper, mtgo, t)
        ml = _lists(mtgo)
        last = _norm(_weighted(ml, t, lambda ago: 1.0 if ago == 1 else 0.0))
        prior = _norm(_weighted(ml, t, lambda ago: 1.0 if 2 <= ago <= 4 else 0.0))
        if not p or not last or not prior:
            return p
        out = {x: max(0.0, p.get(x, 0) + b * (last.get(x, 0) - prior.get(x, 0)))
               for x in set(p) | set(last) | set(prior)}
        tot = sum(out.values())
        return {x: v / tot for x, v in out.items()} if tot else p
    model.__name__ = f"momentum({h:g},{b:g})"
    return model


def candidates() -> list:
    out = [paper_last, paper_ewma(1), paper_ewma(2), paper_ewma(4),
           mtgo_recent(1), mtgo_recent(2), mtgo_recent(3),
           mtgo_results(1), mtgo_results(2), mtgo_results(3)]
    for a in (0.3, 0.5, 0.7):
        for h in (1, 2, 4):
            for k in (1, 2):
                out.append(blend(a, h, k))
                out.append(blend_r(a, h, k))
    for h in (1, 2):
        for b in (0.5, 1.0):
            out.append(momentum(h, b))
    return out


# -- scoring -----------------------------------------------------------------

def score(pred: dict[str, float], actual: Counter) -> tuple[float, float]:
    """(mean log-loss per list, total variation distance) of pred against actual."""
    n = sum(actual.values())
    names = set(pred) | set(actual)
    smooth = {a: (pred.get(a, 0) + EPS) for a in names}
    z = sum(smooth.values())
    ll = -sum(c * math.log(smooth[a] / z) for a, c in actual.items()) / n
    act = {a: c / n for a, c in actual.items()}
    tvd = 0.5 * sum(abs(pred.get(a, 0) - act.get(a, 0)) for a in names)
    return ll, tvd


@dataclass
class Backtest:
    rows: list[dict]
    means: dict[str, dict]      # model -> {weeks, log_loss, tvd}
    best: str | None


def backtest(paper: Series, mtgo, models=None,
             min_lists: int = MIN_PAPER_LISTS) -> Backtest:
    models = models or candidates()
    weeks = sorted(paper)
    rows = []
    for i, t in enumerate(weeks):
        if i < MIN_HISTORY_WEEKS or sum(paper[t].values()) < min_lists:
            continue
        for m in models:
            pred = m(paper, mtgo, t)
            if not pred:
                continue
            ll, tvd = score(pred, paper[t])
            rows.append({"model": m.__name__, "week": t.isoformat(),
                         "lists": sum(paper[t].values()), "log_loss": ll, "tvd": tvd})
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["model"]].append(r)
    n_weeks = max((len(v) for v in by.values()), default=0)
    means = {m: {"weeks": len(v),
                 "log_loss": sum(r["log_loss"] for r in v) / len(v),
                 "tvd": sum(r["tvd"] for r in v) / len(v)}
             for m, v in by.items() if len(v) == n_weeks}   # compare on the same weeks
    best = min(means, key=lambda m: means[m]["log_loss"]) if means else None
    return Backtest(rows, means, best)


# -- the forecast ------------------------------------------------------------

def next_target_week(today: date) -> date:
    """Monday of the week holding the next Saturday (this week's if it is still ahead)."""
    sat = today + timedelta(days=(5 - today.weekday()) % 7)
    return week_of(sat)


def field_forecast(paper: Series, mtgo, model, t: date) -> list[dict]:
    pred = model(paper, mtgo, t)
    def two(ago: int) -> float:
        return 1.0 if ago <= 2 else 0.0
    recent_p = _norm(_weighted(paper, t, two))
    recent_m = _norm(_weighted(_lists(mtgo), t, two))
    recent_r = _norm(_weighted(_results(mtgo), t, two))
    return sorted([{"archetype": a, "share": pred[a], "paper_recent": recent_p.get(a, 0.0),
                    "mtgo_recent": recent_m.get(a, 0.0),
                    "mtgo_results_recent": recent_r.get(a, 0.0), "model": model.__name__,
                    "target_week": t.isoformat()} for a in pred],
                  key=lambda r: -r["share"])


def position(data_root: Path, field: list[dict], period: str | None) -> list[dict]:
    """Expected match win rate against the forecast field, from the shrunk matrix."""
    path = gold_root(data_root) / "matchups.parquet"
    if not path.exists() or not field:
        return []
    con = duckdb.connect()
    rows = con.execute(f"""
        select archetype, opponent, win_rate_shrunk, matches from '{path.as_posix()}'
        {"where period = ?" if period else ""}
    """, [period] if period else []).fetchall()
    summ_path = gold_root(data_root) / "archetype_summary.parquet"
    summary = {}
    if summ_path.exists():
        summary = {r[0]: (r[1], r[2]) for r in con.execute(f"""
            select archetype, share, position from '{summ_path.as_posix()}'
            {"where period = ?" if period else ""}""", [period] if period else []).fetchall()}
    con.close()
    wr: dict[str, dict[str, tuple[float, int]]] = defaultdict(dict)
    for a, b, w, n in rows:
        wr[a][b] = (w, n)
    share = {f["archetype"]: f["share"] for f in field}
    out = []
    for a in wr:
        num = cov = 0.0
        for b, s in share.items():
            if b == a:
                num += s * 0.5
                cov += s
            elif b in wr[a]:
                num += s * wr[a][b][0]
                cov += s
        if cov <= 0:
            continue
        cur = summary.get(a, (None, None))
        out.append({"archetype": a, "expected_wr": num / cov, "matchup_coverage": cov,
                    "forecast_share": share.get(a, 0.0), "current_share": cur[0],
                    "current_position": cur[1],
                    "matches": sum(n for _, n in wr[a].values())})
    return sorted(out, key=lambda r: -r["expected_wr"])


def write(bt: Backtest, field: list[dict], pos: list[dict], data_root: Path) -> dict[str, Path]:
    out_dir = forecast_root(data_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, rows in (("backtest", bt.rows), ("field_forecast", field),
                       ("position_forecast", pos)):
        if not rows:
            continue
        tmp = out_dir / f"{name}.parquet.tmp"
        pq.write_table(pa.Table.from_pylist(rows), tmp)
        tmp.replace(out_dir / f"{name}.parquet")
        written[name] = out_dir / f"{name}.parquet"
    return written


def report(bt: Backtest, field: list[dict], pos: list[dict], top: int = 15) -> str:
    lines = ["Field forecast"]
    if bt.means:
        lines.append(f"  backtest over {max(v['weeks'] for v in bt.means.values())} paper weeks "
                     "(lower is better; log-loss per list, TVD = share mass misplaced):")
        lines.append(f"  {'model':<22}{'log-loss':>10}{'TVD':>8}")
        for m, v in sorted(bt.means.items(), key=lambda kv: kv[1]["log_loss"])[:10]:
            mark = "  <- chosen" if m == bt.best else ""
            lines.append(f"  {m:<22}{v['log_loss']:>10.4f}{v['tvd']:>8.3f}{mark}")
        lines.append("  best of each family (is the blend really beating its parts?):")
        fams: dict[str, tuple[str, dict]] = {}
        for m, v in bt.means.items():
            fam = m.split("(")[0]
            if fam not in fams or v["log_loss"] < fams[fam][1]["log_loss"]:
                fams[fam] = (m, v)
        for _, (m, v) in sorted(fams.items(), key=lambda kv: kv[1][1]["log_loss"]):
            lines.append(f"  {m:<22}{v['log_loss']:>10.4f}{v['tvd']:>8.3f}")
    else:
        lines.append("  not enough history to backtest; using paper_ewma(2)")
    if field:
        lines.append(f"\n  forecast field for week of {field[0]['target_week']} "
                     f"({field[0]['model']}):")
        lines.append(f"  {'archetype':<24}{'share':>7}{'paper 2w':>10}{'mtgo 2w':>9}"
                     f"{'results 2w':>12}")
        for f in field[:top]:
            lines.append(f"  {f['archetype']:<24}{f['share']:>7.1%}{f['paper_recent']:>10.1%}"
                         f"{f['mtgo_recent']:>9.1%}{f.get('mtgo_results_recent', 0.0):>12.1%}")
    if pos:
        lines.append("\n  position against that field (expected match win rate, shrunk matrix):")
        lines.append(f"  {'archetype':<24}{'exp WR':>7}{'cover':>7}{'fcst sh':>8}{'matches':>8}")
        for p in pos[:top]:
            lines.append(f"  {p['archetype']:<24}{p['expected_wr']:>7.1%}"
                         f"{p['matchup_coverage']:>7.0%}{p['forecast_share']:>8.1%}"
                         f"{p['matches']:>8}")
    return "\n".join(lines)


def run(data_root: Path, today: date | None = None, min_lists: int = MIN_PAPER_LISTS,
        ) -> tuple[Backtest, list[dict], list[dict]]:
    paper, mtgo, period = load_series(data_root)
    bt = backtest(paper, mtgo, min_lists=min_lists)
    model = next((m for m in candidates() if m.__name__ == bt.best), paper_ewma(2))
    t = next_target_week(today or date.today())
    field = field_forecast(paper, mtgo, model, t) if paper else []
    pos = position(data_root, field, period)
    return bt, field, pos
