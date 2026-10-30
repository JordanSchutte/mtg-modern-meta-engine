"""Propose changes to a rules file from recent lists; a person approves them.

Runs weekly over the last ``window_days`` of lists. It detects, mechanically:

1. **New decks** — clusters of unclassified lists big enough to be a real deck.
   Proposed as a rule named ``NEW: <top cards>`` so it visibly needs a name.
2. **Trims** — a signature card that has fallen out of its own archetype's
   lists (presence below ``trim_below``). Proposed removal, never dropping a
   signature under ``min_signature`` (``min_signature`` is lowered instead).
3. **Additions** — a non-land card now in nearly all of a rule's lists
   (``add_above``) and rare elsewhere, not yet in the signature.
4. **Variant candidates** — a distinct sub-cluster inside a big archetype.
   Reported only; naming a variant is a human decision.

It never renames, merges or deletes archetypes. The result is a modified
rules file plus a Markdown summary; the workflow turns those into a pull
request. Rules with fewer than ``min_rule_lists`` matches in the window are
left alone — not enough data to say anything about them.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from mtgmeta.cards.scryfall import normalize

from . import mine
from .rules import UNCLASSIFIED, RuleSet, classify_all, load_rules


@dataclass
class Proposal:
    kind: str            # new | trim | add | variant
    variant: str         # rule it concerns (or the proposed name)
    detail: str
    cards: list[str] = field(default_factory=list)


@dataclass
class ProposalResult:
    proposals: list[Proposal]
    rules_text: str | None      # the modified YAML, or None when nothing changed
    summary_md: str
    before: Counter
    after: Counter

    @property
    def changes_file(self) -> bool:
        return self.rules_text is not None


def _display_names(decklists: dict[str, list[tuple[int, str]]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for main in decklists.values():
        for _, name in main:
            out.setdefault(normalize(name), name.split(" // ")[0])
    return out


def propose(decklists: dict[str, list[tuple[int, str]]], ruleset: RuleSet,
            rules_path: Path, cards: dict[str, dict] | None,
            min_new: int = 25, min_rule_lists: int = 15, trim_below: float = 0.5,
            add_above: float = 0.9, add_lift: float = 6.0, max_signature: int = 8,
            max_global: float = 0.12, max_adds: int = 2,
            variant_min: int = 40, today: str | None = None) -> ProposalResult:
    """See the module docstring. Additions must be engine cards, not staples: a
    card in more than ``max_global`` of all lists in the window is never
    proposed, whatever its lift, and at most ``max_adds`` are proposed per rule."""
    today = today or datetime.now(UTC).strftime("%Y-%m-%d")
    results = classify_all(decklists, ruleset, cards)
    before = Counter(r.archetype for r in results)
    by_variant: dict[str, list[str]] = defaultdict(list)
    for r in results:
        by_variant[r.variant].append(r.decklist_id)
    display = _display_names(decklists)
    proposals: list[Proposal] = []

    # Work on the raw YAML so comments-as-notes and ordering survive.
    raw = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    raw_rules = {r["variant"] if r.get("variant") else r["archetype"]: r for r in raw["rules"]}
    changed = False

    # 2 + 3: trims and additions per rule, from the lists it matched.
    presence_all: Counter[str] = Counter()
    sets_by_id: dict[str, set[str]] = {}
    for did, main in decklists.items():
        s = mine._present_cards(main, cards)
        sets_by_id[did] = s
        presence_all.update(s)
    n_all = len(decklists)

    for rule in ruleset.rules:
        ids = by_variant.get(rule.variant, [])
        if len(ids) < min_rule_lists:
            continue
        pres: Counter[str] = Counter()
        for did in ids:
            pres.update(sets_by_id[did])
        n = len(ids)
        raw_rule = raw_rules.get(rule.variant)
        if raw_rule is None:
            continue
        sig_keys = list(rule.signature)
        sig_display = list(raw_rule["signature"])

        # trims
        drop = [i for i, k in enumerate(sig_keys) if pres[k] / n < trim_below]
        if drop:
            kept = [c for i, c in enumerate(sig_display) if i not in drop]
            names = [sig_display[i] for i in drop]
            new_min = min(int(raw_rule.get("min_signature") or 1), max(1, len(kept)))
            proposals.append(Proposal("trim", rule.variant,
                                      f"present in under {trim_below:.0%} of its {n} lists: "
                                      + ", ".join(f"{sig_display[i]} ({pres[sig_keys[i]] / n:.0%})"
                                                  for i in drop), names))
            raw_rule["signature"] = kept
            raw_rule["min_signature"] = new_min
            raw_rule["note"] = (f"proposed {today}: trimmed {', '.join(names)}. "
                                + str(raw_rule.get("note") or "")).strip()
            changed = True
            sig_keys = [k for i, k in enumerate(sig_keys) if i not in drop]

        # additions
        if len(sig_keys) < max_signature:
            cands = []
            for k, c in pres.items():
                if k in sig_keys:
                    continue
                p_in = c / n
                p_global = presence_all[k] / max(1, n_all)
                p_out = (presence_all[k] - c) / max(1, n_all - n)
                lift = p_in / p_out if p_out > 0 else float("inf")
                if p_in >= add_above and lift >= add_lift and p_global <= max_global:
                    cands.append((p_in * min(lift, 50.0), k, p_in))
            cands.sort(reverse=True)
            room = min(max_adds, max_signature - len(sig_keys))
            for _, k, p_in in cands[:room]:
                name = display.get(k, k)
                proposals.append(Proposal(
                    "add", rule.variant,
                    f"{name} is in {p_in:.0%} of its {n} lists and rare elsewhere", [name]))
                raw_rule["signature"] = list(raw_rule["signature"]) + [name]
                raw_rule["note"] = (f"proposed {today}: added {name}. "
                                    + str(raw_rule.get("note") or "")).strip()
                changed = True

    # 1: new decks among the unclassified
    unclassified = {r.decklist_id: decklists[r.decklist_id]
                    for r in results if r.archetype == UNCLASSIFIED}
    if len(unclassified) >= min_new:
        big, _ = mine.mine(unclassified, cards, min_cluster=min_new,
                           background=(presence_all, n_all))
        for d in big:
            sig = [c for c, _, _ in d.signature]
            if not sig:
                continue
            name = f"NEW: {d.name}"
            proposals.append(Proposal("new", name,
                                      f"{d.size} unclassified lists share "
                                      + ", ".join(f"{c} ({p:.0%})" for c, p, _ in d.signature),
                                      sig))
            raw["rules"].append({
                "archetype": name, "variant": name, "signature": sig,
                "min_signature": max(1, min(3, (len(sig) + 1) // 2)),
                "note": f"proposed {today}: {d.size} unclassified lists in the window; "
                        "rename archetype/variant before merging",
            })
            changed = True

    # 4: variant candidates inside big archetypes (report only)
    by_arch: dict[str, dict[str, list[tuple[int, str]]]] = defaultdict(dict)
    for r in results:
        if r.archetype != UNCLASSIFIED:
            by_arch[r.archetype][r.decklist_id] = decklists[r.decklist_id]
    for arch, lists in by_arch.items():
        if len(lists) < 2 * variant_min:
            continue
        big, _ = mine.mine(lists, cards, distance_threshold=0.45, min_cluster=variant_min)
        if len(big) >= 2:
            known = {r.variant for r in ruleset.rules if r.archetype == arch}
            desc = "; ".join(f"{d.size} lists: " + ", ".join(c for c, _, _ in d.signature[:4])
                             for d in big[:4])
            proposals.append(Proposal("variant", arch,
                                      f"{len(big)} distinct builds (known variants: "
                                      f"{', '.join(sorted(known))}) — {desc}"))

    rules_text = None
    after = before
    if changed:
        head = _header(rules_path)
        rules_text = head + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100)
        tmp = rules_path.with_suffix(".proposed.yml")
        tmp.write_text(rules_text, encoding="utf-8")
        after = Counter(r.archetype for r in classify_all(decklists, load_rules(tmp), cards))
        tmp.unlink()

    return ProposalResult(proposals, rules_text, _summary(proposals, before, after, len(decklists),
                                                          rules_path, today), before, after)


def _header(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            lines.append(line)
        else:
            break
    return "\n".join(lines) + ("\n" if lines else "")


def _summary(proposals: list[Proposal], before: Counter, after: Counter, n: int,
             path: Path, today: str) -> str:
    lines = [f"## Archetype proposals — {today}", "",
             f"Window: {n} lists. Rules file: `{path.as_posix()}`.", ""]
    if not proposals:
        lines.append("No changes proposed: the rules still fit the recent lists.")
        return "\n".join(lines)
    kinds = {"new": "New decks (need a name)", "trim": "Signature trims",
             "add": "Signature additions", "variant": "Variant candidates (no file change)"}
    for kind, title in kinds.items():
        items = [p for p in proposals if p.kind == kind]
        if not items:
            continue
        lines.append(f"### {title}")
        for p in items:
            lines.append(f"- **{p.variant}** — {p.detail}")
        lines.append("")
    u_before, u_after = before.get(UNCLASSIFIED, 0), after.get(UNCLASSIFIED, 0)
    lines.append(f"Unclassified in window: {u_before} → {u_after} "
                 f"({u_before / n:.1%} → {u_after / n:.1%}) if merged as proposed.")
    lines.append("")
    lines.append("Rename `NEW:` archetypes before merging; edit anything else freely. "
                 "Nothing changes until this is merged.")
    return "\n".join(lines)
