from datetime import UTC, datetime, timedelta

import pytest

from mtgmeta.contracts import ContractError
from mtgmeta.sources.melee.discovery import (
    DiscoveredEvent,
    parse_search_items,
    select_events,
    should_stop,
)


def test_parses_live_cards(melee_fixture):
    events = parse_search_items(melee_fixture("search_page.html"))
    assert [e.tournament_id for e in events] == [485382, 470109, 490001]

    weekly = events[0]
    assert weekly.title == "Fortnightly Modern"
    assert weekly.organizer == "Bristol Community Competitive MTG"
    assert weekly.enrolled_players == 7
    assert weekly.ended is True
    assert weekly.start == datetime(2026, 10, 20, 17, 30, tzinfo=UTC)
    assert "Modern" in weekly.tags


def test_comma_player_counts_and_not_ended(melee_fixture):
    events = {e.tournament_id: e for e in parse_search_items(melee_fixture("search_page.html"))}
    assert events[470109].enrolled_players == 1193
    assert events[490001].ended is False


def test_select_keeps_completed_big_events_since_cutoff(melee_fixture):
    events = parse_search_items(melee_fixture("search_page.html"))
    picked = select_events(events, since=datetime(2026, 10, 1, tzinfo=UTC), min_players=100)
    assert [e.tournament_id for e in picked] == [470109]  # weekly too small, RCQ not ended


def test_contract_breaks_loudly_when_card_markup_changes(melee_fixture):
    broken = melee_fixture("search_page.html").replace(
        "home-tournament-search-item-subtitle", "renamed-subtitle"
    )
    with pytest.raises(ContractError, match="subtitle"):
        parse_search_items(broken)


def _ev(i: int, start: datetime) -> DiscoveredEvent:
    return DiscoveredEvent(i, f"e{i}", None, start, 150, True, ("Modern",))


def test_stop_rule_needs_a_run_of_old_results():
    since = datetime(2026, 4, 20, tzinfo=UTC)
    new, old = since + timedelta(days=30), since - timedelta(days=30)
    # Ordered by creation: an old event appearing among new ones must not stop paging.
    mixed = [_ev(i, new) for i in range(10)] + [_ev(10, old)] + [_ev(i, new) for i in range(11, 20)]
    assert should_stop(mixed, since, stop_after_old=5) is False
    tail_old = mixed + [_ev(100 + i, old) for i in range(5)]
    assert should_stop(tail_old, since, stop_after_old=5) is True
