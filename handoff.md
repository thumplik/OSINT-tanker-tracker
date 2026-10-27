# Handoff

_Last updated: 2026-10-27_

## Current milestone: M2 (sortie segmenter + airfield resolution)

## M1 done
- 12-month backfill (2025-10-27..2026-10-26, ~11 h, 2.1 GB): 360 days, 65M points, 1,209
  aircraft. Monthly mean distinct tankers/day 100–152. No prod release: 2025-12-25,
  2026-05-05..07, 2026-06-11. 2026-04-30 is a thin source day (coverage 44% of median).
- KC-46A is typed `B762` by adsb.lol: 106 hexes in a year vs ~105 delivered. In the tanker family.
- Releases load only rows inside their own UTC day (2025 releases carry pre-midnight rows and
  leftover traces). Corrupt trace files are skipped and listed (≤ 50 per day; three days had
  one each, in the source). Heatmap skip, `--workers N`, per-day registry (SPEC §11).

## M2 plan (approved 2026-10-27)
- Sortie rules, thresholds in `detectors.yaml` → `sortie:`:
  - Starts at the first airborne point.
  - Landing = ground run ≥ 60 s.
  - A gap > 30 min ends the sortie unless both sides are ≥ 10,000 ft, the gap is ≤ 6 h and the
    implied speed is ≤ 600 kt (owner chose bridging: 397 high gaps in 3 Sept days).
- Origin/destination: nearest airfield ≤ 10 km while AGL < 3,000 ft, including the ground point
  next to takeoff/landing. Null if first/last seen high.
- `sorties/date=D/` = sorties with a point in D, built from points of D−1 and D only.
  Airborne at end of D → `is_open`, closed by D+1 under the same `sortie_id`.
- Schema: `type` + `families[]` replace `type_family`; orbit fields null until M6.
- Airfields: `tw airfields update` pulls OurAirports (runway ≥ 3,000 ft) → `config/airfields.csv`,
  indexed by H3 r5.
- CLI: `build-sorties`, `sortie-stats` (resolution % + top unresolved cells).
- Done when ≥ 90% of closed sorties in 2026-09-01..28 have both ends; then build the full year.

## Open questions
- Parallel backfill is order-dependent for untyped hexes (day D loaded before D−1 can't use
  D−1's registry). Measure impact before re-running sequentially.
- Region polygons in `regions.yaml` are coarse boxes. Refine before promoting any region.
- Worker host (VPS vs Pi) and X account name are still open (SPEC §15).
