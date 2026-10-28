# Handoff

_Last updated: 2026-10-27_

## Current milestone: M4 next (live ingest worker). M0–M3 done.

## Data (M1–M2)
- History 2025-10-27..2026-10-26: 360 days, 65M points, 2.1 GB.
  - No prod release: 2025-12-25, 2026-05-05..07, 2026-06-11.
  - KC-46A is typed `B762` (106 hexes vs ~105 delivered).
- Sorties: 93,684.
  - High coverage gaps are bridged.
  - `sorties/date=D/` comes from D−1 and D only.
  - Airfields resolve within 15 km / 4,000 ft. Ends seen low resolve 90.5% (origin) and 90.4% (dest).
  - 32% of ends are coverage edges, first/last seen at altitude.
- C30J counts as **tactical airlift only** (owner, Oct 2026): ADS-B can't tell KC-130J tankers from
  C-130J-30s. Tanker = KC-135 + KC-46.

## M3 (done 2026-10-27)
- `baselines/aggregates.py`: `aggregates/*/date=D/`.
  - A sortie counts in every region it flies in. Sorties under 30 min don't count.
  - Missing days are absent.
- `baselines/build.py`: `baselines/*/as_of=D/`, from days D−56..D−1. D2's "seen before" stops
  before its 72 h window.
- `detectors/` (pure, mypy strict):
  - D1 volume surge.
  - D2 novel destination: distinct aircraft; daily, so an event can repeat for 3 days (owner kept this).
  - Guards: coverage outside 60–167% of the weekday median suppresses (upper bound added by the
    owner). Known events downgrade. 12 h cooldown.
- `pipeline.py` (`prepare_day`, `detect_day`, `run_days`), `backtest.py`, `tw replay`, `tw backtest`.
  The golden file is `tests/golden/pipeline_surge.json`.
- Codex adversarial review (PR #1): 14 findings. 12 fixed with regression tests
  (`tests/detectors/test_review_findings.py`, `tests/unit/test_pipeline.py`). #9 (retrospective
  parquet export) is by design and now labeled as such; #10 is hardened. Changes:
  - write-once snapshots;
  - complete-day inputs only;
  - one config through the pipeline;
  - per-day coverage guard;
  - cooldown within a run;
  - provenance on alerts and baselines.
- Backtest (2026-04-28..10-26, `reports/backtest_2026-10-26.md`):
  - D1 6.2/week (3.3 medium).
  - D2 13.3/week.
  - Suppressed: 306 by cooldown (same region, same day), 45 by coverage.
- Biggest signal: Gulf tankers (KC-135/KC-46) 2026-06-22..07-03, ~30/day vs a baseline of 0. It
  was suppressed: regional traffic was 4.9–7.2× normal. Receivers, or real? Worth labeling.

## Next
- Owner: start the truth set (`eval/truth_set.yaml`, 20–40 periods). The Gulf June days and the
  Eastern Med C-17 day (2026-08-18, 16 vs 3.5) are candidates. Precision and recall show up in the
  backtest once periods exist.
- D2 is noisy (~25/week, mostly CONUS civil/ANG fields). Revisit with a sweep on
  `min_arrivals` / `max_baseline_arrivals` once labels exist.
- M4: live worker (rolling 24 h windows over the same `pipeline` functions, rate-limited providers).
  Start in plan mode.

## Open questions
- The Codex review was run by the owner with a prompt from the session (in PR #1's thread).
  Consider committing that prompt as a script so the CLAUDE.md step is repeatable.
- Parallel backfill is order-dependent for untyped hexes. Measure before re-running sequentially.
- Region polygons in `regions.yaml` are coarse boxes. Refine before promoting any region.
- Worker host (VPS vs Pi) and X account name are still open (SPEC §15).
