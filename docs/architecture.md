# Architecture and design decisions

## Principles

- **Each source does only the job it's reliable at.** Full-field paper events (every decklist and every Swiss pairing published) build the matchup matrix. MTGO only shows the top 32 lists and the top 8 bracket, so it drives the meta forecast and contributes top 8 matchups at low weight.
- **Nothing is deleted.** Bronze is append-only and versioned. Rows that can't be used for the matrix (an unknown opponent deck, a bye) stay in silver with flags.
- **Fail loudly.** Every source parser is covered by contract tests on fixtures captured from the live site. A shape change stops the run and goes to a dead-letter topic; bad rows never reach bronze.
- **Near-zero cost.** Everything runs locally in Docker Compose; cloud targets (AWS, Snowflake) are Terraform-managed profiles that are created, run and destroyed.

## Event-driven ingestion

Sources publish irregularly: some weekends have six large events, some none, and MTGO posts dozens of times a week. Instead of a fixed schedule:

1. **Watchers** turn publications into events. Melee and MTGO only publish pages, so small pollers detect new completed events and emit `tournament.published` (keyed by source + event ID). TopDeck's webhook posts into the same topic.
2. An **ingestion consumer** fetches the full event and lands raw responses in bronze. Parse failures go to a dead-letter topic.
3. Landing in bronze updates an **Airflow asset**, which triggers a debounced rebuild of only the affected marts. A paper event updates the matrix and the forecast; a 5-0 dump only updates the forecast.

Kafka is used for decoupling and replay, not throughput: changing archetype rules means replaying every event from the start of the topic to reclassify history.

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

## Matchup matrix

- Mirrors are excluded.
- Within a format period every match counts equally; a matchup is mostly a property of the two decks. A light decay for list drift is added only if a backtest shows it helps.
- Each match keeps its round number so late-round effects can be checked.
- Win rates are shrunk toward 50% (beta-binomial) and reported with intervals.

## Imputation

Melee lets players reuse an exact 75 they've already submitted, so an unpublished list at a side event is most likely the player's most recent Modern submission. Imputed rows use the same player's latest list before the event (ideally within two weeks) and carry an `imputed_same_75` flag so the matrix can down-weight them.

## Archetypes

Rules live in `config/archetypes/<format>-<period-start>.yml` and name only a deck's **signature** cards — the engine, not the list — so flex slots can change with the meta without changing the classification. A list is scored by how many signature cards it holds; the rule with the most hits wins. `archetype` is the level the matrix uses; `variant` is the color/build label underneath (Esper vs Jeskai Blink). Unknown shells stay in one archetype row on purpose: an unsolved deck (Darklight Phoenix) gets a signature of its invariant cards and its sub-packages are *measured* inside the archetype, not written into rules, until a package stabilises and earns a variant.

**Periods.** Each rules file governs events from its date until the next file's date. A B&R announcement is `scripts/new_period.py --start <date> --ban ...`: the newest file is copied, banned cards leave every signature, the old file keeps classifying old events. The classifier picks the file per event date, so history never silently reclassifies.

**Bootstrapping and drift.** The first file was mined (`scripts/mine_archetypes.py`): IDF-weighted clustering of the period's lists, each cluster's high-presence/high-lift cards as a draft signature, then named by hand. Every Wednesday `propose-rules.yml` re-mines the last 30 days and opens one pull request with mechanical proposals only — `NEW:` rules for unclassified clusters, trims of signature cards that fell out of their own deck, additions of cards that became ubiquitous in it — plus variant candidates in the PR text. It never renames, merges or deletes. A person names and merges; nothing changes until then. The job replaces only its own commits (a branch with a human commit is left alone), closes its PR when a later week finds nothing, and the twice-daily report names any proposal PR still open.

**Failure modes.** No rules file for a list's date fails the classify step loudly instead of writing an empty table; the file is in git, so an accidental delete is a checkout. Leftover lists (staples only, no shared engine) are `Unclassified` and are reported with their most common cards.
