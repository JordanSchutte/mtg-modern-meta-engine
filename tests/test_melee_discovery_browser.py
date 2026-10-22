"""Runs the real Playwright discovery loop against a fake Melee search page.

The fake page reproduces what matters about the real one: a jQuery SignalR
``searchHub`` whose ``search`` call answers asynchronously through
``receiveResponse`` (searchStarted, tournamentSearchResults, searchCompleted),
paging by page number, newest-created first. Skipped when no Chromium is
available (e.g. a machine that hasn't run ``playwright install chromium``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from mtgmeta.sources.melee import discovery  # noqa: E402

TOTAL_PAGES = 12  # 96 events, newest first, one every 2 days going back


def _events_json() -> list[dict]:
    start = datetime(2026, 10, 20, tzinfo=UTC)
    rows = []
    for i in range(TOTAL_PAGES * 8):
        rows.append({
            "ID": 500000 - i,
            "Name": f"Event {i}",
            "StartDate": (start - timedelta(days=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Players": 150 if i % 5 == 0 else 12,
            "StatusDescription": "Ended",
            "Tags": ["Modern"],
            "TagDescriptions": ["Modern", "Magic: The Gathering"],
            "OrganizationName": "Org",
        })
    return rows


FAKE_PAGE = """<!doctype html><html><body>
<input name="__RequestVerificationToken" value="tok">
<script>
const ROWS = %s;
const handlers = [];
window.$ = function (sel) { return { val: () => 'tok' }; };
$.signalR = { connectionState: { connected: 1, disconnected: 4 } };
$.connection = {
  hub: { state: 1, proxies: { searchhub: { on: (name, fn) => handlers.push(fn) } },
         start: () => ({ done: f => { f(); return { fail: () => {} }; } }) },
  searchHub: { server: { search: (token, id, ctx, page, game, q, loc, filters, tz, fol) => {
    const send = (msg, data, type) => setTimeout(
      () => handlers.forEach(h => h({ RequestId: String(id), Type: type, MessageType: msg,
                                      Data: data })), 50);
    send('searchStarted', {}, 0);
    const rows = ROWS.slice((page - 1) * 8, page * 8);
    if (rows.length) send('tournamentSearchResults', { Code: 200, Json: JSON.stringify(rows) }, 2);
    else send('tournamentSearchResults', { Code: 404, Json: null }, 2);
    send('searchCompleted', {}, 3);
  } } },
};
</script></body></html>"""


@pytest.fixture
def fake_melee(monkeypatch):
    """Route melee.gg to the fake page and use whatever Chromium is installed."""
    from playwright.sync_api import sync_playwright as real_sync_playwright

    html = FAKE_PAGE % json.dumps(_events_json())

    class Wrapped:
        def __enter__(self):
            self._cm = real_sync_playwright()
            pw = self._cm.__enter__()
            real_launch = pw.chromium.launch

            def launch(**kw):
                import glob
                exes = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")
                if exes:
                    kw.setdefault("executable_path", exes[0])
                browser = real_launch(**kw)
                real_new_context = browser.new_context

                def new_context(**ckw):
                    ctx = real_new_context(**ckw)
                    ctx.route("https://melee.gg/**",
                              lambda r: r.fulfill(body=html, content_type="text/html"))
                    return ctx

                browser.new_context = new_context
                return browser

            pw.chromium.launch = launch
            return pw

        def __exit__(self, *exc):
            return self._cm.__exit__(*exc)

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: Wrapped())


def _run(**kw):
    try:
        return discovery.discover(page_wait_seconds=0.0, **kw)
    except Exception as exc:  # no browser installed on this machine
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium not installed")
        raise


def test_pages_until_cutoff_and_selects_big_events(fake_melee):
    since = datetime(2026, 9, 1, tzinfo=UTC)
    result = _run(since=since, min_players=100, stop_after_old=16)
    assert "passed the cutoff" in result.stop_reason
    # Events every 2 days from Oct 20; Sep 1 is index 24, so 25 qualify by date,
    # every 5th is big: indices 0,5,10,15,20 -> 5 events.
    assert [e.title for e in result.selected] == [f"Event {i}" for i in (0, 5, 10, 15, 20)]
    assert len(result.scanned) < TOTAL_PAGES * 8  # stopped early, didn't read everything


def test_stops_when_melee_runs_out(fake_melee):
    result = _run(since=datetime(2020, 1, 1, tzinfo=UTC), min_players=100)
    assert result.stop_reason == f"no more results after {TOTAL_PAGES} pages"
    assert len(result.scanned) == TOTAL_PAGES * 8
