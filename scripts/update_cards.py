#!/usr/bin/env python3
"""Refresh the Scryfall card table (silver/cards.parquet) if older than a week.

    python scripts/update_cards.py [--force]
"""

from __future__ import annotations

import argparse
import logging

from mtgmeta.cards import scryfall
from mtgmeta.config import Settings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    path = scryfall.update(Settings.from_env().data_root, force=args.force)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
