# Handoff

_Last updated: 2026-10-28_

## Branches (stacked; nothing merged yet)
- `m4-live-worker` (PR #2) → `m5-dashboard` (PR #3) → `m5c-theaters` (this work), all in the
  `../tanker-hunt-m5` worktree. The M4 72 h trial runs from the main checkout until about
  2026-10-31 03:30 UTC. Don't restart it before then. History 2025-10-27..2026-10-26.

## M5 (dashboard, live)
- https://tanker-watch-dun.vercel.app (Vercel project `tanker-watch`, root `web/`). Production
  branch is `m5-dashboard` for now; set it back to `main` after the merges.
- Blob store `tanker-watch-data` = `VITE_DATA_BASE_URL`; RW token in the main checkout's `.env`.

## M5c (owner request 2026-10-28; plan approved)
- **Families kept apart.**
  - The cooldown is keyed by detector + region + family (+ D6 series). The old key let one family
    silence another in 46% of cooldown suppressions.
  - D2 over 6 months: 346 → 452 live alerts. Keying by airfield as well would have given 647,
    so it was left out.
  - The web app opens on tankers; alerts filter by family and show a family chip.
- **Theater layer** (`src/tw/theater/`, `config/ccmds.yaml`, SPEC §5-8):
  - fixes come from sortie starts and ends, not overflights;
  - inventory ("seen in", 14 days), moves (entries/exits), transits from NORTHCOM (destination =
    farthest fix, 48 h dwell);
  - aggregates + write-once `baselines/ccmd`.
- **D6/D7:** D6 is a 7-day flow surge; D7 an inventory shift, up or down. All CCMDs are
  `watch`; official tier only.
- **Theaters tab:** per-CCMD cards, sparklines, transit arcs, CCMD cells on the map.
- **Full-year backtest:** `reports/backtest_2026-10-26.md` (gitignored), on a scratch copy at
  `/Volumes/Code/tanker-hunt-m5c-data` (points and live symlinked) so the trial's data dir isn't
  touched.
  - D6 4.8/wk, D7 1.1/wk; CENTCOM tankers fire on 2026-02-26 (D6 6 in vs 0; D7 6 vs 0).
- Blob was re-exported from the scratch copy (theaters in every day file). The public site shows
  them only once this branch reaches the production branch.

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
