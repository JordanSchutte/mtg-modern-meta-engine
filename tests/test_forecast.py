"""Field forecast: models, backtest selection, positioning."""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from mtgmeta.gold import forecast as fc
from mtgmeta.gold.matrix import gold_root

W = [date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21), date(2026, 9, 28),
     date(2026, 10, 5), date(2026, 10, 12)]


def test_week_helpers():
    assert fc.week_of(date(2026, 11, 8)) == date(2026, 11, 2)      # Sunday -> its Monday
    assert fc.next_target_week(date(2026, 11, 9)) == date(2026, 11, 9)   # Mon -> this Sat
    assert fc.next_target_week(date(2026, 11, 14)) == date(2026, 11, 9)  # Sat -> today
    assert fc.next_target_week(date(2026, 11, 15)) == date(2026, 11, 16)  # Sun -> next Sat


def test_models_use_only_earlier_weeks():
    paper = {W[0]: Counter(A=50, B=50), W[1]: Counter(A=80, B=20), W[2]: Counter(A=0, B=100)}
    mtgo = {W[1]: Counter(A=10, B=30), W[2]: Counter(A=100)}
    assert fc.paper_last(paper, mtgo, W[2]) == {"A": 0.8, "B": 0.2}
    ew = fc.paper_ewma(1)(paper, mtgo, W[2])
    assert abs(ew["A"] - (80 + 25) / 150) < 1e-9          # week 0 weighted 0.5
    assert fc.mtgo_recent(1)(paper, mtgo, W[2]) == {"A": 0.25, "B": 0.75}   # ignores W[2]
    bl = fc.blend(0.5, 1, 1)(paper, mtgo, W[2])
    assert abs(sum(bl.values()) - 1) < 1e-9 and bl["B"] > ew["B"]
    assert fc.paper_last(paper, mtgo, W[0]) == {}


def test_score_prefers_the_right_distribution():
    actual = Counter(A=70, B=30)
    good, _ = fc.score({"A": 0.7, "B": 0.3}, actual)
    bad, tvd = fc.score({"A": 0.3, "B": 0.7}, actual)
    assert good < bad and abs(tvd - 0.4) < 1e-9
    # an archetype the model never saw is a finite loss
    ll, _ = fc.score({"A": 1.0}, Counter(A=1, C=1))
    assert ll < 10


def test_backtest_picks_the_model_that_tracks_mtgo():
    # Paper follows MTGO with a one-week lag: deck C surges online in week 3, in paper week 4.
    paper = {W[0]: Counter(A=100, B=100), W[1]: Counter(A=100, B=100),
             W[2]: Counter(A=100, B=100), W[3]: Counter(A=100, B=100),
             W[4]: Counter(A=60, B=60, C=80), W[5]: Counter(A=50, B=50, C=100)}
    mtgo = {W[0]: Counter(A=20, B=20), W[1]: Counter(A=20, B=20), W[2]: Counter(A=20, B=20),
            W[3]: Counter(A=12, B=12, C=16), W[4]: Counter(A=10, B=10, C=20),
            W[5]: Counter(A=10, B=10, C=20)}
    bt = fc.backtest(paper, mtgo, min_lists=100)
    assert bt.best is not None and bt.best != "paper_last"
    assert bt.means[bt.best]["log_loss"] < bt.means["paper_last"]["log_loss"]
    weeks = {r["week"] for r in bt.rows}
    assert weeks == {W[3].isoformat(), W[4].isoformat(), W[5].isoformat()}
    model = next(m for m in fc.candidates() if m.__name__ == bt.best)
    field = fc.field_forecast(paper, mtgo, model, date(2026, 10, 19))
    assert field[0]["archetype"] == "C" and abs(sum(f["share"] for f in field) - 1) < 1e-9
    assert "chosen" in fc.report(bt, field, [])


def test_position_weights_matrix_by_forecast(tmp_path):
    gold = gold_root(tmp_path)
    gold.mkdir(parents=True)
    rows = []
    for a, b, wr in (("A", "B", 0.7), ("A", "C", 0.4), ("B", "A", 0.3), ("B", "C", 0.6),
                     ("C", "A", 0.6), ("C", "B", 0.4)):
        rows.append({"period": "2026-05-18", "archetype": a, "opponent": b,
                     "win_rate_shrunk": wr, "matches": 50})
    pq.write_table(pa.Table.from_pylist(rows), gold / "matchups.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"period": "2026-05-18", "archetype": x, "share": 1 / 3, "position": 0.5}
        for x in "ABC"]), gold / "archetype_summary.parquet")
    field = [{"archetype": "A", "share": 0.2}, {"archetype": "B", "share": 0.2},
             {"archetype": "C", "share": 0.6}]
    pos = fc.position(tmp_path, field, "2026-05-18")
    by = {p["archetype"]: p for p in pos}
    # A: 0.2*0.5 (mirror) + 0.2*0.7 + 0.6*0.4 = 0.48
    assert abs(by["A"]["expected_wr"] - 0.48) < 1e-9
    assert abs(by["B"]["expected_wr"] - (0.2 * 0.3 + 0.1 + 0.36)) < 1e-9
    assert pos[0]["archetype"] == "B" and by["A"]["matchup_coverage"] == 1.0
    assert by["A"]["current_share"] == 1 / 3
    written = fc.write(fc.Backtest([], {}, None), field, pos, tmp_path)
    # no target_week on these rows, so there is nothing to date the history copy by
    assert set(written) == {"field_forecast", "position_forecast"}
    assert Path(written["position_forecast"]).exists()


def test_mcp_tables_point_at_written_forecast_files(tmp_path):
    from mtgmeta.mcp.queries import TABLES
    fc.gold_root(tmp_path).mkdir(parents=True)
    written = fc.write(fc.Backtest([{"model": "m", "week": "w", "log_loss": 1.0, "tvd": 0.1,
                                     "lists": 1}], {}, None),
                       [{"archetype": "A", "share": 1.0, "target_week": "2026-11-23"}],
                       [{"archetype": "A"}], tmp_path)
    for table, key in (("forecast_backtest", "backtest"), ("field_forecast", "field_forecast"),
                       ("position_forecast", "position_forecast")):
        assert Path(written[key]) == tmp_path / TABLES[table]
    assert Path(written["forecast_history"]).match(TABLES["forecast_history"])


def test_write_keeps_a_dated_copy_per_target_week(tmp_path):
    fc.gold_root(tmp_path).mkdir(parents=True)
    bt = fc.Backtest([], {}, None)
    t0 = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    week1 = [{"archetype": "A", "share": 0.6, "model": "m", "target_week": "2026-11-16"},
             {"archetype": "B", "share": 0.4, "model": "m", "target_week": "2026-11-16"}]
    w = fc.write(bt, week1, [], tmp_path, generated_at=t0)
    assert w["forecast_history"] == fc.history_path(tmp_path, "2026-11-16")
    assert w["forecast_history"].parent == fc.forecast_root(tmp_path) / "field_forecast"
    # a refresh within the same target week replaces that week's file ...
    week1b = [{"archetype": "A", "share": 0.7, "model": "m", "target_week": "2026-11-16"},
              {"archetype": "B", "share": 0.3, "model": "m", "target_week": "2026-11-16"}]
    fc.write(bt, week1b, [], tmp_path, generated_at=t0 + timedelta(days=3))
    # ... and the next week adds a new one; field_forecast.parquet is only ever the latest
    week2 = [{"archetype": "A", "share": 0.5, "model": "m", "target_week": "2026-11-23"},
             {"archetype": "C", "share": 0.5, "model": "m", "target_week": "2026-11-23"}]
    fc.write(bt, week2, [], tmp_path, generated_at=t0 + timedelta(days=7))
    files = sorted(p.name for p in fc.history_root(tmp_path).glob("*.parquet"))
    assert files == ["2026-11-16.parquet", "2026-11-23.parquet"]
    assert not list(fc.history_root(tmp_path).glob("*.tmp"))
    old = pq.read_table(fc.history_path(tmp_path, "2026-11-16")).to_pylist()
    assert {r["archetype"]: r["share"] for r in old} == {"A": 0.7, "B": 0.3}
    assert {r["generated_at"] for r in old} == {"2026-11-19T09:00:00Z"}
    latest = pq.read_table(fc.forecast_root(tmp_path) / "field_forecast.parquet").to_pylist()
    assert {r["target_week"] for r in latest} == {"2026-11-23"}
    assert "generated_at" not in latest[0]


def test_result_weights_and_results_models():
    assert fc.result_weight("TOURNAMENT", 1) == 3.0 and fc.result_weight("TOURNAMENT", 8) == 3.0
    assert fc.result_weight("TOURNAMENT", 12) == 2.0 and fc.result_weight("TOURNAMENT", 30) == 1.0
    assert fc.result_weight("LEAGUE", None) == 0.5
    # Online: many C lists 5-0 leagues (volume), but B keeps top-8ing challenges (results).
    lists = {W[1]: Counter(A=10, B=4, C=20)}
    results = {W[1]: Counter(A=10.0, B=12.0, C=10.0)}
    m = fc.MtgoSeries(lists, results)
    assert fc.mtgo_recent(1)({}, m, W[2])["C"] > fc.mtgo_recent(1)({}, m, W[2])["B"]
    assert fc.mtgo_results(1)({}, m, W[2])["B"] > fc.mtgo_results(1)({}, m, W[2])["C"]
    # plain dicts still work as the mtgo input (both flavours read the same series)
    assert fc.mtgo_results(1)({}, lists, W[2]) == fc.mtgo_recent(1)({}, lists, W[2])
    # paper copies the results, so the results-weighted blend scores better
    paper = {w: Counter(A=100, B=100, C=100) for w in W[:4]}
    paper[W[4]] = Counter(A=90, B=130, C=80)
    paper[W[5]] = Counter(A=90, B=140, C=70)
    lists = {w: Counter(A=10, B=4, C=20) for w in W}
    results = {w: Counter(A=10.0, B=16.0, C=8.0) for w in W}
    bt = fc.backtest(paper, fc.MtgoSeries(lists, results), min_lists=100)
    assert bt.best.startswith("blend_r")
    field = fc.field_forecast(paper, fc.MtgoSeries(lists, results), fc.blend_r(0.5, 1, 1),
                              date(2026, 10, 19))
    assert field[0]["archetype"] == "B" and field[0]["mtgo_results_recent"] > \
        field[0]["mtgo_recent"]
    assert "best of each family" in fc.report(bt, field, [])
