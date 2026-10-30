"""Archetype rules file and classifier.

File format (``config/archetypes/<format>-<period-start>.yml``)::

    format: Modern
    period_start: 2026-05-18
    rules:
      - archetype: Blink
        variant: Esper Blink
        signature: [Phelia, Exuberant Shepherd, Flickerwisp, Witch Enchanter, Solitude]
        min_signature: 3
        colors: WUB            # optional: the list's colors must equal this
        excludes: []           # optional: any of these present -> rule fails
        priority: 50           # optional tie-break, higher wins

Scoring a list against a rule:

* ``score`` = number of signature cards present in the main deck;
* the rule applies only if ``score >= min_signature``, no excluded card is
  present, and (if given) the list's colors equal ``colors``;
* among applying rules the highest score wins, then the highest share of the
  signature, then ``priority``.

A list that no rule applies to is ``Unclassified``. The report lists the most
common cards among unclassified lists; that is the to-do list for new rules.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mtgmeta.cards.scryfall import normalize

UNCLASSIFIED = "Unclassified"


def wubrg(colors: str) -> str:
    """Canonical colour string in WUBRG order (``"BUW"`` -> ``"WUB"``)."""
    present = set(colors.upper())
    return "".join(ch for ch in "WUBRG" if ch in present)


@dataclass(frozen=True)
class Rule:
    archetype: str
    variant: str
    signature: tuple[str, ...]          # normalized names
    signature_display: tuple[str, ...]  # as written in the file
    min_signature: int
    colors: str | None = None
    excludes: tuple[str, ...] = ()
    priority: int = 0


@dataclass(frozen=True)
class RuleSet:
    format: str
    period_start: str
    rules: tuple[Rule, ...]
    source: str = ""


@dataclass
class Classification:
    decklist_id: str
    archetype: str
    variant: str
    score: int
    signature_size: int
    rule_index: int | None
    colors: str = ""
    runner_up: str | None = None
    runner_up_score: int = 0
    extra: dict = field(default_factory=dict)


def rules_files(folder: Path) -> list[Path]:
    """Non-draft rules files, oldest period first (``<format>-<YYYY-MM-DD>.yml``)."""
    return sorted(p for p in folder.glob("*.yml") if ".draft" not in p.name
                  and ".proposed" not in p.name)


def rules_for_date(folder: Path, day: str, fmt: str = "modern") -> Path | None:
    """The newest rules file whose period starts on or before ``day``.

    A B&R announcement starts a new period: a new file with that date. Old
    files keep governing old events, so history classifies consistently.
    """
    best = None
    for p in rules_files(folder):
        stem = p.stem
        if not stem.lower().startswith(fmt.lower() + "-"):
            continue
        start = stem[len(fmt) + 1:]
        if start <= day:
            best = p
    return best


def load_rules(path: Path) -> RuleSet:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = []
    for i, r in enumerate(raw.get("rules") or []):
        sig = [str(c) for c in (r.get("signature") or [])]
        if not sig:
            raise ValueError(f"{path}: rule {i} ({r.get('variant')}) has no signature")
        variant = str(r.get("variant") or r["archetype"])
        rules.append(Rule(
            archetype=str(r["archetype"]),
            variant=variant,
            signature=tuple(normalize(c) for c in sig),
            signature_display=tuple(sig),
            min_signature=int(r.get("min_signature") or max(1, (len(sig) + 1) // 2)),
            colors=wubrg(str(r["colors"])) if r.get("colors") else None,
            excludes=tuple(normalize(str(c)) for c in (r.get("excludes") or [])),
            priority=int(r.get("priority") or 0),
        ))
    return RuleSet(format=str(raw.get("format") or "Modern"),
                   period_start=str(raw.get("period_start") or ""),
                   rules=tuple(rules), source=str(path))


def list_colors(main: Iterable[tuple[int, str]], cards: dict[str, dict],
                min_copies: int = 2) -> str:
    """Colors of the deck from its non-land main cards (WUBRG order).

    A color counts once it appears on at least ``min_copies`` total copies, so
    a single off-color splash card doesn't change the deck's colors.
    """
    counts: Counter[str] = Counter()
    for qty, name in main:
        card = cards.get(normalize(name))
        if card is None or card["is_land"]:
            continue
        for ch in card["colors"]:
            counts[ch] += qty
    return "".join(ch for ch in "WUBRG" if counts[ch] >= min_copies)


def classify(decklist_id: str, main: Iterable[tuple[int, str]], ruleset: RuleSet,
             cards: dict[str, dict] | None = None) -> Classification:
    present = {normalize(name) for qty, name in main if qty > 0}
    colors = list_colors(main, cards) if cards else ""
    scored: list[tuple[tuple[int, float, int], int, Rule]] = []
    for i, rule in enumerate(ruleset.rules):
        if any(x in present for x in rule.excludes):
            continue
        if rule.colors is not None and cards is not None and colors != rule.colors:
            continue
        score = sum(1 for c in rule.signature if c in present)
        if score < rule.min_signature:
            continue
        scored.append(((score, score / len(rule.signature), rule.priority), i, rule))
    if not scored:
        return Classification(decklist_id, UNCLASSIFIED, UNCLASSIFIED, 0, 0, None, colors)
    scored.sort(key=lambda t: t[0], reverse=True)
    (score, _, _), i, rule = scored[0]
    runner = scored[1][2].variant if len(scored) > 1 else None
    runner_score = scored[1][0][0] if len(scored) > 1 else 0
    return Classification(decklist_id, rule.archetype, rule.variant, score, len(rule.signature),
                          i, colors, runner, runner_score)


def classify_all(decklists: dict[str, list[tuple[int, str]]], ruleset: RuleSet,
                 cards: dict[str, dict] | None = None) -> list[Classification]:
    return [classify(did, main, ruleset, cards) for did, main in decklists.items()]


def unclassified_report(decklists: dict[str, list[tuple[int, str]]],
                        results: Iterable[Classification], cards: dict[str, dict] | None,
                        top: int = 25) -> str:
    """Most common non-land cards among unclassified lists: the next rules to write."""
    unclassified = [r.decklist_id for r in results if r.archetype == UNCLASSIFIED]
    if not unclassified:
        return "every list is classified"
    presence: Counter[str] = Counter()
    for did in unclassified:
        for _qty, name in decklists[did]:
            card = cards.get(normalize(name)) if cards else None
            if card is not None and card["is_land"]:
                continue
            presence[name] += 1
    n = len(unclassified)
    lines = [f"{n} unclassified lists; most common non-land cards among them:"]
    for name, k in presence.most_common(top):
        lines.append(f"  {k / n:>5.0%}  {name}")
    return "\n".join(lines)


def summary(results: Iterable[Classification]) -> str:
    results = list(results)
    n = len(results)
    by_arch: Counter[str] = Counter(r.archetype for r in results)
    by_var: Counter[str] = Counter(f"{r.archetype} / {r.variant}" for r in results
                                   if r.archetype != UNCLASSIFIED)
    lines = [f"{n} lists classified; {by_arch[UNCLASSIFIED]} unclassified "
             f"({by_arch[UNCLASSIFIED] / n:.1%})" if n else "no lists"]
    for arch, k in by_arch.most_common():
        if arch == UNCLASSIFIED:
            continue
        variants = [(v.split(" / ", 1)[1], c) for v, c in by_var.most_common()
                    if v.startswith(arch + " / ")]
        vtext = ", ".join(f"{v} {c}" for v, c in variants) if len(variants) > 1 else ""
        lines.append(f"  {k:>5}  {k / n:>5.1%}  {arch}" + (f"  [{vtext}]" if vtext else ""))
    return "\n".join(lines)
