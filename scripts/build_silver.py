#!/usr/bin/env python3
"""Rebuild silver from bronze and print the coverage report.

    python scripts/build_silver.py

Reads the latest version of every Melee event in bronze, writes
data/silver/melee/{events,matches,decklists,decklist_cards,players}.parquet
and prints, per event, how much of the Swiss field the matrix can use.
"""

from __future__ import annotations

import argparse
import logging

from mtgmeta.config import Settings
from mtgmeta.silver import melee as silver


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--no-write", action="store_true", help="report only")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()

    build = silver.build(settings.data_root)
    print(silver.coverage_report(build))
    if not args.no_write:
        out = silver.silver_root(settings.data_root)
        written = silver.write_parquet(build, out)
        print(f"\nwrote {len(written)} tables to {out}: "
              + ", ".join(f"{t}={n}" for t, n in build.counts().items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
