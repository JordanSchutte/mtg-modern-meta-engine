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
from mtgmeta.archetypes.loader import load_main_decks
from mtgmeta.cards import scryfall
from mtgmeta.config import Settings
from mtgmeta.silver.melee import silver_root


def newest_rules_file(folder: Path) -> Path | None:
    files = sorted(p for p in folder.glob("*.yml") if ".draft" not in p.name)
    return files[-1] if files else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("rules", nargs="?", type=Path)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--top", type=int, default=25, help="cards to list for unclassified")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()
    path = args.rules or newest_rules_file(Path("config/archetypes"))
    if path is None:
        print("no rules file in config/archetypes (drafts are skipped); "
              "run mine_archetypes.py and save a non-draft file")
        return 0
    ruleset = rulesmod.load_rules(path)
    cards = scryfall.load(settings.data_root) if scryfall.cards_path(settings.data_root).exists() \
        else None
    decks = load_main_decks(settings.data_root, fmt=ruleset.format,
                            since=ruleset.period_start or None)
    results = rulesmod.classify_all(decks, ruleset, cards)

    print(f"rules: {path} ({len(ruleset.rules)} rules)")
    print(rulesmod.summary(results))
    print()
    print(rulesmod.unclassified_report(decks, results, cards, top=args.top))

    if not args.no_write:
        out = silver_root(settings.data_root) / "decklist_archetypes.parquet"
        rows = [{
            "decklist_id": r.decklist_id, "archetype": r.archetype, "variant": r.variant,
            "score": r.score, "signature_size": r.signature_size, "colors": r.colors,
            "runner_up": r.runner_up, "runner_up_score": r.runner_up_score,
            "rules_file": path.name,
        } for r in results]
        tmp = out.with_suffix(".parquet.tmp")
        pq.write_table(pa.Table.from_pylist(rows), tmp)
        tmp.replace(out)
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
