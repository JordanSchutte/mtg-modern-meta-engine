"""Weekly meta brief: a short written report from the gold tables.

Written to ``gold/melee/brief/brief-<target_week>.md`` (one per forecast target
week, overwritten within that week) and ``brief.md`` (always the latest). The
Melee watcher copies both to the ``reports`` branch and the MCP ``brief()``
tool returns the latest one.

Sections, in order: the forecast field for the coming weekend and the model
behind it; movers against the previous week's brief; the best-positioned decks
and the underplayed ones; what moved in the matchup matrix this week; and data
notes (paper lists, MTGO events, coverage flags).

Two things need "last week": movers compare against the previous brief, whose
forecast field is kept in a machine-readable comment at the end of its own
markdown, so a brief is self-contained and no extra table is required. Matchup
swings are computed by rebuilding the matrix without this week's matches and
comparing it to the full one, which is what last week's matrix was up to any
rule or imputation change since.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date, timedelta
from pathlib import Path

import duckdb

from mtgmeta.gold import matrix
from mtgmeta.gold.forecast import forecast_root, next_target_week
from mtgmeta.gold.matrix import gold_root
from mtgmeta.silver.melee import silver_root as melee_root
from mtgmeta.silver.mtgo import silver_root as mtgo_root

MOVER_POINTS = 1.5          # forecast share change (percentage points) that counts as a mover
MIN_COVERAGE = 0.9          # matchup coverage a deck needs to be called "best positioned"
TOP_POSITIONED = 5
UNDERPLAYED_SHARE = 0.03    # forecast share below this ...
UNDERPLAYED_WR = 0.52       # ... with expected win rate above this = underplayed
SWING_MIN_MATCHES = 30      # a pair needs this many matches for its swing to be reported
SWING_POINTS = 3.0          # shrunk win-rate change (percentage points) that counts as a swing
LOW_KNOWN_SHARE = 0.5       # an event with fewer known-known matches than this is flagged
WEEK_DAYS = 7
BASELINE_MODEL = "paper_last"
MARKER = "mtgmeta-brief"    # the machine-readable comment that carries the forecast field


def brief_root(data_root: Path) -> Path:
    return gold_root(data_root) / "brief"


# -- inputs ------------------------------------------------------------------

@dataclass
class BriefInputs:
    today: date
    target_week: date
    since: date                                   # start of "this week" (today - 7 days)
    model: str | None = None
    period: str | None = None                     # rules period the matrix/forecast are on
    backtest: dict[str, dict] = dc_field(default_factory=dict)   # model -> {weeks, log_loss, tvd}
    field: list[dict] = dc_field(default_factory=list)           # field_forecast rows, by share
    position: list[dict] = dc_field(default_factory=list)       # position_forecast rows
    previous_date: date | None = None
    previous_field: dict[str, float] = dc_field(default_factory=dict)
    matchups_now: list[dict] = dc_field(default_factory=list)   # latest period only
    matchups_before: list[dict] = dc_field(default_factory=list)
    new_mtgo_matches: int = 0
    paper_lists_this: int = 0
    paper_lists_last: int = 0
    paper_events_this: list[dict] = dc_field(default_factory=list)
    mtgo_events_this: int = 0
    mtgo_lists_this: int = 0


def _exists(*paths: Path) -> bool:
    return all(p.exists() for p in paths)


def _rows(con, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def load_forecast(data_root: Path, inputs: BriefInputs) -> None:
    root = forecast_root(data_root)
    con = duckdb.connect()
    if _exists(root / "field_forecast.parquet"):
        inputs.field = _rows(con, f"select * from '{(root / 'field_forecast.parquet').as_posix()}' "
                                  "order by share desc")
        if inputs.field:
            inputs.model = inputs.field[0].get("model")
    if _exists(root / "position_forecast.parquet"):
        inputs.position = _rows(con, "select * from "
                                     f"'{(root / 'position_forecast.parquet').as_posix()}' "
                                     "order by expected_wr desc")
    if _exists(root / "backtest.parquet"):
        for r in _rows(con, f"""
            select model, count(*) as weeks, avg(log_loss) as log_loss, avg(tvd) as tvd
            from '{(root / 'backtest.parquet').as_posix()}' group by model"""):
            inputs.backtest[r["model"]] = r
    con.close()


def load_matrix_change(data_root: Path, inputs: BriefInputs) -> None:
    """Matchups of the latest period now, and as they were without this week's matches."""
    mroot, oroot = melee_root(data_root), mtgo_root(data_root)
    if not _exists(mroot / "matches.parquet", mroot / "decklists.parquet",
                   mroot / "decklist_archetypes.parquet", mroot / "events.parquet"):
        return
    rows, archetypes = matrix.load_inputs(data_root)
    con = duckdb.connect()
    day: dict = {}
    for tid, start in con.execute(f"""
        select tournament_id, substr(start, 1, 10)
        from '{(mroot / 'events.parquet').as_posix()}'""").fetchall():
        day[tid] = start
    if _exists(oroot / "events.parquet"):
        for site, d in con.execute(f"""
            select site_name, day from '{(oroot / 'events.parquet').as_posix()}'""").fetchall():
            day[site] = d
    con.close()
    since = inputs.since.isoformat()
    before = [m for m in rows if (day.get(m["tournament_id"]) or "") < since]
    now_rows, _ = matrix.build(rows, archetypes)
    before_rows, _ = matrix.build(before, archetypes)
    period = max((r["period"] for r in now_rows), default=None)
    inputs.period = period
    inputs.matchups_now = [r for r in now_rows if r["period"] == period]
    inputs.matchups_before = [r for r in before_rows if r["period"] == period]
    inputs.new_mtgo_matches = sum(
        1 for m in rows if m["source"] == "mtgo" and (day.get(m["tournament_id"]) or "") >= since
        and all(archetypes.get(m[k]) for k in ("decklist_a", "decklist_b")))


def latest_rules_period(data_root: Path) -> str | None:
    """Newest rules period any usable paper list was classified under (``"2026-05-18"``)."""
    path = melee_root(data_root) / "decklist_archetypes.parquet"
    if not path.exists():
        return None
    con = duckdb.connect()
    (newest,) = con.execute(f"select max(rules_file) from '{path.as_posix()}'").fetchone()
    con.close()
    return matrix._period(newest) if newest else None


def load_data_notes(data_root: Path, inputs: BriefInputs) -> None:
    mroot, oroot = melee_root(data_root), mtgo_root(data_root)
    since, last = inputs.since.isoformat(), (inputs.since - timedelta(days=WEEK_DAYS)).isoformat()
    today = inputs.today.isoformat()
    con = duckdb.connect()
    if _exists(mroot / "decklists.parquet", mroot / "events.parquet"):
        counts = con.execute(f"""
            select sum(case when substr(e.start, 1, 10) >= ? and substr(e.start, 1, 10) < ?
                            then 1 else 0 end),
                   sum(case when substr(e.start, 1, 10) >= ? and substr(e.start, 1, 10) < ?
                            then 1 else 0 end)
            from '{(mroot / 'decklists.parquet').as_posix()}' d
            join '{(mroot / 'events.parquet').as_posix()}' e
              on e.tournament_id = d.first_tournament_id
            where d.usable""", [since, today, last, since]).fetchone()
        inputs.paper_lists_this = int(counts[0] or 0)
        inputs.paper_lists_last = int(counts[1] or 0)
        inputs.paper_events_this = _rows(con, f"""
            select substr(start, 1, 10) as start, tournament_id, name, players_estimate as players,
                   modern_swiss_matches as modern, modern_swiss_known as known,
                   decklist_ids as lists, unusable_decklists as empty_lists,
                   decklists_missing as missing
            from '{(mroot / 'events.parquet').as_posix()}'
            where substr(start, 1, 10) >= ? and substr(start, 1, 10) < ? order by start""",
            [since, today])
    if _exists(oroot / "events.parquet"):
        n, lists = con.execute(f"""
            select count(*), coalesce(sum(decklists), 0)
            from '{(oroot / 'events.parquet').as_posix()}'
            where status = 'complete' and day >= ? and day < ?""", [since, today]).fetchone()
        inputs.mtgo_events_this, inputs.mtgo_lists_this = int(n), int(lists)
    con.close()


def previous_brief(root: Path, before: date) -> tuple[date, dict[str, float]] | None:
    """The newest dated brief for a target week before ``before``, with its forecast field."""
    best: tuple[date, Path] | None = None
    for p in root.glob("brief-*.md") if root.exists() else []:
        m = re.fullmatch(r"brief-(\d{4}-\d{2}-\d{2})\.md", p.name)
        if not m:
            continue
        d = date.fromisoformat(m.group(1))
        if d < before and (best is None or d > best[0]):
            best = (d, p)
    if best is None:
        return None
    payload = read_marker(best[1].read_text(encoding="utf-8"))
    return best[0], {k: float(v) for k, v in (payload.get("field") or {}).items()}


def read_marker(text: str) -> dict:
    m = re.search(rf"<!-- {MARKER} (.*?) -->", text, re.S)
    return json.loads(m.group(1)) if m else {}


def load_inputs(data_root: Path, today: date | None = None) -> BriefInputs:
    today = today or date.today()
    inputs = BriefInputs(today=today, target_week=next_target_week(today),
                         since=today - timedelta(days=WEEK_DAYS))
    load_forecast(data_root, inputs)
    if inputs.field and inputs.field[0].get("target_week"):
        inputs.target_week = date.fromisoformat(inputs.field[0]["target_week"])
    prev = previous_brief(brief_root(data_root), inputs.target_week)
    if prev:
        inputs.previous_date, inputs.previous_field = prev
    load_matrix_change(data_root, inputs)
    if inputs.period is None:
        inputs.period = latest_rules_period(data_root)   # classified lists but no matrix yet
    load_data_notes(data_root, inputs)
    return inputs


# -- the pieces (pure, tested on their own) -----------------------------------

def movers(now: list[dict], before: dict[str, float], points: float = MOVER_POINTS) -> list[dict]:
    """Archetypes whose forecast share moved by more than ``points`` percentage points."""
    if not before:
        return []
    shares = {f["archetype"]: f["share"] for f in now}
    out = []
    for a in set(shares) | set(before):
        delta = shares.get(a, 0.0) - before.get(a, 0.0)
        if abs(delta) * 100 > points:
            out.append({"archetype": a, "now": shares.get(a, 0.0), "before": before.get(a, 0.0),
                        "delta": delta})
    return sorted(out, key=lambda r: -abs(r["delta"]))


def best_positioned(position: list[dict], min_coverage: float = MIN_COVERAGE,
                    top: int = TOP_POSITIONED) -> list[dict]:
    rows = [p for p in position if (p.get("matchup_coverage") or 0) >= min_coverage]
    return sorted(rows, key=lambda p: -p["expected_wr"])[:top]


def underplayed(position: list[dict], max_share: float = UNDERPLAYED_SHARE,
                min_wr: float = UNDERPLAYED_WR, min_coverage: float = MIN_COVERAGE) -> list[dict]:
    rows = [p for p in position if (p.get("forecast_share") or 0) < max_share
            and p["expected_wr"] > min_wr and (p.get("matchup_coverage") or 0) >= min_coverage]
    return sorted(rows, key=lambda p: -p["expected_wr"])


def swings(now: list[dict], before: list[dict], min_matches: int = SWING_MIN_MATCHES,
           points: float = SWING_POINTS) -> list[dict]:
    """Pairs (one row per unordered pair) whose shrunk win rate moved by ``points`` or more."""
    prev = {(r["archetype"], r["opponent"]): r for r in before}
    out = []
    for r in now:
        a, b = r["archetype"], r["opponent"]
        if r.get("is_mirror") or a > b or r["matches"] < min_matches:
            continue
        p = prev.get((a, b))
        if p is None:
            continue
        delta = r["win_rate_shrunk"] - p["win_rate_shrunk"]
        if abs(delta) * 100 >= points:
            out.append({"archetype": a, "opponent": b, "now": r["win_rate_shrunk"],
                        "before": p["win_rate_shrunk"], "delta": delta,
                        "matches": r["matches"], "new_matches": r["matches"] - p["matches"]})
    return sorted(out, key=lambda r: -abs(r["delta"]))


def describe_model(name: str | None) -> str:
    """A model's name in words, for readers who don't want to open forecast.py."""
    if not name:
        return "no model (no forecast yet)"
    fam, _, rest = name.partition("(")
    args = [x for x in rest.rstrip(")").split(",") if x]
    if fam == "paper_last":
        return "last week's paper field, unchanged"
    if fam == "paper_ewma":
        return f"paper lists weighted toward recent weeks (half-life {args[0]} week(s))"
    if fam == "mtgo_recent":
        return f"MTGO lists from the last {args[0]} week(s)"
    if fam == "mtgo_results":
        return f"MTGO lists from the last {args[0]} week(s), each weighted by its finish"
    if fam in ("blend", "blend_r") and len(args) == 3:
        a = float(args[0])
        online = ("recent MTGO lists" if fam == "blend"
                  else "recent MTGO lists weighted by finish")
        return (f"{a:.0%} paper (recent weeks weighted more, half-life {args[1]} week(s)) "
                f"+ {1 - a:.0%} {online} (last {args[2]} week(s))")
    if fam == "momentum":
        return "recent paper, nudged toward where MTGO is heading"
    return name


# -- rendering ---------------------------------------------------------------

def _pct(x: float | None, digits: int = 1) -> str:
    return "–" if x is None else f"{x * 100:.{digits}f}%"


def _pts(x: float) -> str:
    return f"{x * 100:+.1f} pts"


def _table(header: list[str], rows: list[list[str]], align: str | None = None) -> list[str]:
    align = align or "l" * len(header)
    sep = ["---:" if a == "r" else "---" for a in align]
    return ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |",
            *("| " + " | ".join(r) + " |" for r in rows)]


def render(inp: BriefInputs, top: int = 12) -> str:
    saturday = inp.target_week + timedelta(days=5)
    lines = [f"# Modern meta brief — week of {inp.target_week.isoformat()}", "",
             f"_Generated {inp.today.isoformat()} from the gold tables. \"This week\" below is "
             f"{inp.since.isoformat()} to {inp.today.isoformat()}._", "",
             _period_line(inp.period), ""]

    # 1. Forecast field
    lines += [f"## 1. Forecast field for the weekend of {saturday.isoformat()}", ""]
    if not inp.field:
        lines += ["No forecast has been built yet (`gold/melee/forecast/field_forecast.parquet` "
                  "is missing).", ""]
    else:
        lines.append(f"Model: `{inp.model}` — {describe_model(inp.model)}. "
                     + _backtest_sentence(inp))
        lines.append("")
        lines += _table(["Archetype", "Forecast share", "Paper, last 2 wks", "MTGO, last 2 wks"],
                        [[f["archetype"], _pct(f["share"]), _pct(f.get("paper_recent")),
                          _pct(f.get("mtgo_recent"))] for f in inp.field[:top]], "lrrr")
        lines.append("")

    # 2. Movers
    lines += ["## 2. Movers", ""]
    if not inp.field:
        lines += ["Nothing to compare: no forecast.", ""]
    elif inp.previous_date is None:
        lines += ["No earlier brief to compare against yet; movers start next week.", ""]
    else:
        mv = movers(inp.field, inp.previous_field)
        lead = (f"Compared with the brief for the week of {inp.previous_date.isoformat()}, "
                f"archetypes whose forecast share moved by more than {MOVER_POINTS:g} points")
        if not mv:
            lines += [f"{lead}: none.", ""]
        else:
            lines += [f"{lead}:", ""]
            lines += _table(["Archetype", "Now", "Then", "Change"],
                            [[m["archetype"], _pct(m["now"]), _pct(m["before"]),
                              _pts(m["delta"])] for m in mv], "lrrr")
            lines.append("")

    # 3. Best positioned
    lines += ["## 3. Best-positioned decks", ""]
    if not inp.position:
        lines += ["No position table yet.", ""]
    else:
        best = best_positioned(inp.position)
        lines.append(
            "Expected win rate = the deck's matchup win rates weighted by the forecast field "
            "(a mirror counts 50%). Matchup win rates are *shrunk*: pulled toward 50% by ten "
            "imaginary even matches, so a 3–0 record doesn't read as 100%. Coverage is how much "
            f"of the forecast field the deck has a known matchup against; decks below "
            f"{MIN_COVERAGE:.0%} coverage are left out.")
        lines.append("")
        if best:
            lines += _table(["Archetype", "Expected WR", "Forecast share", "Coverage", "Matches"],
                            [[p["archetype"], _pct(p["expected_wr"]), _pct(p["forecast_share"]),
                              _pct(p["matchup_coverage"], 0), str(p["matches"])]
                             for p in best], "lrrrr")
        else:
            lines.append(f"No deck reaches {MIN_COVERAGE:.0%} coverage.")
        lines.append("")
        up = underplayed(inp.position)
        if up:
            lines.append(f"Underplayed (forecast share under {UNDERPLAYED_SHARE:.0%} but expected "
                         f"win rate over {UNDERPLAYED_WR:.0%}): "
                         + "; ".join(f"**{p['archetype']}** ({_pct(p['expected_wr'])} on "
                                     f"{_pct(p['forecast_share'])} of the field, "
                                     f"{p['matches']} matches)" for p in up) + ".")
        else:
            lines.append(f"No underplayed deck (forecast share under {UNDERPLAYED_SHARE:.0%} "
                         f"with expected win rate over {UNDERPLAYED_WR:.0%}).")
        lines.append("")

    # 4. Matrix changes
    lines += ["## 4. Matrix changes", ""]
    if not inp.matchups_now:
        lines += ["No matchup matrix available.", ""]
    else:
        lines.append(f"{inp.new_mtgo_matches} new MTGO top-8 bracket matches this week "
                     "(they count toward win rates, never toward meta share).")
        lines.append("")
        sw = swings(inp.matchups_now, inp.matchups_before)
        lead = (f"Matchups with at least {SWING_MIN_MATCHES} matches whose shrunk win rate moved "
                f"{SWING_POINTS:g} points or more once this week's matches were added")
        if not sw:
            lines += [f"{lead}: none.", ""]
        else:
            lines += [f"{lead}:", ""]
            lines += _table(["Matchup", "Now", "Before", "Change", "Matches (new)"],
                            [[f"{s['archetype']} vs {s['opponent']}", _pct(s["now"]),
                              _pct(s["before"]), _pts(s["delta"]),
                              f"{s['matches']} (+{s['new_matches']})"] for s in sw[:10]],
                            "lrrrr")
            lines.append("")

    # 5. Data notes
    lines += ["## 5. Data notes", ""]
    delta = inp.paper_lists_this - inp.paper_lists_last
    lines.append(f"Paper: {inp.paper_lists_this} usable Melee lists from "
                 f"{len(inp.paper_events_this)} event(s) this week, against "
                 f"{inp.paper_lists_last} last week ({delta:+d}). "
                 f"MTGO: {inp.mtgo_events_this} event(s) fetched with {inp.mtgo_lists_this} lists.")
    lines.append("")
    if inp.paper_events_this:
        rows = []
        for e in inp.paper_events_this:
            modern, known = e.get("modern") or 0, e.get("known") or 0
            share = known / modern if modern else None
            flags = []
            if share is not None and share < LOW_KNOWN_SHARE:
                flags.append("lists mostly unpublished")
            if e.get("missing"):
                flags.append(f"{e['missing']} decklist page(s) missing from bronze")
            if e.get("empty_lists"):
                flags.append(f"{e['empty_lists']} empty list(s)")
            rows.append([e["start"], str(e["name"])[:48], str(e.get("players") or ""),
                         str(e.get("lists") or 0), _pct(share, 0), "; ".join(flags)])
        lines += _table(["Date", "Event", "Players", "Lists", "Both decks known", "Flags"],
                        rows, "llrrrl")
        lines.append("")
        lines.append(f"\"Both decks known\" is the share of Modern Swiss matches where both "
                     f"decklists are published; under {LOW_KNOWN_SHARE:.0%} is flagged.")
        lines.append("")

    payload = {"target_week": inp.target_week.isoformat(), "generated": inp.today.isoformat(),
               "model": inp.model, "period": inp.period,
               "field": {f["archetype"]: round(f["share"], 6) for f in inp.field}}
    lines.append(f"<!-- {MARKER} {json.dumps(payload, sort_keys=True)} -->")
    return "\n".join(lines) + "\n"


def _period_line(period: str | None) -> str:
    """Which rules file the matrix and the forecast's positioning are pooled under."""
    if not period:
        return ("_Rules period: none yet (no classified paper lists; the matrix and forecast "
                "have nothing to pool)._")
    return (f"_Rules period: **{period}** (`config/archetypes/modern-{period}.yml`) — the "
            f"matchups and positioning below count only matches from this period._")


def _backtest_sentence(inp: BriefInputs) -> str:
    bt = inp.backtest
    if not bt or inp.model not in bt:
        return "It was not chosen by backtest (not enough paper history yet)."
    chosen, base = bt[inp.model], bt.get(BASELINE_MODEL)
    s = (f"Chosen by backtest over {chosen['weeks']} past paper week(s): its log-loss was "
         f"{chosen['log_loss']:.3f}")
    if base and BASELINE_MODEL != inp.model:
        s += f" against {base['log_loss']:.3f} for the paper-only baseline (`{BASELINE_MODEL}`)"
    s += (". Log-loss measures how surprised the model was by the lists that actually showed up "
          "(lower is better; 0 would mean it called every list).")
    return s


# -- output ------------------------------------------------------------------

def write(text: str, target_week: date, data_root: Path) -> dict[str, Path]:
    out = brief_root(data_root)
    out.mkdir(parents=True, exist_ok=True)
    dated = out / f"brief-{target_week.isoformat()}.md"
    latest = out / "brief.md"
    for p in (dated, latest):
        tmp = p.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
    return {"dated": dated, "latest": latest}


def latest(data_root: Path) -> str | None:
    p = brief_root(data_root) / "brief.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def run(data_root: Path, today: date | None = None) -> tuple[str, BriefInputs]:
    inp = load_inputs(data_root, today)
    return render(inp), inp
