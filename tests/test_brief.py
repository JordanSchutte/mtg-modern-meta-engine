"""Weekly meta brief: the pure pieces and an end-to-end run on synthetic silver + gold."""

from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from mtgmeta.gold import brief as b
from mtgmeta.gold.forecast import forecast_root
from mtgmeta.gold.matrix import gold_root
from mtgmeta.mcp import queries as q
from mtgmeta.silver.melee import silver_root as melee_root
from mtgmeta.silver.mtgo import silver_root as mtgo_root

TODAY = date(2026, 11, 16)          # a Monday; target week = this week (Saturday 11-21)
RULES = "modern-2026-05-18.yml"


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_movers_and_positioning_rules():
    now = [{"archetype": "A", "share": 0.20}, {"archetype": "B", "share": 0.10},
           {"archetype": "C", "share": 0.05}]
    before = {"A": 0.17, "B": 0.10, "D": 0.02}
    mv = b.movers(now, before)
    assert [(m["archetype"], round(m["delta"], 2)) for m in mv] == [("C", 0.05), ("A", 0.03),
                                                                   ("D", -0.02)]
    assert b.movers(now, {}) == []                      # no earlier brief: nothing to say
    pos = [{"archetype": "A", "expected_wr": 0.55, "matchup_coverage": 0.95,
            "forecast_share": 0.2, "matches": 500},
           {"archetype": "B", "expected_wr": 0.56, "matchup_coverage": 0.5,    # too little cover
            "forecast_share": 0.1, "matches": 50},
           {"archetype": "C", "expected_wr": 0.53, "matchup_coverage": 0.92,
            "forecast_share": 0.01, "matches": 120}]
    assert [p["archetype"] for p in b.best_positioned(pos)] == ["A", "C"]
    assert [p["archetype"] for p in b.underplayed(pos)] == ["C"]


def test_swings_use_unordered_pairs_with_enough_matches():
    def row(a, o, wr, n):
        return {"archetype": a, "opponent": o, "win_rate_shrunk": wr, "matches": n,
                "is_mirror": a == o}
    now = [row("A", "B", 0.58, 50), row("B", "A", 0.42, 50), row("A", "C", 0.70, 10),
           row("C", "A", 0.30, 10), row("A", "A", 0.5, 20)]
    before = [row("A", "B", 0.50, 40), row("B", "A", 0.50, 40), row("A", "C", 0.50, 5),
              row("C", "A", 0.50, 5)]
    sw = b.swings(now, before)
    assert len(sw) == 1 and sw[0]["archetype"] == "A" and sw[0]["opponent"] == "B"
    assert sw[0]["new_matches"] == 10 and abs(sw[0]["delta"] - 0.08) < 1e-9


def test_describe_model_reads_as_english():
    assert "50% paper" in b.describe_model("blend(0.5,1,1)")
    assert "weighted by finish" in b.describe_model("blend_r(0.5,1,1)")
    assert "last week" in b.describe_model("paper_last")
    assert b.describe_model(None).startswith("no model")
    assert b.describe_model("something_new(1)") == "something_new(1)"


def _synthetic(root: Path, today: date, a_share: float) -> None:
    """Two archetypes A and B. Last week's event: 40 A-vs-B matches split evenly. This
    week's event: 10 more, all won by A, plus one MTGO bracket match."""
    last, this = today - timedelta(days=10), today - timedelta(days=3)
    m = melee_root(root)
    _write(m / "events.parquet", [
        {"tournament_id": 1, "start": f"{last.isoformat()}T09:00:00", "name": "Last week RCQ",
         "players_estimate": 120, "modern_swiss_matches": 40, "modern_swiss_known": 40,
         "decklist_ids": 2, "unusable_decklists": 0, "decklists_missing": 0},
        {"tournament_id": 2, "start": f"{this.isoformat()}T09:00:00", "name": "This week RCQ",
         "players_estimate": 110, "modern_swiss_matches": 10, "modern_swiss_known": 10,
         "decklist_ids": 3, "unusable_decklists": 1, "decklists_missing": 0}])
    _write(m / "decklists.parquet", [
        {"decklist_id": "a1", "usable": True, "first_tournament_id": 1, "format": "Modern"},
        {"decklist_id": "b1", "usable": True, "first_tournament_id": 1, "format": "Modern"},
        {"decklist_id": "a2", "usable": True, "first_tournament_id": 2, "format": "Modern"},
        {"decklist_id": "b2", "usable": True, "first_tournament_id": 2, "format": "Modern"},
        {"decklist_id": "x2", "usable": False, "first_tournament_id": 2, "format": "Modern"}])
    _write(m / "decklist_archetypes.parquet", [
        {"decklist_id": d, "archetype": a, "rules_file": RULES}
        for d, a in (("a1", "A"), ("b1", "B"), ("a2", "A"), ("b2", "B"))])
    matches = []
    for i in range(40):
        matches.append({"tournament_id": 1, "round_number": 1 + i // 10, "result": "win",
                        "winner_player_id": "pa" if i % 2 == 0 else "pb", "game_draws": 0,
                        "player_a_id": "pa", "decklist_a": "a1", "game_wins_a": 2,
                        "player_b_id": "pb", "decklist_b": "b1", "game_wins_b": 1,
                        "is_swiss": True, "format": "Modern"})
    for i in range(10):
        matches.append({"tournament_id": 2, "round_number": 1 + i // 5, "result": "win",
                        "winner_player_id": "pa", "game_draws": 0,
                        "player_a_id": "pa", "decklist_a": "a2", "game_wins_a": 2,
                        "player_b_id": "pb", "decklist_b": "b2", "game_wins_b": 0,
                        "is_swiss": True, "format": "Modern"})
    _write(m / "matches.parquet", matches)

    o = mtgo_root(root)
    _write(o / "events.parquet", [
        {"site_name": "modern-challenge-32-x", "day": this.isoformat(), "status": "complete",
         "decklists": 32, "type": "TOURNAMENT", "kind": "challenge-32"}])
    _write(o / "decklist_archetypes.parquet", [
        {"decklist_id": "mtgo:modern-challenge-32-x:1", "archetype": "A", "rules_file": RULES},
        {"decklist_id": "mtgo:modern-challenge-32-x:2", "archetype": "B", "rules_file": RULES}])
    _write(o / "matches.parquet", [
        {"site_name": "modern-challenge-32-x", "bracket": 0, "loginid_a": "1",
         "decklist_a": "mtgo:modern-challenge-32-x:1", "game_wins_a": 2, "loginid_b": "2",
         "decklist_b": "mtgo:modern-challenge-32-x:2", "game_wins_b": 0, "winner_loginid": "1"}])

    f = forecast_root(root)
    target = b.next_target_week(today).isoformat()
    _write(f / "field_forecast.parquet", [
        {"archetype": "A", "share": a_share, "paper_recent": 0.5, "mtgo_recent": 0.5,
         "model": "blend(0.5,1,1)", "target_week": target},
        {"archetype": "B", "share": 1 - a_share, "paper_recent": 0.5, "mtgo_recent": 0.5,
         "model": "blend(0.5,1,1)", "target_week": target}])
    _write(f / "position_forecast.parquet", [
        {"archetype": "A", "expected_wr": 0.54, "matchup_coverage": 1.0,
         "forecast_share": a_share, "matches": 50},
        {"archetype": "B", "expected_wr": 0.46, "matchup_coverage": 1.0,
         "forecast_share": 1 - a_share, "matches": 50}])
    _write(f / "backtest.parquet", [
        {"model": "blend(0.5,1,1)", "week": "2026-11-02", "log_loss": 3.1, "tvd": 0.15},
        {"model": "paper_last", "week": "2026-11-02", "log_loss": 3.2, "tvd": 0.18}])


def test_brief_end_to_end(tmp_path):
    _synthetic(tmp_path, TODAY, a_share=0.6)
    text, inp = b.run(tmp_path, today=TODAY)
    assert inp.target_week == date(2026, 11, 16)
    assert text.startswith("# Modern meta brief — week of 2026-11-16")
    for heading in ("## 1. Forecast field", "## 2. Movers", "## 3. Best-positioned decks",
                    "## 4. Matrix changes", "## 5. Data notes"):
        assert heading in text
    # 1: model in words plus the baseline comparison, with log-loss explained
    assert "`blend(0.5,1,1)` — 50% paper" in text
    assert "3.100 against 3.200 for the paper-only baseline" in text
    assert "Log-loss measures" in text
    # 2: first brief ever, so no movers yet
    assert "No earlier brief to compare against yet" in text
    # 3: A is best positioned; nothing is underplayed (shares are 60/40)
    assert "| A | 54.0% | 60.0% | 100% | 50 |" in text
    assert "No underplayed deck" in text
    # 4: this week's 10 paper A wins plus the MTGO bracket win move A vs B from 50%
    # to (31+5)/61 = 59.0%
    assert "1 new MTGO top-8 bracket matches this week" in text
    assert "| A vs B | 59.0% | 50.0% | +9.0 pts | 51 (+11) |" in text
    # 5: lists this week vs last, and the empty-list flag on this week's event
    assert ("Paper: 2 usable Melee lists from 1 event(s) this week, against 2 last week (+0)."
            in text)
    assert "MTGO: 1 event(s) fetched with 32 lists." in text
    assert "1 empty list(s)" in text
    # the marker carries the forecast field for next week's movers
    marker = b.read_marker(text)
    assert marker["target_week"] == "2026-11-16" and marker["field"] == {"A": 0.6, "B": 0.4}

    written = b.write(text, inp.target_week, tmp_path)
    assert written["dated"].name == "brief-2026-11-16.md" and written["latest"].name == "brief.md"
    assert b.latest(tmp_path) == text

    # Next week: A's forecast share jumps 15 points -> a mover vs the previous brief.
    next_monday = TODAY + timedelta(days=7)
    _synthetic(tmp_path, next_monday, a_share=0.75)
    text2, inp2 = b.run(tmp_path, today=next_monday)
    assert inp2.previous_date == date(2026, 11, 16)
    assert "Compared with the brief for the week of 2026-11-16" in text2
    assert "| A | 75.0% | 60.0% | +15.0 pts |" in text2
    assert "| B | 25.0% | 40.0% | -15.0 pts |" in text2
    b.write(text2, inp2.target_week, tmp_path)
    assert sorted(p.name for p in b.brief_root(tmp_path).glob("*.md")) == [
        "brief-2026-11-16.md", "brief-2026-11-23.md", "brief.md"]

    # The MCP query hands back the latest one.
    out = q.brief(tmp_path)
    assert out["target_week"] == "2026-11-23" and out["markdown"] == text2
    assert "error" in q.brief(tmp_path / "nowhere")


def test_brief_without_any_tables_still_renders(tmp_path):
    gold_root(tmp_path).mkdir(parents=True)
    text, inp = b.run(tmp_path, today=TODAY)
    assert "No forecast has been built yet" in text
    assert "No matchup matrix available" in text
    assert b.read_marker(text)["field"] == {}
