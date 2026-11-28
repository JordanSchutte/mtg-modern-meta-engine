"""The HTML report: rendered from the brief's own fixtures, self-contained, three tables."""

import re
from datetime import date
from html.parser import HTMLParser

from test_brief import TODAY, _synthetic

from mtgmeta import local_sync as ls
from mtgmeta.gold import brief as b
from mtgmeta.gold import report_html as rh
from mtgmeta.mcp import queries as q

VOID = {"meta", "br", "hr", "img", "input", "link"}


class _Tables(HTMLParser):
    """Round-trip the page: track open tags (unbalanced = fail), collect each table's
    labelled name, header cells, body row count and the row classes."""

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.tables: list[dict] = []
        self.unbalanced: list[tuple[str, str]] = []
        self.external: list[str] = []
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("src", "href"):
            if k in a:
                self.external.append(a[k])
        if tag == "script":
            self.scripts += 1
        if tag in VOID:
            return
        self.stack.append(tag)
        if tag == "table":
            self.tables.append({"name": a.get("aria-label"), "head": [], "rows": 0,
                                "classes": [], "in_body": False})
        elif tag == "tbody":
            self.tables[-1]["in_body"] = True
        elif tag == "tr" and self.tables and self.tables[-1]["in_body"]:
            self.tables[-1]["rows"] += 1
            self.tables[-1]["classes"].append(a.get("class", ""))
        elif tag == "th":
            self.tables[-1]["head"].append("")

    def handle_data(self, data):
        if self.stack and self.stack[-1] == "th":
            self.tables[-1]["head"][-1] += data

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.unbalanced.append((self.stack[-1] if self.stack else "", tag))
        else:
            self.stack.pop()


def _parse(html: str) -> _Tables:
    p = _Tables()
    p.feed(html)
    p.close()
    assert not p.unbalanced, p.unbalanced
    assert not p.stack, p.stack
    return p


def test_report_renders_the_fixture_with_three_tables_and_nothing_external(tmp_path):
    _synthetic(tmp_path, TODAY, a_share=0.6)
    text, inp = b.run(tmp_path, today=TODAY)
    # a low-coverage deck, to check it is greyed and not dropped
    inp.position.append({"archetype": "C", "expected_wr": 0.58, "matchup_coverage": 0.4,
                         "forecast_share": 0.0, "matches": 7})
    html = rh.render(inp, inp.position, inp.field)

    assert html.startswith("<!DOCTYPE html>")
    assert "<title>Modern meta brief — week of 2026-11-16" in html
    # the inline rule, as a test: nothing is fetched from anywhere
    assert not re.search(r"https?://", html)
    page = _parse(html)
    assert page.external == [] and page.scripts == 0

    tables = {t["name"]: t for t in page.tables}
    assert set(tables) == {"Forecast field", "Position", "Matchup swings", "Paper events this week"}
    assert tables["Forecast field"]["rows"] == 2                      # A and B
    assert tables["Forecast field"]["head"] == ["Archetype", "Forecast share",
                                                 "Paper, last 2 wks", "MTGO, last 2 wks"]
    assert tables["Position"]["rows"] == 3                            # A, B and the added C
    assert tables["Position"]["classes"] == ["low", "", ""]           # C first by WR, greyed
    assert tables["Matchup swings"]["rows"] == 1
    assert tables["Paper events this week"]["rows"] == 1

    # share bars: plain divs/spans with a width, one scale (the 60% bar fills the track)
    assert 'style="display:block;width:100.0%"' in html                # A's 60% forecast share
    assert 'style="display:block;width:83.3%"' in html                 # the 50% columns
    assert html.count('class="fill"') == 6
    # the same numbers as the markdown brief, and the same wording for the model
    assert "59.0%" in html and "+9.0 pts" in html and "51 (+11)" in html
    assert "<code>blend(0.5,1,1)</code> — 50% paper" in html
    assert "3.100 against 3.200 for the paper-only baseline (<code>paper_last</code>)" in html
    assert "<em>shrunk</em>" in html and "*shrunk*" not in html
    # the footer names the run
    for item in ("<dt>Generated</dt><dd>2026-11-16</dd>", "<dt>Target week</dt><dd>2026-11-16</dd>",
                 "<dt>Rules period</dt><dd>2026-05-18</dd>", "<code>blend(0.5,1,1)</code></dd>"):
        assert item in html
    # phone + dark mode plumbing
    assert 'name="viewport"' in html and "prefers-color-scheme: dark" in html


def test_position_rows_are_ordered_by_expected_wr_and_low_coverage_is_greyed_not_hidden():
    inp = b.BriefInputs(today=TODAY, target_week=date(2026, 11, 16), since=date(2026, 11, 9))
    position = [{"archetype": "Weak", "expected_wr": 0.45, "matchup_coverage": 0.95,
                 "forecast_share": 0.1, "matches": 100},
                {"archetype": "Thin", "expected_wr": 0.60, "matchup_coverage": 0.5,
                 "forecast_share": 0.01, "matches": 9},
                {"archetype": "Solid", "expected_wr": 0.55, "matchup_coverage": 1.0,
                 "forecast_share": 0.2, "matches": 300}]
    html = rh.render(inp, position, field=[])
    page = _parse(html)
    tables = {t["name"]: t for t in page.tables}
    assert list(tables) == ["Position"]                 # no forecast: only the position table
    assert tables["Position"]["classes"] == ["low", "", ""]          # Thin first (by WR), greyed
    assert html.index("Thin") < html.index("Solid ★") < html.index("Weak ★")
    assert "No forecast has been built yet" in html
    assert "Nothing to compare: no forecast." in html


def test_report_without_any_tables_still_renders(tmp_path):
    (tmp_path / "gold").mkdir()
    text, inp = b.run(tmp_path, today=TODAY)
    html = rh.render(inp, inp.position, inp.field)
    page = _parse(html)
    assert page.tables == []
    assert "No position table yet." in html and "No matchup matrix available." in html
    assert "<dt>Model</dt><dd><code>none</code></dd>" in html
    assert not re.search(r"https?://", html)


def test_write_puts_the_html_beside_the_markdown_and_the_tools_find_it(tmp_path):
    _synthetic(tmp_path, TODAY, a_share=0.6)
    text, inp = b.run(tmp_path, today=TODAY)
    assert q.brief(tmp_path / "nowhere").get("html_path") is None
    written = b.write(text, inp.target_week, tmp_path)                 # markdown only, as before
    assert set(written) == {"dated", "latest"}
    assert b.latest_report_path(tmp_path) is None
    assert q.brief(tmp_path)["html_path"] is None                      # key present, nothing yet

    html = rh.render(inp, inp.position, inp.field)
    written = b.write(text, inp.target_week, tmp_path, html=html)
    assert written["html_dated"].name == "report-2026-11-16.html"
    assert written["html_latest"].name == "report.html"
    assert written["html_latest"].read_text(encoding="utf-8") == html
    assert written["latest"].read_text(encoding="utf-8") == text
    assert sorted(p.name for p in b.brief_root(tmp_path).iterdir()) == [
        "brief-2026-11-16.md", "brief.md", "report-2026-11-16.html", "report.html"]
    assert b.latest_report_path(tmp_path) == written["html_latest"]
    assert q.brief(tmp_path)["html_path"] == str(written["html_latest"])

    # the sync summary tells Jordan what to open
    snap = ls.snapshot(tmp_path / "gold")
    out = ls.report({"gold": snap}, {"gold": snap}, "x", "x", report_path=tmp_path / ls.REPORT_FILE)
    assert f"report: open {written['html_latest'].resolve()} in a browser" in out
    out = ls.report({"gold": snap}, {"gold": snap}, "x", "x",
                    report_path=tmp_path / "elsewhere" / ls.REPORT_FILE)
    assert "report: none locally (gold/melee/brief/report.html missing)" in out
    assert "report:" not in ls.report({"gold": snap}, {"gold": snap}, "x", "x")  # as before
