"""MTGO: index discovery, embedded-JSON parsing, bronze fetch, silver build."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from mtgmeta.archetypes.loader import load_deck_dates, load_main_decks
from mtgmeta.silver import mtgo as silver
from mtgmeta.sources.melee.http import PoliteClient
from mtgmeta.sources.mtgo import BASE_URL, discovery, fetch, parse

FIX = Path(__file__).parent / "fixtures" / "mtgo"


def test_index_lists_modern_events_only():
    refs = discovery.parse_index((FIX / "index.html").read_text(encoding="utf-8"))
    assert refs and all(r.site_name.startswith("modern-") for r in refs)
    assert not any("premodern" in r.site_name for r in refs)
    kinds = {r.kind for r in refs}
    assert {"challenge-32", "challenge-16", "league"} <= kinds
    ch = next(r for r in refs if r.site_name == "modern-challenge-16-2026-11-0312857420")
    assert (ch.day, ch.event_id, ch.path) == (date(2026, 11, 3), "12857420",
                                               "/decklist/modern-challenge-16-2026-11-0312857420")
    lg = next(r for r in refs if r.kind == "league")
    assert lg.event_id == "11161"
    assert refs == sorted(refs, key=lambda r: (r.day, r.site_name))


def test_months_span():
    assert discovery.months(date(2026, 5, 18), date(2026, 7, 2)) == \
        ["2026/05", "2026/06", "2026/07"]
    assert discovery.months(date(2026, 11, 1), date(2026, 11, 30)) == ["2026/11"]


def test_parse_challenge():
    ev = parse.parse_event((FIX / "challenge.html").read_text(encoding="utf-8"),
                           "modern-challenge-16-2026-11-0312857420")
    assert (ev.kind, ev.event_id, ev.day, ev.player_count) == \
        ("TOURNAMENT", "12857420", date(2026, 11, 3), 34)
    assert len(ev.decklists) == 3 and len(ev.top8) == 4
    for d in ev.decklists:
        assert (d.main_count, d.side_count) == (60, 15)
        assert d.rank is not None and d.wins is not None and d.losses is not None
    names = {c for d in ev.decklists for _, c in d.main + d.side}
    assert "Wear // Tear" in names and not any("/" in n and "//" not in n for n in names)


def test_parse_league():
    ev = parse.parse_event((FIX / "league.html").read_text(encoding="utf-8"),
                           "modern-league-2026-11-0111161")
    assert (ev.kind, ev.day, ev.player_count) == ("LEAGUE", date(2026, 11, 1), None)
    assert ev.event_id == "11161_2026-11-01"
    assert [d.wins for d in ev.decklists] == [5, 5] and all(d.rank is None for d in ev.decklists)


def test_incomplete_pages_raise():
    with pytest.raises(parse.IncompleteEventError):
        parse.parse_event("<html>index page</html>", "x")
    with pytest.raises(parse.IncompleteEventError):
        parse.parse_event('<script>\nwindow.MTGO.decklists.data = {"event_id": "1", '
                          '"brackets": []};\n</script>\n', "x")


def _client(pages: dict[str, str], hits: list[str]) -> PoliteClient:
    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.path)
        body = pages.get(request.url.path)
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)
    return PoliteClient("test@example.com", transport=httpx.MockTransport(handler),
                        sleep=lambda s: None, base_url=BASE_URL)


def test_fetch_new_then_silver(tmp_path, monkeypatch):
    pages = {
        "/decklists/2026/11": (FIX / "index.html").read_text(encoding="utf-8"),
        "/decklist/modern-challenge-16-2026-11-0312857420":
            (FIX / "challenge.html").read_text(encoding="utf-8"),
        "/decklist/modern-league-2026-11-0111161":
            (FIX / "league.html").read_text(encoding="utf-8"),
        # not ready yet: a shell with no data
        "/decklist/modern-challenge-32-2026-11-0712857682": "<html>loading</html>",
    }
    hits: list[str] = []
    # fix "today" so min_age_days is deterministic
    import mtgmeta.sources.mtgo.fetch as fmod

    class FakeDT:
        @staticmethod
        def now(tz=None):
            from datetime import datetime
            return datetime(2026, 11, 9, tzinfo=tz)
    monkeypatch.setattr(fmod, "datetime", FakeDT)

    counts = fetch.fetch_new(_client(pages, hits), tmp_path, since=date(2026, 11, 1))
    # 2 real pages; the not-ready page and every 404 are attempts. Events a week
    # old or more are given up on at once (empty); fresher ones are retried later.
    refs = [r for r in discovery.parse_index(pages["/decklists/2026/11"])
            if r.day <= date(2026, 11, 8)]
    stale = len([r for r in refs if r.day <= date(2026, 11, 2)]) - 1   # the league is real
    assert counts == {"complete": 2, "incomplete": len(refs) - 2 - stale, "empty": stale,
                      "skipped": 0}
    assert hits[0] == "/decklists/2026/11"
    assert (tmp_path / "bronze/mtgo/index/2026-11/2026-11-09.html").exists()
    ev = tmp_path / "bronze/mtgo/events/modern-challenge-16-2026-11-0312857420"
    mf = json.loads((ev / "manifest.json").read_text())
    assert mf["status"] == "complete" and mf["decklists"] == 3 and mf["player_count"] == 34
    inc = tmp_path / "bronze/mtgo/events/modern-challenge-32-2026-11-0712857682"
    assert not (inc / "manifest.json").exists() and (inc / "attempts.json").exists()
    # a 404 (unknown page) is also an attempt, not a crash; a stale one is recorded empty
    assert "modern-challenge-16-2026-11-0312857420" in fetch.known(tmp_path)
    old = tmp_path / "bronze/mtgo/events/modern-challenge-32-2026-11-0112857398"
    assert json.loads((old / "manifest.json").read_text())["status"] == "empty"

    # second run: known events are skipped, the incomplete one is retried
    hits.clear()
    fetch.fetch_new(_client(pages, hits), tmp_path, since=date(2026, 11, 1))
    assert "/decklist/modern-challenge-16-2026-11-0312857420" not in hits
    assert "/decklist/modern-challenge-32-2026-11-0712857682" in hits

    b = silver.build(tmp_path)
    assert b.counts() == {"events": 2 + stale, "decklists": 5,   # empty events are rows too
                          "decklist_cards": len(b.decklist_cards), "matches": 4}
    # top-8 bracket matches: both lists named, winner and game score carried
    t8 = b.matches[0]
    assert t8["site_name"] == "modern-challenge-16-2026-11-0312857420" and t8["bracket"] == 2
    assert t8["winner_loginid"] == t8["loginid_a"] == "3499660"
    assert t8["decklist_b"] == f"mtgo:{t8['site_name']}:1239343"
    assert (t8["game_wins_a"], t8["game_wins_b"]) == (2, 0)
    assert all(d["usable"] for d in b.decklists)
    silver.write_parquet(b, silver.silver_root(tmp_path))
    decks = load_main_decks(tmp_path, source="mtgo")
    assert len(decks) == 5 and all(sum(q for q, _ in v) == 60 for v in decks.values())
    dates = load_deck_dates(tmp_path, source="mtgo")
    assert set(dates.values()) == {"2026-11-03", "2026-11-01"}
    assert "challenge-16" in silver.report(b) and "league" in silver.report(b)


def test_report_names_events_with_unusable_lists(mtgo_bronze):
    MTGO_SHORT_EVENT = "modern-challenge-32-2026-11-0812857700"   # conftest's synthetic event
    b = silver.build(mtgo_bronze)
    assert b.counts()["decklists"] == 9
    by_site = {}
    for d in b.decklists:
        by_site.setdefault(d["site_name"], []).append(d)
    assert all(d["usable"] for s, ds in by_site.items() if s != MTGO_SHORT_EVENT for d in ds)
    assert sorted(d["main_count"] for d in by_site[MTGO_SHORT_EVENT]) == [0, 0, 59, 60]

    rows = silver.unusable_events(b)
    assert rows == [{"day": "2026-11-08", "kind": "challenge-32", "site_name": MTGO_SHORT_EVENT,
                     "lists": 4, "unusable": 3, "main_count": 0}]
    text = silver.report(b)
    assert "  3 lists in 1 events are not usable (main deck under 60 cards)" in text
    row = next(ln for ln in text.splitlines() if ln.endswith(MTGO_SHORT_EVENT))
    assert row.split() == ["2026-11-08", "challenge-32", "4", "3", "0", MTGO_SHORT_EVENT]
    # the unusable lists still reach silver; only the classifier's loader skips them
    silver.write_parquet(b, silver.silver_root(mtgo_bronze))
    assert len(load_main_decks(mtgo_bronze, source="mtgo")) == 6


def test_report_counts_zero_unusable_and_caps_rows(tmp_path):
    import copy

    b = silver.MtgoSilver(events=[], decklists=[], decklist_cards=[], matches=[])
    assert "  0 lists in 0 events are not usable (main deck under 60 cards)" in silver.report(b)
    assert "mtgo event" not in silver.report(b)   # no table header when there is nothing
    # newest first, capped, with the overflow counted
    for i in range(25):
        site = f"modern-league-2026-10-{i + 1:02d}1"
        b.events.append({"site_name": site, "kind": "league", "day": f"2026-10-{i + 1:02d}",
                         "status": "complete", "decklists": 2})
        for j, n in enumerate((59, 60)):
            b.decklists.append({"decklist_id": f"mtgo:{site}:{j}", "site_name": site,
                                "usable": n >= 60, "main_count": n})
    rows = silver.unusable_events(copy.deepcopy(b))
    assert [r["day"] for r in rows][:3] == ["2026-10-25", "2026-10-24", "2026-10-23"]
    text = silver.report(b)
    assert "  25 lists in 25 events are not usable" in text
    assert sum(ln.endswith("1") and "league" in ln for ln in text.splitlines()) == 20
    assert "... and 5 more events" in text
