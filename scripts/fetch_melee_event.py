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

from mtgmeta.config import Settings
from mtgmeta.contracts import ContractError
from mtgmeta.sources.melee.fetch import fetch_event
from mtgmeta.sources.melee.http import PoliteClient, RateLimitedError
from mtgmeta.sources.melee.parse import NoRoundsError


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("tournament_ids", nargs="*", type=int)
    ap.add_argument("--from-discovery", action="store_true",
                    help="fetch every event in data/discovery/melee_events.json")
    ap.add_argument("--force", action="store_true", help="write a new version even if present")
    ap.add_argument("--no-decklists", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()

    ids = list(args.tournament_ids)
    if args.from_discovery:
        path = settings.data_root / "discovery" / "melee_events.json"
        ids += [row["tournament_id"] for row in json.loads(path.read_text())]
    if not ids:
        ap.error("give tournament ids or --from-discovery")

    ids = list(dict.fromkeys(ids))
    skipped: list[str] = []
    with PoliteClient(settings.melee_contact_email, settings.melee_request_interval) as client:
        for tid in ids:
            try:
                manifest = fetch_event(tid, client, settings.data_root, force=args.force,
                                       fetch_decklists=not args.no_decklists)
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
    if skipped:
        print(f"\nskipped {len(skipped)} event(s) with no rounds:")
        for line in skipped:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
