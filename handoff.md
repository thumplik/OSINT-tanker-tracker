# Handoff

_Last updated: 2026-10-26_

## Current milestone: M1 (history loader, normalize, storage)

## Done
- M0: scaffold, config, CI, public repo (thumplik/OSINT-tanker-tracker).
- M1 core: parsers, fleet filter + registry, H3 cells, coverage counts, date-partitioned Parquet.
- Parser checked against a real dump (`v2026.10.25-planes-readsb-prod-0`, readsb 3.16.15).
  Confirmed: 14-field rows, gzip members, `details.flight` callsigns, `~` non-ICAO traces,
  `dt` always in [0, 86400) (a dump is exactly one UTC day, no midnight spill).
  Fixed: `flags & 8` means the altitude is geometric, so it no longer lands in `alt_baro`.
  Six real trace files are now regression fixtures (`tests/fixtures/real_2026-10-25/`, ODbL).
- Downloader (`ingest/history_download.py`): streams `<tag>.tar.aa`, `.ab`, ... through the rate
  limiter straight into the loader. It tries the day's year repo, then the next year's (releases
  around New Year live there), with suffixes `-0` and then `-0tmp`. Prod pod only (SPEC §2).
  Retries a failed stream; nothing is written until a whole day reads cleanly.
- `tw load-history --days N | --from D [--to D] | --path P [--skip-existing]`.
- Full real day 2026-10-25 streamed + loaded in 3 min (67,333 traces → 106,676 points, 4.3 MB):
  C17 43, C30J 36, K35R 21, C5M 9, C130 1 aircraft. K35R/C5M are ~91–96% MLAT and K35R had
  no callsigns. Dumps only hold positioned rows, so Mode-S-only aircraft never appear.

## Next
- Backfill 12 months (about 1.3 TB streamed; run on the worker box, not a laptop) and review
  `tw stats` per-family counts across weeks. Then M1 is done.
- Possible speedup: skip the ~800 MB heatmap prefix of each tar with range requests.
- M2: track segmenter + airfield resolution (OurAirports CSV). Trace flag bit 2 marks the start
  of a new leg in readsb; consider carrying it on points for the segmenter (SPEC §5 change).

## Open questions
- **KC-46 type code.** No `KC46` appeared on 2026-10-25, but 12 military-block hexes are typed
  `B762`, very likely KC-46A. Confirm (e.g. from registry `desc`) and add B762 to the tanker
  family in `fleet.yaml`, which would change tanker counts. Waiting on owner.
- The registry (hex → type) is global, so a later load can change which points an earlier day
  keeps. Store dated registry snapshots for SPEC §11?
- About 16% of tracked-type traces in the sample had no `t`; the registry fills them only
  after that hex has been seen with a type on some day.
- Prod is missing some days (e.g. 353 prod releases for 2025). Staging could fill gaps, but
  mixing pods is untested. Keep prod-only unless gaps hurt baselines.
- Region polygons in `regions.yaml` are coarse boxes. Refine before promoting any region.
- Worker host (VPS vs Pi) and X account name are still open (SPEC §15).
