#!/usr/bin/env python3
"""Forecast next weekend's paper field and each archetype's position against it.

    python scripts/build_forecast.py            # writes gold/forecast/*.parquet
    python scripts/build_forecast.py --no-write

Backtests every candidate model on past paper weeks (Melee), picks the best by
log-loss, forecasts the week of the next Saturday from paper + MTGO history,
and weights the current matchup matrix by that field. Run after build_gold.py.
"""

from __future__ import annotations

import argparse
import logging

from mtgmeta.config import Settings
from mtgmeta.gold import forecast


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--min-lists", type=int, default=forecast.MIN_PAPER_LISTS,
                    help="paper lists a week needs to count in the backtest")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = Settings.from_env().data_root
    bt, field, pos = forecast.run(root, min_lists=args.min_lists)
    print(forecast.report(bt, field, pos))
    if not args.no_write:
        written = forecast.write(bt, field, pos, root)
        print(f"\nwrote {', '.join(str(p) for p in written.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
