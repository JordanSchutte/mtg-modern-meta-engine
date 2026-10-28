"""Draft archetype rules from the lists themselves.

The data knows what this period's archetypes are better than memory does, so
the first rules file is mined, then named and corrected by hand:

1. One row per usable main deck, one column per non-land card, 1 if present.
   Cards are weighted by inverse document frequency so format staples that
   appear in every colour's decks (Thoughtseize, Prismatic Ending) count for
   little and engine cards count for a lot.
2. Agglomerative clustering on cosine distance with a distance threshold, so
   the number of clusters comes from the data, not from a guess.
3. For each cluster: its *signature* = cards present in most of its lists and
   rare outside it (high lift). Small clusters are reported but left out of
   the draft; they are usually variants that belong under a bigger rule.

Output is a YAML draft with one rule per cluster named after its top
signature cards; the person renames, merges and splits from there.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
import yaml

from mtgmeta.cards.scryfall import normalize


@dataclass
class ClusterDraft:
    cluster_id: int
    size: int
    signature: list[tuple[str, float, float]]  # (card, presence in cluster, lift)
    sample_ids: list[str]

    @property
    def name(self) -> str:
        return " + ".join(c for c, _, _ in self.signature[:2]) or f"cluster_{self.cluster_id}"


def _present_cards(main, cards, drop_lands=True) -> set[str]:
    out = set()
    for qty, name in main:
        if qty <= 0:
            continue
        key = normalize(name)
        card = cards.get(key) if cards else None
        if drop_lands and card is not None and card["is_land"]:
            continue
        out.add(key)
    return out


def mine(decklists: dict[str, list[tuple[int, str]]], cards: dict[str, dict] | None,
         distance_threshold: float = 0.55, min_cluster: int = 8,
         signature_presence: float = 0.75, signature_lift: float = 2.5,
         max_signature: int = 6) -> tuple[list[ClusterDraft], list[ClusterDraft]]:
    """Return (clusters large enough for a rule, small clusters) sorted by size."""
    from sklearn.cluster import AgglomerativeClustering

    ids = list(decklists)
    sets = [_present_cards(decklists[d], cards) for d in ids]
    df: Counter[str] = Counter()
    for s in sets:
        df.update(s)
    vocab = sorted(df)
    col = {c: i for i, c in enumerate(vocab)}
    n = len(ids)
    idf = np.array([math.log((n + 1) / (df[c] + 1)) + 1.0 for c in vocab], dtype=np.float32)

    x = np.zeros((n, len(vocab)), dtype=np.float32)
    for r, s in enumerate(sets):
        for c in s:
            x[r, col[c]] = idf[col[c]]
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    x /= norms

    model = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                    distance_threshold=distance_threshold)
    labels = model.fit_predict(x)

    members: dict[int, list[int]] = defaultdict(list)
    for r, lab in enumerate(labels):
        members[int(lab)].append(r)

    display: dict[str, str] = {}
    for d in ids:
        for _, name in decklists[d]:
            display.setdefault(normalize(name), name.split(" // ")[0])

    drafts: list[ClusterDraft] = []
    for lab, rows in members.items():
        size = len(rows)
        presence: Counter[str] = Counter()
        for r in rows:
            presence.update(sets[r])
        sig = []
        for c, k in presence.items():
            p_in = k / size
            p_out = (df[c] - k) / max(1, n - size)
            lift = p_in / p_out if p_out > 0 else float("inf")
            if p_in >= signature_presence and lift >= signature_lift:
                sig.append((display.get(c, c), p_in, lift))
        sig.sort(key=lambda t: (t[1] * min(t[2], 50.0)), reverse=True)
        drafts.append(ClusterDraft(lab, size, sig[:max_signature], [ids[r] for r in rows[:3]]))

    drafts.sort(key=lambda d: d.size, reverse=True)
    big = [d for d in drafts if d.size >= min_cluster and d.signature]
    small = [d for d in drafts if d not in big]
    return big, small


def draft_yaml(big: list[ClusterDraft], small: list[ClusterDraft], fmt: str,
               period_start: str, n_lists: int) -> str:
    rules = []
    for d in big:
        rules.append({
            "archetype": d.name,
            "variant": d.name,
            "signature": [c for c, _, _ in d.signature],
            "min_signature": max(1, min(3, (len(d.signature) + 1) // 2)),
            "_mined": {
                "cluster": d.cluster_id, "lists": d.size,
                "signature_presence": {c: f"{p:.0%}" for c, p, _ in d.signature},
                "sample_decklists": d.sample_ids,
            },
        })
    header = (
        f"# DRAFT mined from {n_lists} lists. Rename archetype/variant, merge or split\n"
        f"# clusters, trim signatures to the cards that define the deck, then save as\n"
        f"# config/archetypes/{fmt.lower()}-{period_start}.yml (drop the .draft).\n"
        f"# Keys starting with _ are notes for you and are ignored by the classifier.\n"
        f"# {len(big)} clusters of >= min size; {len(small)} small clusters listed at the end.\n"
    )
    body = yaml.safe_dump({"format": fmt, "period_start": period_start, "rules": rules},
                          sort_keys=False, allow_unicode=True, width=100)
    tail = ["", "# Small clusters (not drafted; usually variants of a rule above):"]
    for d in small[:40]:
        sig = ", ".join(c for c, _, _ in d.signature[:4]) or "(no distinct signature)"
        tail.append(f"#   {d.size:>4} lists  {sig}")
    return header + body + "\n".join(tail) + "\n"
