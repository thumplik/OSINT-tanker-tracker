# Handoff

_Last updated: 2026-10-28_

## Current milestone: M4 (live ingest worker). Built on `m4-live-worker`; 72 h trial running. M0–M3 done.

## Data (M1–M3)
- History 2025-10-27..2026-10-26: 360 days, 65M points, 2.1 GB. No release on 2025-12-25,
  2026-05-05..07 or 2026-06-11. KC-46A is typed `B762`. C30J counts as tactical airlift only.
- Sorties: 93,684. Ends seen low resolve 90.5% (origin) and 90.4% (destination) at 15 km / 4,000 ft.
- M3 backtest (2026-04-28..10-26): D1 6.2/week (3.3 medium), D2 13.3/week. Gulf tankers
  2026-06-22..07-03 (~30/day vs 0) were suppressed by coverage (4.9–7.2×). Worth labeling.

## M4 (owner decisions 2026-10-27)
- **Two tiers.**
  - Provisional: `/v2/mil` every 15 s, hourly rolling 24 h/72 h D1/D2, `coverage: unchecked`,
    in `provisional_alerts/`, never queued.
  - Official: nightly on the release (~03:25 UTC) through the same pipeline → write-once
    `alerts/date=D/`.
- **The 72 h trial runs on this Mac** (`caffeinate -i uv run tw worker`). VPS vs Pi is still
  open.
- Code:
  - `ingest/live.py`: client, failover/backoff, raw store, request log, live points from raw.
  - `worker.py`: jobs + APScheduler. `store/alerts.py`. `worker_report.py`.
  - `pipeline.prepare_window`.
  - Detectors take `date | Window`. A day is the window ending at the next midnight, so the
    golden file is unchanged.
  - Aggregates share the pure `count_sorties`.
- Live data lives only under `data/live/`. Official `points/` and `sorties/` come from releases
  only.
- Official alerts start with the day before the worker's first start (`worker/state.json`).
  Earlier days are backtest territory.
- Smoke test 2026-10-28:
  - adsb.lol: 200, ~80 kB, ~200 aircraft.
  - adsb.fi: 200.
  - airplanes.live: 403 "please contact us". Configured, but left out of `order`.

## Next
- When the trial ends: `uv run tw worker-report --from <start> --to <end>`. Done if:
  - no crashes;
  - longest poll gap under 5 min outside outages;
  - rate limits respected;
  - 3 official days loaded.
- Then run the Codex adversarial review (detector signature change, provisional path), address
  it, and merge.
- Owner: start the truth set (`eval/truth_set.yaml`, 20–40 periods). Candidates: the Gulf June
  days and the Eastern Med C-17 day (2026-08-18).
- M5 dashboard: publish aggregates and alerts to Postgres + Blob for Vercel.

## Open questions
- airplanes.live API access: register (owner) or drop it from config.
- Provisional D2 uses live sorties only, so its 72 h window is partial for the first 3 days of a
  run (evidence shows the snapshot used).
- If day D's release lands after D+1 was already loaded, D+1's sorties aren't rebuilt (rare;
  not handled).
- Worker host (VPS vs Pi) and X account name (SPEC §15). Region polygons are still coarse boxes.
- Commit the Codex review prompt as a script so the CLAUDE.md step is repeatable.
