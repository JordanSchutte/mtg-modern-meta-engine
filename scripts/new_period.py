#!/usr/bin/env python3
"""Start a new format period (a B&R announcement or a format-warping set).

    python scripts/new_period.py --start 2026-12-15 --ban "Basking Broodscale" --ban "Card B"

Copies the newest rules file to config/archetypes/<format>-<start>.yml with
the banned cards removed from every signature (min_signature lowered where
needed) and a note on each rule touched. The old file stays and keeps
governing events before <start>; the matrix pools within a period.

A rule whose signature loses most of its cards is kept but flagged: the deck
may be dead, may survive with a replacement, or may become something else —
the next weekly proposal run will say which.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from mtgmeta.archetypes.rules import rules_files
from mtgmeta.cards.scryfall import normalize


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--start", required=True, help="YYYY-MM-DD the new period starts")
    ap.add_argument("--ban", action="append", default=[], help="banned card (repeatable)")
    ap.add_argument("--unban", action="append", default=[], help="unbanned card, noted only")
    ap.add_argument("--format", default="modern")
    ap.add_argument("--note", default="", help="what happened (e.g. 'B&R 2026-12-15')")
    args = ap.parse_args()

    folder = Path("config/archetypes")
    source = (rules_files(folder) or [None])[-1]
    if source is None:
        print("no rules file to copy from")
        return 1
    target = folder / f"{args.format}-{args.start}.yml"
    if target.exists():
        print(f"{target} already exists")
        return 1

    text = source.read_text(encoding="utf-8")
    head = "".join(line + "\n" for line in text.splitlines()
                   if line.startswith("#") or not line.strip())
    raw = yaml.safe_load(text)
    raw["period_start"] = args.start
    banned = {normalize(b) for b in args.ban}
    touched, hollowed = [], []
    for r in raw["rules"]:
        sig = list(r["signature"])
        kept = [c for c in sig if normalize(c) not in banned]
        if len(kept) != len(sig):
            removed = [c for c in sig if normalize(c) in banned]
            r["signature"] = kept
            r["min_signature"] = min(int(r.get("min_signature") or 1), max(1, len(kept)))
            r["note"] = (f"{args.start}: banned {', '.join(removed)}. "
                         + str(r.get("note") or "")).strip()
            touched.append(r["variant"])
            if len(kept) <= 1:
                hollowed.append(r["variant"])
    why = args.note or f"new period from {args.start}"
    extra = (f"# Period from {args.start}: {why}. Copied from {source.name}; "
             f"bans removed from signatures: {', '.join(args.ban) or 'none'}"
             + (f"; unbanned: {', '.join(args.unban)}" if args.unban else "") + ".\n")
    target.write_text(head + extra + "\n"
                      + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100),
                      encoding="utf-8")
    print(f"wrote {target} from {source.name}")
    print(f"rules touched by bans: {len(touched)}"
          + (f" ({', '.join(touched)})" if touched else ""))
    if hollowed:
        print("rules left with <= 1 signature card (deck may be dead or changed): "
              + ", ".join(hollowed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
