#!/usr/bin/env python3
"""Start a new format period (a B&R announcement or a format-warping set).

    python scripts/new_period.py --start 2026-12-15 --ban "Basking Broodscale" --ban "Card B"

Copies the newest rules file to config/archetypes/<format>-<start>.yml with
the banned cards removed from every signature (min_signature lowered where
needed) and a note on each rule touched. The old file stays and keeps
governing events before <start>; the matrix pools within a period.

A rule whose signature loses most of its cards is kept but its note says
REVIEW: the deck may be dead, may survive with a replacement, or may become
something else — the next weekly proposal run will say which. A rule that
loses every signature card is dropped and named in the header comment.

The logic lives in mtgmeta.archetypes.period; the step list for the day is
docs/runbook-new-period.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mtgmeta.archetypes.period import start_period


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--start", required=True, help="YYYY-MM-DD the new period starts")
    ap.add_argument("--ban", action="append", default=[], help="banned card (repeatable)")
    ap.add_argument("--unban", action="append", default=[], help="unbanned card, noted only")
    ap.add_argument("--format", default="modern")
    ap.add_argument("--note", default="", help="what happened (e.g. 'B&R 2026-12-15')")
    ap.add_argument("--folder", default="config/archetypes", type=Path,
                    help="rules folder (default config/archetypes)")
    args = ap.parse_args()
    try:
        result = start_period(args.folder, args.start, args.ban, args.unban,
                              fmt=args.format, note=args.note)
    except (FileNotFoundError, FileExistsError) as e:
        print(e)
        return 1
    print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
