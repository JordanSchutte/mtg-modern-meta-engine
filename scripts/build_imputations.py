#!/usr/bin/env python3
"""Fill missing decklists from the same player's nearby published list.

    python scripts/build_imputations.py                 # 14-day window
    python scripts/build_imputations.py --window-days 3

Writes silver/melee/imputations.parquet and prints the evidence for the
assumption (how often two nearby lists by one player were the same deck).
Run after classify_decklists.py so the archetype comparison is available.
"""

from __future__ import annotations

import argparse
import logging

from mtgmeta.config import Settings
from mtgmeta.silver import impute


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--window-days", type=int, default=impute.DEFAULT_WINDOW_DAYS)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = Settings.from_env().data_root

    validation = impute.validate(root, args.window_days)
    imputations = impute.impute(root, args.window_days)
    gain = impute.coverage_gain(root, imputations)
    print(impute.report(imputations, validation, gain, impute.per_event(root, imputations)))
    if not args.no_write:
        print(f"wrote {impute.write(imputations, root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
