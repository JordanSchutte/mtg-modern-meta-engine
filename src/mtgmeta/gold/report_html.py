"""The weekly brief as one self-contained HTML page ("a page to look at").

Rendered from the same ``BriefInputs`` the markdown brief is rendered from, so
the two can never disagree: there is no markdown-to-HTML step, the numbers are
read from the dataclass and the pure pieces in ``brief.py`` (movers, best
positioned, underplayed, swings, the model and backtest sentences) are reused.

Everything is inline — CSS in a ``<style>`` block, no scripts, no fonts, no
images, no external reference of any kind — because the file is opened from
disk (``file://``) after ``scripts/sync_local.py`` and will later be served
from a bucket behind access control, where nothing else may load. Written next
to the markdown as ``gold/melee/brief/report-<target_week>.html`` and
``report.html`` (always the latest) by ``brief.write``.

Layout: single column that reads on a phone, tables scroll horizontally when
they must, light and dark through ``prefers-color-scheme``. Share bars are
plain ``<div>`` widths on one hue and one scale per table, with the value
printed beside each bar in text (never only as a bar).
"""

from __future__ import annotations

import re
from datetime import timedelta
from html import escape

from mtgmeta.gold import brief as b
from mtgmeta.gold.brief import BriefInputs

TOP_FIELD = 12           # same cut as the markdown brief's forecast table
TOP_SWINGS = 10

# One hue for every bar (the dataviz palette's sequential blue, stepped per mode).
CSS = """
:root {
  color-scheme: light;
  --bg: #ffffff; --surface: #f4f4f4; --text: #191919; --muted: #666666;
  --line: #dddddd; --bar: #257de0; --track: #e6e6e6; --code: #f0f0f0;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --bg: #191919; --surface: #222222; --text: #efefef; --muted: #9c9c9c;
    --line: #3a3a3a; --bar: #338ced; --track: #2e2e2e; --code: #2a2a2a;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; padding: 16px; background: var(--bg); color: var(--text);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 56rem; margin: 0 auto; }
h1 { font-size: 1.6rem; line-height: 1.2; margin: 0 0 .25rem; }
h2 { font-size: 1.15rem; margin: 2rem 0 .5rem; padding-top: .75rem;
     border-top: 1px solid var(--line); }
p { margin: .5rem 0; }
.muted { color: var(--muted); }
small, .small { font-size: .875rem; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .9em;
       background: var(--code); padding: 0 .3em; border-radius: 3px; }
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: .75rem 0; }
table { border-collapse: collapse; width: 100%; font-size: .9375rem;
        font-variant-numeric: tabular-nums; }
th, td { padding: .35rem .6rem; text-align: left; vertical-align: middle; white-space: nowrap;
         border-bottom: 1px solid var(--line); }
th { font-weight: 600; color: var(--muted); font-size: .8125rem; text-transform: uppercase;
     letter-spacing: .02em; }
td.n, th.n { text-align: right; }
td.wrap { white-space: normal; min-width: 12rem; }
tr.low td { color: var(--muted); }
tr.low td.archetype { font-style: italic; }
.share { display: flex; align-items: center; gap: .5rem; justify-content: flex-end;
         min-width: 9rem; }
.share .v { min-width: 3.4em; text-align: right; }
.bar { flex: 0 0 6rem; height: 10px; background: var(--track); border-radius: 0 4px 4px 0;
       overflow: hidden; }
.bar .fill { height: 100%; background: var(--bar); border-radius: 0 4px 4px 0; }
tr.low .bar .fill { opacity: .45; }
ul { padding-left: 1.2rem; }
footer { margin-top: 2.5rem; padding-top: .75rem; border-top: 1px solid var(--line);
         color: var(--muted); font-size: .875rem; }
footer dl { display: grid; grid-template-columns: max-content 1fr; gap: .15rem 1rem;
            margin: .5rem 0 0; }
footer dt { font-weight: 600; }
footer dd { margin: 0; }
"""


# -- small helpers ---------------------------------------------------------------

def _t(text: str) -> str:
    """Escape a sentence shared with the markdown brief, then turn its `code` and
    *emphasis* spans into <code> and <em> (the only two inline marks those sentences use)."""
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", escape(text, quote=False))
    return re.sub(r"\*([^*]+)\*", r"<em>\1</em>", out)


def _pct(x: float | None, digits: int = 1) -> str:
    return b._pct(x, digits)


def _share_cell(x: float | None, scale: float) -> str:
    """A right-aligned value with a bar beside it; the bar's width is x / scale."""
    if x is None or scale <= 0:
        return f'<td class="n"><span class="share"><span class="v">{_pct(x)}</span></span></td>'
    width = max(0.0, min(100.0, x / scale * 100))
    return (f'<td class="n"><span class="share"><span class="bar" aria-hidden="true">'
            f'<span class="fill" style="display:block;width:{width:.1f}%"></span></span>'
            f'<span class="v">{_pct(x)}</span></span></td>')


def _table(header: list[tuple[str, str]], rows: list[tuple[list[str], str]],
           summary: str) -> str:
    """header: (label, class); rows: (cells already rendered as <td>, row class)."""
    head = "".join(f'<th class="{cls}" scope="col">{escape(lbl)}</th>' for lbl, cls in header)
    body = "".join(f'<tr class="{cls}">' + "".join(cells) + "</tr>" for cells, cls in rows)
    return (f'<div class="scroll"><table aria-label="{escape(summary)}">'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>")


def _td(text: str, cls: str = "") -> str:
    return f'<td class="{cls}">{escape(text)}</td>' if cls else f"<td>{escape(text)}</td>"


# -- sections ----------------------------------------------------------------------

def _header(inp: BriefInputs) -> list[str]:
    period = (f"Rules period <strong>{escape(inp.period)}</strong> "
              f"(<code>config/archetypes/modern-{escape(inp.period)}.yml</code>) — the matchups "
              "and positioning below count only matches from this period."
              if inp.period else
              "Rules period: none yet (no classified paper lists; the matrix and forecast have "
              "nothing to pool).")
    return [f"<h1>Modern meta brief — week of {inp.target_week.isoformat()}</h1>",
            f'<p class="muted small">Generated {inp.today.isoformat()} from the gold tables. '
            f'“This week” below is {inp.since.isoformat()} to {inp.today.isoformat()}.</p>',
            f'<p class="muted small">{period}</p>']


def _forecast_field(inp: BriefInputs, field: list[dict]) -> list[str]:
    saturday = inp.target_week + timedelta(days=5)
    out = [f"<h2>1. Forecast field for the weekend of {saturday.isoformat()}</h2>"]
    if not field:
        out.append("<p>No forecast has been built yet "
                   "(<code>gold/melee/forecast/field_forecast.parquet</code> is missing).</p>")
        return out
    out.append(f"<p>Model: <code>{escape(str(inp.model))}</code> — "
               f"{_t(b.describe_model(inp.model))}. {_t(b._backtest_sentence(inp))}</p>")
    shown = field[:TOP_FIELD]
    scale = max((v for f in shown for v in (f.get("share"), f.get("paper_recent"),
                                              f.get("mtgo_recent")) if v is not None), default=0)
    rows = [([_td(f["archetype"], "archetype"), _share_cell(f.get("share"), scale),
              _share_cell(f.get("paper_recent"), scale), _share_cell(f.get("mtgo_recent"), scale)],
             "") for f in shown]
    out.append(_table([("Archetype", ""), ("Forecast share", "n"), ("Paper, last 2 wks", "n"),
                       ("MTGO, last 2 wks", "n")], rows, "Forecast field"))
    out.append('<p class="muted small">Bars share one scale across the three columns, so a '
               'longer bar is a larger share wherever it sits.</p>')
    return out


def _movers(inp: BriefInputs, field: list[dict]) -> list[str]:
    out = ["<h2>2. Movers</h2>"]
    if not field:
        out.append("<p>Nothing to compare: no forecast.</p>")
    elif inp.previous_date is None:
        out.append("<p>No earlier brief to compare against yet; movers start next week.</p>")
    else:
        mv = b.movers(field, inp.previous_field)
        lead = (f"Compared with the brief for the week of {inp.previous_date.isoformat()}, "
                f"archetypes whose forecast share moved by more than {b.MOVER_POINTS:g} points")
        if not mv:
            out.append(f"<p>{lead}: none.</p>")
        else:
            out.append(f"<p>{lead}:</p>")
            rows = [([_td(m["archetype"], "archetype"), _td(_pct(m["now"]), "n"),
                      _td(_pct(m["before"]), "n"), _td(b._pts(m["delta"]), "n")], "") for m in mv]
            out.append(_table([("Archetype", ""), ("Now", "n"), ("Then", "n"), ("Change", "n")],
                              rows, "Movers"))
    return out


def _position(position: list[dict]) -> list[str]:
    out = ["<h2>3. Position against the forecast field</h2>"]
    if not position:
        out.append("<p>No position table yet.</p>")
        return out
    out.append(f"<p>{_t(b.position_explainer())} Here every deck is listed; rows under "
               f"{b.MIN_COVERAGE:.0%} coverage are greyed rather than left out, because their "
               "expected win rate rests on too little of the field to rank by.</p>")
    best = {p["archetype"] for p in b.best_positioned(position)}
    rows = []
    for p in sorted(position, key=lambda r: -r["expected_wr"]):
        low = (p.get("matchup_coverage") or 0) < b.MIN_COVERAGE
        name = p["archetype"] + (" ★" if p["archetype"] in best else "")
        rows.append(([_td(name, "archetype"), _td(_pct(p["expected_wr"]), "n"),
                      _td(_pct(p.get("matchup_coverage"), 0), "n"),
                      _td(_pct(p.get("forecast_share")), "n"),
                      _td(str(p.get("matches") or 0), "n")],
                     "low" if low else ""))
    out.append(_table([("Archetype", ""), ("Expected WR", "n"), ("Coverage", "n"),
                       ("Forecast share", "n"), ("Matches", "n")], rows, "Position"))
    out.append(f'<p class="muted small">★ = among the {b.TOP_POSITIONED} best-positioned decks '
               f"with at least {b.MIN_COVERAGE:.0%} coverage (the markdown brief's table).</p>")
    up = b.underplayed(position)
    if up:
        out.append(f"<p>Underplayed (forecast share under {b.UNDERPLAYED_SHARE:.0%} but expected "
                   f"win rate over {b.UNDERPLAYED_WR:.0%}): "
                   + "; ".join(f"<strong>{escape(p['archetype'])}</strong> "
                               f"({_pct(p['expected_wr'])} on {_pct(p['forecast_share'])} of the "
                               f"field, {p['matches']} matches)" for p in up) + ".</p>")
    else:
        out.append(f"<p>No underplayed deck (forecast share under {b.UNDERPLAYED_SHARE:.0%} "
                   f"with expected win rate over {b.UNDERPLAYED_WR:.0%}).</p>")
    return out


def _matrix_changes(inp: BriefInputs) -> list[str]:
    out = ["<h2>4. Matrix changes</h2>"]
    if not inp.matchups_now:
        out.append("<p>No matchup matrix available.</p>")
        return out
    out.append(f"<p>{inp.new_mtgo_matches} new MTGO top-8 bracket matches this week "
               "(they count toward win rates, never toward meta share).</p>")
    sw = b.swings(inp.matchups_now, inp.matchups_before)
    lead = (f"Matchups with at least {b.SWING_MIN_MATCHES} matches whose shrunk win rate moved "
            f"{b.SWING_POINTS:g} points or more once this week's matches were added")
    if not sw:
        out.append(f"<p>{lead}: none.</p>")
        return out
    out.append(f"<p>{lead}:</p>")
    rows = [([_td(f"{s['archetype']} vs {s['opponent']}", "archetype"), _td(_pct(s["now"]), "n"),
              _td(_pct(s["before"]), "n"), _td(b._pts(s["delta"]), "n"),
              _td(f"{s['matches']} (+{s['new_matches']})", "n")], "") for s in sw[:TOP_SWINGS]]
    out.append(_table([("Matchup", ""), ("Now", "n"), ("Before", "n"), ("Change", "n"),
                       ("Matches (new)", "n")], rows, "Matchup swings"))
    return out


def _data_notes(inp: BriefInputs) -> list[str]:
    out = ["<h2>5. Data notes</h2>"]
    delta = inp.paper_lists_this - inp.paper_lists_last
    out.append(f"<p>Paper: {inp.paper_lists_this} usable Melee lists from "
               f"{len(inp.paper_events_this)} event(s) this week, against "
               f"{inp.paper_lists_last} last week ({delta:+d}). "
               f"MTGO: {inp.mtgo_events_this} event(s) fetched with "
               f"{inp.mtgo_lists_this} lists.</p>")
    if not inp.paper_events_this:
        return out
    rows = []
    for e in inp.paper_events_this:
        modern, known = e.get("modern") or 0, e.get("known") or 0
        share = known / modern if modern else None
        flags = []
        if share is not None and share < b.LOW_KNOWN_SHARE:
            flags.append("lists mostly unpublished")
        if e.get("missing"):
            flags.append(f"{e['missing']} decklist page(s) missing from bronze")
        if e.get("empty_lists"):
            flags.append(f"{e['empty_lists']} empty list(s)")
        rows.append(([_td(str(e["start"])), _td(str(e["name"])[:48], "wrap"),
                      _td(str(e.get("players") or ""), "n"), _td(str(e.get("lists") or 0), "n"),
                      _td(_pct(share, 0), "n"), _td("; ".join(flags), "wrap")], ""))
    out.append(_table([("Date", ""), ("Event", ""), ("Players", "n"), ("Lists", "n"),
                       ("Both decks known", "n"), ("Flags", "")], rows, "Paper events this week"))
    out.append(f'<p class="muted small">“Both decks known” is the share of Modern Swiss matches '
               f"where both decklists are published; under {b.LOW_KNOWN_SHARE:.0%} is flagged.</p>")
    return out


def _footer(inp: BriefInputs) -> str:
    return ("<footer><dl>"
            f"<dt>Generated</dt><dd>{inp.today.isoformat()}</dd>"
            f"<dt>Target week</dt><dd>{inp.target_week.isoformat()}</dd>"
            f"<dt>Model</dt><dd><code>{escape(str(inp.model or 'none'))}</code></dd>"
            f"<dt>Rules period</dt><dd>{escape(inp.period or 'none')}</dd>"
            "</dl><p>Built from the gold tables by <code>scripts/build_brief.py</code>; the same "
            "numbers as <code>brief.md</code>.</p></footer>")


# -- the page ----------------------------------------------------------------------

def render(inputs: BriefInputs, position: list[dict], field: list[dict]) -> str:
    """One self-contained HTML document for ``inputs``.

    ``field`` and ``position`` are the forecast tables to show (normally
    ``inputs.field`` and ``inputs.position``); they are parameters so a caller
    can hand in a cut of either without touching the inputs.
    """
    body = (_header(inputs) + _forecast_field(inputs, field) + _movers(inputs, field)
            + _position(position) + _matrix_changes(inputs) + _data_notes(inputs))
    title = f"Modern meta brief — week of {inputs.target_week.isoformat()}"
    return ("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<meta name=\"color-scheme\" content=\"light dark\">\n"
            f"<title>{escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n<main>\n"
            + "\n".join(body) + "\n" + _footer(inputs) + "\n</main>\n</body>\n</html>\n")
