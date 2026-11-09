"""Fetch MTGO event pages into bronze.

Layout (append-only, like Melee's)::

    bronze/mtgo/index/YYYY-MM/<fetch day>.html     the month index as seen that day
    bronze/mtgo/events/<site_name>/page.html        the event page, written once complete
    bronze/mtgo/events/<site_name>/manifest.json    counts, fetch time, parser version
    bronze/mtgo/events/<site_name>/attempts.json    incomplete fetches (page not ready yet)

An event is "known" once its manifest says complete; later runs skip it.
Published MTGO lists do not change after publication, so there is no refresh
pass. A page that arrives without decklists is logged in attempts.json and
retried on the next run; after ``MAX_ATTEMPTS``, or once the event is
``STALE_DAYS`` old (MTGO publishes within a day; an old event that still
redirects to /decklists never had its lists published), it is recorded as empty
so the run stops asking (the file stays, so the decision is reversible).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from mtgmeta.sources.melee.http import PoliteClient
from mtgmeta.sources.mtgo import BASE_URL
from mtgmeta.sources.mtgo.discovery import EventRef, months, parse_index
from mtgmeta.sources.mtgo.parse import IncompleteEventError, parse_event

log = logging.getLogger(__name__)
PARSER_VERSION = 1
MAX_ATTEMPTS = 6
STALE_DAYS = 7


def bronze_root(data_root: Path) -> Path:
    return data_root / "bronze" / "mtgo"


def event_dir(data_root: Path, site_name: str) -> Path:
    return bronze_root(data_root) / "events" / site_name


def known(data_root: Path) -> set[str]:
    """Site names with a complete (or given-up) manifest."""
    out = set()
    for mf in (bronze_root(data_root) / "events").glob("*/manifest.json"):
        try:
            if json.loads(mf.read_text(encoding="utf-8")).get("status") in {"complete", "empty"}:
                out.add(mf.parent.name)
        except (OSError, ValueError):
            continue
    return out


def _attempts(d: Path) -> list[dict]:
    f = d / "attempts.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def discover(client: PoliteClient, data_root: Path, since: date, until: date | None = None,
             ) -> list[EventRef]:
    """Modern events on every month index from ``since`` to today, saving each index."""
    until = until or datetime.now(UTC).date()
    refs: dict[str, EventRef] = {}
    today = until.isoformat()
    for ym in months(since, until):
        html = client.get_text(f"/decklists/{ym}")
        out = bronze_root(data_root) / "index" / ym.replace("/", "-") / f"{today}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        found = [r for r in parse_index(html) if r.day >= since]
        log.info("index %s: %d Modern events", ym, len(found))
        for r in found:
            refs[r.site_name] = r
    return sorted(refs.values(), key=lambda r: (r.day, r.site_name))


def fetch_event(ref: EventRef, client: PoliteClient, data_root: Path) -> str:
    """Fetch one event. Returns 'complete', 'incomplete' or 'empty'."""
    d = event_dir(data_root, ref.site_name)
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        html = client.get_text(ref.path, referer=f"{BASE_URL}/decklists")
        event = parse_event(html, ref.site_name)
    except (IncompleteEventError, httpx.HTTPStatusError) as exc:
        html = exc.response.text if isinstance(exc, httpx.HTTPStatusError) else html
        attempts = _attempts(d) + [{"at": now, "reason": str(exc), "bytes": len(html)}]
        (d / "attempts.json").write_text(json.dumps(attempts, indent=1), encoding="utf-8")
        (d / "page.incomplete.html").write_text(html, encoding="utf-8")
        age = (datetime.now(UTC).date() - ref.day).days
        if len(attempts) >= MAX_ATTEMPTS or age >= STALE_DAYS:
            (d / "manifest.json").write_text(json.dumps({
                "site_name": ref.site_name, "status": "empty", "attempts": len(attempts),
                "fetched_at": now, "day": ref.day.isoformat(), "kind": ref.kind,
            }, indent=1), encoding="utf-8")
            log.warning("%s: no decklists (%d attempts, %d days old); recorded as empty",
                        ref.site_name, len(attempts), age)
            return "empty"
        log.info("%s: %s (attempt %d)", ref.site_name, exc, len(attempts))
        return "incomplete"
    (d / "page.html").write_text(html, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({
        "site_name": ref.site_name, "status": "complete", "fetched_at": now,
        "parser_version": PARSER_VERSION, "event_id": event.event_id, "name": event.name,
        "type": event.kind, "kind": ref.kind, "day": event.day.isoformat(),
        "player_count": event.player_count, "decklists": len(event.decklists),
        "top8_matches": len(event.top8), "ref": asdict(ref) | {"day": ref.day.isoformat()},
    }, indent=1), encoding="utf-8")
    log.info("%s: %d lists%s", ref.site_name, len(event.decklists),
             f" of {event.player_count} players" if event.player_count else "")
    return "complete"


def fetch_new(client: PoliteClient, data_root: Path, since: date, max_events: int = 0,
              budget_minutes: float = 0, min_age_days: int = 1) -> dict[str, int]:
    """Discover, then fetch everything not yet known. Events younger than
    ``min_age_days`` are left for later (lists are published after the event)."""
    t0 = time.monotonic()
    have = known(data_root)
    today = datetime.now(UTC).date()
    todo = [r for r in discover(client, data_root, since)
            if r.site_name not in have and (today - r.day).days >= min_age_days]
    log.info("%d events to fetch (%d already in bronze)", len(todo), len(have))
    counts = {"complete": 0, "incomplete": 0, "empty": 0, "skipped": 0}
    for i, ref in enumerate(todo):
        if max_events and i >= max_events:
            counts["skipped"] += len(todo) - i
            break
        if budget_minutes and time.monotonic() - t0 > budget_minutes * 60:
            log.warning("budget reached; %d events left for the next run", len(todo) - i)
            counts["skipped"] += len(todo) - i
            break
        counts[fetch_event(ref, client, data_root)] += 1
    return counts
