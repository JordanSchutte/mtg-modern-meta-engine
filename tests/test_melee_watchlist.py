"""Watchlist: scan stop rule, merge/due bookkeeping, and the size check on settle."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from mtgmeta.sources.melee import watchlist
from mtgmeta.sources.melee.discovery import DiscoveredEvent, first_known
from mtgmeta.sources.melee.fetch import TooSmallError, bronze_root, fetch_event
from mtgmeta.sources.melee.http import PoliteClient

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def _ev(i: int, start: datetime, title: str = "") -> DiscoveredEvent:
    return DiscoveredEvent(i, title or f"e{i}", "Org", start, 0, False, ("Modern",))


def test_first_known_stops_at_an_already_seen_id():
    batch = [_ev(500, NOW), _ev(499, NOW), _ev(400, NOW), _ev(399, NOW)]
    assert first_known(batch, frozenset({400, 300})) == 400
    assert first_known(batch, frozenset({300})) is None


def test_merge_adds_pending_and_refreshes_rescheduled_start():
    entries: dict[int, dict] = {}
    rc = _ev(405588, NOW + timedelta(days=60), "Regional Championship")
    assert watchlist.merge(entries, [rc, _ev(1, NOW + timedelta(days=3))], NOW) == 2
    assert entries[405588]["status"] == watchlist.PENDING
    assert entries[405588]["first_seen"] == NOW.isoformat()

    # Seen again, moved a week: still one entry, start updated, status untouched.
    watchlist.settle(entries[1], watchlist.TOO_SMALL, NOW, players_estimate=12)
    moved = _ev(405588, NOW + timedelta(days=67), "Regional Championship")
    assert watchlist.merge(entries, [moved, _ev(1, NOW + timedelta(days=3))], NOW) == 0
    assert entries[405588]["start"] == moved.start.isoformat()
    assert entries[1]["status"] == watchlist.TOO_SMALL


def test_due_waits_min_age_and_ignores_settled():
    entries: dict[int, dict] = {}
    watchlist.merge(entries, [
        _ev(10, NOW - timedelta(days=5)),   # due
        _ev(11, NOW - timedelta(days=1)),   # too recent
        _ev(12, NOW + timedelta(days=9)),   # future
        _ev(13, NOW - timedelta(days=30)),  # due, but settled below
    ], NOW)
    watchlist.settle(entries[13], watchlist.FETCHED, NOW)
    assert [e["tournament_id"] for e in watchlist.due(entries, NOW)] == [10]


def test_save_and_load_round_trip(tmp_path):
    path = watchlist.watchlist_path(tmp_path)
    entries: dict[int, dict] = {}
    watchlist.merge(entries, [_ev(7, NOW)], NOW)
    watchlist.save(path, entries)
    assert watchlist.load(path) == entries
    assert "updated_at" in json.loads(path.read_text())


def test_fetch_rejects_small_event_after_two_requests(tmp_path, melee_fixture):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/Tournament/View/"):
            return httpx.Response(200, text=melee_fixture("tournament_view.html"))
        if request.url.path.startswith("/Match/GetRoundMatches/"):
            return httpx.Response(200, json=json.loads(melee_fixture("round_matches.json")))
        return httpx.Response(404)

    client = PoliteClient("me@example.com", transport=httpx.MockTransport(handler),
                          sleep=lambda s: None)
    # The fixture round has 3 matches -> ~6 players.
    with pytest.raises(TooSmallError) as info:
        fetch_event(453138, client, tmp_path, min_players=100)
    assert info.value.players_estimate == 6
    assert client.requests_made == 2
    assert not (bronze_root(tmp_path) / "events").exists()

    # Below the bar it fetches normally and records the estimate.
    manifest = fetch_event(453138, client, tmp_path, min_players=4, fetch_decklists=False)
    assert manifest["players_estimate"] == 6
