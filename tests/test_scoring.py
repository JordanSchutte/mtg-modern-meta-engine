"""Out-of-sample forecast scoring: history files vs the paper week they were made for."""

import math
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from mtgmeta.gold import forecast as fc
from mtgmeta.gold import scoring as sc
from mtgmeta.mcp import queries as q
from mtgmeta.silver.melee import silver_root as melee_root

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "score_forecasts.py"
RULES = "modern-2026-05-18.yml"

# Three paper weeks. Week 11-16 (events Sat 11-21 and Sun 11-22) is the one forecast
# for: 100 A, 40 B, 20 C = 160 lists over 2 events. Week 11-09 (one event Sat 11-14):
# 90 A, 60 B = 150 lists. Week 11-02 (Sat 11-07): 50 A, 50 B = 100, under MIN_PAPER_LISTS.
WEEKS = {
    date(2026, 11, 2): [(1, date(2026, 11, 7), {"A": 50, "B": 50})],
    date(2026, 11, 9): [(2, date(2026, 11, 14), {"A": 90, "B": 60})],
    date(2026, 11, 16): [(3, date(2026, 11, 21), {"A": 70, "B": 20, "C": 10}),
                         (4, date(2026, 11, 22), {"A": 30, "B": 20, "C": 10})],
}


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _silver(root: Path) -> None:
    events, lists, archs = [], [], []
    for evs in WEEKS.values():
        for tid, day, counts in evs:
            events.append({"tournament_id": tid, "start": f"{day.isoformat()}T09:00:00",
                           "name": f"Event {tid}"})
            for arch, n in counts.items():
                for i in range(n):
                    did = f"{tid}-{arch}-{i}"
                    lists.append({"decklist_id": did, "usable": True, "first_tournament_id": tid,
                                  "format": "Modern"})
                    archs.append({"decklist_id": did, "archetype": arch, "rules_file": RULES})
    # an unusable list and an unclassified one never count
    lists.append({"decklist_id": "junk", "usable": False, "first_tournament_id": 3,
                  "format": "Modern"})
    archs.append({"decklist_id": "junk", "archetype": "A", "rules_file": RULES})
    lists.append({"decklist_id": "unk", "usable": True, "first_tournament_id": 3,
                  "format": "Modern"})
    archs.append({"decklist_id": "unk", "archetype": fc.UNCLASSIFIED, "rules_file": RULES})
    m = melee_root(root)
    _write(m / "events.parquet", events)
    _write(m / "decklists.parquet", lists)
    _write(m / "decklist_archetypes.parquet", archs)


def _history(root: Path, target: str, generated_at: datetime, shares: dict[str, float],
             model: str = "blend(0.5,2,1)") -> Path:
    field = [{"archetype": a, "share": s, "paper_recent": 0.0, "mtgo_recent": 0.0,
              "mtgo_results_recent": 0.0, "model": model, "target_week": target}
             for a, s in shares.items()]
    return fc.write(fc.Backtest([], {}, None), field, [], root,
                    generated_at=generated_at)["forecast_history"]


def test_paper_week_events_and_history_loading(tmp_path):
    _silver(tmp_path)
    weeks = sc.paper_week_events(tmp_path)
    assert weeks[date(2026, 11, 16)] == sc.WeekEvents(date(2026, 11, 21), date(2026, 11, 22), 2)
    assert weeks[date(2026, 11, 9)].events == 1
    paper, _, _ = fc.load_series(tmp_path)
    assert paper[date(2026, 11, 16)] == Counter(A=100, B=40, C=20)
    assert sc.load_history(tmp_path) == []
    p = _history(tmp_path, "2026-11-16", datetime(2026, 11, 19, 4, 17, tzinfo=UTC),
                 {"A": 0.5, "B": 0.3, "C": 0.2})
    (fc.history_root(tmp_path) / "notes.parquet").write_text("not parquet")   # ignored
    hist = sc.load_history(tmp_path)
    assert len(hist) == 1 and hist[0].path == p
    assert hist[0].target_week == date(2026, 11, 16) and hist[0].model == "blend(0.5,2,1)"
    assert hist[0].generated_at == datetime(2026, 11, 19, 4, 17, tzinfo=UTC)
    assert hist[0].shares == {"A": 0.5, "B": 0.3, "C": 0.2}


def test_clean_forecast_scored_leaky_one_skipped(tmp_path):
    _silver(tmp_path)
    # clean: written Thursday 11-19, two days before the first event of its week
    _history(tmp_path, "2026-11-16", datetime(2026, 11, 19, 4, 17, tzinfo=UTC),
             {"A": 0.5, "B": 0.3, "C": 0.2})
    # leaky: the watcher's Saturday-noon run rewrote the 11-09 file on the event day itself
    _history(tmp_path, "2026-11-09", datetime(2026, 11, 14, 16, 17, tzinfo=UTC),
             {"A": 0.6, "B": 0.4})
    # too early to score: that weekend has no lists yet
    _history(tmp_path, "2026-11-23", datetime(2026, 11, 23, 4, 17, tzinfo=UTC),
             {"A": 0.6, "B": 0.4})
    scoring, found = sc.run(tmp_path)
    assert found == 3 and len(scoring.rows) == 1
    assert [(r.target_week.isoformat(), why.split(" ")[0]) for r, why in scoring.skipped] == \
        [("2026-11-09", "leak"), ("2026-11-23", "no_paper")]
    row = scoring.rows[0]
    assert (row["target_week"], row["generated_at"], row["model"], row["forecast_lists"],
            row["events"]) == ("2026-11-16", "2026-11-19T04:17:00Z", "blend(0.5,2,1)", 160, 2)
    # by hand: actual shares .625/.25/.125 against .5/.3/.2, EPS smoothing as in score()
    smooth = {"A": 0.502, "B": 0.302, "C": 0.202}
    z = sum(smooth.values())
    ll = -(100 * math.log(0.502 / z) + 40 * math.log(0.302 / z) + 20 * math.log(0.202 / z)) / 160
    assert abs(row["log_loss"] - ll) < 1e-9
    assert abs(row["tvd"] - 0.5 * (0.125 + 0.05 + 0.075)) < 1e-9
    # baseline = last paper week as of 11-19, i.e. week 11-09 (A .6, B .4; C unseen)
    base = {"A": 0.602, "B": 0.402, "C": 0.002}
    zb = sum(base.values())
    bll = -(100 * math.log(0.602 / zb) + 40 * math.log(0.402 / zb)
            + 20 * math.log(0.002 / zb)) / 160
    assert abs(row["baseline_log_loss"] - bll) < 1e-9
    assert abs(row["baseline_tvd"] - 0.5 * (0.025 + 0.15 + 0.125)) < 1e-9
    assert row["log_loss"] < row["baseline_log_loss"]
    text = sc.report(scoring, found)
    assert "1 of 3 recorded forecasts scored" in text
    assert "beat the baseline on log-loss: 1 of 1" in text
    assert "leak" in text and "no_paper" in text
    # the table has exactly the documented columns, and run_sql reaches it
    path = sc.write(scoring, tmp_path)
    assert path == tmp_path / q.TABLES["forecast_scores"]
    assert pq.read_table(path).column_names == list(sc.COLUMNS)
    rows = q.run_sql(tmp_path, "select target_week, forecast_lists from forecast_scores")["rows"]
    assert rows == [{"target_week": "2026-11-16", "forecast_lists": 160}]


def test_too_few_lists_waits_and_baseline_respects_generated_at(tmp_path):
    _silver(tmp_path)
    paper, _, _ = fc.load_series(tmp_path)
    weeks = sc.paper_week_events(tmp_path)
    # week 11-02 has 100 lists: "too few" at the default, scored at a lower threshold —
    # and with no earlier paper at all, the row still scores, with an empty baseline
    rec = sc.Recorded(date(2026, 11, 2), datetime(2026, 10, 30, tzinfo=UTC), "m",
                      {"A": 0.5, "B": 0.5}, Path("x"))
    assert sc.score_history([rec], paper, weeks).skipped[0][1].startswith("too_few_lists")
    out = sc.score_history([rec], paper, weeks, min_lists=100)
    row = out.rows[0]
    assert row["forecast_lists"] == 100 and abs(row["tvd"]) < 1e-9
    assert row["baseline_log_loss"] is None and row["baseline_tvd"] is None
    assert "-" in sc.report(out, 1)
    # a forecast for 11-16 written on Saturday 11-14, while week 11-09's event was still
    # on, cannot have known that week: its baseline falls back to week 11-02
    early = sc.Recorded(date(2026, 11, 16), datetime(2026, 11, 14, 4, 17, tzinfo=UTC), "m",
                        {"A": 0.5, "B": 0.5}, Path("y"))
    assert sc.baseline(paper, weeks, early) == {"A": 0.5, "B": 0.5}
    late = sc.Recorded(date(2026, 11, 16), datetime(2026, 11, 15, 4, 17, tzinfo=UTC), "m",
                       {"A": 0.5, "B": 0.5}, Path("z"))
    assert sc.baseline(paper, weeks, late) == {"A": 0.6, "B": 0.4}


def test_generated_at_parsing():
    assert sc.parse_generated_at("2026-11-19T04:17:00Z") == datetime(2026, 11, 19, 4, 17,
                                                                     tzinfo=UTC)
    assert sc.parse_generated_at("2026-11-19T04:17:00+00:00").tzinfo is not None
    assert sc.parse_generated_at(datetime(2026, 11, 19, 4, 17)).tzinfo == UTC


def test_empty_data_root_scores_nothing_and_script_exits_clean(tmp_path):
    scoring, found = sc.run(tmp_path)
    assert (scoring.rows, scoring.skipped, found) == ([], [], 0)
    assert sc.write(scoring, tmp_path) is None
    assert "no recorded forecasts yet" in sc.report(scoring, found)
    # history but no silver: everything waits, nothing written
    _history(tmp_path, "2026-11-16", datetime(2026, 11, 19, tzinfo=UTC), {"A": 1.0})
    scoring, found = sc.run(tmp_path)
    assert found == 1 and not scoring.rows and scoring.skipped[0][1] == "no_paper"
    assert sc.write(scoring, tmp_path) is None
    empty = tmp_path / "empty"
    empty.mkdir()
    res = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                         cwd=REPO, env={**os.environ, "DATA_ROOT": str(empty)}, check=False)
    assert res.returncode == 0, res.stderr
    assert "no recorded forecasts yet" in res.stdout and "nothing to write" in res.stdout
    assert not list(empty.rglob("*"))
