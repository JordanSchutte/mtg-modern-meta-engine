# MTG Modern Meta Engine

A small data platform that forecasts the Magic: The Gathering **Modern** metagame and says which deck is best positioned against it. Two polite scrapers feed an append-only bronze layer in Cloudflare R2; DuckDB builds silver Parquet tables; gold holds a matchup matrix, archetype shares and a backtested field forecast; an MCP server exposes all of it to Claude Desktop so a matchup or coverage question is a sentence. GitHub Actions on a cron schedule is the whole orchestrator.

> **Status:** running unattended. 32 large paper events (36k Swiss matches, 89% with both decks known) and 470 MTGO events (18k lists) since the May 2026 format period, refreshed twice a day; the forecast is published every run. See [docs/architecture.md](docs/architecture.md) for the design record.

## What it answers

| Question | Where the answer comes from |
| --- | --- |
| **What will the field look like next weekend?** | `field_forecast`: each archetype's expected share of the next paper field, from the model that won the backtest (below). |
| **How does deck A do against deck B?** | The matchup matrix: shrunk match win rate with a 90% interval and sample size, one row per ordered pair per format period. |
| **What should I play?** | `position_forecast`: each archetype's expected win rate against the forecast field, i.e. the matrix weighted by the forecast, plus underplayed decks that are well positioned. |
| **How much data is behind that?** | Coverage per event, imputation evidence and failed fetches are all tables, not log lines. |

The MCP server (`mtgmeta-mcp`) exposes these as tools: `forecast`, `recommend`, `meta_share`, `matchup`, `matrix`, `archetype_cards` (core / package / flex-slot breakdown of a deck), `card_usage`, `archetypes`, `coverage`, `failed_events` and a read-only `run_sql` over every silver and gold table.

## Architecture

```
 Melee.gg  ──PoliteClient──►  bronze/melee  ┐
 (paper: every list and every Swiss pairing) │   Cloudflare R2, append-only:
                                              ├─  raw pages + JSON, versioned,
 mtgo.com  ──PoliteClient──►  bronze/mtgo   ┘   never rewritten or deleted
 (online: Challenge / League lists, top-8 brackets)
                     │
                     ▼  DuckDB (scripts/build_silver.py, classify_decklists.py, build_imputations.py)
              silver/  Parquet: events, matches, decklists, decklist_cards, players,
                       decklist_archetypes (rules-based), imputations, cards (Scryfall)
                     │
                     ▼  DuckDB (scripts/build_gold.py, build_forecast.py)
              gold/    matchups + archetype_summary (one matrix, `source` = paper | mtgo)
                       field_forecast, position_forecast, forecast_backtest
                     │
                     ▼
              mtgmeta-mcp  ──stdio──►  Claude Desktop / Claude Code
```

Orchestration is four GitHub Actions workflows on cron: `mtgo-watcher.yml` (daily, bronze only), `melee-watcher.yml` (twice a day: discover, fetch, then rebuild silver and gold for both sources and publish the text reports to the `reports` branch), `propose-rules.yml` (Wednesdays: re-mines the last 30 days and opens one pull request of mechanical archetype-rule proposals for a person to name and merge) and `ci.yml` (lint + contract tests on fixtures, offline). The pipeline runs only there; dev sessions never write to R2.

Design choices worth knowing, all recorded in [docs/architecture.md](docs/architecture.md):

- **Each source does the job it is reliable at.** Paper events publish the whole field and every pairing, so they build the matchup matrix and define meta share. MTGO publishes only the top 32 lists and the top-8 bracket, so it drives the forecast (online players adopt decks a week or two before paper does) and adds bracket matches to the matrix at `source = 'mtgo'`; MTGO lists never count toward meta share.
- **Archetypes are rules, not clusters.** A human-editable YAML file per format period names each deck's signature cards; the classifier picks the file by event date, so history never silently reclassifies. Clustering is only used to *draft* proposals.
- **Nothing is deleted.** Bronze is append-only with retry history; silver and gold are rebuilt from it on every run. Missing paper decklists are filled from the same player's list at a nearby event (`imputations`), kept as a separate table with measured evidence (79% same archetype, 41% identical 75 on same-weekend pairs), and every matchup row says how many of its matches used one.
- **Fail loudly.** Every parser sits behind contract tests on fixtures trimmed from real pages; a page-shape change stops the run instead of writing bad rows.

## How the forecast is chosen

Each run backtests a set of candidate models on every past paper week with at least 150 lists, using only data from before that week, and uses whichever scored best. Nothing is hand-picked. Two metrics, both lower-is-better:

- **log-loss** — how surprised the model was by the lists that actually showed up (per list; it punishes giving a deck a tiny share and then seeing a lot of it).
- **TVD** (total variation distance) — the share of the field the model put in the wrong archetypes (0.147 means about 15% of the forecast mass was misplaced).

Latest backtest (`reports/forecast.txt` on the `reports` branch, 2026-11-18, 6 paper weeks), best of each model family:

| model | what it is | log-loss | TVD |
| --- | --- | ---: | ---: |
| `blend(0.5,1,1)` ← chosen | half paper (1-week half-life), half recent MTGO lists | 3.1490 | 0.147 |
| `blend_r(0.5,1,1)` | same, but MTGO lists weighted by finish (top 8 = 3, 9–16 = 2, other = 1, league 5-0 = 0.5) | 3.1493 | 0.150 |
| `mtgo_recent(2)` | MTGO lists from the last 2 weeks | 3.1675 | 0.164 |
| `mtgo_results(2)` | the same, results-weighted | 3.1756 | 0.177 |
| `paper_last` | last paper week (the naive baseline) | 3.1863 | 0.179 |
| `momentum(1,0.5)` | paper nudged by MTGO's recent trend | 3.1913 | 0.179 |
| `paper_ewma(1)` | exponentially weighted paper | 3.1924 | 0.184 |

Beliefs about how players behave ("paper memory is long", "people copy results, not volume") are encoded as candidate models so the backtest can confirm or reject them rather than being assumed. The forecast field is then run through the current period's matchup matrix to get each archetype's expected win rate against the field it will actually meet.

## Data sources and politeness

| Source | Used for | How |
| --- | --- | --- |
| [Melee.gg](https://melee.gg) | Matchup matrix and meta share: completed Modern events with 100+ players, every decklist and every Swiss pairing | Public tournament pages and the JSON a browser loads for them; event discovery through the public *Find Events* search (Playwright) plus a watchlist for events created months ahead |
| [mtgo.com/decklists](https://www.mtgo.com/decklists) | Forecast input and top-8 bracket matches: Challenges, Preliminaries, Showcases, League 5-0s | Monthly index pages; each event page embeds the whole event as JSON |
| Scryfall bulk data | Card dimension (colors, types, lands) | Official bulk files, refreshed weekly |

Both sources go through one `PoliteClient`: one request at a time, a fixed interval of 2.5 s (2 s floor enforced in code), exponential backoff on HTTP 429 and a hard stop after repeated 429s. Each event is fetched once; late decklist uploads are picked up by a light daily re-check for 14 days that re-fetches only what changed. A contact address travels in a `From` header on every request. There is no parallel fetching and no dependency on third-party decklist caches: discovery is the project's own.

## Run it locally

The MCP server reads local Parquet only, so a laptop needs the tables, not the pipeline.

```bash
pip install -e ".[dev,mcp]"

# Pull the current silver and gold tables (rclone + read access to the bucket needed)
python scripts/sync_local.py            # rclone copy r2:mtgmeta-bronze/{silver,gold} -> data/
python scripts/sync_local.py --dry-run  # show the commands and the local state, copy nothing

mtgmeta-mcp          # stdio MCP server; DATA_ROOT defaults to ./data
```

`sync_local.py` is the two `rclone copy` commands with a check in front and a
summary after: it stops with a one-line hint if `rclone` is missing or no `r2`
remote is configured (either `rclone config`, or `RCLONE_CONFIG_R2_*` variables
in `.env` — see `.env.example`), then prints per-layer file counts, which files
were added or updated, the forecast's target week, and the local path of
`report.html` — the weekly brief as a self-contained page; open it in a browser.
It only ever copies *from* R2; nothing local is deleted and nothing is written
to the bucket.

For Claude Desktop, add it to `claude_desktop_config.json`:

```json
{ "mcpServers": { "mtg-modern-meta": { "command": "mtgmeta-mcp", "env": { "DATA_ROOT": "/path/to/repo/data" } } } }
```

(Claude Desktop does not inherit your shell's `PATH`, so use the full path to `mtgmeta-mcp` from `which mtgmeta-mcp` if the server does not start.) For Claude Code, `.mcp.json` in the repo registers the same server; open the repo and the `mtg-modern-meta` tools appear.

To rebuild tables yourself from a bronze copy under `data/bronze`:

```bash
python scripts/build_silver.py             # bronze -> silver Parquet, prints per-event coverage
python scripts/classify_decklists.py       # rules file chosen per event date -> decklist_archetypes
python scripts/coverage_flags.py           # events whose lists never published; days since last big paper event
python scripts/build_imputations.py        # same-75 imputation with evidence
python scripts/build_gold.py               # matchup matrix + archetype summary
python scripts/build_forecast.py --no-write  # backtest table + forecast, printed
ruff check . && python -m pytest -q        # offline; tests run on fixtures
```

## Repository layout

```
src/mtgmeta/
  sources/melee/        discovery (Find Events via Playwright), watchlist, PoliteClient, parsers, fetch
  sources/mtgo/         monthly index discovery, embedded-JSON parser, fetch
  silver/               bronze -> Parquet for each source; same-75 imputation
  archetypes/           rules file format, signature classifier, mining and weekly proposals
  gold/                 matchup matrix + archetype summary; forecast models, backtest, position
  cards/                Scryfall bulk data -> cards.parquet
  mcp/                  questions as plain functions (queries.py) and as MCP tools (server.py)
config/archetypes/      one YAML rules file per format period, edited by hand
scripts/                CLI entry points, one per pipeline step
tests/                  contract and unit tests; fixtures are trimmed real pages
.github/workflows/      the orchestrator
docs/architecture.md    design record, kept current with the code
```

## Roadmap

The backlog is the open issues labeled [`ready`](https://github.com/JordanSchutte/mtg-modern-meta-engine/issues?q=is%3Aissue+is%3Aopen+label%3Aready); at the time of writing:

- [#6](https://github.com/JordanSchutte/mtg-modern-meta-engine/issues/6) Weekly meta brief generated from gold
- [#7](https://github.com/JordanSchutte/mtg-modern-meta-engine/issues/7) Coverage report: flag events whose lists never published
- [#8](https://github.com/JordanSchutte/mtg-modern-meta-engine/issues/8) One-command local data refresh for the MCP server
- [#9](https://github.com/JordanSchutte/mtg-modern-meta-engine/issues/9) Keep a dated history of field forecasts

## Acknowledgements

The Melee endpoint layout was first documented by [zachyattack23/mtg-modern-meta](https://github.com/zachyattack23/mtg-modern-meta).

This project is not affiliated with or endorsed by Wizards of the Coast, Melee.gg or Daybreak Games. Magic: The Gathering is a trademark of Wizards of the Coast LLC.
