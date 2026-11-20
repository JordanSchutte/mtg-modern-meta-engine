"""Coverage flags over silver built from the Melee fixture plus synthetic events."""

import json
from datetime import date

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mtgmeta.gold import forecast
from mtgmeta.mcp import queries as q
from mtgmeta.silver import coverage
from mtgmeta.silver import melee as silver
from mtgmeta.silver import mtgo as silver_mtgo
from mtgmeta.sources.melee.fetch import fetch_event
from mtgmeta.sources.melee.http import PoliteClient

TODAY = date(2026, 11, 19)


def _event(template: dict, tid: int, start: str, name: str, players: int) -> dict:
    return {**template, "tournament_id": tid, "start": f"{start}T10:00:00", "name": name,
            "players_estimate": players, "decklist_ids": 0, "unusable_decklists": 0,
            "decklists_missing": 0}


def _match(template: dict, tid: int, i: int, deck_a: str | None, deck_b: str | None) -> dict:
    return {**template, "match_guid": f"m-{tid}-{i}", "tournament_id": tid,
            "round_id": tid * 10, "round_number": 1, "is_swiss": True, "format": "Modern",
            "result": "win", "player_a_id": i * 2, "player_b_id": i * 2 + 1,
            "decklist_a": deck_a, "deck_a_known": deck_a is not None,
            "decklist_b": deck_b, "deck_b_known": deck_b is not None,
            "both_decks_known": deck_a is not None and deck_b is not None}


def _deck(template: dict, did: str, tid: int, usable: bool = True) -> dict:
    return {**template, "decklist_id": did, "first_tournament_id": tid, "usable": usable,
            "in_bronze": True, "format": "Modern"}


@pytest.fixture
def dataset(tmp_path, melee_fixture):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/Tournament/View/"):
            return httpx.Response(200, text=melee_fixture("tournament_view.html"))
        if path.startswith("/Match/GetRoundMatches/"):
            return httpx.Response(200, json=json.loads(melee_fixture("round_matches.json")))
        return httpx.Response(200, text=melee_fixture("decklist_view.html"))

    client = PoliteClient("me@example.com", transport=httpx.MockTransport(handler),
                          sleep=lambda s: None)
    fetch_event(453138, client, tmp_path)
    build = silver.build(tmp_path)   # 2026-10-11, 6 players, 3 usable lists -> fine
    ev_t, m_t, d_t = build.events[0], build.matches[0], build.decklists[0]
    classified: dict[str, str] = {d["decklist_id"]: "Energy" for d in build.decklists}

    def add_event(tid, start, name, players, lists, classified_n, unusable=0):
        build.events.append(_event(ev_t, tid, start, name, players))
        ids = [f"d-{tid}-{i}" for i in range(lists + unusable)]
        for i, did in enumerate(ids):
            build.decklists.append(_deck(d_t, did, tid, usable=i < lists))
            if i < classified_n:
                classified[did] = "Blink"
            elif i < lists:
                classified[did] = "Unclassified"
        # Every list is referenced by a match of its event (a few lists per match
        # slot are reused across matches; that must not double count).
        pairs = [ids[i:i + 2] for i in range(0, len(ids), 2)] or [[None, None]]
        for i, pair in enumerate(pairs):
            a, b = (pair + [None])[:2]
            build.matches.append(_match(m_t, tid, i, a, b))
            build.matches.append(_match(m_t, tid, 1000 + i, a, None))  # reuse of a

    add_event(900001, "2026-11-01", "Silent RCQ", 200, lists=0, classified_n=0)
    add_event(900002, "2026-11-08", "Half-published Open", 300, lists=20, classified_n=20,
              unusable=5)
    add_event(900003, "2026-11-17", "Fresh RCQ", 150, lists=0, classified_n=0)  # in grace
    add_event(900004, "2026-10-25", "Regional Championship", 400, lists=160, classified_n=155)
    add_event(900005, "2026-11-14", "Big but unclassified", 400, lists=160, classified_n=100)
    silver.write_parquet(build, silver.silver_root(tmp_path))
    rows = [{"decklist_id": did, "archetype": arch, "variant": "x", "score": 3,
             "signature_size": 4, "colors": "W", "runner_up": None, "runner_up_score": 0,
             "rules_file": "modern-2026-05-18.yml"} for did, arch in classified.items()]
    pq.write_table(pa.Table.from_pylist(rows),
                   silver.silver_root(tmp_path) / "decklist_archetypes.parquet")

    mtgo = silver_mtgo.MtgoSilver(events=[
        {"site_name": "modern-challenge-2026-11-1512345", "event_id": 12345,
         "name": "Modern Challenge", "type": "CHALLENGE", "kind": "challenge",
         "day": "2026-11-15", "player_count": 64, "decklists": 32, "status": "complete",
         "fetched_at": "2026-11-16T00:00:00Z"},
        {"site_name": "modern-league-2026-11-16", "event_id": None, "name": None,
         "type": None, "kind": "league", "day": "2026-11-16", "player_count": None,
         "decklists": 0, "status": "empty", "fetched_at": "2026-11-17T00:00:00Z"},
    ], decklists=[], decklist_cards=[], matches=[])
    silver_mtgo.write_parquet(mtgo, silver_mtgo.silver_root(tmp_path))
    return tmp_path


def test_unpublished_melee_events(dataset):
    rows = coverage.unpublished_melee(dataset, TODAY)
    assert [r["tournament_id"] for r in rows] == [900002, 900001]   # most recent first
    half = rows[0]
    assert half["usable_lists"] == 20 and half["players"] == 300
    assert half["list_share"] == pytest.approx(20 / 300, abs=1e-3)
    assert rows[1]["usable_lists"] == 0
    # The 2-day-old event is still inside the grace window; a week later it is flagged.
    later = coverage.unpublished_melee(dataset, date(2026, 11, 26))
    assert 900003 in {r["tournament_id"] for r in later}


def test_empty_mtgo_and_totals(dataset):
    empty = coverage.empty_mtgo(dataset)
    assert [e["site_name"] for e in empty] == ["modern-league-2026-11-16"]
    f = coverage.flags(dataset, TODAY)
    assert f["unpublished_total"] == 3 and f["as_of"] == "2026-11-19"


def test_last_big_paper_event_uses_classified_lists(dataset):
    big = coverage.last_big_paper_event(dataset, TODAY)
    # 900005 is more recent and has 160 usable lists but only 100 classified.
    assert big["tournament_id"] == 900004 and big["classified"] == 155
    assert big["days_ago"] == 25 and big["min_classified"] == 150
    assert coverage.last_big_paper_event(dataset, TODAY, min_classified=1000) is None
    assert coverage.MIN_CLASSIFIED == forecast.MIN_PAPER_LISTS


def test_report_text(dataset):
    text = coverage.report(dataset, TODAY)
    assert "Silent RCQ" in text and "Half-published Open" in text
    assert "Fresh RCQ" not in text
    assert "modern-league-2026-11-16" in text
    assert "2 Melee + 1 MTGO = 3 events without published lists" in text
    assert "Regional Championship (155 lists), 25 days ago" in text


def test_without_classification_or_mtgo(tmp_path, dataset):
    (silver.silver_root(dataset) / "decklist_archetypes.parquet").unlink()
    (silver_mtgo.silver_root(dataset) / "events.parquet").unlink()
    f = coverage.flags(dataset, TODAY)
    assert f["last_big_paper_event"] is None and f["empty_mtgo"] == []
    assert len(f["unpublished_melee"]) == 2
    assert "no event with >= 150 classified lists yet" in coverage.report(dataset, TODAY)
    assert coverage.flags(tmp_path / "nothing", TODAY)["unpublished_total"] == 0


def test_mcp_coverage_carries_the_flags(dataset):
    cov = q.coverage(dataset, days=36500, today=TODAY)   # the fixture's dates are relative to TODAY
    assert cov["unpublished_total"] == 3
    assert {r["tournament_id"] for r in cov["unpublished_melee"]} == {900001, 900002}
    assert cov["last_big_paper_event"]["tournament_id"] == 900004
    assert cov["flag_rules"]["min_classified"] == 150
