# CLAUDE.md — how to work in this repo

Project memory for any Claude session (chat, scheduled task, or `@claude` on
GitHub). Read this before touching anything. Jordan owns the repo; you open
pull requests, he merges.

## What this is

A data platform that forecasts the Magic: The Gathering **Modern** metagame.
Two scrapers (Melee.gg for paper, mtgo.com for online) feed an append-only
bronze layer in Cloudflare R2; DuckDB builds silver Parquet tables; gold holds
the matchup matrix, archetype shares and a backtested field forecast; an MCP
server (`mtgmeta-mcp`) exposes it all to Claude Desktop. `docs/architecture.md`
is the design record and is kept current — update it in the same PR as any
design change.

Source layout: `src/mtgmeta/{sources/melee,sources/mtgo,silver,gold,archetypes,mcp}`,
entry points in `scripts/`, tests in `tests/` (fixtures are trimmed real pages),
archetype rules in `config/archetypes/modern-<period>.yml`.

## Commands

```
pip install -e ".[dev,mcp]"
ruff check . && python -m pytest -q        # must pass before every commit
python scripts/build_silver.py             # bronze -> silver (needs data/bronze)
python scripts/classify_decklists.py       # silver -> decklist_archetypes
python scripts/build_gold.py               # matchup matrix + summary
python scripts/build_forecast.py --no-write
```

Data lives under `DATA_ROOT` (default `data/`), which is git-ignored. Sessions
usually have no data; tests use fixtures and never need it.

## Rules (not suggestions)

1. **Branch + PR, never push to `main`.** `main` is protected; CI must pass.
   Branch names: `agent/<issue-number>-<slug>`.
2. **Lint and tests gate every commit:** run
   `ruff check . && set -o pipefail && python -m pytest -q | tail -1` and
   commit only if it passes. A PR with a red check is not done.
3. **Bronze is append-only.** Nothing ever rewrites or deletes a fetched page.
   Retries leave their history (`attempts.json`); "empty" is a status, not a
   deletion.
4. **Sample first.** Before any long or expensive job (backfill, batch run,
   anything over a few minutes), prove the whole path end to end on a tiny
   sample (1–3 events) and check the output. Jordan has lost a 6-hour pull to
   an untested last step; don't repeat it.
5. **The data pipeline runs only in GitHub Actions.** Dev sessions do not
   write to R2 and do not run the watchers. Secrets exist only as Actions
   secrets and env vars; never paste a key, token or URL with credentials into
   a chat, commit, issue or PR.
6. **Be polite to the sources.** `PoliteClient`: one request at a time, fixed
   interval, hard stop on HTTP 429. Never add parallel fetching, never lower
   the interval, never bypass the client. No dependency on third-party
   decklist caches (own discovery only).
7. **Rules config stays human-editable.** Archetype rules are YAML Jordan
   edits by hand; keep the schema simple and never generate rules that a
   person couldn't read and reason about.
8. **Keep repo changes lean.** No generated data, large SQL dumps or scratch
   files in commits. One concern per PR.
9. **Never truncate, drop or rewrite a table as a "fix".** Changes to silver
   or gold are rebuilds from bronze, which is always possible.
10. **Never merge your own PR.** Never force-push. Never edit a branch that
    has a human commit on it (open a new branch instead).

## Working from the backlog (scheduled sessions)

Work items are GitHub Issues. Only take an issue labeled **`ready`**; take the
oldest one that is not already referenced by an open PR — unless the run's
own instructions name a specific issue, in which case take that one. Then:

1. Comment on the issue: "Starting — plan: …" (two or three lines).
2. Branch from `main`, implement, add or update tests, update
   `docs/architecture.md` if the design changed.
3. Run the lint+test gate. Push. Open a PR that references the issue
   (`Closes #N`), with a short description of what changed and how it was
   verified. Draft PR if anything is unfinished.
4. Comment on the issue with the PR link and any open questions.

If you cannot finish, push what you have as a draft PR and say what is left.
If a decision is needed that you can't make from this file, the code or the
issue, **do not guess**: write the question as a comment on the issue, add
the label **`needs-jordan`**, remove `ready`, and stop. Do the preparatory
work first so the question is concrete.

**One issue per session, then stop.** Once the PR is open and the issue has
its comment, the session is finished — do not start a second issue even if
time remains. Do not refactor beyond what the issue needs.

When an issue leaves a design choice open and you can make a reasonable call,
make it, state it in the PR description and in `docs/architecture.md`, and
say in the issue comment that it is a choice Jordan may want to reverse. Ask
first (label `needs-jordan`) only when the choice is hard to undo or changes
what other issues assume.

## Planning (scheduled sessions)

When asked to plan: read `docs/ROADMAP.md` (the 1.0 definition and what is
after it), the open issues, the latest Status comment and the latest brief
on the `reports` branch. Then:

- For each roadmap gap with no open issue, and for each issue labeled
  **`idea`**, write one or more issues labeled **`proposed`**, each sized for
  one nightly session and shaped like the existing ones: what, where in the
  code, how to verify, what it must not touch. Link them to the roadmap item
  or the `idea` issue; on the `idea` issue, comment the breakdown and remove
  the `idea` label.
- Mark a proposed issue **`ready`** yourself only when it is small by the
  roadmap's definition (one PR; no new workflow; no change to table schemas
  the MCP tools read; no new external surface; no change to R2 layout).
  Everything else stays `proposed` for Jordan.
- Do not duplicate: search open and recently closed issues first. Do not
  reopen decisions recorded in `docs/architecture.md` or the roadmap.
- Finish with one comment on the Status issue: what you proposed, what you
  marked ready, what needs Jordan's call.

## Health check (scheduled sessions)

When asked to check on the platform:

- Last 24 h of runs for `melee-watcher.yml`, `mtgo-watcher.yml`, `ci.yml`,
  `propose-rules.yml` (`gh api .../actions/workflows/<file>/runs`).
- The `reports` branch: `reports/{coverage,matchups,forecast,archetypes}.txt`
  (`git fetch origin +refs/heads/reports:refs/remotes/origin/reports`).
- Open bot PRs on `archetype-proposals`.

Post one comment on the standing issue titled **"Status"** (create it if
missing): green/red per workflow, anything that failed and why (read the
log), counts from the reports, the chosen forecast model and any flip, and
what needs Jordan. If nothing is wrong, one line. A transient network
timeout that the next run recovered from is not a problem; a run that failed
twice in a row is.

## How to reach Jordan

Comments on the relevant issue, or the "Status" issue. Keep questions
concrete: what you found, the options, what you'd pick and why. He reads
PRs in the morning (US Eastern); don't expect same-session answers.

## Context worth knowing

- Paper Modern data is bursty (large events cluster at the end of RCQ
  seasons); MTGO is daily. The forecast exists to bridge the gaps.
- Jordan is a data engineer and plays Modern; he is not a statistician.
  Explain any modeling term the first time it appears (log-loss, EWMA, TVD).
- Design decisions already made (don't relitigate without a reason): one
  matchup matrix with a `source` column, not separate paper/online matrices;
  MTGO lists never count toward meta share; rules-based classification, not
  clustering; model choice by backtest, never hand-picked.
