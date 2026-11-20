#!/usr/bin/env python3
"""Print the coverage flags: events whose lists never published, and how long
since the last paper event the forecast could score.

    python scripts/coverage_flags.py
    python scripts/coverage_flags.py --today 2026-11-19

Reads silver only (Melee events/matches/decklists/decklist_archetypes, MTGO
events), so run it after classify_decklists.py; the watcher appends its
output to reports/coverage.txt. The MCP ``coverage`` tool returns the same.
"""

from __future__ import annotations

import argparse
from datetime import date

from mtgmeta.config import Settings
from mtgmeta.silver import coverage


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--today", type=date.fromisoformat, default=None,
                    help="reference date for 'days ago' and the grace window (default: today)")
    args = ap.parse_args()
    print(coverage.report(Settings.from_env().data_root, args.today))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
