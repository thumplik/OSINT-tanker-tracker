"""Per-day aggregates: the inputs to baselines and detectors (SPEC §7, §11).

Built from `sorties/date=D/` and `coverage/date=D/` only:

- `aggregates/region_family`: sortie count and distinct aircraft per (region, family), one row for
  every key, zeros included. A sortie counts once in each region it flies in (any of its H3 r4
  cells). Sorties shorter than `counting.min_sortie_minutes` don't count.
- `aggregates/region_coverage`: total aircraft-hours per region (civil included), the
  denominator that separates real surges from receiver changes.
- `aggregates/arrivals`: closed sorties with a destination airfield, one row per family.

A day without a points partition gets no aggregates: it is missing, never a day of zeros.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import h3
import pyarrow as pa

from tw.config import Fleet, Regions, load_fleet, load_regions, load_yaml
from tw.store.parquet import partition_dir, read_day, register_schema, write_partition

AGG_REGION_FAMILY = "aggregates/region_family"
AGG_COVERAGE = "aggregates/region_coverage"
AGG_ARRIVALS = "aggregates/arrivals"
PART = "aggregates"

REGION_FAMILY_SCHEMA = register_schema(
    AGG_REGION_FAMILY,
    pa.schema(
        [
            ("region", pa.string()),
            ("family", pa.string()),
            ("sorties", pa.int32()),
            ("hexes", pa.int32()),
            ("sortie_ids", pa.list_(pa.string())),
        ]
    ),
)
COVERAGE_AGG_SCHEMA = register_schema(
    AGG_COVERAGE, pa.schema([("region", pa.string()), ("aircraft_hours", pa.int64())])
)
ARRIVALS_SCHEMA = register_schema(
    AGG_ARRIVALS,
    pa.schema(
        [
            ("airfield", pa.string()),
            ("region", pa.string()),
            ("family", pa.string()),
            ("sortie_id", pa.string()),
            ("hex", pa.string()),
            ("end_ts", pa.timestamp("us", tz="UTC")),
        ]
    ),
)


class RegionMapper:
    """H3 r4 cell → region id: the first region whose boxes hold the cell centre, else the
    catch-all last region ("other"). Cached, since a day touches a few thousand cells."""

    def __init__(self, regions: Regions) -> None:
        self._regions = regions.regions
        self._resolution = regions.h3_resolution
        self._cache: dict[str, str] = {}

    @property
    def ids(self) -> list[str]:
        return [r.id for r in self._regions]

    def region(self, cell: str) -> str:
        hit = self._cache.get(cell)
        if hit is None:
            lat, lon = h3.cell_to_latlng(cell)
            hit = next((r.id for r in self._regions if r.contains(lat, lon)), self._regions[-1].id)
            self._cache[cell] = hit
        return hit

    def region_at(self, lat: float, lon: float) -> str:
        return self.region(h3.latlng_to_cell(lat, lon, self._resolution))


def build_aggregates(
    day: date,
    data_dir: Path,
    fleet: Fleet | None = None,
    mapper: RegionMapper | None = None,
    min_sortie_minutes: float | None = None,
) -> bool:
    """Write the three aggregate partitions for `day`. Returns False (writing nothing) when the
    day has no points partition."""
    if not partition_dir(data_dir, "points", day).exists():
        return False
    fleet = fleet or load_fleet()
    mapper = mapper or RegionMapper(load_regions())
    if min_sortie_minutes is None:
        min_sortie_minutes = float(load_yaml("detectors.yaml")["counting"]["min_sortie_minutes"])

    ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    hexes: dict[tuple[str, str], set[str]] = defaultdict(set)
    arrivals: list[dict[str, Any]] = []
    for s in read_day(data_dir, "sorties", day).to_pylist():
        if (s["end_ts"] - s["start_ts"]).total_seconds() < min_sortie_minutes * 60:
            continue
        regions = sorted({mapper.region(c) for c in s["h3_cells"]})
        for family in s["families"]:
            for region in regions:
                ids[(region, family)].append(s["sortie_id"])
                hexes[(region, family)].add(s["hex"])
            if s["dest_icao"] and not s["is_open"]:
                arrivals.append(
                    {
                        "airfield": s["dest_icao"],
                        "region": mapper.region_at(s["end_lat"], s["end_lon"]),
                        "family": family,
                        "sortie_id": s["sortie_id"],
                        "hex": s["hex"],
                        "end_ts": s["end_ts"],
                    }
                )
    region_family = [
        {
            "region": region,
            "family": family,
            "sorties": len(ids[(region, family)]),
            "hexes": len(hexes[(region, family)]),
            "sortie_ids": sorted(ids[(region, family)]),
        }
        for region in mapper.ids
        for family in sorted(fleet.families)
    ]

    coverage: dict[str, int] = dict.fromkeys(mapper.ids, 0)
    cov = read_day(data_dir, "coverage", day)
    for cell_id, aircraft in zip(
        cov["h3_r4"].to_pylist(), cov["aircraft"].to_pylist(), strict=True
    ):
        coverage[mapper.region(cell_id)] += int(aircraft)

    arrivals.sort(key=lambda r: (r["airfield"], r["family"], r["sortie_id"]))
    write_partition(data_dir, AGG_REGION_FAMILY, day, region_family, REGION_FAMILY_SCHEMA, PART)
    write_partition(
        data_dir,
        AGG_COVERAGE,
        day,
        [{"region": r, "aircraft_hours": n} for r, n in coverage.items()],
        COVERAGE_AGG_SCHEMA,
        PART,
    )
    write_partition(data_dir, AGG_ARRIVALS, day, arrivals, ARRIVALS_SCHEMA, PART)
    return True
