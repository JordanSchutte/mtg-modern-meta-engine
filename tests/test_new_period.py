"""Format-period rollover: ``start_period`` on a small rules fixture and on the live file."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from mtgmeta.archetypes import period as p
from mtgmeta.archetypes.rules import load_rules, rules_files, rules_for_date

REPO = Path(__file__).resolve().parents[1]
LIVE = REPO / "config" / "archetypes" / "modern-2026-05-18.yml"
SCRIPT = REPO / "scripts" / "new_period.py"

FIXTURE = """\
# Archetype rules for Modern, period starting 2026-01-05.
# Second header line, kept verbatim.

format: Modern
period_start: '2026-01-05'
rules:
- archetype: Combo
  variant: Combo
  signature:
  - Card One
  - Card Two
  - Card Three
  - Card Four
  min_signature: 3
  note: the combo deck
- archetype: Control
  variant: Azorius Control
  signature:
  - Card Five
  - Card Six
  - Card Seven
  - Card Eight
  colors: WU
- archetype: Aggro
  variant: Aggro
  signature:
  - Card Nine
  - Card Ten
  - Card Eleven
  min_signature: 1
- archetype: Doomed
  variant: Doomed
  signature:
  - Card Twelve
  - Card Thirteen
  min_signature: 1
"""


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "modern-2026-01-05.yml").write_text(FIXTURE, encoding="utf-8")
    return tmp_path


def _rules(path: Path) -> dict[str, dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {r["variant"]: r for r in raw["rules"]}


def test_bans_leave_every_signature_and_lower_min_signature(folder):
    res = p.start_period(folder, "2026-03-02", ["Card Two", "card three // back face",
                                                 "Card Eleven"])
    assert res.target == folder / "modern-2026-03-02.yml" and res.target.exists()
    rules = _rules(res.target)

    combo = rules["Combo"]                      # 4 -> 2 cards: min_signature 3 -> 2
    assert combo["signature"] == ["Card One", "Card Four"]
    assert combo["min_signature"] == 2
    assert combo["note"].startswith("2026-03-02: banned Card Two, Card Three.")
    assert combo["note"].endswith("the combo deck")          # old note kept after the new one

    aggro = rules["Aggro"]                      # 3 -> 2 cards: min_signature 1 is fine as is
    assert aggro["signature"] == ["Card Nine", "Card Ten"] and aggro["min_signature"] == 1
    assert "REVIEW" not in aggro["note"]

    control = rules["Azorius Control"]          # untouched: no note, no min_signature added
    assert control["signature"] == ["Card Five", "Card Six", "Card Seven", "Card Eight"]
    assert "note" not in control and "min_signature" not in control and control["colors"] == "WU"

    assert res.touched == {"Combo": ["Card Two", "Card Three"], "Aggro": ["Card Eleven"]}
    assert res.flagged == [] and res.dropped == []


def test_rule_without_min_signature_gets_the_loader_default_when_it_must_shrink(folder):
    # Azorius Control has 4 cards and no min_signature, so load_rules assumes 2. Banning
    # three of them leaves one card: min_signature must be written as 1, not left implicit.
    res = p.start_period(folder, "2026-03-02", ["Card Five", "Card Six", "Card Seven"])
    control = _rules(res.target)["Azorius Control"]
    assert control["signature"] == ["Card Eight"] and control["min_signature"] == 1
    assert "REVIEW: 1 of 4 signature cards left" in control["note"]
    assert res.flagged == ["Azorius Control"]
    assert load_rules(res.target).rules[1].min_signature == 1


def test_rule_losing_most_of_its_signature_is_kept_and_flagged(folder):
    res = p.start_period(folder, "2026-03-02", ["Card One", "Card Two", "Card Three"])
    combo = _rules(res.target)["Combo"]
    assert combo["signature"] == ["Card Four"] and combo["min_signature"] == 1
    assert "REVIEW: 1 of 4 signature cards left" in combo["note"]
    assert res.flagged == ["Combo"]
    # 4 -> 2 is "half left", not "most lost": no flag. 3 -> 1 is.
    res2 = p.start_period(folder, "2026-03-09", ["Card One", "Card Two", "Card Nine",
                                                  "Card Ten"])
    assert res2.flagged == ["Aggro"]


def test_rule_losing_every_card_is_dropped_and_named(folder):
    res = p.start_period(folder, "2026-03-02", ["Card Twelve", "Card Thirteen"])
    rules = _rules(res.target)
    assert "Doomed" not in rules and len(rules) == 3
    assert res.dropped == ["Doomed"] and res.touched == {"Doomed": ["Card Twelve",
                                                                    "Card Thirteen"]}
    text = res.target.read_text(encoding="utf-8")
    assert "# Dropped (every signature card banned): Doomed." in text
    assert "dropped (every signature card banned): Doomed" in res.summary()
    load_rules(res.target)                      # still a valid rules file


def test_old_file_untouched_header_kept_and_dates_route_to_the_right_file(folder):
    old = folder / "modern-2026-01-05.yml"
    before = old.read_bytes()
    res = p.start_period(folder, "2026-03-02", ["Card Two"], unbans=["Card Zero"],
                         note="B&R 2026-03-02")
    assert old.read_bytes() == before

    text = res.target.read_text(encoding="utf-8")
    assert text.startswith("# Archetype rules for Modern, period starting 2026-01-05.\n"
                           "# Second header line, kept verbatim.\n"
                           "# Period from 2026-03-02: B&R 2026-03-02. Copied from "
                           "modern-2026-01-05.yml; bans removed from signatures: Card Two; "
                           "unbanned: Card Zero.\n\n")
    rs = load_rules(res.target)
    assert rs.period_start == "2026-03-02" and rs.format == "Modern"
    assert [r.variant for r in rs.rules] == ["Combo", "Azorius Control", "Aggro", "Doomed"]

    assert rules_files(folder) == [old, res.target]
    assert rules_for_date(folder, "2026-03-01") == old
    assert rules_for_date(folder, "2026-03-02") == res.target
    assert rules_for_date(folder, "2026-12-31") == res.target


def test_never_overwrites_and_needs_a_source(folder, tmp_path):
    p.start_period(folder, "2026-03-02", [])
    with pytest.raises(FileExistsError):
        p.start_period(folder, "2026-03-02", [])
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        p.start_period(empty, "2026-03-02", [])
    # drafts and proposals are never the source
    (empty / "modern-2026-01-05.draft.yml").write_text(FIXTURE, encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        p.start_period(empty, "2026-03-02", [])


def test_dry_run_on_the_live_rules_file(tmp_path):
    """The real file with a made-up ban, into a temp dir: it loads, the live file is untouched."""
    live_bytes = LIVE.read_bytes()
    shutil.copy(LIVE, tmp_path / LIVE.name)
    res = p.start_period(tmp_path, "2026-12-15", ["Basking Broodscale"], note="rehearsal")
    assert LIVE.read_bytes() == live_bytes
    old, new = load_rules(tmp_path / LIVE.name), load_rules(res.target)
    assert new.period_start == "2026-12-15"
    assert len(new.rules) == len(old.rules) - len(res.dropped)
    assert all("basking broodscale" not in r.signature for r in new.rules)
    assert "Broodscale Combo" in res.touched
    assert all(r.min_signature <= len(r.signature) for r in new.rules)
    assert rules_for_date(tmp_path, "2026-12-14") == tmp_path / LIVE.name
    assert rules_for_date(tmp_path, "2026-12-15") == res.target


def test_script_wraps_start_period(tmp_path):
    shutil.copy(LIVE, tmp_path / LIVE.name)
    cmd = [sys.executable, str(SCRIPT), "--start", "2026-12-15", "--ban", "Basking Broodscale",
           "--folder", str(tmp_path)]
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, check=False)
    assert out.returncode == 0, out.stderr
    assert "wrote" in out.stdout and "rules touched by bans: " in out.stdout
    assert (tmp_path / "modern-2026-12-15.yml").exists()
    again = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, check=False)
    assert again.returncode == 1 and "already exists" in again.stdout
