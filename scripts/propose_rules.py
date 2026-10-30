#!/usr/bin/env python3
"""Propose archetype rule changes from recent lists (weekly; a person approves).

    python scripts/propose_rules.py                      # last 30 days, newest rules file
    python scripts/propose_rules.py --window-days 45 --min-new 20

Writes the modified rules file in place only with --apply (the workflow does
this on a branch and opens a pull request); otherwise prints the proposals
and leaves the file untouched. --summary writes the Markdown summary to a file.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mtgmeta.archetypes import propose as proposemod
from mtgmeta.archetypes.loader import load_main_decks
from mtgmeta.archetypes.rules import load_rules, rules_files
from mtgmeta.cards import scryfall
from mtgmeta.config import Settings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("rules", nargs="?", type=Path, help="rules file (default: newest period)")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--min-new", type=int, default=25, help="lists for a NEW rule")
    ap.add_argument("--min-rule-lists", type=int, default=15)
    ap.add_argument("--apply", action="store_true", help="write the modified rules file")
    ap.add_argument("--summary", type=Path, help="write the Markdown summary here")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()
    path = args.rules or (rules_files(Path("config/archetypes")) or [None])[-1]
    if path is None:
        print("no rules file in config/archetypes")
        return 1
    ruleset = load_rules(path)
    cards = scryfall.load(settings.data_root)
    since = (datetime.now(UTC) - timedelta(days=args.window_days)).strftime("%Y-%m-%d")
    since = max(since, ruleset.period_start or since)
    decks = load_main_decks(settings.data_root, fmt=ruleset.format, since=since)
    print(f"{len(decks)} lists since {since}; rules {path} ({len(ruleset.rules)} rules)")

    result = proposemod.propose(decks, ruleset, path, cards, min_new=args.min_new,
                                min_rule_lists=args.min_rule_lists)
    print(result.summary_md)
    if args.summary:
        args.summary.write_text(result.summary_md, encoding="utf-8")
    if args.apply and result.changes_file:
        path.write_text(result.rules_text, encoding="utf-8")
        print(f"\nwrote proposed rules to {path}")
    # exit code tells the workflow whether there is anything to open a PR for
    return 0 if result.changes_file else 3


if __name__ == "__main__":
    raise SystemExit(main())
