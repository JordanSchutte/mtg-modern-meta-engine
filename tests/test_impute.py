"""Same-75 imputation on a hand-built silver."""

from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from mtgmeta.gold import matrix
from mtgmeta.silver import impute
from mtgmeta.silver.melee import silver_root


def test_nearest_prefers_closest_then_earlier():
    lists = [(date(2026, 9, 4), 1, "fri"), (date(2026, 9, 6), 3, "sun"),
             (date(2026, 9, 20), 4, "late")]
    # Saturday event (id 2): Friday and Sunday are both 1 day away -> earlier wins
    assert impute.nearest(lists, 2, date(2026, 9, 5), 14) == (date(2026, 9, 4), 1, "fri")
    # the player's own list at this event is never a candidate
    assert impute.nearest(lists, 1, date(2026, 9, 4), 14) == (date(2026, 9, 6), 3, "sun")
    # outside the window -> nothing
    assert impute.nearest([(date(2026, 9, 20), 4, "late")], 2, date(2026, 9, 5), 3) is None


def _silver(tmp_path):
    root = silver_root(tmp_path)
    root.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"tournament_id": 1, "start": "2026-09-04", "name": "Friday RCQ"},
        {"tournament_id": 2, "start": "2026-09-05", "name": "Saturday RC"},
    ]), root / "events.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"decklist_id": "p1-fri", "usable": True, "format": "Modern"},
        {"decklist_id": "p2-fri", "usable": True, "format": "Modern"},
        {"decklist_id": "p2-sat", "usable": True, "format": "Modern"},
        {"decklist_id": "p3-sat-empty", "usable": False, "format": "Modern"},
        {"decklist_id": "p3-fri", "usable": True, "format": "Modern"},
    ]), root / "decklists.parquet")
    base = {"is_swiss": True, "format": "Modern", "result": "win", "round_number": 1,
            "game_draws": 0, "game_wins_a": 2, "game_wins_b": 0}
    # PlayerId is per tournament on Melee, so Saturday's ids differ; usernames match.
    pq.write_table(pa.Table.from_pylist([
        # Friday: everyone published
        {**base, "match_guid": "f1", "tournament_id": 1, "player_a_id": 1, "decklist_a": "p1-fri",
         "player_a_user": "alice", "player_a_name": "Alice", "player_b_id": 2,
         "decklist_b": "p2-fri", "player_b_user": "bob", "player_b_name": "Bob",
         "winner_player_id": 1, "both_decks_known": True},
        {**base, "match_guid": "f2", "tournament_id": 1, "player_a_id": 3, "decklist_a": "p3-fri",
         "player_a_user": None, "player_a_name": "Carol", "player_b_id": 4, "decklist_b": None,
         "player_b_user": "dan", "player_b_name": "Dan", "winner_player_id": 3,
         "both_decks_known": False},
        # Saturday: player 1 has no list (-> p1-fri), player 3 has an empty record (-> p3-fri),
        # player 2 published, player 4 never published anywhere
        {**base, "match_guid": "s1", "tournament_id": 2, "player_a_id": 11, "decklist_a": None,
         "player_a_user": "alice", "player_a_name": "Alice", "player_b_id": 12,
         "decklist_b": "p2-sat", "player_b_user": "bob", "player_b_name": "Bob",
         "winner_player_id": 12, "both_decks_known": False},
        {**base, "match_guid": "s2", "tournament_id": 2, "player_a_id": 13,
         "decklist_a": "p3-sat-empty", "player_a_user": None, "player_a_name": "Carol",
         "player_b_id": 14, "decklist_b": None, "player_b_user": "dan", "player_b_name": "Dan",
         "winner_player_id": 13, "both_decks_known": True},
    ]), root / "matches.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"decklist_id": d, "section": "main", "qty": 4, "card": c}
        for d, c in (("p1-fri", "Phelia"), ("p2-fri", "Ragavan"), ("p2-sat", "Ragavan"),
                     ("p3-fri", "Karn"))
    ]), root / "decklist_cards.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"decklist_id": d, "archetype": a, "rules_file": "modern-2026-05-18.yml"}
        for d, a in (("p1-fri", "Blink"), ("p2-fri", "Prowess"), ("p2-sat", "Prowess"),
                     ("p3-fri", "Tron"))
    ]), root / "decklist_archetypes.parquet")
    return tmp_path


def test_impute_fills_missing_and_empty_from_nearby_events(tmp_path):
    root = _silver(tmp_path)
    imps = impute.impute(root, window=14)
    by = {(i.match_guid, i.side): i for i in imps}
    assert by[("s1", "a")].decklist_id == "p1-fri" and by[("s1", "a")].days_delta == -1
    assert by[("s1", "a")].had_record is False
    assert by[("s2", "a")].decklist_id == "p3-fri" and by[("s2", "a")].had_record is True
    assert ("s2", "b") not in by and ("f2", "b") not in by   # player 4 never published
    impute.write(imps, root)

    # Validation: player 2 published both days, same archetype and same 75.
    v = impute.validate(root, 14)
    assert v["0-1d"]["pairs"] == 2 and v["0-1d"]["same_75"] == 1.0
    assert v["0-1d"]["same_archetype"] == 1.0
    gain = impute.coverage_gain(root, imps)
    assert (gain["both_known_before"], gain["both_known_after"]) == (1, 2)
    assert "filled 2 match sides" in impute.report(imps, v, gain)
    assert "1 -> 2 of 4" in impute.report(imps, v, gain)
    pe = {e["tournament_id"]: e for e in impute.per_event(root, imps)}
    # Saturday: alice, carol and dan had no usable list; alice and carol were filled
    assert pe[2]["players_without_list"] == 3 and pe[2]["filled"] == 2
    assert pe[2]["filled_same_weekend"] == 2
    assert "players without a usable list: 4; given one by imputation: 2" in \
        impute.report(imps, v, gain, list(pe.values()))

    # Gold: without imputation only f1 counts; with it, s1 and s2 join in.
    plain, arch = matrix.load_inputs(root, impute=False)
    assert {m["tournament_id"] for m in plain} == {1} and len(plain) == 1
    with_imp, _ = matrix.load_inputs(root, impute=True)
    assert len(with_imp) == 2   # s2's side b is still unknown
    s1 = next(m for m in with_imp if m["tournament_id"] == 2)
    assert s1["imputed_a"] and not s1["imputed_b"] and s1["decklist_a"] == "p1-fri"
    pairs, summary = matrix.build(with_imp, arch)
    bp = next(r for r in pairs if (r["archetype"], r["opponent"]) == ("Blink", "Prowess"))
    assert bp["matches"] == 2 and bp["imputed_matches"] == 1
