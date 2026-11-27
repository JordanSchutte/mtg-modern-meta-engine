#!/usr/bin/env python3
"""Score every recorded forecast against the paper week it was made for.

    python scripts/score_forecasts.py           # -> gold/melee/forecast/forecast_scores.parquet
    python scripts/score_forecasts.py --no-write

Reads the dated copies under gold/melee/forecast/field_forecast/ (the watcher
pulls them back from R2 first), finds each one's realized paper week in silver
and, if that week has enough lists and the forecast was written before the
week's first event, scores it with the same log-loss/TVD as the backtest plus
the naive "same as last paper week" baseline. Run after build_forecast.py.
No history, or nothing scorable yet, writes nothing and exits 0.
"""

from __future__ import annotations

import argparse
import logging

from mtgmeta.config import Settings
from mtgmeta.gold import forecast, scoring


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--min-lists", type=int, default=forecast.MIN_PAPER_LISTS,
                    help="paper lists a week needs before a forecast for it is scored")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = Settings.from_env().data_root
    sc, found = scoring.run(root, min_lists=args.min_lists)
    print(scoring.report(sc, found))
    if not args.no_write:
        written = scoring.write(sc, root)
        print(f"\nwrote {written}" if written else "\nnothing to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
