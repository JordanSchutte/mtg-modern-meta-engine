"""Field forecast: models, backtest selection, positioning."""

from collections import Counter
from datetime import date
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
    assert set(written) == {"field_forecast", "position_forecast"}
    assert Path(written["position_forecast"]).exists()


def test_mcp_tables_point_at_written_forecast_files(tmp_path):
    from mtgmeta.mcp.queries import TABLES
    fc.gold_root(tmp_path).mkdir(parents=True)
    written = fc.write(fc.Backtest([{"model": "m", "week": "w", "log_loss": 1.0, "tvd": 0.1,
                                     "lists": 1}], {}, None),
                       [{"archetype": "A", "share": 1.0}], [{"archetype": "A"}], tmp_path)
    for table, key in (("forecast_backtest", "backtest"), ("field_forecast", "field_forecast"),
                       ("position_forecast", "position_forecast")):
        assert Path(written[key]) == tmp_path / TABLES[table]
