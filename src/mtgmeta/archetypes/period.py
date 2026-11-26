"""Start a new format period: copy the newest rules file minus the banned cards.

A B&R announcement (or a format-warping set) starts a new period on its
effective date. ``start_period`` copies the newest rules file in a folder to
``<format>-<start>.yml`` with every banned card removed from every signature,
``min_signature`` lowered where a signature shrank below it, and a ``note`` on
each rule touched. The old file is never modified: it keeps classifying the
events before ``<start>`` (``rules.rules_for_date``), and the matrix and the
forecast pool within a period.

What happens to a rule depends on how much signature it keeps:

* most of it: edited in place, note records the removed cards;
* less than half, or a single card: kept, but the note says ``REVIEW`` — the
  deck may be dead, may survive with a replacement, or may become something
  else, and the next weekly proposal run will say which;
* nothing: the rule cannot stay (a rule without a signature does not load),
  so it is dropped from the new file and named in the file's header comment
  and in the result. Re-add it by hand if the deck survives in a new shape.

``scripts/new_period.py`` is the command-line wrapper; the runbook is
``docs/runbook-new-period.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mtgmeta.archetypes.rules import rules_files
from mtgmeta.cards.scryfall import normalize

REVIEW = "REVIEW"


@dataclass
class PeriodResult:
    source: Path
    target: Path
    start: str
    touched: dict[str, list[str]] = field(default_factory=dict)    # variant -> cards removed
    flagged: list[str] = field(default_factory=list)              # variants left thin
    dropped: list[str] = field(default_factory=list)              # variants with no card left

    def summary(self) -> str:
        lines = [f"wrote {self.target} from {self.source.name}",
                 f"rules touched by bans: {len(self.touched)}"
                 + (f" ({', '.join(self.touched)})" if self.touched else "")]
        if self.flagged:
            lines.append("rules to review (lost most of their signature; note says REVIEW): "
                         + ", ".join(self.flagged))
        if self.dropped:
            lines.append("rules dropped (every signature card banned): " + ", ".join(self.dropped))
        return "\n".join(lines)


def default_min_signature(signature_size: int) -> int:
    """What ``rules.load_rules`` assumes when a rule names no ``min_signature``."""
    return max(1, (signature_size + 1) // 2)


def header_comments(text: str) -> str:
    """The leading comment block of a rules file (kept verbatim in the copy)."""
    out = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            out.append(line)
        else:
            break
    while out and not out[-1].strip():
        out.pop()
    return "".join(line + "\n" for line in out)


def start_period(folder: Path, start: str, bans: list[str], unbans: list[str] | None = None,
                 fmt: str = "modern", note: str = "", source: Path | None = None) -> PeriodResult:
    """Write ``folder/<fmt>-<start>.yml`` from the newest rules file in ``folder``.

    Raises ``FileNotFoundError`` when there is nothing to copy from and
    ``FileExistsError`` when the target already exists (never overwritten).
    """
    unbans = unbans or []
    source = source or (rules_files(folder) or [None])[-1]
    if source is None:
        raise FileNotFoundError(f"no rules file to copy from in {folder}")
    target = folder / f"{fmt}-{start}.yml"
    if target.exists():
        raise FileExistsError(f"{target} already exists")

    text = source.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    raw["period_start"] = start
    banned = {normalize(b) for b in bans}
    result = PeriodResult(source=source, target=target, start=start)
    kept_rules = []
    for r in raw["rules"]:
        sig = [str(c) for c in r["signature"]]
        kept = [c for c in sig if normalize(c) not in banned]
        if len(kept) == len(sig):
            kept_rules.append(r)
            continue
        removed = [c for c in sig if normalize(c) in banned]
        variant = str(r.get("variant") or r["archetype"])
        result.touched[variant] = removed
        if not kept:
            result.dropped.append(variant)
            continue
        r["signature"] = kept
        effective = int(r.get("min_signature") or default_min_signature(len(sig)))
        if effective > len(kept):
            r["min_signature"] = len(kept)
        prefix = f"{start}: banned {', '.join(removed)}."
        if len(kept) <= 1 or 2 * len(kept) < len(sig):
            result.flagged.append(variant)
            prefix += f" {REVIEW}: {len(kept)} of {len(sig)} signature cards left."
        r["note"] = f"{prefix} {r.get('note') or ''}".strip()
        kept_rules.append(r)
    raw["rules"] = kept_rules

    why = note or f"new period from {start}"
    extra = (f"# Period from {start}: {why}. Copied from {source.name}; "
             f"bans removed from signatures: {', '.join(bans) or 'none'}"
             + (f"; unbanned: {', '.join(unbans)}" if unbans else "") + ".\n")
    if result.dropped:
        extra += ("# Dropped (every signature card banned): " + ", ".join(result.dropped)
                  + ". Re-add by hand if the deck survives.\n")
    target.write_text(header_comments(text) + extra + "\n"
                      + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100),
                      encoding="utf-8")
    return result
