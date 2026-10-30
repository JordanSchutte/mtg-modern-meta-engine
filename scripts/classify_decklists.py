#!/usr/bin/env python3
"""Classify every usable decklist in silver with an archetype rules file.

    python scripts/classify_decklists.py                       # newest rules file
    python scripts/classify_decklists.py config/archetypes/modern-2026-05-18.yml

Writes silver/melee/decklist_archetypes.parquet and prints the archetype
breakdown plus the most common cards among unclassified lists (the next
rules to write). Rerun after every edit of the rules file; it takes seconds.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from mtgmeta.archetypes import rules as rulesmod
from mtgmeta.archetypes.loader import load_deck_dates, load_main_decks
from mtgmeta.cards import scryfall
from mtgmeta.config import Settings
from mtgmeta.silver.melee import silver_root


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("rules", nargs="?", type=Path,
                    help="one rules file for every list (default: pick per event date)")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--top", type=int, default=25, help="cards to list for unclassified")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()
    folder = Path("config/archetypes")
    cards = scryfall.load(settings.data_root) if scryfall.cards_path(settings.data_root).exists() \
        else None
    decks = load_main_decks(settings.data_root, fmt="Modern")
    dates = load_deck_dates(settings.data_root, fmt="Modern")

    # Group lists by the rules file that governs their event date.
    groups: dict[Path, dict] = {}
    unmatched = 0
    for did, main in decks.items():
        path = args.rules or rulesmod.rules_for_date(folder, dates.get(did, "9999-12-31"))
        if path is None:
            unmatched += 1
            continue
        groups.setdefault(path, {})[did] = main
    if not groups:
        print(f"no rules file in {folder} applies to any of the {len(decks)} lists "
              "(drafts are skipped); the classifier refuses to write an empty table")
        return 1
    if unmatched:
        print(f"WARNING: {unmatched} lists predate every rules file and were skipped")

    results = []
    for path, group in sorted(groups.items()):
        ruleset = rulesmod.load_rules(path)
        results += rulesmod.classify_all(group, ruleset, cards)
        print(f"rules: {path} ({len(ruleset.rules)} rules) -> {len(group)} lists")
    print(rulesmod.summary(results))
    print()
    print(rulesmod.unclassified_report(decks, results, cards, top=args.top))

    if not args.no_write:
        out = silver_root(settings.data_root) / "decklist_archetypes.parquet"
        file_of = {did: p.name for p, g in groups.items() for did in g}
        rows = [{
            "decklist_id": r.decklist_id, "archetype": r.archetype, "variant": r.variant,
            "score": r.score, "signature_size": r.signature_size, "colors": r.colors,
            "runner_up": r.runner_up, "runner_up_score": r.runner_up_score,
            "rules_file": file_of[r.decklist_id],
        } for r in results]
        tmp = out.with_suffix(".parquet.tmp")
        pq.write_table(pa.Table.from_pylist(rows), tmp)
        tmp.replace(out)
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
