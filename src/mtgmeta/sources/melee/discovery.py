"""Find completed Melee events through the public *Find Events* search.

Melee has no tournament index, but its search page is public. The page talks to
a SignalR hub (``searchHub.search``) and gets each page of results back as
JSON. Clicking the page's own "Show more" button proved fragile (start-up
searches supersede clicks, a cookie modal blocks the mouse), so instead we:

1. open the search page once in a headless browser (Playwright), which sets up
   the SignalR connection and the anti-forgery token, with geolocation denied
   so results are global rather than sorted by distance;
2. call ``searchHub.search`` ourselves for page 1, 2, 3, ... and read the
   structured JSON (ID, Name, StartDate, Players, StatusDescription, Tags).

Results are ordered newest-*created* first, not by when the event was played,
so an old date doesn't mean we've passed the cutoff: paging stops only after
``stop_after_old`` consecutive results dated before ``since``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from mtgmeta.contracts import ContractError, require

from . import SOURCE
from .query import search_url

log = logging.getLogger(__name__)

TOURNAMENTS_CONTEXT = 2
_REQUIRED_KEYS = ("ID", "Name", "StartDate", "Players", "StatusDescription", "Tags")


@dataclass(frozen=True)
class DiscoveredEvent:
    tournament_id: int
    title: str
    organizer: str | None
    start: datetime
    enrolled_players: int
    ended: bool
    tags: tuple[str, ...]

    def to_json(self) -> dict:
        row = asdict(self)
        row["start"] = self.start.isoformat()
        row["tags"] = list(self.tags)
        return row


def parse_search_results(json_text: str) -> list[DiscoveredEvent]:
    """Parse one page of ``tournamentSearchResults`` JSON."""
    try:
        rows = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ContractError(SOURCE, "search results are not JSON", str(exc)) from exc
    require(isinstance(rows, list), SOURCE, "search results are not a JSON list")
    events: list[DiscoveredEvent] = []
    for row in rows:
        missing = [k for k in _REQUIRED_KEYS if k not in row]
        require(not missing, SOURCE, "search result missing fields",
                f"{row.get('ID', '?')}: {missing}")
        start = datetime.fromisoformat(str(row["StartDate"]).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        tags = row.get("TagDescriptions") or row["Tags"] or []
        events.append(
            DiscoveredEvent(
                tournament_id=int(row["ID"]),
                title=str(row["Name"]).strip(),
                organizer=row.get("OrganizationName"),
                start=start,
                enrolled_players=int(row["Players"] or 0),
                ended=row["StatusDescription"] == "Ended",
                tags=tuple(str(t).strip() for t in tags if str(t).strip()),
            )
        )
    return events


def is_finished(ev: DiscoveredEvent, now: datetime, grace: timedelta = timedelta(days=2)) -> bool:
    """Finished = status Ended, or it started more than ``grace`` ago.

    The status isn't always reliable: some completed events (e.g. a 538-player
    Open) never flip to Ended. ``grace`` covers multi-day events in progress.
    """
    return ev.ended or ev.start <= now - grace


def select_events(
    events: Iterable[DiscoveredEvent],
    since: datetime,
    min_players: int,
    now: datetime | None = None,
) -> list[DiscoveredEvent]:
    """Keep finished events on/after ``since`` at or above the player bar.

    The format is already filtered by the search query; the fetch step confirms
    it again from each match's ``Format`` field.
    """
    now = now or datetime.now(UTC)
    keep: dict[int, DiscoveredEvent] = {}
    for ev in events:
        if is_finished(ev, now) and ev.start >= since and ev.enrolled_players >= min_players:
            keep[ev.tournament_id] = ev
    return sorted(keep.values(), key=lambda e: e.start, reverse=True)


def looks_newest_first(events: Sequence[DiscoveredEvent], min_share: float = 0.8) -> bool:
    """Global results come newest-created first, so tournament IDs mostly descend.

    When Melee knows a location it sorts by distance instead, IDs jump around,
    and the stop rule would be meaningless.
    """
    ids = [e.tournament_id for e in events]
    pairs = list(zip(ids, ids[1:], strict=False))
    if len(pairs) < 5:
        return True
    return sum(a > b for a, b in pairs) / len(pairs) >= min_share


def should_stop(events: Sequence[DiscoveredEvent], since: datetime, stop_after_old: int) -> bool:
    """True once the last ``stop_after_old`` results are all dated before ``since``."""
    if len(events) < stop_after_old:
        return False
    return all(ev.start < since for ev in events[-stop_after_old:])


@dataclass(frozen=True)
class DiscoveryResult:
    selected: list[DiscoveredEvent]
    scanned: list[DiscoveredEvent]
    stop_reason: str


# Runs before Melee's scripts: forget any saved location and deny geolocation,
# so results are global and newest-first rather than sorted by distance.
_NO_LOCATION_SCRIPT = """
try { localStorage.removeItem('flattsware.location'); } catch (e) {}
Object.defineProperty(navigator, 'geolocation', { value: {
  getCurrentPosition: (ok, err) => err && err({ code: 1, PERMISSION_DENIED: 1,
    POSITION_UNAVAILABLE: 2, TIMEOUT: 3, message: 'denied' }),
  watchPosition: () => 0,
  clearWatch: () => {},
}});
"""

# Calls searchHub.search for one page and waits for that request's results.
# Arguments mirror the page's own call: (token, requestId, context, page, game,
# search, location, filters, timeZone, followedOrganizersOnly).
_FETCH_PAGE_JS = """
async ([pageNo, context, game, filters]) => {
  const hub = $.connection.searchHub;
  if (!window.__mme) {
    window.__mme = [];
    $.connection.hub.proxies.searchhub.on('receiveResponse', n => window.__mme.push(n));
  }
  if ($.connection.hub.state !== $.signalR.connectionState.connected) {
    await new Promise((res, rej) => $.connection.hub.start().done(res).fail(e => rej(String(e))));
  }
  const id = Date.now();
  const token = $('input[name="__RequestVerificationToken"]').val();
  hub.server.search(token, id, context, pageNo, game, null, null, filters,
                    Intl.DateTimeFormat().resolvedOptions().timeZone, false);
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 250));
    const mine = window.__mme.filter(n => String(n.RequestId) === String(id));
    const res = mine.find(n => n.MessageType === 'tournamentSearchResults');
    if (res) {
      return { code: res.Data.Code, json: res.Data.Json,
               error: res.Data.Error ? String(res.Data.Message) : null };
    }
    const err = mine.find(n => n.Type === 1);
    if (err) return { error: String(err.Data) };
    if (mine.some(n => n.MessageType === 'searchCompleted')) return { code: 404, json: null };
  }
  return { timeout: true };
}
"""


def discover(
    since: datetime,
    min_players: int = 100,
    filters: Sequence[str] = ("Modern", "Past"),
    game: str = "MagicTheGathering",
    max_pages: int = 300,
    stop_after_old: int = 40,
    page_wait_seconds: float = 2.5,
    headed: bool = True,
) -> DiscoveryResult:
    """Page through Melee's search until past ``since``; return qualifying events."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)

    url = search_url(filters, game=game)
    log.info("opening %s", url)
    events: list[DiscoveredEvent] = []
    stop_reason = f"reached max_pages ({max_pages})"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(permissions=[])  # no geolocation grant
        context.add_init_script(_NO_LOCATION_SCRIPT)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_function(
                "() => window.$ && $.connection && $.connection.searchHub"
                " && $.connection.hub.proxies && $.connection.hub.proxies.searchhub",
                timeout=30_000,
            )
        except PlaywrightTimeout:
            # Say what we actually got: a firewall/challenge page looks very different.
            title = page.title()
            body = page.evaluate("() => (document.body && document.body.innerText) || ''")
            require(False, SOURCE, "search page never set up its SignalR searchHub",
                    f"{'headless' if not headed else 'headed'} browser got title={title!r}, "
                    f"body starts {body[:200]!r}")
        # Let the page's own start-up search finish; it restarts the hub connection.
        page.wait_for_timeout(max(page_wait_seconds, 3.0) * 1000)

        for page_no in range(1, max_pages + 1):
            result = None
            for attempt in range(1, 4):
                result = page.evaluate(_FETCH_PAGE_JS, [page_no, TOURNAMENTS_CONTEXT, game,
                                                        list(filters)])
                if not result.get("timeout") and not result.get("error"):
                    break
                log.info("page %d attempt %d: %s; retrying", page_no, attempt, result)
                page.wait_for_timeout(3_000)
            if result.get("timeout") or result.get("error"):
                raise ContractError(SOURCE, "search hub gave no results",
                                    f"page {page_no}: {result}")
            if result.get("code") == 404 or not result.get("json"):
                stop_reason = f"no more results after {page_no - 1} pages"
                break

            batch = parse_search_results(result["json"])
            if not batch:
                stop_reason = f"empty page {page_no}"
                break
            events.extend(batch)
            if page_no == 3:  # 24 results: enough to judge the ordering
                require(looks_newest_first(events), SOURCE,
                        "search results aren't newest-first (location sort applied?)",
                        f"first ids: {[e.tournament_id for e in events[:10]]}")
            if should_stop(events, since, stop_after_old):
                tail = events[-stop_after_old:]
                lo, hi = min(e.start for e in tail), max(e.start for e in tail)
                stop_reason = (
                    f"passed the cutoff after {page_no} pages: last {stop_after_old} results "
                    f"dated {lo:%Y-%m-%d}..{hi:%Y-%m-%d}"
                )
                break
            page.wait_for_timeout(page_wait_seconds * 1000)  # be gentle between pages
        browser.close()

    log.info("%s", stop_reason)
    log.info("scanned %d search results", len(events))
    return DiscoveryResult(
        selected=select_events(events, since, min_players),
        scanned=events,
        stop_reason=stop_reason,
    )
