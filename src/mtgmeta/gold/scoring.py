"""Score each recorded forecast against the paper week it was made for.

The backtest in ``gold/forecast.py`` is in-sample: pretend-forecasts for past
weeks, made today, and the model list itself was chosen on those weeks. This
module is the out-of-sample record: every dated copy under
``gold/melee/forecast/field_forecast/<target_week>.parquet`` was written down
*before* its weekend (``generated_at``), and once that week's Melee lists are
in silver it is scored against what actually showed up. Nothing here feeds
back into the model — scores are reported, never used to pick anything.

Per history file, in order:

* the realized paper week is the ``load_series`` paper share series keyed by
  ``target_week``; no lists, or fewer than ``MIN_PAPER_LISTS``, and the file
  waits (reason ``no_paper`` / ``too_few_lists``);
* **leak guard** — a file whose ``generated_at`` falls on or after the first
  event day of its target week was overwritten by a watcher run that already
  knew the weekend (the watcher runs twice a day and rewrites the file within
  the target week), so it is not a forecast and is skipped (reason ``leak``);
* otherwise ``forecast.score`` gives log-loss and TVD, and the same for the
  naive baseline — ``paper_last`` as of ``generated_at``, i.e. the latest paper
  week whose events had all finished before the forecast was written — so
  each row says what "same as last paper week" would have got.

Table ``gold/melee/forecast/forecast_scores.parquet``: ``target_week,
generated_at, model, forecast_lists, log_loss, tvd, baseline_log_loss,
baseline_tvd, events``. Rebuilt from the history files + silver on every run.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from mtgmeta.archetypes.rules import UNCLASSIFIED
from mtgmeta.gold import forecast as fc
from mtgmeta.silver.melee import silver_root as melee_root

log = logging.getLogger(__name__)

SCORES_FILE = "forecast_scores.parquet"
COLUMNS = ("target_week", "generated_at", "model", "forecast_lists", "log_loss", "tvd",
           "baseline_log_loss", "baseline_tvd", "events")


@dataclass
class WeekEvents:
    first_day: date     # first event day in the week (the leak-guard cutoff)
    last_day: date      # last event day (a week counts for the baseline once this has passed)
    events: int


@dataclass
class Recorded:
    """One history file: what was forecast for ``target_week``, and when."""
    target_week: date
    generated_at: datetime
    model: str | None
    shares: dict[str, float]
    path: Path


@dataclass
class Scoring:
    rows: list[dict]              # the forecast_scores table
    skipped: list[tuple[Recorded, str]]


def scores_path(data_root: Path) -> Path:
    return fc.forecast_root(data_root) / SCORES_FILE


def parse_generated_at(value) -> datetime:
    """``generated_at`` as written by ``forecast.write`` (``...Z``), or any ISO stamp."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def load_history(data_root: Path) -> list[Recorded]:
    """Every dated field forecast on record, oldest target week first.

    The target week is the file stem (``2026-11-16.parquet``); the rows carry
    the same ``target_week``, one ``model`` and one ``generated_at``. A file
    that cannot be read, or has no shares, is logged and left out.
    """
    out = []
    for path in sorted(fc.history_root(data_root).glob("*.parquet")):
        try:
            week = date.fromisoformat(path.stem)
            rows = pq.read_table(path).to_pylist()
        except (ValueError, OSError) as exc:    # a stray file, not a reason to stop scoring
            log.warning("skipping unreadable history file %s: %s", path, exc)
            continue
        rows = [r for r in rows if r.get("archetype") and r.get("share") is not None]
        if not rows or not rows[0].get("generated_at"):
            log.warning("skipping history file without shares or generated_at: %s", path)
            continue
        out.append(Recorded(week, parse_generated_at(rows[0]["generated_at"]),
                            rows[0].get("model"), {r["archetype"]: float(r["share"])
                                                   for r in rows}, path))
    return out


def paper_week_events(data_root: Path) -> dict[date, WeekEvents]:
    """Per paper week (same events as ``load_series``: usable, classified Modern lists):
    first and last event day and the number of events."""
    mroot = melee_root(data_root)
    if not (mroot / "decklist_archetypes.parquet").exists():
        return {}
    con = duckdb.connect()
    rows = con.execute(f"""
        select distinct substr(e.start, 1, 10) as day, e.tournament_id
        from '{(mroot / "decklist_archetypes.parquet").as_posix()}' a
        join '{(mroot / "decklists.parquet").as_posix()}' d using (decklist_id)
        join '{(mroot / "events.parquet").as_posix()}' e
          on e.tournament_id = d.first_tournament_id
        where d.usable and d.format = 'Modern' and a.archetype <> ?
    """, [UNCLASSIFIED]).fetchall()
    con.close()
    by_week: dict[date, list[date]] = {}
    for day, _ in rows:
        if day:
            d = date.fromisoformat(day)
            by_week.setdefault(fc.week_of(d), []).append(d)
    return {wk: WeekEvents(min(days), max(days), len(days)) for wk, days in by_week.items()}


def baseline(paper: fc.Series, weeks: dict[date, WeekEvents], rec: Recorded) -> dict[str, float]:
    """``paper_last`` as the forecast's author could have computed it: the latest paper
    week before the target whose events had all ended before ``generated_at``."""
    cutoff = rec.generated_at.date()
    known = {wk: c for wk, c in paper.items()
             if wk < rec.target_week and wk in weeks and weeks[wk].last_day < cutoff}
    return fc.paper_last(known, None, rec.target_week)


def score_history(history: list[Recorded], paper: fc.Series, weeks: dict[date, WeekEvents],
                  min_lists: int = fc.MIN_PAPER_LISTS) -> Scoring:
    rows, skipped = [], []
    for rec in history:
        actual: Counter | None = paper.get(rec.target_week)
        wk = weeks.get(rec.target_week)
        if not actual or wk is None:
            skipped.append((rec, "no_paper"))
            continue
        n = sum(actual.values())
        if n < min_lists:
            skipped.append((rec, f"too_few_lists ({n} < {min_lists})"))
            continue
        if rec.generated_at.date() >= wk.first_day:
            skipped.append((rec, f"leak (generated {rec.generated_at.date()} on/after "
                                 f"first event {wk.first_day})"))
            continue
        ll, tvd = fc.score(rec.shares, actual)
        base = baseline(paper, weeks, rec)
        bll, btvd = fc.score(base, actual) if base else (None, None)
        rows.append({"target_week": rec.target_week.isoformat(),
                     "generated_at": rec.generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "model": rec.model, "forecast_lists": n, "log_loss": ll, "tvd": tvd,
                     "baseline_log_loss": bll, "baseline_tvd": btvd, "events": wk.events})
    return Scoring(rows, skipped)


def write(sc: Scoring, data_root: Path) -> Path | None:
    """Write ``forecast_scores.parquet``; nothing scored, nothing written."""
    if not sc.rows:
        return None
    path = scores_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    return fc._write_parquet([{c: r[c] for c in COLUMNS} for r in sc.rows], path)


def report(sc: Scoring, history_count: int) -> str:
    lines = ["Forecast scoring (out of sample: written before the weekend, scored after)"]
    if not history_count:
        lines.append("  no recorded forecasts yet")
        return "\n".join(lines)
    if sc.rows:
        lines.append(f"  {len(sc.rows)} of {history_count} recorded forecasts scored "
                     "(log-loss per list, TVD = share mass misplaced; lower is better; "
                     "baseline = last paper week as of the forecast):")
        lines.append(f"  {'target week':<13}{'generated':<12}{'model':<22}{'lists':>6}"
                     f"{'log-loss':>10}{'TVD':>7}{'base ll':>9}{'base TVD':>9}")
        for r in sc.rows:
            bll = f"{r['baseline_log_loss']:.4f}" if r["baseline_log_loss"] is not None else "-"
            btvd = f"{r['baseline_tvd']:.3f}" if r["baseline_tvd"] is not None else "-"
            lines.append(f"  {r['target_week']:<13}{r['generated_at'][:10]:<12}"
                         f"{(r['model'] or '?'):<22}{r['forecast_lists']:>6}"
                         f"{r['log_loss']:>10.4f}{r['tvd']:>7.3f}{bll:>9}{btvd:>9}")
        beat = sum(1 for r in sc.rows if r["baseline_log_loss"] is not None
                   and r["log_loss"] < r["baseline_log_loss"])
        with_base = sum(1 for r in sc.rows if r["baseline_log_loss"] is not None)
        if with_base:
            lines.append(f"  beat the baseline on log-loss: {beat} of {with_base}")
    else:
        lines.append(f"  0 of {history_count} recorded forecasts scored")
    if sc.skipped:
        lines.append("  not scored:")
        for rec, why in sc.skipped:
            lines.append(f"  {rec.target_week.isoformat():<13}{why}")
    return "\n".join(lines)


def run(data_root: Path, min_lists: int = fc.MIN_PAPER_LISTS) -> tuple[Scoring, int]:
    """(scoring, number of history files found)."""
    history = load_history(data_root)
    if not history:
        return Scoring([], []), 0
    paper, _, _ = fc.load_series(data_root)
    weeks = paper_week_events(data_root)
    return score_history(history, paper, weeks, min_lists=min_lists), len(history)
