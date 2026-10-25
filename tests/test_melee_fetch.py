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
    state = {"break_decklists": False, "empty_decklists": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/Tournament/View/"):
            return httpx.Response(200, text=melee_fixture("tournament_view.html"))
        if path.startswith("/Match/GetRoundMatches/"):
            return httpx.Response(200, json=json.loads(melee_fixture("round_matches.json")))
        if path.startswith("/Decklist/View/"):
            if state["break_decklists"]:
                return httpx.Response(200, text="<html>redesigned</html>")
            if state["empty_decklists"]:
                return httpx.Response(
                    200, text='<html><pre class="d-none" id="decklist-text"></pre></html>'
                )
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


def test_empty_decklist_is_kept_and_flagged(tmp_path, fake_melee):
    handler, state = fake_melee
    state["empty_decklists"] = True
    manifest = fetch_event(453138, client_for(handler), tmp_path)
    # the event still lands, the empty pages are kept so they're never re-requested
    assert latest_version(bronze_root(tmp_path) / "events" / "453138") is not None
    assert len(list((bronze_root(tmp_path) / "decklists").glob("*.html"))) == 3
    assert set(manifest["unusable_decklists"]) == set(manifest["decklist_ids"])
    assert all(n == 0 for n in manifest["unusable_decklists"].values())


def test_refresh_writes_a_version_only_when_something_changed(tmp_path, melee_fixture):
    state = {"flip": False, "empty_decks": False}
    rounds = json.loads(melee_fixture("round_matches.json"))

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/Tournament/View/"):
            return httpx.Response(200, text=melee_fixture("tournament_view.html"))
        if path.startswith("/Match/GetRoundMatches/"):
            payload = json.loads(json.dumps(rounds))
            if state["flip"]:
                # An organizer correction: first match's first competitor loses a game win.
                comp = payload["data"][0]["Competitors"][0]
                comp["GameWins"] = (comp.get("GameWins") or 0) + 1
            return httpx.Response(200, json=payload)
        if path.startswith("/Decklist/View/"):
            if state["empty_decks"]:
                return httpx.Response(
                    200, text='<html><pre class="d-none" id="decklist-text"></pre></html>'
                )
            return httpx.Response(200, text=melee_fixture("decklist_view.html"))
        return httpx.Response(404)

    client = client_for(handler)
    event_root = bronze_root(tmp_path) / "events" / "453138"

    first = fetch_event(453138, client, tmp_path)
    assert first["matches_sha256"]

    # Nothing changed: no new version, one refresh record, no decklist requests.
    before = client.requests_made
    again = fetch_event(453138, client, tmp_path, refresh=True)
    assert again["version"] == first["version"]
    assert len(list(event_root.iterdir())) == 2  # version dir + refreshes.json
    history = json.loads((event_root / "refreshes.json").read_text())
    assert [h["changed"] for h in history] == [False]
    assert client.requests_made - before == 1 + 6  # page + rounds, nothing else

    # A result changed: new version beside the old one, old untouched.
    state["flip"] = True
    third = fetch_event(453138, client, tmp_path, refresh=True)
    assert third["version"] != first["version"]
    assert third["refreshed_from"] == first["version"]
    assert (event_root / first["version"] / "manifest.json").exists()
    history = json.loads((event_root / "refreshes.json").read_text())
    assert [h["changed"] for h in history] == [False, True]


def test_refresh_retries_empty_decklists_and_keeps_the_old_copy(tmp_path, melee_fixture):
    state = {"empty_decks": True}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/Tournament/View/"):
            return httpx.Response(200, text=melee_fixture("tournament_view.html"))
        if path.startswith("/Match/GetRoundMatches/"):
            return httpx.Response(200, json=json.loads(melee_fixture("round_matches.json")))
        if path.startswith("/Decklist/View/"):
            if state["empty_decks"]:
                return httpx.Response(
                    200, text='<html><pre class="d-none" id="decklist-text"></pre></html>'
                )
            return httpx.Response(200, text=melee_fixture("decklist_view.html"))
        return httpx.Response(404)

    client = client_for(handler)
    first = fetch_event(453138, client, tmp_path)
    assert len(first["unusable_decklists"]) == 3

    # Lists published since: refresh requests them again, keeps the empty copies aside.
    state["empty_decks"] = False
    second = fetch_event(453138, client, tmp_path, refresh=True)
    assert second["unusable_decklists"] == {}
    decks = sorted(p.name for p in (bronze_root(tmp_path) / "decklists").iterdir())
    assert len([d for d in decks if ".superseded-" in d]) == 3
    assert len([d for d in decks if ".superseded-" not in d]) == 3
