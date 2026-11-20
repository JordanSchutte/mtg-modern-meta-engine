# Roadmap

What "1.0" means, what is left to get there, and what is deliberately after
it. The planning agent reads this to propose work; Jordan edits it to change
direction. Work items themselves live in GitHub Issues (see CLAUDE.md).

## Who it is for

A small private group (Jordan, and eventually a team he plays with). The
output is for them, not the public. The code is public as a portfolio; the
*outputs* (forecasts, briefs, matrix) are not meant to be. The "everyone
bandwagons the recommended deck" problem is therefore out of scope for now,
but see "After 1.0".

## 1.0 definition

Someone on the team who isn't Jordan can, on a Thursday, see what next
weekend's paper field is forecast to be, which decks are best positioned
against it and why, and how well past forecasts did — and the platform keeps
itself running and reporting without anyone touching it.

### Done

- Melee and MTGO ingestion, append-only bronze in R2, daily watchers
- Silver/gold in DuckDB Parquet; rules-based archetype classification with
  weekly proposal PRs
- Matchup matrix from paper Swiss pairings (with same-75 imputation) and MTGO
  top-8 brackets, one matrix with a `source` column
- Backtested field forecast (paper/MTGO blends; results-weighted MTGO
  candidates), position against the forecast field
- MCP server for Claude Desktop; weekly written brief
- CI, branch protection, nightly backlog worker, morning health check

### Remaining for 1.0

1. **Out-of-sample scoring.** Keep every forecast on record (#9) and, once a
   paper weekend's lists land, score the forecast that was made for it
   (log-loss and TVD, same as the backtest) and show the running record in
   the brief. Until one real weekend has been forecast and scored, the
   headline claim is untested.
2. **Private outputs.** Stop committing reports and briefs to the public
   `reports` branch and stop printing forecast tables in Actions summaries;
   write them to R2 only, and have the local sync script (#8) fetch them.
   Keep code and tests public.
3. **A page to look at.** An HTML report built each run from gold (brief,
   forecast field, position table, matrix heatmap), written to R2 next to the
   markdown brief. Viewing it is via the sync script locally for now; a
   team-facing hosted page behind access control is after 1.0.
4. **Saturation share.** For each positioned deck, the share of the field at
   which its expected win rate falls to 50% with everything else
   proportional. Reported next to expected WR in `position_forecast`, the
   `recommend()` tool and the brief: "favored up to ~14% of the field."
5. **Coverage flags** (#7): events whose lists never published, and the
   "most recent paper week with ≥150 lists" indicator.
6. **Format-period rollover, exercised.** The next ban announcement:
   `scripts/new_period.py` creates the new rules file, the matrix and
   forecast pool by period correctly, the brief says which period it is on.
   Write the runbook in docs/ before it happens; do it when it happens.
7. **Docs current.** Remove the stale "Event-driven ingestion" (Kafka/Airflow)
   section from `docs/architecture.md`; every section describes what runs.
8. Ops: gzip bronze pages when the bucket passes ~5 GB. (The `mtgmeta-local`
   R2 token has been rotated — done.)

## After 1.0

- Flex-slot tuning: split each archetype into core and flex slots and score
  flex choices against the forecast field (personal to the deck you play).
- Team page: hosted HTML behind access control (e.g. Cloudflare Access),
  built from the R2 outputs.
- MTGO result-weight tuning by backtest once ≥10 paper weeks are scored (#10).
- Metagame-equilibrium solve (the field where no deck has an edge) if the
  tool ever gets a public audience.
- MTGO Preliminaries and Showcase events if they publish separately.

## How the planner uses this

Weekly, the planning agent compares this file, the open issues, the Status
thread and the latest brief, and opens `proposed` issues for gaps — each
small enough for one nightly session. It may mark an issue `ready` itself
when it is **small**: one PR, no new workflow, no change to table schemas
the MCP tools read, no new external surface, no change to R2 layout.
Anything larger stays `proposed` until Jordan promotes it. Ideas from Jordan
arrive as issues labeled `idea`; the planner breaks them into `proposed`
items and links them back.
