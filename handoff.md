# Handoff

_Last updated: 2026-10-28_

## Current milestone: M5 (dashboard), on `m5-dashboard`
- Worktree: `../tanker-hunt-m5`, based on `m4-live-worker`.
- M4 is built (PR #2). Its 72 h trial runs from the main checkout until about 2026-10-31
  03:30 UTC. Don't restart that worker before then.

## Data (M1–M3)
- History 2025-10-27..2026-10-26: 360 days, 65M points, 2.1 GB. KC-46A is typed `B762`; C30J
  counts as tactical airlift only.
- M3 backtest: D1 6.2/week, D2 13.3/week. The Gulf tankers 2026-06-22..07-03 were suppressed by
  coverage. Worth labeling.

## M4 (live worker)
- **Two tiers.**
  - Provisional: hourly rolling D1/D2 on live `/v2/mil`, stored in `provisional_alerts/`, never
    queued.
  - Official: nightly on the release, write-once `alerts/`. Official alerts start at 2026-10-27.
- adsb.lol returns HTTP 429 at times. The worker fails over to adsb.fi and back every 15 min. Watch
  for it in the trial report.

## M5 (owner decisions 2026-10-28: Vercel + static day files; public)
- `views/day.py` (point-in-time, never writes) -> `export/` (local or Blob, rate-limited) ->
  `web/` (Vite/React, MapLibre + deck.gl). Worker `export` job hourly; live only after the trial.
- Deferred to M5b: per-hex baseline diff toggle, airfield board, playback, compare, re-run.

## M5 status
- Built and checked locally (browser pane, desktop + phone): views + export (14 tests), web app
  (8 vitest), CI web job. Real day: ~0.15 s to build, ~355 KB JSON + 1.4 MB r6 file.
- Local loop: `uv run tw export --from .. --to .. --dest local:web/public/data`, then
  `cd web && npm run dev` (TW_DATA_DIR points at the main checkout's data from the worktree).
- MapLibre 6 needs `setWorkerUrl(...?worker&url)` under Vite (done in MapView.tsx).

## Vercel (set up 2026-10-28 with the CLI, logged in as thumplik-2699)
- Site: https://tanker-watch-dun.vercel.app (project `tanker-watch`, root `web/`, team
  thumplik-2699s-projects). Production branch is `m5-dashboard` for now; set it back to `main`
  after PRs #2/#3 merge (`vercel api /v9/projects/tanker-watch/branch --method PATCH`).
- Blob store `tanker-watch-data` (public, iad1): https://pd1haiwyvvtzrh5i.public.blob.vercel-storage.com
  = `VITE_DATA_BASE_URL` (production + preview). RW token in the main checkout's `.env` (600).
- Backfill 2026-10-28: 362 days, 517 MB (r6 files ~1.4 MB/day dominate), 378 s. Day file
  ~25-60 KB brotli: ~80 ms cached, ~0.24 s uncached. Today's file is frozen until the worker
  restarts with the export job (after the trial) or `tw export --dest blob` is run by hand.

## Next
- After the trial: `uv run tw worker-report --from 2026-10-28 --to 2026-10-31`. Done if:
  - no crashes;
  - longest poll gap < 5 min outside outages;
  - rate limits respected;
  - 3 official days loaded.
- Then run the Codex review of PR #2 and merge; retarget the M5 PR to `main`.
- Owner: start the truth set (`eval/truth_set.yaml`, 20–40 periods).

## Open questions
- airplanes.live API access: register (owner) or drop it.
- Public dashboard shows provisional alerts, labeled. Hide them if preferred (one flag).
- If D's release lands after D+1 was already loaded, D+1's sorties aren't rebuilt (rare).
- Worker host (VPS vs Pi), X account name (SPEC §15). Region polygons are still coarse boxes.
