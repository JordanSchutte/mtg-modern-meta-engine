#!/usr/bin/env python3
"""Fetch completed Melee events into bronze, slowly.

    python scripts/fetch_melee_event.py 453138
    python scripts/fetch_melee_event.py --from-discovery          # everything discovered
    python scripts/fetch_melee_event.py 453138 --force            # new version (corrections)

A 270-player event is roughly 290 requests (~12 minutes at 2.5 s apart);
a 1,500-player RC is roughly 1,550 requests (~65 minutes). Decklists already in
bronze are never requested again.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from datetime import UTC, datetime, timedelta

from mtgmeta.config import Settings
from mtgmeta.contracts import ContractError
from mtgmeta.sources.melee.fetch import fetch_event
from mtgmeta.sources.melee.http import PoliteClient, RateLimitedError
from mtgmeta.sources.melee.parse import NoRoundsError


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("tournament_ids", nargs="*", type=int)
    ap.add_argument("--from-discovery", action="store_true",
                    help="fetch every event in data/discovery/melee_events.json plus "
                         "config/extra_melee_events.json")
    ap.add_argument("--force", action="store_true", help="write a new version even if present")
    ap.add_argument("--no-decklists", action="store_true")
    ap.add_argument("--max-events", type=int, default=0,
                    help="fetch at most this many events not yet in bronze this run (0 = all)")
    ap.add_argument("--budget-minutes", type=float, default=0.0,
                    help="stop starting new events after this many minutes so a job with a "
                         "time limit ends on an event boundary (0 = no budget)")
    ap.add_argument("--min-age-days", type=float, default=0.0,
                    help="with --from-discovery: skip events that started less than this many "
                         "days ago, so late decklist uploads and result corrections land before "
                         "the event's single fetch (default 0)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()

    ids = list(args.tournament_ids)
    if args.from_discovery:
        path = settings.data_root / "discovery" / "melee_events.json"
        cutoff = datetime.now(UTC) - timedelta(days=args.min_age_days)
        for row in json.loads(path.read_text()):
            start = datetime.fromisoformat(row["start"])
            if start > cutoff:
                logging.info("event %s started %s; waiting until it is %.0f days old",
                             row["tournament_id"], start.date(), args.min_age_days)
                continue
            ids.append(row["tournament_id"])
        extra = Path("config") / "extra_melee_events.json"
        if extra.exists():
            extra_ids = [e["tournament_id"] for e in json.loads(extra.read_text())["events"]]
            logging.info("adding %d event(s) from %s", len(extra_ids), extra)
            ids += extra_ids
    if not ids:
        ap.error("give tournament ids or --from-discovery")

    ids = list(dict.fromkeys(ids))
    skipped: list[str] = []
    deferred: list[int] = []
    fetched = 0
    t0 = time.monotonic()
    with PoliteClient(settings.melee_contact_email, settings.melee_request_interval) as client:
        for tid in ids:
            elapsed_min = (time.monotonic() - t0) / 60
            if args.budget_minutes and elapsed_min >= args.budget_minutes:
                deferred.append(tid)
                continue
            if args.max_events and fetched >= args.max_events:
                deferred.append(tid)
                continue
            try:
                before = client.requests_made
                manifest = fetch_event(tid, client, settings.data_root, force=args.force,
                                       fetch_decklists=not args.no_decklists)
                if client.requests_made > before:
                    fetched += 1
            except NoRoundsError as exc:
                logging.warning("skipping: %s", exc)
                skipped.append(str(exc))
                # Many "no rounds" in one run looks like a page change, not shell events.
                if len(skipped) >= 3 and len(skipped) > len(ids) / 2:
                    logging.error("most events have no rounds; Melee's page probably changed")
                    return 2
                continue
            except ContractError as exc:
                # Fail loudly: a changed page means the parser needs attention.
                logging.error("%s", exc)
                return 2
            except RateLimitedError as exc:
                logging.error("%s", exc)
                return 3
            swiss = [r for r in manifest["rounds"] if r["is_swiss"]]
            known = sum(r["both_decks_known"] for r in swiss)
            total = sum(r["matches"] for r in swiss)
            print(f"{tid}: {manifest['name'][:60]} | swiss matches {total}, "
                  f"both decks known {known}")
    if deferred:
        print(f"\n{len(deferred)} event(s) left for the next run (budget or max-events): "
              f"{deferred}")
    if skipped:
        print(f"\nskipped {len(skipped)} event(s) with no rounds:")
        for line in skipped:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
