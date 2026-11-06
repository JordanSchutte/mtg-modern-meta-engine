"""Matchup matrix on hand-made matches."""

from mtgmeta.gold import matrix


def _m(a, b, result="win", winner="a", ga=2, gb=0, period="p"):
    return {"tournament_id": 1, "round_number": 1, "result": result,
            "winner_player_id": 1 if winner == "a" else 2, "game_draws": 0,
            "player_a_id": 1, "decklist_a": a, "game_wins_a": ga,
            "player_b_id": 2, "decklist_b": b, "game_wins_b": gb}


def test_pairs_are_symmetric_and_shrunk():
    arch = {"e1": ("Energy", "p"), "b1": ("Blink", "p"), "u1": ("Unclassified", "p")}
    matches = [_m("e1", "b1")] * 3 + [_m("e1", "b1", winner="b", ga=1, gb=2)] \
        + [_m("e1", "b1", result="draw", ga=1, gb=1)] + [_m("e1", "u1")]
    pairs, summary = matrix.build(matches, arch, prior_n=10)
    by = {(r["archetype"], r["opponent"]): r for r in pairs}
    eb, be = by[("Energy", "Blink")], by[("Blink", "Energy")]
    assert eb["matches"] == be["matches"] == 5
    assert eb["wins"] == 3.5 and be["wins"] == 1.5
    assert abs(eb["win_rate"] - 0.7) < 1e-9
    assert abs(eb["win_rate_shrunk"] - (3.5 + 5) / 15) < 1e-9   # pulled toward 50%
    assert eb["wr_lo"] < 0.7 < eb["wr_hi"]
    assert eb["game_win_rate"] == 8 / 11 and be["game_win_rate"] == 3 / 11
    # Unclassified counts toward share but never forms a pair.
    s = {r["archetype"]: r for r in summary}
    assert "Unclassified" not in s
    assert abs(s["Energy"]["share"] - 6 / 12) < 1e-9 and abs(s["Blink"]["share"] - 5 / 12) < 1e-9
    assert abs(s["Energy"]["unclassified_share"] - 1 / 12) < 1e-9
    assert s["Energy"]["position"] == eb["win_rate_shrunk"]  # only one opponent


def test_mirrors_are_rows_at_fifty_percent():
    arch = {"e1": ("Energy", "p"), "e2": ("Energy", "p"), "b1": ("Blink", "p")}
    pairs, summary = matrix.build([_m("e1", "e2"), _m("e1", "b1")], arch)
    mirror = next(r for r in pairs if r["is_mirror"])
    assert mirror["matches"] == 2 and mirror["win_rate_shrunk"] == 0.5
    s = {r["archetype"]: r for r in summary}
    assert s["Energy"]["matches"] == 1   # mirror excluded from the record vs the field


def test_periods_do_not_mix():
    arch = {"e1": ("Energy", "p1"), "b1": ("Blink", "p1"), "e2": ("Energy", "p2"),
            "b2": ("Blink", "p2")}
    pairs, _ = matrix.build([_m("e1", "b1"), _m("e2", "b2"), _m("e1", "b2")], arch)
    assert {r["period"] for r in pairs} == {"p1", "p2"}
    assert all(r["matches"] == 1 for r in pairs)   # the cross-period match is dropped


def test_text_reports_render():
    arch = {"e1": ("Energy", "2026-05-18"), "b1": ("Blink", "2026-05-18")}
    pairs, summary = matrix.build([_m("e1", "b1")] * 25, arch)
    assert "Energy" in matrix.summary_text(summary)
    text = matrix.matrix_text(pairs, summary, min_matches=20)
    assert "Blink" in text and "%" in text
