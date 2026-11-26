# Architecture and design decisions

## Principles

- **Each source does only the job it's reliable at.** Full-field paper events (every decklist and every Swiss pairing published) build the matchup matrix. MTGO only shows the top 32 lists and the top 8 bracket, so it drives the meta forecast and contributes its top-8 bracket matches to the matrix (tagged `source = 'mtgo'`, never counted toward meta share).
- **Nothing is deleted.** Bronze is append-only and versioned. Rows that can't be used for the matrix (an unknown opponent deck, a bye) stay in silver with flags.
- **Fail loudly.** Every source parser is covered by contract tests on fixtures trimmed from real pages; a page-shape change fails CI (and the watcher's build step) instead of writing bad rows. The one expected failure is handled as data, not as an error: an MTGO event page served before its lists land is logged in `attempts.json` and retried on later runs, and recorded as `empty` after six tries or once the event is a week old — reversibly, since bronze keeps every attempt.
- **Near-zero cost.** GitHub Actions free minutes are the compute, one Cloudflare R2 bucket is the storage, and there are no servers: silver and gold are rebuilt from bronze on the runner every run and pushed back as Parquet.

## How it runs

Everything is scheduled GitHub Actions; there is no orchestrator, queue or server, and dev sessions never write to R2. The workflow files under `.github/workflows/` are the authoritative description of each run, including the step order and the cron times; this section only says how they fit together.

- **`melee-watcher.yml`** — twice a day (around midnight and noon US Eastern). Pulls `bronze/` from R2 to the runner (`rclone sync`; the runner is disposable), discovers recently created Modern events, scans upcoming events onto the watchlist, fetches new and settled events plus a cheap 14-day refresh of recent ones, and pushes bronze back (`rclone copy`, never deleting in R2). It then rebuilds **silver and gold from scratch** for both sources — build silver, refresh the Scryfall card table, classify, coverage flags, imputations, matrix, forecast, brief — and pushes `silver/` and `gold/` to R2. The earlier briefs are pulled from R2 first because movers in the brief diff against the previous one.
- **`mtgo-watcher.yml`** — daily, US morning, bronze only: reads the month index pages, fetches every Modern event not yet in `bronze/mtgo`, pushes it back. The Melee watcher's noon run builds silver and gold over it.
- **`propose-rules.yml`** — weekly, Wednesday morning US Eastern: pulls silver from R2, re-mines the last 30 days and opens or refreshes one pull request of mechanical archetype-rule proposals on `archetype-proposals` (see Archetypes). Nothing changes until a person merges.
- **`ci.yml`** — lint and the offline contract/unit tests on every push to `main` and every pull request. `main` is protected by it.
- `melee-probe.yml`, `mtgo-probe.yml` and `mine-archetypes.yml` are manual (`workflow_dispatch`): the probes fetch one event or one month to prove a path before scheduling it, and the mining job writes a draft rules file to the `archetype-draft` branch for bootstrapping a new period.

**Reports.** Each pipeline step prints a plain-text report (`tee`), which the Melee watcher appends to the Actions step summary and commits to the `reports` branch as `reports/{coverage,archetypes,matchups,imputation,forecast}.txt` plus the markdown briefs — readable with `git show origin/reports:reports/forecast.txt`, no Actions UI needed. A health-check session reads the same files.

**Replay.** Because silver and gold are rebuilt from bronze every run, a changed archetype rule, imputation window or model candidate reclassifies and re-scores all of history on the next run; nothing has to be replayed or migrated.

## Melee discovery

Melee has no public tournament index, but its *Find Events* search is public. The search is a base64url-encoded JSON query in the URL:

```json
{"context": 2, "game": "MagicTheGathering", "search": null, "filters": ["Modern", "Past"]}
```

Results load over SignalR (`searchHub.search(token, requestId, context, page, game, search, location, filters, timeZone, followedOnly)`, answered through `receiveResponse`). Discovery opens the page once in Playwright to get the hub connection and anti-forgery token, then calls `search` itself page by page and reads the structured JSON (ID, Name, StartDate, Players, StatusDescription, Tags). Clicking the page's own "Show more" button was abandoned: the page's start-up searches superseded the clicks and a cookie modal blocked the mouse.

Two traps found on live runs:

- **Location sorting.** If Melee knows a location (browser geolocation or a saved `flattsware.location`), results are sorted by distance across all dates. Discovery denies geolocation and clears the saved value, and fails loudly if IDs aren't mostly descending.
- **Creation order.** Results are ordered by when the event was **created**, not when it was played, so paging stops after a long run of consecutive results dated before the cutoff rather than at the first old one.
- **Events created months ahead.** Regional Championships and Spotlights are created long before they run (the September Baltimore RC has a March-era id), so a past-events scan with a short lookback stops before reaching them, and enrollment isn't visible until close to the event. The watcher therefore also scans `Modern + Upcoming` each run, stopping at the first id already seen, and keeps every upcoming event on a watchlist in bronze. Once an event's start is two days past it is settled: round 1's match count gives a player estimate after two requests; too small is recorded and never revisited, otherwise the event is fetched in full.
- **Unlisted events.** Some organizer events (the Pro Tour and its PTQs) don't appear in Find Events at all; `config/extra_melee_events.json` lists their ids by hand.

## Melee event fetch

| Request | Returns |
| --- | --- |
| `GET /Tournament/View/{id}` | HTML; round IDs in `round-selector` buttons |
| `POST /Match/GetRoundMatches/{roundId}` | DataTables JSON: every match, both players, decklist IDs, game wins |
| `GET /Decklist/View/{guid}` | HTML; the 75 in `<pre id="decklist-text">` |

Results are derived from structured fields (`GameWins`, `GameDraws`, `ByeReason`), not the display string. Some competitors have an empty `Decklists` array; those rows are kept with the deck marked unknown and become candidates for same-75 imputation.

### Late changes

An event is fetched once its start is two days past, but decklists can be published and results corrected after that. For 14 days after an event the watcher re-pulls just its tournament page and round JSON (about 17 requests for an RC, once a day) and compares a signature of who played whom, with which lists, and the result. Unchanged: nothing is written except a line in `events/<id>/refreshes.json`. Changed: a new version is written beside the old one and newly published lists are fetched; a list that was empty before is kept as `<guid>.superseded-<version>.html`. The refresh log is the data for deciding whether the two-day wait and the refresh window are right.

## Imputation

Melee lets players reuse an exact 75 they've already submitted, so an unpublished list at a side event is most likely the same player's list from the neighbouring event. `silver/impute.py` (`scripts/build_imputations.py`) fills a player's missing list from their own nearest usable published Modern list at another event within 7 days (`DEFAULT_WINDOW_DAYS`; the earlier event wins ties), as its own table `silver/melee/imputations.parquet` — one row per filled match side with the list used, its source event and the day gap. Nothing in silver is overwritten; a list that exists but is empty counts as missing. `gold/matrix.py` joins the table in when loading matches (each match row gets `imputed_a` / `imputed_b`) and can be built without it (`build_gold.py --no-impute`). Matchup and summary rows carry `imputed_matches` so a cell's dependence on imputation is always visible; there is no per-row weight or down-weighting, a match is in or out.

The assumption is measured, not assumed: for players with published lists at two events inside the window, `validate` reports the share with the same archetype and the identical 75 per day-gap bucket, and that evidence sets the window. Measured in Nov 2026 on ~3,600 same-weekend pairs: 79% same archetype and 41% identical 75 within a day, 78% / 6% at 4–7 days, 56% same archetype at 8–14 days (on 9 pairs). So imputed lists feed the archetype-level matrix only; card-level views (`archetype_cards`, packages, flex slots) use published lists, which card tables already do because imputations are a separate table.

## Matchup matrix

Built in `gold/matrix.py` (`scripts/build_gold.py`) from silver on every run: one row per ordered archetype pair per format period, from paper matches that are Modern, Swiss, not a bye, with both decks known and classified, plus the MTGO top-8 bracket matches (below). Draws count half a win. `Unclassified` is left out of pairs but counts toward meta share. Each match keeps its round number in silver so late-round effects can be checked; within a period every match counts equally, because a matchup is mostly a property of the two decks — a decay for list drift would be added only if a backtest showed it helps.

**Mirrors** are kept as a row (they are real meta share), flagged `is_mirror`, with `win_rate_shrunk` fixed at 50% by definition. The current-field `position` in the archetype summary is computed over the *other* archetypes only (the mirror row is skipped); the forecast's `position_forecast` counts the forecast mirror share at 50% in the same weighted average (`gold/forecast.position`). Both read the same way: a mirror is a coin flip, and the difference is only whether the deck's own share is in the denominator.

Win rates are shrunk toward 50% by 10 pseudo-matches (`prior_n`; 3–1 reads as about 57%, not 75%, and the pull fades as matches accumulate) with a 90% Wilson interval on the raw rate, and game-level rates sit beside match-level. The archetype summary adds share of match slots, record vs the field, and **position**: the share-weighted shrunk win rate against every other archetype, i.e. how well a deck is placed against the field as it currently is. The forecast later swaps the current field for an expected one in that same formula.

## Archetypes

Rules live in `config/archetypes/<format>-<period-start>.yml` and name only a deck's **signature** cards — the engine, not the list — so flex slots can change with the meta without changing the classification. A list is scored by how many signature cards it holds; the rule with the most hits wins. `archetype` is the level the matrix uses; `variant` is the color/build label underneath (Esper vs Jeskai Blink). Unknown shells stay in one archetype row on purpose: an unsolved deck (Darklight Phoenix) gets a signature of its invariant cards and its sub-packages are *measured* inside the archetype, not written into rules, until a package stabilises and earns a variant.

**Periods.** Each rules file governs events from its date until the next file's date. A B&R announcement is `scripts/new_period.py --start <date> --ban ...`: the newest file is copied, banned cards leave every signature, the old file keeps classifying old events. The classifier picks the file per event date, so history never silently reclassifies.

**Bootstrapping and drift.** The first file was mined (`scripts/mine_archetypes.py`): IDF-weighted clustering of the period's lists, each cluster's high-presence/high-lift cards as a draft signature, then named by hand. Every Wednesday `propose-rules.yml` re-mines the last 30 days and opens one pull request with mechanical proposals only — `NEW:` rules for unclassified clusters, trims of signature cards that fell out of their own deck, additions of cards that became ubiquitous in it — plus variant candidates in the PR text. It never renames, merges or deletes. A person names and merges; nothing changes until then. The job replaces only its own commits (a branch with a human commit is left alone), closes its PR when a later week finds nothing, and the twice-daily report names any proposal PR still open.

**Failure modes.** If no rules file applies to any list, the classify step exits non-zero instead of writing an empty table (lists that predate every rules file are skipped with a warning); the files are in git, so an accidental delete is a checkout. Leftover lists (staples only, no shared engine) are `Unclassified` and are reported with their most common cards.

## MTGO (the forecast input)

MTGO publishes every Modern Challenge, Preliminary, Showcase and League 5-0
list on `mtgo.com/decklists`. Unlike Melee there is nothing to scrape in the
HTML: each event page embeds the whole event as JSON
(`window.MTGO.decklists.data`) — decklists with card names, standings with
rank and tiebreakers, win/loss records, the top-8 bracket and the entrant
count. Discovery is the monthly index (`/decklists/YYYY/MM`); the site name in
each link (`modern-challenge-32-2026-11-07<id>`) gives kind, date and id.

* `sources/mtgo/discovery.py` — parse the month index into event refs
* `sources/mtgo/parse.py` — the embedded JSON to lists; `Wear/Tear` → `Wear // Tear`
* `sources/mtgo/fetch.py` — `bronze/mtgo/events/<site_name>/{page.html,manifest.json}`,
  append-only; a page served before its lists land is logged in `attempts.json`
  and retried (recorded as `empty` after 6 tries or once the event is a week
  old, reversibly)
* `silver/mtgo.py` — `silver/mtgo/{events,decklists,decklist_cards,matches}`;
  the card table has the same shape as Melee's, so `classify_decklists.py` runs
  the same rules file over both sources (`silver/mtgo/decklist_archetypes`).
  `matches` is the published top-8 bracket of each challenge: both lists are on
  the same page, so every bracket match is a known archetype-vs-archetype
  result with no imputation. The Swiss rounds are not published.
* `gold/matrix.py` takes those bracket matches into the matchup matrix with
  `source = 'mtgo'` (paper rows are `source = 'paper'`); each pair and each
  archetype reports `mtgo_matches`. They never count toward meta share — a top
  8 is not the field — only toward win rates. One matrix, not two: there is not
  enough data to keep them apart, and the MTGO grinders are largely the same
  people doing well at the large paper events.
* `.github/workflows/mtgo-watcher.yml` — daily in the US morning, bronze only;
  the Melee watcher's noon run builds silver and gold for both sources

The MTGO field leads the paper field by a week or two (online players adopt
new decks first), which is what the forecast uses.

## Forecast

`gold/forecast.py` turns the weekly share series (paper = Melee lists; mtgo =
MTGO lists, counted plainly and weighted by finish) into next weekend's
expected paper field. Candidate models — last paper week, exponentially
weighted paper, recent MTGO (by lists or by results), paper/MTGO blends of
both kinds, and paper nudged by MTGO momentum — are backtested on every past
paper week with at least 150 lists (multinomial log-loss per list, plus total
variation distance), and the best one is used. The backtest table is in the
run summary and `reports/forecast.txt`, so the choice is visible.

Two beliefs about how paper players behave are encoded as candidates rather
than assumed, so the backtest can confirm or reject them:

* paper memory is long — people testing for a big event anchor on the last
  RC's top tables, not last week's lists: paper half-lives of 1, 2 and 4 weeks;
* MTGO signal is results-weighted, not volume-weighted — a random league 5-0
  is one data point, but a list that top-8s a Showcase and two more challenges
  in a fortnight is what people copy: the `results` series counts a challenge
  top 8 as 3, 9–16 as 2, other challenge lists 1, a league 5-0 as 0.5
  (`RESULT_WEIGHTS`), and `blend_r` models blend paper with it.

`position_forecast` weights the current period's shrunk matchup matrix by the
forecast field: each archetype's expected match win rate against the field it
will actually meet, with `matchup_coverage` saying how much of that field has
a known matchup. The MCP tools `forecast()` and `recommend()` read these.

### Forecast history

`field_forecast.parquet` is only ever the latest forecast, so `forecast.write`
also keeps a dated copy, `gold/melee/forecast/field_forecast/<target_week>.parquet`:
the same rows plus `generated_at` (UTC), one file per target week and
overwritten within it (a Monday run and a Thursday refresh of the same weekend
leave one file — the last forecast made before that weekend, which is the one
to score). That record is what out-of-sample scoring will compare against the
paper lists that actually land, and what the brief's movers can read instead
of the previous brief's marker.

Choices: the copies live under the gold prefix, so the watcher's existing
`rclone copy data/gold` pushes them and, because `copy` never deletes, they
accumulate in R2 even though every run rebuilds local gold from scratch; the
local sync script brings the whole directory down the same way. Nothing pulls
the history back into an Actions run yet — the step that scores or shows
movers from it will need a `rclone copy` of that prefix first, as the brief
step does for earlier briefs. The MCP table `forecast_history` is one DuckDB
view over the directory's files (`read_parquet(..., union_by_name=true)`, so a
file written before a column existed still reads), mounted only when at least
one file is present; it is for `run_sql`, there is no dedicated tool yet.

## Coverage flags

Events that were discovered but whose lists never published used to vanish
from every report: a zero-list event is just a short row in the per-event
coverage table. `silver/coverage.py` (`scripts/coverage_flags.py`) names them
and appends a section to `reports/coverage.txt`; the MCP `coverage()` tool
returns the same data. Three facts, each over silver only:

* **Unpublished Melee events** — start at least 3 days in the past and either
  no usable decklist or fewer usable lists than 10% of the players. Silver has
  no end date, so the grace window counts from the start; a two-day RC is
  therefore flagged a day or so earlier than "end + 3" would (the issue's
  wording), which only matters for events that do eventually publish. A list
  belongs to an event when one of that event's matches references it (not
  `first_tournament_id`), so a 75 reused from an earlier event still counts as
  published here.
* **Empty MTGO events** — manifest status `empty`: the page was fetched but
  never carried lists.
* **Paper dark** — the most recent Melee event with ≥ 150 classified lists
  (`MIN_CLASSIFIED`, pinned by a test to the forecast's `MIN_PAPER_LISTS`) and
  how many days ago it was. When this passes a couple of weeks the forecast is
  running on MTGO alone.

Classified counts exist only after `classify_decklists.py`, so the section
cannot come from `build_silver.py`; the watcher runs the script after
classification and appends its output to the coverage report. The flag list
is not windowed: an event that never published stays listed, which is the
point (the total is the number to watch). The thresholds are module constants,
not configuration.

## Weekly brief

`gold/brief.py` (`scripts/build_brief.py`, last step of the watcher's gold
build) writes a short markdown report for people rather than tables for
queries: the forecast field and the model in words with its backtest score
against the paper-only baseline; movers against last week's brief; the
best-positioned decks (coverage ≥ 90%) and underplayed ones (forecast share
< 3%, expected win rate > 52%); new MTGO bracket matches and matchup swings
(pairs with ≥ 30 matches whose shrunk win rate moved ≥ 3 points); and data
notes (paper lists this week vs last, MTGO events fetched, coverage flags).
Every statistical term is explained where it first appears.

Output is `gold/melee/brief/brief-<target_week>.md`, one per forecast target
week and overwritten within it (so a Monday brief and a Thursday refresh are
the same file), plus `brief.md` = latest. The watcher copies both to the
`reports` branch and the MCP tool `brief()` returns the latest. Two design
choices keep it self-contained:

* **Movers read the previous brief, not a history table.** Each brief ends
  with an HTML comment (`<!-- mtgmeta-brief {...} -->`) carrying its target
  week and forecast field as JSON; the next week's brief finds the newest
  dated brief before its own target week and diffs against that. The watcher
  rebuilds gold from scratch, so it pulls `gold/melee/brief/` back down from
  R2 first. The dated forecast history (#9, above) is the better source for
  movers once a run pulls it back down; the marker costs nothing to keep.
* **Matchup swings are recomputed, not stored.** The matrix is rebuilt from the
  same silver inputs without this week's matches (by event date) and compared
  to the full one. That is what last week's matrix was, up to any rules or
  imputation change since, and it needs no snapshot of gold.

## Local refresh for the MCP server

The MCP server reads Parquet under `DATA_ROOT`; a laptop gets those tables by
copying the published `silver/` and `gold/` prefixes down from R2, never by
running the pipeline. `scripts/sync_local.py` (`mtgmeta/local_sync.py`) is
that copy as one command: a preflight (rclone on `PATH`, a remote named by
`Settings.rclone_remote` known to rclone through `rclone.conf` or
`RCLONE_CONFIG_<REMOTE>_*` variables — `.env` works, since `Settings` loads it
before the subprocess starts), then `rclone copy <remote>:<bucket>/<layer>
DATA_ROOT/<layer>` per layer, then a diff of local file snapshots (path, size,
mtime) taken before and after, printed with the newest `target_week` in
`field_forecast.parquet`.

Choices: it is `copy`, not `sync`, so a table that disappears from R2 lingers
locally rather than being deleted on a laptop (consistent with "nothing is
deleted"; `rm -r data/silver data/gold` and re-run is the reset). The remote
name and bucket became `Settings` fields (`RCLONE_REMOTE`, `R2_BUCKET`,
defaults `r2` / `mtgmeta-bronze`) so the script and the workflows agree on one
place for them; the workflows still set `BUCKET` in their own `env:` block.
`--dry-run` prints the commands and the local state without calling rclone,
which is also how the tests exercise the script; the copy path is tested
against a stand-in `rclone` on `PATH`.
