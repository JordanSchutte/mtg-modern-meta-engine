#!/usr/bin/env python3
"""Add upcoming Modern events on Melee to the watchlist.

    python scripts/update_watchlist.py --headless

Scans Find Events with the Upcoming filter, newest-created first, and stops
at the first event already on the list, so after the first full scan a run
is usually one or two pages. Settling (fetching events once they're over)
is done by ``fetch_melee_event.py --from-watchlist``.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

from mtgmeta.config import Settings
from mtgmeta.sources.melee import watchlist
from mtgmeta.sources.melee.discovery import discover


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--filter", action="append", dest="filters",
                    help="search filter (repeatable); default: Modern + Upcoming")
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()
    path = watchlist.watchlist_path(settings.data_root)
    entries = watchlist.load(path)
    known = frozenset(entries)
    now = datetime.now(UTC)

    result = discover(
        since=now,  # date rule can't fire on upcoming events; known_ids is the stop rule
        min_players=0,
        filters=tuple(args.filters or ("Modern", "Upcoming")),
        max_pages=args.max_pages,
        page_wait_seconds=settings.melee_request_interval,
        headed=not args.headless,
        known_ids=known,
    )
    added = watchlist.merge(entries, result.scanned, now)
    watchlist.save(path, entries)

    pending = [e for e in entries.values() if e["status"] == watchlist.PENDING]
    print(f"\nstopped: {result.stop_reason}")
    print(f"scanned {len(result.scanned)} upcoming events, {added} new; "
          f"watchlist has {len(entries)} entries, {len(pending)} pending")
    for e in sorted(pending, key=lambda e: e["start"])[:15]:
        print(f"  {e['start'][:10]}  {e['tournament_id']:>7}  {e['title'][:60]}")
    if len(pending) > 15:
        print(f"  ... {len(pending) - 15} more")
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
