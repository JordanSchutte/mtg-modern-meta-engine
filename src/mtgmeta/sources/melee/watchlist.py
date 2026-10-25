"""Watchlist of upcoming Melee events, settled after they finish.

Melee's *Find Events* search is ordered by when an event was **created**, and
large events (Regional Championships, Spotlights) are created months before
they run. A search for past events therefore stops paging long before it
reaches them. Enrollment isn't visible ahead of time either (an RC shows 0-2
players until registration fills), so the size bar can't be applied up front.

So the watcher keeps a list:

1. **Scan** ``Modern + Upcoming`` each run, stopping at the first id already on
   the list (results are newest-created first). Every event seen is recorded as
   ``pending`` with its start date.
2. **Settle** entries whose start is at least ``min_age`` in the past: fetch the
   event with a player minimum. Too small -> ``too_small`` (two requests, never
   looked at again); no rounds -> ``no_rounds``; otherwise ``fetched``.

The list lives in bronze (``<data_root>/bronze/melee/watchlist.json``) so it
rides along with the rclone sync and nothing is ever dropped from it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .discovery import DiscoveredEvent
from .fetch import bronze_root

PENDING = "pending"
FETCHED = "fetched"
TOO_SMALL = "too_small"
NO_ROUNDS = "no_rounds"


def watchlist_path(data_root: Path) -> Path:
    return bronze_root(data_root) / "watchlist.json"


def load(path: Path) -> dict[int, dict]:
    """Entries keyed by tournament id (JSON keys are strings on disk)."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw["events"].items()}


def save(path: Path, entries: dict[int, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "updated_at": datetime.now(UTC).isoformat(),
        "events": {str(k): entries[k] for k in sorted(entries)},
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def merge(entries: dict[int, dict], scanned: Iterable[DiscoveredEvent],
          now: datetime | None = None) -> int:
    """Add newly seen events as pending; refresh title/start on known ones.

    Returns how many were new. Statuses are never changed here.
    """
    now = now or datetime.now(UTC)
    added = 0
    for ev in scanned:
        entry = entries.get(ev.tournament_id)
        if entry is None:
            entries[ev.tournament_id] = {
                "tournament_id": ev.tournament_id,
                "title": ev.title,
                "organizer": ev.organizer,
                "start": ev.start.isoformat(),
                "tags": list(ev.tags),
                "first_seen": now.isoformat(),
                "status": PENDING,
            }
            added += 1
        else:
            # Organizers reschedule; keep the latest start so settling waits correctly.
            entry["title"] = ev.title
            entry["start"] = ev.start.isoformat()
    return added


def due(entries: dict[int, dict], now: datetime | None = None,
        min_age: timedelta = timedelta(days=2)) -> list[dict]:
    """Pending entries whose start is at least ``min_age`` ago, oldest first."""
    now = now or datetime.now(UTC)
    cutoff = now - min_age
    out = [e for e in entries.values()
           if e["status"] == PENDING and datetime.fromisoformat(e["start"]) <= cutoff]
    return sorted(out, key=lambda e: e["start"])


def settle(entry: dict, status: str, now: datetime | None = None, **facts) -> None:
    entry["status"] = status
    entry["settled_at"] = (now or datetime.now(UTC)).isoformat()
    entry.update(facts)
