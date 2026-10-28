# Handoff

_Last updated: 2026-10-28 (M5d)_

## Branches (stacked; nothing merged yet)
- `m4-live-worker` (PR #2) → `m5-dashboard` (PR #3) → `m5c-theaters` (this work), all in the
  `../tanker-hunt-m5` worktree. The M4 72 h trial runs from the main checkout until about
  2026-10-31 03:30 UTC. Don't restart it before then. History 2025-10-27..2026-10-26.

## M5 (dashboard, live)
- https://tanker-watch-dun.vercel.app (Vercel project `tanker-watch`, root `web/`). Production
  branch is `m5-dashboard` for now; set it back to `main` after the merges.
- Blob store `tanker-watch-data` = `VITE_DATA_BASE_URL`; RW token in the main checkout's `.env`.

## M5c (theaters; plan approved 2026-10-28)
- Families kept apart: cooldown keyed by detector + region + family (+ D6 series); D2 over 6
  months 346 → 452 live alerts (airfield in the key would give 647, so left out).
- Theater layer (`src/tw/theater/`, `config/ccmds.yaml`, SPEC §5-8): fixes from sortie
  starts/ends; inventory ("seen in", 14 d), entries/exits, transits from NORTHCOM; write-once
  `baselines/ccmd`. D6 flow surge, D7 inventory shift; all CCMDs `watch`; official tier only.
- Full-year backtest `reports/backtest_2026-10-26.md` on the scratch copy
  `/Volumes/Code/tanker-hunt-m5c-data`: D6 4.8/wk, D7 1.1/wk; CENTCOM tankers fire 2026-02-26.

## M5d (owner request 2026-10-28: "too much data at first glance")
- New default tab **Overview**: what stands out → where they are → over time, one family.
  - Stored official alerts once the run exists for a day; otherwise **indicators** (D1/D6/D7 +
    coverage guard run at export by `views/indicators.py`, never stored). Suppressed = "held back".
  - Map defaults to each aircraft's latest fix (`theaters.positions`, with callsign); heat map is
    the "Activity" mode. Region boxes only on Regions/Flights. Family is a 3-button switch.
  - Trends: `dashboard.history_days: 180` (detectors.yaml, dashboard only). Schema 3.
- Local export re-done (scratch data); Blob and the live site are still schema 2 until published.

## Open question (owner)
- **The coverage guard hides the CENTCOM build-up.**
  - From 2026-02-27, CENTCOM total traffic was 17-20% of usual (civil traffic gone, likely
    airspace closures) while visible tankers rose 0 → 24. The guard suppressed every D1/D6/D7
    there through March.
  - Proposal: make the guard direction-aware. Low coverage can't create a rise, and high coverage
    can't create a drop. This changes SPEC §8, so it needs the owner's decision.

## Next
- After the trial:
  1. Run `uv run tw worker-report --from 2026-10-28 --to 2026-10-31`.
  2. Run the Codex review of PRs #2, #3 and M5c (detectors/ changed: cooldown, D6, D7).
  3. Merge in order.
  4. Run `uv run tw build-theater --from 2025-10-27 --to <yesterday>` on the main data.
  5. Restart the worker.
  6. Set the Vercel production branch to `main`.
- Owner: start the truth set (`eval/truth_set.yaml`, 20-40 periods; CENTCOM Feb-Jul 2026 is a
  candidate).

## Open questions
- airplanes.live API access: register (owner) or drop it.
- CCMD polygons are approximate (43 checkpoints pass). Region polygons are still coarse boxes.
- Worker host (VPS vs Pi), X account name (SPEC §15).
