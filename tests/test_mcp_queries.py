"""MCP query functions over a tiny silver + gold built from the fixture bronze."""

import asyncio
import json

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mtgmeta.gold import matrix
from mtgmeta.mcp import queries as q
from mtgmeta.silver import melee as silver
from mtgmeta.sources.melee.fetch import fetch_event
from mtgmeta.sources.melee.http import PoliteClient


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
    build = silver.build(tmp_path)
    silver.write_parquet(build, silver.silver_root(tmp_path))
    # Classification by hand: the two lists that meet in the fixture's known-decks
    # match become Energy (a) and Blink (b); anything else is Energy.
    known = next(m for m in build.matches if m["both_decks_known"])
    arch = {known["decklist_a"]: "Energy", known["decklist_b"]: "Blink"}
    rows = [{"decklist_id": d["decklist_id"], "archetype": arch.get(d["decklist_id"], "Energy"),
             "variant": "x", "score": 3, "signature_size": 4, "colors": "W",
             "runner_up": None, "runner_up_score": 0, "rules_file": "modern-2026-05-18.yml"}
            for d in build.decklists]
    pq.write_table(pa.Table.from_pylist(rows),
                   silver.silver_root(tmp_path) / "decklist_archetypes.parquet")
    matches, archs = matrix.load_inputs(tmp_path)
    pairs, summary = matrix.build(matches, archs)
    matrix.write(pairs, summary, matrix.gold_root(tmp_path))
    return tmp_path


def test_coverage_and_meta_share(dataset):
    cov = q.coverage(dataset, days=36500)
    assert cov["events"] == 1 and cov["rows"][0]["tournament_id"] == 453138
    share = q.meta_share(dataset)
    assert {r["archetype"] for r in share} <= {"Energy", "Blink"}
    assert abs(sum(r["share"] for r in share) - 1.0) < 0.01


def test_matchup_resolves_names_loosely(dataset):
    m = q.matchup(dataset, "energy", "BLINK")
    assert m["archetype"] == "Energy" and m["opponent"] == "Blink"
    assert "matches" in m
    with pytest.raises(ValueError):
        q.matchup(dataset, "Dredge", "Blink")


def test_matrix_and_cards_and_sql(dataset):
    grid = q.matrix(dataset, top=5, min_matches=1)
    assert set(grid["archetypes"]) <= {"Energy", "Blink"}
    cards = q.archetype_cards(dataset, "Energy")
    assert cards["lists"] >= 1 and cards["cards"]
    assert all(c["tag"] in {"core", "package", "slot"} for c in cards["cards"])
    assert cards["flex_budget"] <= 60
    ok = q.run_sql(dataset, "select count(*) as n from matches")
    assert ok["rows"][0]["n"] == 18
    assert "error" in q.run_sql(dataset, "drop table matches")
    assert "error" in q.run_sql(dataset, "select * from nope")
    usage = q.card_presence_by_archetype(dataset, cards["cards"][0]["card"])
    assert usage and usage[0]["share"] > 0


def test_server_exposes_the_tools(monkeypatch, dataset):
    monkeypatch.setenv("DATA_ROOT", str(dataset))
    monkeypatch.setenv("MELEE_CONTACT_EMAIL", "x@y.z")
    import importlib

    from mtgmeta.mcp import server as srv
    importlib.reload(srv)
    tools = asyncio.run(srv.server.list_tools())
    names = {t.name for t in tools}
    assert {"coverage", "matchup", "matrix", "archetype_cards", "run_sql", "meta_share",
            "failed_events", "card_usage", "archetypes"} <= names
