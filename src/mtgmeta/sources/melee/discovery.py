"""Find completed Melee events through the public *Find Events* search.

Melee has no tournament index, but its search page is public. Results arrive
over SignalR, so the page is rendered in a headless browser (Playwright) and the
result cards are parsed from the DOM.

Results are ordered by when the event was *created* on Melee, not when it was
played, so an old date does not mean we've passed the cutoff: paging stops
only after ``stop_after_old`` consecutive results dated before ``since``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup

from mtgmeta.contracts import require

from . import SOURCE
from .query import search_url

log = logging.getLogger(__name__)

ITEM_SELECTOR = ".home-tournament-search-item"
SHOW_MORE_SELECTOR = ".home-tournament-show-more-tournaments-button"
_ID_RE = re.compile(r"/Tournament/View/(\d+)")
_PLAYERS_RE = re.compile(r"([\d,]+)\s+Enrolled Players?", re.I)


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


def parse_search_items(page_html: str) -> list[DiscoveredEvent]:
    """Parse every result card on a rendered search page."""
    soup = BeautifulSoup(page_html, "html.parser")
    events: list[DiscoveredEvent] = []
    for item in soup.select(ITEM_SELECTOR):
        link = item.select_one('a[href*="/Tournament/View/"]')
        require(link is not None, SOURCE, "search card has no /Tournament/View/ link")
        match = _ID_RE.search(link["href"])
        require(match is not None, SOURCE, "search card link has no tournament id", link["href"])
        tid = int(match.group(1))

        date_el = item.select_one(".home-tournament-search-item-date")
        require(date_el is not None and date_el.get("data-value"), SOURCE,
                "search card has no date data-value", str(tid))
        start = datetime.fromisoformat(date_el["data-value"].replace("Z", "+00:00"))

        title_el = item.select_one(".home-tournament-search-item-title")
        require(title_el is not None, SOURCE, "search card has no title", str(tid))

        subtitle = item.select_one(".home-tournament-search-item-subtitle")
        require(subtitle is not None, SOURCE, "search card has no subtitle", str(tid))
        players = _PLAYERS_RE.search(subtitle.get_text(" ", strip=True))
        require(players is not None, SOURCE, "search card has no 'N Enrolled Players'",
                f"{tid}: {subtitle.get_text(' ', strip=True)!r}")

        organizer = item.select_one(".home-tournament-search-item-organizer")
        badge = item.select_one(".home-tournament-status-label")
        tags = tuple(
            a.get_text(strip=True)
            for a in item.select(".home-tournament-search-item-tag a")
            if a.get_text(strip=True)
        )
        events.append(
            DiscoveredEvent(
                tournament_id=tid,
                title=title_el.get_text(strip=True),
                organizer=organizer.get_text(strip=True) if organizer else None,
                start=start,
                enrolled_players=int(players.group(1).replace(",", "")),
                ended=bool(badge and "ended" in badge.get_text(strip=True).lower()),
                tags=tags,
            )
        )
    return events


def is_finished(ev: DiscoveredEvent, now: datetime, grace: timedelta = timedelta(days=2)) -> bool:
    """Finished = badge says Ended, or it started more than ``grace`` ago.

    The "Ended" badge isn't reliable: some completed events (e.g. a 538-player
    Open) never get it. ``grace`` covers multi-day events still in progress.
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


@dataclass(frozen=True)
class DiscoveryResult:
    selected: list[DiscoveredEvent]
    scanned: list[DiscoveredEvent]
    stop_reason: str


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


def should_stop(events: Sequence[DiscoveredEvent], since: datetime, stop_after_old: int) -> bool:
    """True once the last ``stop_after_old`` results are all dated before ``since``."""
    if len(events) < stop_after_old:
        return False
    return all(ev.start < since for ev in events[-stop_after_old:])


def discover(
    since: datetime,
    min_players: int = 100,
    filters: Sequence[str] = ("Modern", "Past"),
    max_pages: int = 300,
    stop_after_old: int = 40,
    page_wait_seconds: float = 2.5,
    headed: bool = False,
) -> DiscoveryResult:
    """Page through Melee's search until past ``since``; return qualifying events."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)

    url = search_url(filters)
    log.info("searching %s", url)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(permissions=[])  # no geolocation grant
        context.add_init_script(_NO_LOCATION_SCRIPT)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(ITEM_SELECTOR, timeout=30_000)
        except PlaywrightTimeout:
            require(False, SOURCE, "search page rendered no result cards", url)

        events = parse_search_items(page.content())
        stop_reason = f"reached max_pages ({max_pages})"
        for page_no in range(1, max_pages + 1):
            if should_stop(events, since, stop_after_old):
                tail = events[-stop_after_old:]
                lo, hi = min(e.start for e in tail), max(e.start for e in tail)
                stop_reason = (
                    f"passed the cutoff after {page_no} pages: last {stop_after_old} results "
                    f"dated {lo:%Y-%m-%d}..{hi:%Y-%m-%d}"
                )
                break
            more = page.query_selector(SHOW_MORE_SELECTOR)
            if more is None or not more.is_visible():
                stop_reason = f"no show-more button after {page_no} pages"
                break
            before = len(events)
            more.click()
            try:
                page.wait_for_function(
                    "([sel, n]) => document.querySelectorAll(sel).length > n",
                    arg=[ITEM_SELECTOR, before],
                    timeout=20_000,
                )
            except PlaywrightTimeout:
                stop_reason = f"show-more returned nothing new after {page_no} pages"
                break
            page.wait_for_timeout(page_wait_seconds * 1000)  # be gentle between pages
            events = parse_search_items(page.content())
            if page_no == 2:  # 24 results: enough to judge the ordering
                require(looks_newest_first(events), SOURCE,
                        "search results aren't newest-first (location sort applied?)",
                        f"first ids: {[e.tournament_id for e in events[:10]]}")
        browser.close()

    log.info("%s", stop_reason)
    log.info("scanned %d search results", len(events))
    return DiscoveryResult(
        selected=select_events(events, since, min_players),
        scanned=events,
        stop_reason=stop_reason,
    )
