# Handoff

_Last updated: 2026-10-27_

## Current milestone: M1 (history loader, normalize, storage)

## Done
- KC-46A: adsb.lol types it `B762`; owner confirmed, now in the tanker family (military block only).
- M0: scaffold, config, CI, public repo (thumplik/OSINT-tanker-tracker).
- M1 core: parsers, fleet filter, H3 cells, coverage counts, date-partitioned Parquet.
- Parser verified on real dumps (readsb 3.16.15); `flags & 8` geometric altitude fixed.
  Six real traces are ODbL regression fixtures (`tests/fixtures/real_2026-10-25/`).
- Downloader streams `<tag>.tar.aa/.ab/...` through the rate limiter (prod pod, year-repo and
  `-0tmp` fallbacks, retries, nothing written until a day reads cleanly).
- Heatmap skip: 512-byte header probes find the `traces/` entry (handles PAX/GNU extended
  headers); the stream stops after the `traces/` subtree. Tar layout varies by day: usually
  heatmap → traces, but 2026-10-22 was acas → traces → heatmap. Both handled.
- `--workers N`: process pool, each worker gets 1/N of every rate limit.
- Registry is per day (`registry/date=…/`); a day resolves untyped hexes from that day and
  earlier only (SPEC §11 updated). Untracked military types feed the registry without parsing.
- 7-day real run (2026-10-20..26): 1.45M points, 501 aircraft, 45 MB (~6.4 MB/day, ~2.3 GB/yr).
  Second run with the speedups gave identical output (row count + content hash).
  Weekday tankers 144–159, weekend 57–61.

## Timing
- 7 days in ~900 s with 4 or 6 workers: download-bound at ~21 MB/s total, not CPU.
  A 12-month backfill is ~13 h; run overnight with `--skip-existing` so it can resume.

## Next
- 12-month backfill (2025-10-27..2026-10-26) started 2026-10-27 into `./data`, 4 workers,
  log at `data/backfill.log`. Resume with the same command plus `--skip-existing`. When done:
  review `tw stats` across weeks, list missing days → M1 done.
- M2: track segmenter + airfield resolution (OurAirports CSV). readsb flag bit 2 marks a new
  leg; consider carrying it on points (SPEC §5 change).

## Open questions
- Parallel backfill is order-dependent for untyped hexes: if day D loads before D-1, D cannot
  use D-1's registry. Sequential re-run of affected days would fix it; measure impact first.
- Prod pod is missing some days (353 in 2025). Keep prod-only unless gaps hurt baselines.
- Region polygons in `regions.yaml` are coarse boxes. Refine before promoting any region.
- Worker host (VPS vs Pi) and X account name are still open (SPEC §15).
