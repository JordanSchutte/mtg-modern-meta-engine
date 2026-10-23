import json

import pytest

from mtgmeta.contracts import ContractError
from mtgmeta.sources.melee.parse import (
    NoRoundsError,
    datatables_form,
    parse_decklist_page,
    parse_round_matches,
    parse_tournament_page,
)

# -- tournament page ----------------------------------------------------------


def test_tournament_page_rounds_deduped_in_order(melee_fixture):
    meta = parse_tournament_page(melee_fixture("tournament_view.html"), 453138)
    assert meta.name.startswith("$uper $unday RCQ - Modern")
    assert [r.id for r in meta.rounds] == list(range(1607188, 1607194))
    assert all(r.is_swiss and r.is_completed for r in meta.rounds)
    assert meta.start == "2026-10-11T17:00:00"


def test_tournament_page_without_rounds_is_skippable(melee_fixture):
    # e.g. "Registration///Champions Cup Final" – a real page, nothing to fetch
    page = melee_fixture("tournament_view.html").replace("round-selector", "round-picker")
    with pytest.raises(NoRoundsError, match="no rounds"):
        parse_tournament_page(page, 410899)


def test_non_tournament_page_fails_loudly():
    with pytest.raises(ContractError, match="title"):
        parse_tournament_page("<html><title>Just a moment...</title></html>", 1)


# -- round matches ------------------------------------------------------------


@pytest.fixture
def matches(melee_fixture):
    return parse_round_matches([json.loads(melee_fixture("round_matches.json"))])


def test_bye(matches):
    bye = matches[0]
    assert bye.result == "bye"
    assert len(bye.competitors) == 1
    assert bye.winner_player_id == bye.competitors[0].player_id
    assert bye.competitors[0].game_wins == 0  # GameWins is null on a bye


def test_intentional_draw_uses_structured_fields(matches):
    draw = matches[1]
    assert draw.result == "draw"
    assert draw.winner_player_id is None
    assert draw.game_draws == 3
    assert draw.both_decks_known
    assert draw.competitors[0].decklist_name == "Azorius Control (Kaheera)"
    assert draw.format == "Modern"


def test_win_with_unpublished_decklists(matches):
    win = matches[2]
    assert win.result == "win"
    assert win.winner_player_id == 4483991
    assert [c.game_wins for c in win.competitors] == [1, 2]
    assert not win.both_decks_known  # empty Decklists arrays: kept, deck unknown
    assert all(c.decklist_id is None for c in win.competitors)
    assert win.round_number == 1


def test_lost_rows_fail_loudly(melee_fixture):
    page = json.loads(melee_fixture("round_matches.json"))
    page["data"] = page["data"][:2]  # recordsTotal still says 3
    with pytest.raises(ContractError, match="recordsTotal"):
        parse_round_matches([page])


def test_missing_field_fails_loudly(melee_fixture):
    page = json.loads(melee_fixture("round_matches.json"))
    del page["data"][1]["Competitors"][0]["GameWins"]
    with pytest.raises(ContractError, match="GameWins"):
        parse_round_matches([page])


def test_datatables_form_shape():
    form = dict(datatables_form(["A", "B"], start=100, length=50))
    assert form["start"] == "100" and form["length"] == "50"
    assert form["columns[1][data]"] == "B"


# -- decklist page ------------------------------------------------------------


def test_decklist_sections_and_entities(melee_fixture):
    deck = parse_decklist_page(melee_fixture("decklist_view.html"), "c9e4fbe8")
    assert deck.main_count == 60
    assert sum(q for q, _ in deck.side) == 14
    assert deck.companion == [(1, "Kaheera, the Orphanguard")]
    assert (3, "Day's Undoing") in deck.main  # &#39; unescaped
    assert (2, "Sink into Stupor // Soporific Springs") in deck.main


def test_decklist_unknown_line_fails_loudly(melee_fixture):
    page = melee_fixture("decklist_view.html").replace("Sideboard", "Side Deck:")
    with pytest.raises(ContractError, match="unrecognised"):
        parse_decklist_page(page, "x")


def test_decklist_missing_block_fails_loudly():
    with pytest.raises(ContractError, match="decklist-text"):
        parse_decklist_page("<html></html>", "x")
