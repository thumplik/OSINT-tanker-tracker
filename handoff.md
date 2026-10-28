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

## M5 plan (owner decisions 2026-10-28: Vercel + static day files; public)
- `views/day.py` `day_view(data_dir, day)` holds one dict per day:
  - `meta` (tier, baseline_as_of);
  - `heat` (r4 x hour x family);
  - `regions` (observed vs snapshot band);
  - `coverage`, `alerts` (official + provisional, tagged), `sorties` (summaries).
  - It reads only partitions <= day.
  - The r6 day heat goes in a separate lazy file (about 40k cells/day).
  - Tier: official if `points/date=D` exists, else provisional from `live/`.
- `export/`: `tw export --from --to --dest local:<dir>|blob` writes `days/D.json`, `days/D.r6.json`,
  `index.json` and `meta.json`.
  - Blob PUTs are rate-limited.
  - The token is in `.env` only.
  - Worker job `export` runs after provisional/official (enabled only after the trial).
- `web/`: Vite + React + TS, MapLibre (OpenFreeMap) + deck.gl H3HexagonLayer.
  - URL state `?date&family&region&res&hour`.
  - Tabs: Alerts / Regions / Sorties. Bottom sheet on phone.
  - Attribution footer. No approve/reject (M7).
- Deferred to M5b: per-hex baseline diff toggle, airfield board, playback, compare, re-run.

## M5 status
- Built and checked locally (browser pane, desktop + phone): views + export (14 tests), web app
  (8 vitest), CI web job. Real day: ~0.15 s to build, ~355 KB JSON + 1.4 MB r6 file.
- Local loop: `uv run tw export --from .. --to .. --dest local:web/public/data`, then
  `cd web && npm run dev` (TW_DATA_DIR points at the main checkout's data from the worktree).
- MapLibre 6 needs `setWorkerUrl(...?worker&url)` under Vite (done in MapView.tsx).

## Next
- Owner OK, then: create the public Blob store and the Vercel project (root `web/`,
  `VITE_DATA_BASE_URL` = Blob URL); owner pastes `BLOB_READ_WRITE_TOKEN` into `.env`;
  `tw export --dest blob` backfills all days (~1,100 PUTs at 2/s, ~10 min); phone check.
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
