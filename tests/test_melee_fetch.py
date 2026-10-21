"""End-to-end fetch against a fake Melee built from the captured fixtures."""

import json
from datetime import UTC, datetime

import httpx
import pytest

import mtgmeta.sources.melee.fetch as fetch_mod
from mtgmeta.contracts import ContractError
from mtgmeta.sources.melee.fetch import bronze_root, fetch_event, latest_version
from mtgmeta.sources.melee.http import PoliteClient


@pytest.fixture
def fake_melee(melee_fixture):
    state = {"break_decklists": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/Tournament/View/"):
            return httpx.Response(200, text=melee_fixture("tournament_view.html"))
        if path.startswith("/Match/GetRoundMatches/"):
            return httpx.Response(200, json=json.loads(melee_fixture("round_matches.json")))
        if path.startswith("/Decklist/View/"):
            if state["break_decklists"]:
                return httpx.Response(200, text="<html>redesigned</html>")
            return httpx.Response(200, text=melee_fixture("decklist_view.html"))
        return httpx.Response(404)

    return handler, state


def client_for(handler):
    return PoliteClient(
        "me@example.com", transport=httpx.MockTransport(handler), sleep=lambda s: None
    )


def test_fetch_writes_event_and_shared_decklists(tmp_path, fake_melee):
    handler, _ = fake_melee
    manifest = fetch_event(453138, client_for(handler), tmp_path)

    event = latest_version(bronze_root(tmp_path) / "events" / "453138")
    assert event is not None
    assert (event / "tournament.html").exists()
    assert len(list((event / "rounds").glob("*.json"))) == 6
    assert manifest["rounds"][0]["both_decks_known"] == 1
    assert manifest["rounds"][0]["formats"] == ["Modern"]
    # 3 distinct decklists across the fixture's matches, stored once, shared by events
    decks = list((bronze_root(tmp_path) / "decklists").glob("*.html"))
    assert len(decks) == 3
    assert manifest["requests"] == 1 + 6 + 3


def test_second_run_is_a_no_op(tmp_path, fake_melee):
    handler, _ = fake_melee
    client = client_for(handler)
    fetch_event(453138, client, tmp_path)
    before = client.requests_made
    fetch_event(453138, client, tmp_path)
    assert client.requests_made == before


def test_force_adds_a_version_and_keeps_the_old_one(tmp_path, fake_melee, monkeypatch):
    handler, _ = fake_melee
    client = client_for(handler)
    first = fetch_event(453138, client, tmp_path)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2030, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(fetch_mod, "datetime", Later)
    second = fetch_event(453138, client, tmp_path, force=True)

    versions = sorted(p.name for p in (bronze_root(tmp_path) / "events" / "453138").iterdir())
    assert versions == sorted([first["version"], second["version"]])
    # decklists were already in bronze, so the forced run didn't re-request them
    assert second["requests"] == 1 + 6


def test_contract_break_writes_nothing(tmp_path, fake_melee):
    handler, state = fake_melee
    state["break_decklists"] = True
    with pytest.raises(ContractError, match="decklist-text"):
        fetch_event(453138, client_for(handler), tmp_path)
    assert not (bronze_root(tmp_path) / "events" / "453138").exists()
    assert not list((bronze_root(tmp_path)).rglob("*.html"))
