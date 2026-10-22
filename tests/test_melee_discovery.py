import json
from datetime import UTC, datetime, timedelta

import pytest

from mtgmeta.contracts import ContractError
from mtgmeta.sources.melee.discovery import (
    DiscoveredEvent,
    looks_newest_first,
    parse_search_results,
    select_events,
    should_stop,
)

NOW = datetime(2026, 10, 22, 18, tzinfo=UTC)


def test_parses_live_search_json(melee_fixture):
    events = parse_search_results(melee_fixture("search_results.json"))
    assert len(events) == 8
    rc = events[0]
    assert rc.tournament_id == 470109
    assert rc.enrolled_players == 1193
    assert rc.ended is True
    assert rc.start == datetime(2026, 10, 10, 7, tzinfo=UTC)
    assert rc.organizer == "Fanfinity"
    assert rc.tags == ("Modern", "Magic: The Gathering", "Tabletop")
    assert events[6].ended is False  # status "Registration"


def test_select_keeps_finished_big_events_since_cutoff(melee_fixture):
    events = parse_search_results(melee_fixture("search_results.json"))
    picked = select_events(events, since=datetime(2026, 9, 1, tzinfo=UTC), min_players=100,
                           now=NOW)
    assert [e.tournament_id for e in picked] == [470109]


def test_past_event_without_ended_status_is_kept():
    # Seen live: a 538-player Open from 10 days earlier never flipped to Ended.
    open_ = DiscoveredEvent(479653, "Open", None, NOW - timedelta(days=10), 538, False, ())
    running = DiscoveredEvent(1, "Day 2 today", None, NOW - timedelta(hours=20), 900, False, ())
    picked = select_events([open_, running], since=NOW - timedelta(days=30), min_players=100,
                           now=NOW)
    assert [e.tournament_id for e in picked] == [479653]


def test_missing_field_fails_loudly(melee_fixture):
    rows = json.loads(melee_fixture("search_results.json"))
    del rows[2]["Players"]
    with pytest.raises(ContractError, match="Players"):
        parse_search_results(json.dumps(rows))


def test_non_json_fails_loudly():
    with pytest.raises(ContractError, match="not JSON"):
        parse_search_results("<html>maintenance</html>")


def _ev(i: int, start: datetime) -> DiscoveredEvent:
    return DiscoveredEvent(i, f"e{i}", None, start, 150, True, ("Modern",))


def test_ordering_check_catches_location_sorted_results():
    # IDs in page order from a real location-sorted run, vs. a real global run.
    location_sorted = [144505, 285576, 332157, 336082, 340603, 444883, 273359, 168976,
                       476000, 438512, 329680, 441336]
    global_order = [485382, 482682, 482135, 482105, 480831, 480464, 479653, 476393,
                    476000, 474904, 473589, 473290]
    assert looks_newest_first([_ev(i, NOW) for i in global_order])
    assert not looks_newest_first([_ev(i, NOW) for i in location_sorted])


def test_stop_rule_needs_a_run_of_old_results():
    since = datetime(2026, 4, 20, tzinfo=UTC)
    new, old = since + timedelta(days=30), since - timedelta(days=30)
    # Ordered by creation: an old event appearing among new ones must not stop paging.
    mixed = [_ev(i, new) for i in range(10)] + [_ev(10, old)] + [_ev(i, new) for i in range(11, 20)]
    assert should_stop(mixed, since, stop_after_old=5) is False
    tail_old = mixed + [_ev(100 + i, old) for i in range(5)]
    assert should_stop(tail_old, since, stop_after_old=5) is True
