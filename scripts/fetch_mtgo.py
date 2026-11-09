#!/usr/bin/env python3
"""Fetch new MTGO Modern events into bronze.

    python scripts/fetch_mtgo.py --since 2026-05-18 --max-events 3    # sample first
    python scripts/fetch_mtgo.py --since 2026-05-18                   # backfill
    python scripts/fetch_mtgo.py                                      # last 45 days

Reads the month index pages from --since to today, fetches every Modern event
not yet in bronze (challenges, leagues, preliminaries, showcases...), and
prints what it did. Events from the last --min-age-days are left for the next
run, since lists appear after the event ends.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, date, datetime, timedelta

from mtgmeta.config import Settings
from mtgmeta.sources.melee.http import PoliteClient
from mtgmeta.sources.mtgo import BASE_URL
from mtgmeta.sources.mtgo import fetch as mtgo


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--since", type=date.fromisoformat,
                    default=datetime.now(UTC).date() - timedelta(days=45))
    ap.add_argument("--max-events", type=int, default=0, help="0 = no cap")
    ap.add_argument("--budget-minutes", type=float, default=0)
    ap.add_argument("--min-age-days", type=int, default=1)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()
    client = PoliteClient(settings.melee_contact_email, interval=settings.melee_request_interval,
                          base_url=BASE_URL)
    counts = mtgo.fetch_new(client, settings.data_root, args.since, args.max_events,
                            args.budget_minutes, args.min_age_days)
    print(f"MTGO: {counts}; requests made: {client.requests_made}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
