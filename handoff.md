# Handoff

_Last updated: 2026-10-26_

## Current milestone: M0 (scaffold) + start of M1 (history loader, normalize, storage)

### Plan (written before coding)
1. M0: `pyproject.toml` (uv, Python 3.12), `src/tw` package layout per CLAUDE.md, ruff + mypy
   (strict on `detectors/`, `publisher/`), pytest, GitHub Actions CI, `.env.example`, `.gitignore`.
2. M0: config files: `fleet.yaml`, `regions.yaml` (all `watch`), `detectors.yaml`,
   `known_events.yaml`, `post_language.yaml`, `publisher.yaml` (`auto_post: false`).
   Typed loader in `tw/config.py` with a stable config hash (SPEC §11).
3. M1: `ingest/ratelimit.py` token bucket (injectable clock, testable without sleeping).
4. M1: parsers for ADSBx v2 `/v2` responses and readsb `globe_history` trace files → raw points.
5. M1: `normalize/`: hex-block + type-family filter, registry resolution for type-less hexes,
   `position_source` kept on every point, H3 r4/r6 (null when no position, never "on ground").
6. M1: `store/`: points + coverage written to `date=YYYY-MM-DD` Parquet partitions; reads touch one partition.
7. `tw load-history --path <dir|tar>` loads local dumps. Tests use synthetic fixtures only.

## Done
- All of the plan above, plus `tw stats --date` and a public repo with README, MIT license, CI.
- 21 tests pass on synthetic fixtures; ruff and mypy are clean.

## Next
- M1: downloader for adsb.lol `globe_history` releases (through `ratelimit`), then load 12 months
  and sanity-check per-family daily counts with `tw stats --date`.
- M2: track segmenter + airfield resolution (OurAirports CSV).

## Open questions
- The trace parser follows the readsb `trace_full` layout (row = `[dt, lat, lon, alt, gs, track,
  flags, vrate, details, source, ...]`). Fixtures are synthetic; **verify against a real dump**
  before trusting M1 counts.
- Region polygons in `regions.yaml` are coarse lat/lon boxes. Refine before any region is promoted.
- Traces spill past midnight, so day N's dump also writes into day N+1. Is that overlap
  duplicated by day N+1's own dump? Dedupe on (hex, ts) at read time or at load.
- The registry (hex → type) is global, so a later load can change which points an earlier day
  would keep. Store the registry as dated snapshots too, for SPEC §11?
- Worker host (VPS vs Pi) and X account name are still open (SPEC §15).
