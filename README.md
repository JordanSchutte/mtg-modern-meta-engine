# MTG Modern Meta Engine

Predicts the Modern field for an upcoming tournament and recommends the best-positioned deck and flex slots for it, built as an event-driven data platform.

> **Status:** Phase 0 — ingestion foundations. Melee event discovery and event fetching work end to end; everything downstream is planned (see [Roadmap](#roadmap)).

## What it does

1. **Matchup history.** Pairings from completed large paper events (100+ players) build an archetype-vs-archetype matchup matrix, pooled within a *format period* (the time between bans, unbans or format-warping set releases).
2. **Meta prediction.** Recent MTGO Challenge top 32s and 5-0 league dumps, corrected for survivorship, forecast each archetype's share of the upcoming field.
3. **Deck selection.** Each archetype's expected win rate against the forecast field is `E[WR_d] = Σ_a share_a · WR(d vs a)`. A metagame-equilibrium solve flags underplayed decks.
4. **Flex tuning.** Each archetype is split into a fixed core and flex slots; flex slots are scored against the forecast field.

## Architecture

```
 Melee watcher ─┐
 MTGO watcher  ─┼─► Kafka: tournament.published ─► ingestion consumer ─► Bronze (Iceberg on MinIO)
 TopDeck hook  ─┘                                   └─► dead-letter topic         │
                                                                                   ▼
                                           Airflow (asset-triggered, debounced rebuild)
                                                   │
                                     Spark: bronze → silver ─► dbt: silver → gold
                                                                    │
                                         matchup matrix · meta forecast · deck ranking + flex
```

More detail in [docs/architecture.md](docs/architecture.md).

## Data sources

| Source | Used for | Access |
| --- | --- | --- |
| Melee.gg | Matchup matrix (full-field decklists and every Swiss pairing) | Public tournament pages, fetched politely (below) |
| mtgo.com decklists | Meta forecast (Challenge top 32, 5-0 leagues); top 8 brackets at low weight | Public pages |
| Scryfall bulk data | Card dimension, role tagging | Official bulk files |
| TopDeck.gg | Optional second source; demonstrates webhook ingestion | Official API (key in `.env`) |

### How Melee data is obtained

Melee doesn't offer API credentials to individuals, so this project reads the same public pages and JSON a browser loads when viewing a finished tournament. It is deliberately conservative:

- Only **completed** Modern events with **100+ players** are fetched, each **once**. Raw responses are cached in bronze and never re-requested.
- One request at a time, **2.5 s apart**, exponential backoff on HTTP 429, and the run **stops** after repeated 429s.
- Requests carry an identifiable User-Agent with a contact address.
- Event discovery uses Melee's public *Find Events* search, rendered with Playwright.
- Every parser sits behind **contract tests** on fixtures captured from live pages. If Melee changes its page or JSON shape, the run fails loudly instead of writing bad rows.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env                                 # set MELEE_CONTACT_EMAIL

# Find completed Modern events with 100+ players since a date
python scripts/discover_melee.py --since 2026-04-20 --min-players 100

# Fetch one event into data/bronze/melee/
python scripts/fetch_melee_event.py 453138

pytest
```

## Repository layout

```
src/mtgmeta/
  contracts.py              ContractError: raised when a source's shape changes
  sources/melee/
    query.py                builds Find Events search URLs
    discovery.py            Playwright search paging + result-card parsing
    http.py                 throttled, backoff-aware HTTP client
    parse.py                tournament page, round matches, decklist parsers
    fetch.py                fetches one event into bronze (idempotent)
scripts/                    CLI entry points
tests/fixtures/melee/       responses captured from live Melee pages
docs/                       architecture and design notes
```

## Roadmap

| Phase | Builds | Done when |
| --- | --- | --- |
| 0 Foundations | Melee discovery + fetch, contract tests, CI, Docker Compose stack, Scryfall cards | One command loads cards and an event to silver; CI green |
| 1 Batch MVP | MTGO scraper, archetype rules, naive matrix and meta share, first ranking (dbt on DuckDB) | Produces a ranking for a real upcoming Modern event |
| 2 Orchestration, Spark, quality | Airflow DAGs, Spark bronze→silver, history backfill, data contracts | Full history rebuilds from bronze in one run |
| 3 Event-driven ingestion | Watchers, Kafka topic, dead-letter topic, debounced asset triggers | A new event rebuilds only the affected marts |
| 4 Statistics | Shrinkage, skill-adjusted model, field-share calibration, equilibrium LP, core/flex | Backtest on past events written up |
| 5 Cloud + BI | Terraform (AWS, Snowflake), dashboards | apply → run → destroy at ≈ $0 |

## Acknowledgements

The Melee endpoint layout was first documented by [zachyattack23/mtg-modern-meta](https://github.com/zachyattack23/mtg-modern-meta). Historical tournament JSON from [Badaro/MTGODecklistCache](https://github.com/Badaro/MTGODecklistCache).

This project is not affiliated with or endorsed by Wizards of the Coast, Melee.gg or Daybreak Games. Magic: The Gathering is a trademark of Wizards of the Coast LLC.
