"""Baseline snapshots taken as of day D, from aggregate days before D only (SPEC §7, §11).

- `baselines/region_family/as_of=D/`: median and MAD of daily sortie counts per (region, family,
  weekday) over the trailing `baseline.window_days`, and how many days fed them.
- `baselines/region_coverage/as_of=D/`: the same for regional aircraft-hours.
- `baselines/airfield_family/as_of=D/`: distinct aircraft of each family that arrived at each
  airfield. These are D2's "seen before" counts, so they stop before D2's own window.

Days without aggregates (no data) are skipped, never counted as zero. A key that exists on a day
but had no sorties counts as 0, because aggregates carry every key.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa

from tw.baselines.aggregates import AGG_ARRIVALS, AGG_COVERAGE, AGG_REGION_FAMILY
from tw.baselines.stats import mad, median
from tw.config import load_yaml
from tw.provenance import code_version, effective_config_hash
from tw.store.parquet import partition_dir, read_day, register_schema, write_partition

BL_REGION_FAMILY = "baselines/region_family"
BL_COVERAGE = "baselines/region_coverage"
BL_AIRFIELD = "baselines/airfield_family"
PART = "baseline"

_STATS = [
    ("weekday", pa.int8()),
    ("median", pa.float64()),
    ("mad", pa.float64()),
    ("n_days", pa.int16()),
    ("config_hash", pa.string()),
    ("code_version", pa.string()),
]
REGION_FAMILY_BL_SCHEMA = register_schema(
    BL_REGION_FAMILY,
    pa.schema([("region", pa.string()), ("family", pa.string()), *_STATS]),
    key="as_of",
)
COVERAGE_BL_SCHEMA = register_schema(
    BL_COVERAGE, pa.schema([("region", pa.string()), *_STATS]), key="as_of"
)
AIRFIELD_BL_SCHEMA = register_schema(
    BL_AIRFIELD,
    pa.schema(
        [
            ("airfield", pa.string()),
            ("family", pa.string()),
            ("hexes", pa.int32()),
            ("config_hash", pa.string()),
            ("code_version", pa.string()),
        ]
    ),
    key="as_of",
)


class AggregateCache:
    """Reads each aggregate day once per process; a backtest builds many overlapping snapshots.

    With `ensure`, a day whose aggregates were never built is built on first use (it returns
    False when the day has no data at all)."""

    def __init__(self, data_dir: Path, ensure: Callable[[date], bool] | None = None) -> None:
        self.data_dir = data_dir
        self._ensure = ensure
        self._cache: dict[tuple[str, date], list[dict[str, Any]] | None] = {}

    def get(self, dataset: str, day: date) -> list[dict[str, Any]] | None:
        """Rows of one aggregate day, or None when that day has no aggregates (missing)."""
        key = (dataset, day)
        if key not in self._cache:
            exists = partition_dir(self.data_dir, dataset, day).exists()
            if not exists and self._ensure is not None:
                exists = self._ensure(day)
            rows = read_day(self.data_dir, dataset, day).to_pylist() if exists else None
            self._cache[key] = rows
        return self._cache[key]


def _stats_rows(
    samples: dict[tuple[Any, ...], list[float]], cfg_hash: str, version: str
) -> list[dict[str, Any]]:
    rows = []
    for key, values in sorted(samples.items()):
        *ids, weekday = key
        rows.append(
            {
                "ids": ids,
                "weekday": weekday,
                "median": median(values),
                "mad": mad(values),
                "n_days": len(values),
                "config_hash": cfg_hash,
                "code_version": version,
            }
        )
    return rows


def snapshot_exists(data_dir: Path, as_of: date) -> bool:
    return partition_dir(data_dir, BL_REGION_FAMILY, as_of).exists()


def latest_snapshot(data_dir: Path, on_or_before: date) -> date | None:
    """The newest snapshot date <= `on_or_before`, or None."""
    base = data_dir / BL_REGION_FAMILY
    if not base.exists():
        return None
    days = [
        date.fromisoformat(p.name[6:])
        for p in base.iterdir()
        if p.name.startswith("as_of=") and p.name[6:] <= on_or_before.isoformat()
    ]
    return max(days, default=None)


def build_baseline(
    as_of: date,
    data_dir: Path,
    cache: AggregateCache | None = None,
    cfg: Mapping[str, Any] | None = None,
    overwrite: bool = False,
) -> bool:
    """Write the three snapshots for `as_of`. Returns False if no prior day has aggregates.

    Snapshots are write-once (SPEC §11): an existing snapshot is kept, so replaying a past day
    after older data was backfilled still judges it by the baseline it had. `overwrite`
    rebuilds deliberately (e.g. after a config change)."""
    if not overwrite and snapshot_exists(data_dir, as_of):
        return True
    cfg = cfg if cfg is not None else load_yaml("detectors.yaml")
    window = int(cfg["baseline"]["window_days"])
    d2_days = int(cfg["d2_novel_destination"]["window_hours"]) // 24
    cache = cache or AggregateCache(data_dir)
    cfg_hash, version = effective_config_hash(cfg), code_version()
    days = [as_of - timedelta(days=n) for n in range(window, 0, -1)]

    counts: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    coverage: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    present = 0
    for day in days:
        rf = cache.get(AGG_REGION_FAMILY, day)
        if rf is None:
            continue
        present += 1
        for r in rf:
            counts[(r["region"], r["family"], day.weekday())].append(r["sorties"])
        for r in cache.get(AGG_COVERAGE, day) or []:
            coverage[(r["region"], day.weekday())].append(r["aircraft_hours"])
        if day <= as_of - timedelta(days=d2_days):  # D2 window is as_of-(d2_days-1)..as_of
            for r in cache.get(AGG_ARRIVALS, day) or []:
                seen[(r["airfield"], r["family"])].add(r["hex"])
    if not present:
        return False

    rf_rows = [
        {"region": r["ids"][0], "family": r["ids"][1], **{k: v for k, v in r.items() if k != "ids"}}
        for r in _stats_rows(counts, cfg_hash, version)
    ]
    cov_rows = [
        {"region": r["ids"][0], **{k: v for k, v in r.items() if k != "ids"}}
        for r in _stats_rows(coverage, cfg_hash, version)
    ]
    af_rows = [
        {
            "airfield": a,
            "family": f,
            "hexes": len(h),
            "config_hash": cfg_hash,
            "code_version": version,
        }
        for (a, f), h in sorted(seen.items())
    ]
    write_partition(data_dir, BL_REGION_FAMILY, as_of, rf_rows, REGION_FAMILY_BL_SCHEMA, PART)
    write_partition(data_dir, BL_COVERAGE, as_of, cov_rows, COVERAGE_BL_SCHEMA, PART)
    write_partition(data_dir, BL_AIRFIELD, as_of, af_rows, AIRFIELD_BL_SCHEMA, PART)
    return True
