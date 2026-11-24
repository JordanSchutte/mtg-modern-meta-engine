# Runbook: starting a new format period (B&R day)

A Banned & Restricted announcement starts a new **rules period** on its
effective date. Everything here is a rehearsed, ten-minute routine; nothing
in it touches data by hand. The design behind it is the "Periods" paragraph
of [architecture.md](architecture.md).

What a period means in this engine, in one breath: the classifier picks the
rules file by **event date** (`rules_for_date`: the newest file whose date is
on or before the event), the matchup matrix and `archetype_summary` have a
`period` column and never pool across it, and the forecast's positioning
reads the newest period's matrix only. So the old file keeps classifying old
events forever, and the new period starts from an empty matrix.

## Before the announcement

Nothing. Do not pre-create a file: `new_period.py` refuses to overwrite, and
a file dated in the future would start classifying events the moment a run
sees an event on or after that date.

## On the day

1. **Note the effective date** (the day the bans apply to paper events, not
   the announcement day if they differ) and the exact card names.

2. **Run the script** from the repo root, on a fresh branch off `main`:

   ```
   git checkout main && git pull && git checkout -b period/<YYYY-MM-DD>
   python scripts/new_period.py --start <YYYY-MM-DD> \
       --ban "<Card A>" --ban "<Card B>" \
       --unban "<Card C>" --note "B&R <announcement date>"
   ```

   It copies the newest `config/archetypes/modern-*.yml` to
   `modern-<YYYY-MM-DD>.yml`, removes every banned card from every signature,
   lowers `min_signature` where a signature shrank below it, adds a dated
   `note` to each rule touched, and prints a summary. Unbans are only written
   into the header comment: an unbanned card earns a signature slot when the
   weekly proposal run sees it in lists, not before. Nothing else is written;
   the old file is byte-identical (check with `git status`: exactly one new,
   untracked file).

3. **Review the new file** — this is the part that needs a human:

   * Every rule whose `note` begins with the new date lost cards. Read the
     surviving signature: does it still single out the deck? If two rules
     now share most of a signature, give the shakier one an `excludes` or a
     lower `priority`, or wait for the proposal run to say what the lists do.
   * A note containing `REVIEW` means the rule lost most of its signature
     (more than half of its cards, or it is down to one card). Options: leave it (the proposal
     run will find the deck's new shape, or nothing, within a few weeks),
     sharpen it by hand if you already know the replacement, or delete the
     rule if the deck is plainly dead. A one-card signature with
     `min_signature: 1` classifies *any* list holding that card, so look at
     those first.
   * A rule whose every signature card was banned is **dropped** from the new
     file and named in its header comment and in the script's output. Re-add
     it by hand only if the deck demonstrably survives.
   * `min_signature` is only ever lowered, never raised: a rule that went from
     six cards at 3 to four cards at 3 is now stricter in proportion. Lower it
     if you want the old tolerance.

4. **Open the PR** with the one new file (`git add config/archetypes/modern-<date>.yml`).
   `ruff check . && python -m pytest -q` must pass; `test_new_period.py`
   rehearses the same step on the live file, so a failure here means the
   file does not load. Merge it before the first event of the new period is
   fetched (the watcher runs twice a day), otherwise those lists classify
   under the old file until the next run — not a disaster, just one run of
   noise, because every run re-classifies all of silver from bronze.

## What the next watcher run does

* `classify_decklists.py` groups every silver list by `rules_for_date(event date)`:
  old events keep the old file, events on or after `<start>` get the new one.
  `decklist_archetypes.rules_file` records which. No history is rewritten by
  hand; it is rebuilt, deterministically, from bronze.
* `build_gold.py` writes matchup and summary rows per `period`. The new period
  begins with the few matches of its first events, so its shrunk win rates
  sit near 50% until it has volume. The old period's rows stay in the table.
* `build_forecast.py`: the **field** forecast is week-based and not
  period-aware. `paper_ewma` remembers the old period's lists, so a dead
  deck's share fades over its half-life rather than dropping to zero on day
  one, and the MTGO blend component (daily, so it reacts first) pulls the
  field toward what is actually being played. **Positioning** reads the new
  period's matrix only, so it is thin or empty until paper events happen.
* `propose_rules.py` (Wednesdays) clamps its window to the newest file's
  `period_start`, so it mines only new-period lists and proposes against the
  new file. Expect its first PR or two after a ban to be the useful ones:
  `NEW:` rules for the shells that replaced the banned deck, and trims for
  signatures that stopped appearing together.

## What to look for in the first brief after the change

* The header line now reads `Rules period: **<new date>**`. If it still shows
  the old date, no paper list of the new period has been classified yet
  (check the `reports` branch's `archetypes.txt` for `rules: ... modern-<date>.yml`).
* Section 4 (matrix changes) will be short and section 3 (best-positioned)
  may say that no deck reaches the coverage threshold: expected for a week
  or two.
* The banned deck may still hold a forecast share in section 1 while its
  paper memory decays (see above); its `MTGO, last 2 wks` column should be
  at or near zero. If it is not, the MTGO classifier is matching the deck's
  successor under the old name — read the `REVIEW` rules again.
* Unclassified share (the `archetypes.txt` report) will rise. That is the
  to-do list for the next proposal PR, not a bug.

## What not to do

* **Never edit the old file.** It governs history; a change there silently
  reclassifies every past event on the next run.
* **Never reclassify history by hand**, and never write to silver or gold to
  "move" matches between periods. Everything is rebuilt from bronze.
* **Never pre-date or back-date the start.** The date is the ban's effective
  date; an event that ran under the old rules belongs to the old period even
  if the announcement had already been made.
* Never run the script twice for the same date; fix the generated file by
  hand instead (it is YAML meant to be edited).
