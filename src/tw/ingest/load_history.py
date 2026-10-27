"""Load local `globe_history` dumps into date-partitioned points + coverage (milestone M1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tw.config import Fleet, load_yaml
from tw.ingest.globe_history import iter_points
from tw.normalize.coverage import CoverageAccumulator
from tw.normalize.points import build_registry, normalize
from tw.store.parquet import load_registry, save_registry, write_coverage, write_points


@dataclass(frozen=True)
class LoadSummary:
    raw_points: int
    military_points: int
    kept_points: int
    points_per_day: dict[date, int]


def load_dump(source: Path, data_dir: Path, fleet: Fleet, part: str | None = None) -> LoadSummary:
    """Parse one dump (directory or tar), write points and coverage partitions.

    Re-loading the same dump replaces its part files, so loads are idempotent.
    """
    providers = load_yaml("providers.yaml")
    coverage = CoverageAccumulator(sample_seconds=float(providers["coverage_sample_seconds"]))
    candidates = []
    raw_count = 0
    # One pass over the dump: coverage sees every aircraft; only military-block points are kept.
    for p in iter_points(source):
        raw_count += 1
        coverage.add((p,))
        if not p.is_non_icao and fleet.in_military_block(p.hex):
            candidates.append(p)

    registry = build_registry(candidates, prior=load_registry(data_dir))
    save_registry(data_dir, registry)
    points = list(normalize(candidates, fleet, registry))

    part_name = part or source.name.split(".")[0]
    per_day = write_points(data_dir, points, part=part_name)
    write_coverage(data_dir, coverage.rows(), part=part_name)
    return LoadSummary(
        raw_points=raw_count,
        military_points=len(candidates),
        kept_points=len(points),
        points_per_day=per_day,
    )
