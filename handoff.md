# Handoff

_Last updated: 2026-10-27_

## Current milestone: M3 next (baselines, D1, D2, coverage guard). M0–M2 done.

## M1
- 12-month backfill (2025-10-27..2026-10-26): 360 days, 65M points, 1,209 aircraft, 2.1 GB.
  No prod release: 2025-12-25, 2026-05-05..07, 2026-06-11. 2026-04-30 is a thin source day
  (coverage 44% of median): a test case for the coverage guard.
- KC-46A is typed `B762` by adsb.lol (106 hexes in a year vs ~105 delivered); in the tanker family.
- Releases load only rows inside their own UTC day; corrupt trace files are skipped and listed.

## M2
- `tracks/segment.py` (pure), `tracks/sorties.py`, `tracks/airfields.py`. The rules are in SPEC §5,
  and the thresholds in `detectors.yaml` → `sortie:`.
  - High coverage gaps are bridged (owner decision).
  - `sorties/date=D/` is built from D−1 and D only. A sortie open at midnight is closed by the
    next day under the same id.
- Airfields: OurAirports, runway ≥ 3,000 ft, 16,350 fields in `config/airfields.csv` (committed).
- Resolution thresholds: 15 km / 4,000 ft, chosen by the owner from a sweep on 7 Sept days.
  The sweep script was in the session scratchpad. Results (both-observable column):

  | radius / AGL | 3,000 ft | 4,000 ft | 5,000 ft |
  |---|---|---|---|
  | 10 km | 71.6% | 77.7% | 84.3% |
  | 15 km | 77.3% | 85.5% | 93.5% |
  | 20 km | 79.1% | 88.4% | 96.9% |
- Done target changed (owner): ≥ 90% of ends *seen low* resolve. Full year: origin 90.5%,
  destination 90.4%.
  - 32% of ends are first/last seen at altitude (coverage edges); only 44% of all sorties have
    both ends.
  - Results: 93,684 sorties, 16 MB, 107 s to build.
- MLAT-heavy sorties resolve worse (85%/86% vs ADS-B 94%). Altus (training patterns) is the
  top unresolved cluster.
- Spot checks look right. C-17s staging via Dublin/Shannon → Ramstein were confirmed on the
  ground at EIDW. KC-46 McConnell → Rickenbacker; local KIAB refueling sorties.

## Next (M3)
- Nightly baselines (median/MAD per region × family × weekday, 56-day window), stored as dated
  snapshots `baselines/as_of=D/`. Then D1 volume surge, D2 novel destination, and the coverage
  guard. Start in plan mode.
- Counting sorties per day = rows in `sorties/date=D/` (includes open). Windows dedupe by
  `sortie_id` (`tracks.sorties.latest_sorties`).

## Open questions
- 20% of closed sorties are < 30 min: pattern work and fragments. Decide whether D1 counts them
  (maybe a minimum duration in `detectors.yaml`).
- Parallel backfill is order-dependent for untyped hexes (D loaded before D−1 can't use D−1's
  registry). Measure impact before re-running sequentially.
- Region polygons in `regions.yaml` are coarse boxes. Refine before promoting any region.
- Worker host (VPS vs Pi) and X account name are still open (SPEC §15).
