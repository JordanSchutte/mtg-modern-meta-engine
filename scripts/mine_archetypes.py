#!/usr/bin/env python3
"""Draft an archetype rules file from the decklists in silver.

    python scripts/mine_archetypes.py --period-start 2026-05-18
    python scripts/mine_archetypes.py --period-start 2026-05-18 --threshold 0.5

Reads silver/melee/decklist_cards.parquet (usable Modern main decks), clusters
them, and writes config/archetypes/modern-<period>.draft.yml plus a cluster
report on stdout. Needs silver/cards.parquet (scripts/update_cards.py).
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from mtgmeta.archetypes import mine
from mtgmeta.archetypes.loader import load_main_decks
from mtgmeta.cards import scryfall
from mtgmeta.config import Settings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--period-start", required=True, help="YYYY-MM-DD, names the file")
    ap.add_argument("--format", default="Modern")
    ap.add_argument("--threshold", type=float, default=0.55,
                    help="cosine distance threshold for merging clusters (lower = more clusters)")
    ap.add_argument("--min-cluster", type=int, default=8)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()
    cards = scryfall.load(settings.data_root)
    decks = load_main_decks(settings.data_root, fmt=args.format, since=args.period_start)
    print(f"{len(decks)} usable {args.format} main decks since {args.period_start}")

    big, small = mine.mine(decks, cards, distance_threshold=args.threshold,
                           min_cluster=args.min_cluster)
    for d in big:
        sig = ", ".join(f"{c} ({p:.0%})" for c, p, _ in d.signature)
        print(f"{d.size:>5}  {d.name:<45} {sig}")
    print(f"\n{len(big)} clusters drafted, {len(small)} small clusters "
          f"({sum(d.size for d in small)} lists) left for review")

    default_out = Path("config/archetypes") / f"{args.format.lower()}-{args.period_start}.draft.yml"
    out = args.out or default_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(mine.draft_yaml(big, small, args.format, args.period_start, len(decks)),
                   encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
