"""Silver build from a bronze produced by the fake-Melee fetch."""

import json

import duckdb
import httpx

from mtgmeta.silver import melee as silver
from mtgmeta.sources.melee.fetch import fetch_event
from mtgmeta.sources.melee.http import PoliteClient


def _bronze(tmp_path, melee_fixture, empty_decks=False):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/Tournament/View/"):
            return httpx.Response(200, text=melee_fixture("tournament_view.html"))
        if path.startswith("/Match/GetRoundMatches/"):
            return httpx.Response(200, json=json.loads(melee_fixture("round_matches.json")))
        if path.startswith("/Decklist/View/"):
            if empty_decks:
                return httpx.Response(
                    200, text='<html><pre class="d-none" id="decklist-text"></pre></html>'
                )
            return httpx.Response(200, text=melee_fixture("decklist_view.html"))
        return httpx.Response(404)

    client = PoliteClient("me@example.com", transport=httpx.MockTransport(handler),
                          sleep=lambda s: None)
    fetch_event(453138, client, tmp_path)
    return tmp_path


def test_build_tables_from_bronze(tmp_path, melee_fixture):
    build = silver.build(_bronze(tmp_path, melee_fixture))
    assert build.counts()["events"] == 1
    ev = build.events[0]
    assert ev["tournament_id"] == 453138
    assert ev["rounds"] == 6 and ev["matches"] == 6 * 3
    assert ev["decklist_ids"] == 3 and ev["unusable_decklists"] == 0
    # 3 rows per round: one bye, one draw with both lists, one win without lists
    assert ev["modern_swiss_matches"] == 6 * 2 and ev["modern_swiss_known"] == 6 * 1
    assert ev["players_estimate"] == 5 and ev["formats"] == "Modern"

    # Competitors flattened; the bye has no b side and is not "both known".
    bye = next(m for m in build.matches if m["result"] == "bye")
    assert bye["player_b_id"] is None and bye["both_decks_known"] is False
    draw = next(m for m in build.matches if m["result"] == "draw")
    assert draw["both_decks_known"] is True
    win = next(m for m in build.matches if m["result"] == "win")  # no lists published
    assert win["both_decks_known"] is False and win["deck_a_known"] is False
    assert {m["format"] for m in build.matches} == {"Modern"}

    assert all(d["usable"] for d in build.decklists)
    assert sum(c["qty"] for c in build.decklist_cards if c["section"] == "main") == 3 * 60
    assert len(build.players) >= 2
    assert "events" in silver.coverage_report(build)


def test_unusable_decklists_are_rows_not_gaps(tmp_path, melee_fixture):
    build = silver.build(_bronze(tmp_path, melee_fixture, empty_decks=True))
    assert build.events[0]["unusable_decklists"] == 3
    assert len(build.decklists) == 3 and not any(d["usable"] for d in build.decklists)
    assert build.decklist_cards == []
    # Matches still count the deck as "known" by id; usability is a decklists fact.
    assert any(m["both_decks_known"] for m in build.matches)


def test_parquet_round_trip(tmp_path, melee_fixture):
    build = silver.build(_bronze(tmp_path, melee_fixture))
    written = silver.write_parquet(build, silver.silver_root(tmp_path))
    assert set(written) == set(silver.TABLES)
    con = duckdb.connect()
    n = con.execute(f"select count(*) from '{written['matches'].as_posix()}'").fetchone()[0]
    assert n == len(build.matches)
    cols = {r[0] for r in con.execute(
        f"describe select * from '{written['events'].as_posix()}'").fetchall()}
    assert {"tournament_id", "swiss_both_known", "refresh_checks"} <= cols
