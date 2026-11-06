#!/usr/bin/env python3
"""Build the matchup matrix and archetype summary (gold) from silver.

    python scripts/build_gold.py            # writes data/gold/melee/*.parquet, prints both
    python scripts/build_gold.py --top 16   # wider matrix
"""

from __future__ import annotations

import argparse
import logging

from mtgmeta.config import Settings
from mtgmeta.gold import matrix


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()

    matches, archetypes = matrix.load_inputs(settings.data_root)
    matchups, summary = matrix.build(matches, archetypes)
    print(matrix.summary_text(summary))
    print()
    print(matrix.matrix_text(matchups, summary, top=args.top))
    if not args.no_write:
        written = matrix.write(matchups, summary, matrix.gold_root(settings.data_root))
        print(f"\nwrote {', '.join(p.as_posix() for p in written.values())}: "
              f"{len(matchups)} pairs, {len(summary)} archetypes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
