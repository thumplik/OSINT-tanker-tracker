"""Write and read `<dataset>/date=YYYY-MM-DD/part-*.parquet` partitions."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from tw.normalize.points import Point

POINT_SCHEMA = pa.schema(
    [
        ("ts", pa.timestamp("us", tz="UTC")),
        ("hex", pa.string()),
        ("callsign", pa.string()),
        ("type", pa.string()),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        ("alt_baro", pa.int32()),
        ("gs", pa.float64()),
        ("track", pa.float64()),
        ("on_ground", pa.bool_()),
        ("position_source", pa.string()),
        ("provider", pa.string()),
        ("h3_r4", pa.string()),
        ("h3_r6", pa.string()),
    ]
)

COVERAGE_SCHEMA = pa.schema(
    [
        ("hour", pa.timestamp("us", tz="UTC")),
        ("h3_r4", pa.string()),
        ("aircraft", pa.int32()),
    ]
)


def partition_dir(root: Path, dataset: str, day: date) -> Path:
    return root / dataset / f"date={day.isoformat()}"


def write_partition(
    root: Path,
    dataset: str,
    day: date,
    rows: Sequence[Mapping[str, Any]],
    schema: pa.Schema,
    part: str,
) -> Path:
    """Write one part file atomically, replacing any earlier file with the same part name."""
    out_dir = partition_dir(root, dataset, day)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"part-{part}.parquet"
    tmp = out.with_suffix(".parquet.tmp")
    table = pa.Table.from_pylist(list(rows), schema=schema)
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(out)
    return out


def write_points(root: Path, points: Iterable[Point], part: str) -> dict[date, int]:
    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for p in points:
        by_day[p.ts.date()].append(asdict(p))
    for day, rows in by_day.items():
        rows.sort(key=lambda r: (r["hex"], r["ts"]))
        write_partition(root, "points", day, rows, POINT_SCHEMA, part)
    return {day: len(rows) for day, rows in sorted(by_day.items())}


def write_coverage(root: Path, rows: Iterable[Mapping[str, Any]], part: str) -> None:
    by_day: dict[date, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_day[r["hour"].date()].append(r)
    for day, day_rows in by_day.items():
        write_partition(root, "coverage", day, day_rows, COVERAGE_SCHEMA, part)


def read_day(root: Path, dataset: str, day: date) -> pa.Table:
    """Read exactly one date partition."""
    files = sorted(partition_dir(root, dataset, day).glob("part-*.parquet"))
    if not files:
        schema = POINT_SCHEMA if dataset == "points" else COVERAGE_SCHEMA
        return schema.empty_table()
    con = duckdb.connect()
    try:
        return con.sql(
            "SELECT * FROM read_parquet($files, union_by_name = true)",
            params={"files": [str(f) for f in files]},
        ).to_arrow_table()
    finally:
        con.close()


REGISTRY_SCHEMA = pa.schema([("hex", pa.string()), ("type", pa.string())])


def load_registry(root: Path, as_of: date) -> dict[str, str]:
    """hex → type from registry partitions dated on or before `as_of` (latest date wins).

    Registry partitions are tiny (hundreds of rows), so reading all past ones is cheap. Only
    days up to `as_of` are used, so a later load never changes how an earlier day resolves.
    """
    base = root / "registry"
    if not base.exists():
        return {}
    days = sorted(
        d for d in base.iterdir() if d.name.startswith("date=") and d.name[5:] <= as_of.isoformat()
    )
    registry: dict[str, str] = {}
    for day_dir in days:
        for f in sorted(day_dir.glob("part-*.parquet")):
            table = pq.read_table(f)
            registry.update(zip(table["hex"].to_pylist(), table["type"].to_pylist(), strict=True))
    return registry


def save_registry(root: Path, day: date, observed: Mapping[str, str], part: str) -> None:
    """Store the hex → type pairs observed in one day's dump."""
    rows = [{"hex": h, "type": t} for h, t in sorted(observed.items())]
    write_partition(root, "registry", day, rows, REGISTRY_SCHEMA, part)
