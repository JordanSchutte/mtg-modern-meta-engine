#!/usr/bin/env python3
"""Find completed Melee Modern events with enough players.

    python scripts/discover_melee.py --since 2026-04-20 --min-players 100
    python scripts/discover_melee.py --since 2026-09-01 --headed      # watch the browser

Writes data/discovery/melee_events.json and prints a table.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime

from mtgmeta.config import Settings
from mtgmeta.sources.melee.discovery import discover


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--since", required=True, help="YYYY-MM-DD, earliest event date to keep")
    ap.add_argument("--min-players", type=int, default=100)
    ap.add_argument("--filter", action="append", dest="filters",
                    help="search filter (repeatable); default: Modern + Past")
    ap.add_argument("--max-pages", type=int, default=300)
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()
    since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)

    result = discover(
        since=since,
        min_players=args.min_players,
        filters=tuple(args.filters or ("Modern", "Past")),
        max_pages=args.max_pages,
        page_wait_seconds=settings.melee_request_interval,
        headed=args.headed,
    )
    events = result.selected

    out = settings.data_root / "discovery" / "melee_events.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([e.to_json() for e in events], indent=2), encoding="utf-8")
    # Everything the search returned, in page order: the audit trail for the stop rule.
    scan = settings.data_root / "discovery" / "melee_search_scan.json"
    scan.write_text(json.dumps([e.to_json() for e in result.scanned], indent=2), encoding="utf-8")

    print(f"\nstopped: {result.stop_reason}")
    print(f"scanned {len(result.scanned)} results (all saved to {scan})")
    print(f"\n{len(events)} events since {args.since} with >= {args.min_players} players\n")
    print(f"{'date':<12}{'players':>8}  {'id':>7}  title")
    for e in events:
        print(f"{e.start:%Y-%m-%d}  {e.enrolled_players:>6}  {e.tournament_id:>7}  {e.title[:60]}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
