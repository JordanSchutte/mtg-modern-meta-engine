"""Archetype rules, classifier and miner on synthetic lists."""

import random
from pathlib import Path

import yaml

from mtgmeta.archetypes import mine
from mtgmeta.archetypes.rules import UNCLASSIFIED, classify, classify_all, load_rules, summary
from mtgmeta.cards import scryfall

# A tiny card universe with colors and lands.
CARDS = {
    "Phelia, Exuberant Shepherd": "W", "Flickerwisp": "W", "Witch Enchanter": "W",
    "Solitude": "W", "Prismatic Ending": "W", "Thoughtseize": "B", "Psychic Frog": "UB",
    "Counterspell": "U", "Murktide Regent": "U", "Subtlety": "U", "Fatal Push": "B",
    "Ragavan, Nimble Pilferer": "R", "Lightning Bolt": "R", "Guide of Souls": "W",
    "Ocelot Pride": "W", "Ajani, Nacatl Pariah": "W", "Galvanic Discharge": "R",
    "Urza's Tower": "", "Karn, the Great Creator": "", "Eldrazi Temple": "",
    "Thought-Knot Seer": "", "Flooded Strand": "", "Island": "", "Plains": "", "Swamp": "",
}
LANDS = {"Urza's Tower", "Eldrazi Temple", "Flooded Strand", "Island", "Plains", "Swamp"}

ARCHETYPES = {
    "Esper Blink": (["Phelia, Exuberant Shepherd", "Flickerwisp", "Witch Enchanter", "Solitude",
                     "Prismatic Ending", "Thoughtseize"], ["Flooded Strand", "Island", "Plains"]),
    "Dimir Control": (["Psychic Frog", "Counterspell", "Murktide Regent", "Subtlety", "Fatal Push",
                       "Thoughtseize"], ["Island", "Swamp"]),
    "Boros Energy": (["Guide of Souls", "Ocelot Pride", "Ajani, Nacatl Pariah",
                      "Galvanic Discharge", "Lightning Bolt", "Ragavan, Nimble Pilferer"],
                     ["Plains"]),
    "Eldrazi Tron": (["Urza's Tower", "Karn, the Great Creator", "Eldrazi Temple",
                      "Thought-Knot Seer"], ["Urza's Tower"]),
}


def card_rows():
    return [{"oracle_id": n, "name": n, "front_name": n, "key": scryfall.normalize(n),
             "colors": c, "color_identity": c, "type_line": "Land" if n in LANDS else "Spell",
             "mana_value": 1.0, "is_land": n in LANDS, "legal_modern": True}
            for n, c in CARDS.items()]


def synthetic(seed=1, per=20):
    rng = random.Random(seed)
    decks = {}
    truth = {}
    for name, (spells, lands) in ARCHETYPES.items():
        for i in range(per):
            main = [(4, c) for c in spells if rng.random() > 0.1]  # some lists cut a card
            main += [(4, c) for c in lands]
            # flex: a random staple from another deck, sometimes
            if rng.random() < 0.3:
                main.append((2, rng.choice(["Prismatic Ending", "Fatal Push", "Lightning Bolt"])))
            did = f"{name[:3].lower()}-{i}"
            decks[did] = main
            truth[did] = name
    return decks, truth


def test_rules_load_and_classify(tmp_path):
    path = tmp_path / "modern-2026-05-18.yml"
    path.write_text(yaml.safe_dump({
        "format": "Modern", "period_start": "2026-05-18",
        "rules": [
            {"archetype": "Blink", "variant": "Esper Blink",
             "signature": ["Phelia, Exuberant Shepherd", "Flickerwisp", "Witch Enchanter",
                           "Solitude"], "min_signature": 3},
            {"archetype": "Dimir Control", "signature": ["Psychic Frog", "Counterspell",
                                                         "Murktide Regent"], "min_signature": 2},
        ],
    }))
    rs = load_rules(path)
    assert [r.variant for r in rs.rules] == ["Esper Blink", "Dimir Control"]

    # A Blink list that also runs Frog stays Blink: 4 signature hits beat 1.
    frog_blink = [(4, "Phelia, Exuberant Shepherd"), (3, "Flickerwisp"), (4, "Witch Enchanter"),
                  (4, "Solitude"), (3, "Psychic Frog"), (4, "Flooded Strand")]
    cards = {r["key"]: r for r in card_rows()}
    c = classify("x", frog_blink, rs, cards)
    assert (c.archetype, c.variant, c.score) == ("Blink", "Esper Blink", 4)
    assert c.colors == "WUB"
    assert c.runner_up is None  # Frog alone is below Dimir's min_signature

    c2 = classify("y", [(4, "Psychic Frog"), (4, "Counterspell"), (4, "Island")], rs, cards)
    assert c2.archetype == "Dimir Control"
    c3 = classify("z", [(4, "Urza's Tower"), (4, "Karn, the Great Creator")], rs, cards)
    assert c3.archetype == UNCLASSIFIED


def test_color_constraint_and_excludes(tmp_path):
    path = tmp_path / "r.yml"
    path.write_text(yaml.safe_dump({"rules": [
        {"archetype": "Blink", "variant": "Orzhov Blink", "colors": "WB",
         "signature": ["Phelia, Exuberant Shepherd", "Solitude"], "min_signature": 2,
         "excludes": ["Counterspell"]},
        {"archetype": "Blink", "variant": "Esper Blink", "colors": "WUB",
         "signature": ["Phelia, Exuberant Shepherd", "Solitude"], "min_signature": 2},
    ]}))
    rs = load_rules(path)
    cards = {r["key"]: r for r in card_rows()}
    esper = [(4, "Phelia, Exuberant Shepherd"), (4, "Solitude"), (4, "Thoughtseize"),
             (2, "Counterspell")]
    orzhov = [(4, "Phelia, Exuberant Shepherd"), (4, "Solitude"), (4, "Thoughtseize"),
              (1, "Subtlety")]  # a single blue card doesn't make it Esper
    assert classify("e", esper, rs, cards).variant == "Esper Blink"
    assert classify("o", orzhov, rs, cards).variant == "Orzhov Blink"


def test_miner_recovers_the_archetypes_and_draft_classifies_them(tmp_path):
    decks, truth = synthetic()
    cards = {r["key"]: r for r in card_rows()}
    big, small = mine.mine(decks, cards, min_cluster=5)
    assert len(big) == 4, [(d.size, d.name) for d in big + small]
    assert all(d.size == 20 for d in big)

    draft = tmp_path / "modern-2026-05-18.draft.yml"
    draft.write_text(mine.draft_yaml(big, small, "Modern", "2026-05-18", len(decks)))
    rs = load_rules(draft)
    results = classify_all(decks, rs, cards)
    assert all(r.archetype != UNCLASSIFIED for r in results)
    # Every synthetic archetype maps to exactly one cluster.
    mapping = {}
    for r in results:
        mapping.setdefault(truth[r.decklist_id], set()).add(r.archetype)
    assert all(len(v) == 1 for v in mapping.values()), mapping
    assert len({next(iter(v)) for v in mapping.values()}) == 4
    assert "unclassified" in summary(results)


def test_draft_yaml_is_loadable_with_notes_ignored(tmp_path):
    decks, _ = synthetic(per=8)
    cards = {r["key"]: r for r in card_rows()}
    big, small = mine.mine(decks, cards, min_cluster=5)
    text = mine.draft_yaml(big, small, "Modern", "2026-05-18", len(decks))
    assert "_mined" in text and "# DRAFT" in text
    rs = load_rules(Path(tmp_path / "d.yml").write_text(text) and tmp_path / "d.yml")
    assert len(rs.rules) == len(big)
