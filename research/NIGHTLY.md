# Nightly check runbook

A scheduled task runs this every night at about 1am Central. It keeps Cap Room's data and settings honest.

## Steps

1. **Get the code.** Work in a clone of `Quinning18/cap-room` on `main` and `git pull`. Configure the commit
   author as `Quinning18 <mitchellquinnell@yahoo.com>`.
2. **Dependencies (if missing).** `pip install numpy highspy` and, in a scratch directory outside the repo,
   `npm install highs@1.15.4` (the smoke test serves the solver from there). Playwright and Chromium are
   preinstalled in the cloud container.
3. **Grade and tune.** `python3 research/nightly.py`
   - Records today's NBA/NFL main Classic slates and snapshots Sleeper's player lines for them.
   - Grades recorded slates whose games are final (projection error, Cash vs. simulated field median,
     Tournament top-1%), appending to `research/nightly/history.json`.
   - May change `docs/tuning.json` (lines blend weight, simulator spread) within its built-in guardrails;
     every change is logged in `research/nightly/changes.md`.
   - Writes `research/nightly/report.md`.
4. **Smoke test the live app.** `node research/smoke_test.js <scratch dir with node_modules/highs>`
5. **If the smoke test fails**, find out why (DraftKings or Sleeper changed a field or endpoint, a slate
   type changed, the page throws). Make the smallest fix in `docs/index.html` that restores the app, re-run the
   smoke test until it passes, and explain the fix in the commit message. Do not change lineup strategy,
   scoring rules or the UI to "improve" results: tuning happens only through `docs/tuning.json` via
   `nightly.py`. If you cannot fix it safely, leave the app unchanged and describe the problem at the top of
   `research/nightly/report.md`.
6. **Look for data problems** in the run output: many projected players missing, a sport with no lines for
   several nights, grading failures. Note anything notable at the top of `report.md`.
7. **Commit and push** `research/nightly/`, `docs/tuning.json` and any fix to `main` with a message that
   summarizes the report (graded slates, cash/top-1% results, changes). Never rewrite history or delete data.

## What the guardrails allow

- Lines blend weight (0 = Sleeper only, 1 = lines only) moves one 0.25 step at a time, and only when 300+
  graded player-games with lines show the other weight is at least 1% more accurate.
- The simulator's spread model is refit only with 1,500+ graded player-games and changed only when it is off
  by 10%+; values are clamped to ranges the app also enforces.
