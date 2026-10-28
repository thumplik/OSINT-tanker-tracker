"""Per-day theater aggregates and their baseline snapshot (SPEC §7, §11; M5c).

For day D, from sorties dated D - `lookback_days` .. D only (latest partition per sortie wins):

- `aggregates/ccmd_inventory`: aircraft whose latest fix (within `max_age_days`) is in each CCMD
  at the end of D, per (ccmd, family, type);
- `aggregates/ccmd_moves`: CCMD changes whose arriving fix is on D (an exit and an entry each);
- `aggregates/ccmd_transits`: trips out of the home CCMD resolved on D;
- `aggregates/ccmd_coverage`: aircraft-hours per CCMD (civil included), the coverage denominator.

Like the region aggregates, a day without points, sorties or coverage gets nothing: it is missing,
never a day of zeros. The snapshot `baselines/ccmd/as_of=D` is write-once and uses only days
before D's flow window, so a build-up is never absorbed into its own baseline.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa

from tw.baselines.build import AggregateCache
from tw.baselines.stats import mad, median
from tw.provenance import code_version, effective_config_hash
from tw.store.parquet import partition_dir, read_day, register_schema, write_partition
from tw.theater.ccmd import CcmdMapper
from tw.theater.flows import fixes, inventory, moves, transits

AGG_INVENTORY = "aggregates/ccmd_inventory"
AGG_MOVES = "aggregates/ccmd_moves"
AGG_TRANSITS = "aggregates/ccmd_transits"
AGG_CCMD_COVERAGE = "aggregates/ccmd_coverage"
BL_CCMD = "baselines/ccmd"
PART = "theater"
SERIES = ("entries", "exits", "transits")  # summed over the flow window
TS = pa.timestamp("us", tz="UTC")

INVENTORY_SCHEMA = register_schema(
    AGG_INVENTORY,
    pa.schema(
        [
            ("ccmd", pa.string()),
            ("family", pa.string()),
            ("type", pa.string()),
            ("aircraft", pa.int32()),
        ]
    ),
)
MOVES_SCHEMA = register_schema(
    AGG_MOVES,
    pa.schema(
        [
            ("hex", pa.string()),
            ("type", pa.string()),
            ("family", pa.string()),
            ("ts", TS),
            ("src", pa.string()),
            ("dst", pa.string()),
            ("quality", pa.string()),
            ("sortie_id", pa.string()),
            ("src_airfield", pa.string()),
            ("dst_airfield", pa.string()),
        ]
    ),
)
TRANSITS_SCHEMA = register_schema(
    AGG_TRANSITS,
    pa.schema(
        [
            ("hex", pa.string()),
            ("type", pa.string()),
            ("family", pa.string()),
            ("depart_ts", TS),
            ("arrive_ts", TS),
            ("resolved_ts", TS),
            ("dest", pa.string()),
            ("via", pa.list_(pa.string())),
            ("resolution", pa.string()),
            ("dest_airfield", pa.string()),
        ]
    ),
)
CCMD_COVERAGE_SCHEMA = register_schema(
    AGG_CCMD_COVERAGE, pa.schema([("ccmd", pa.string()), ("aircraft_hours", pa.int64())])
)
CCMD_BL_SCHEMA = register_schema(
    BL_CCMD,
    pa.schema(
        [
            ("ccmd", pa.string()),
            ("family", pa.string()),  # "" for coverage
            ("series", pa.string()),  # entries | exits | transits | inventory | coverage
            ("weekday", pa.int8()),  # coverage only; -1 otherwise
            ("median", pa.float64()),
            ("mad", pa.float64()),
            ("n", pa.int16()),  # complete blocks (flows) or days (inventory, coverage)
            ("config_hash", pa.string()),
            ("code_version", pa.string()),
        ]
    ),
    key="as_of",
)


def day_end(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(), tzinfo=UTC)


def lookback_sorties(roots: Sequence[Path], day: date, lookback_days: int) -> list[dict[str, Any]]:
    """Sorties dated day - lookback .. day, one row per sortie_id (the latest day wins). Each day
    is read from the first root that has it, so an official day beats its live copy."""
    by_id: dict[str, dict[str, Any]] = {}
    for back in range(lookback_days, -1, -1):
        d = day - timedelta(days=back)
        root = next((r for r in roots if partition_dir(r, "sorties", d).exists()), None)
        if root is None:
            continue
        for row in read_day(root, "sorties", d).to_pylist():
            by_id[row["sortie_id"]] = row
    return sorted(by_id.values(), key=lambda r: (r["start_ts"], r["hex"]))


def theater_day(
    sorties: Sequence[Mapping[str, Any]],
    day: date,
    mapper: CcmdMapper,
    cfg: Mapping[str, Any],
    home: str,
) -> dict[str, list[dict[str, Any]]]:
    """Pure: inventory at the end of `day`, moves on it, transits resolved on it, and transits
    still en route at its end."""
    th = cfg["theater"]
    end = day_end(day)
    start = end - timedelta(days=1)
    fx = [
        f
        for f in fixes(sorties, mapper, float(cfg["counting"]["min_sortie_minutes"]))
        if f.ts < end
    ]
    trips = transits(fx, home, timedelta(hours=float(th["dwell_hours"])), end)
    return {
        "inventory": inventory(fx, end, timedelta(days=float(th["max_age_days"]))),
        "moves": [m.__dict__ for m in moves(fx, start, end)],
        "transits": [
            {**t.__dict__, "via": list(t.via)}
            for t in trips
            if t.resolved_ts is not None and start <= t.resolved_ts < end
        ],
        "en_route": [
            {**t.__dict__, "via": list(t.via)} for t in trips if t.resolution == "en_route"
        ],
    }


def build_theater(
    day: date, data_dir: Path, mapper: CcmdMapper, cfg: Mapping[str, Any], home: str
) -> bool:
    """Write the four theater partitions for `day`; False (writing nothing) when the day is
    incomplete."""
    if not all(
        partition_dir(data_dir, ds, day).exists() for ds in ("points", "sorties", "coverage")
    ):
        return False
    sorties = lookback_sorties([data_dir], day, int(cfg["theater"]["lookback_days"]))
    out = theater_day(sorties, day, mapper, cfg, home)
    hours: dict[str, int] = dict.fromkeys(mapper.ids, 0)
    cov = read_day(data_dir, "coverage", day)
    for cell, n in zip(cov["h3_r4"].to_pylist(), cov["aircraft"].to_pylist(), strict=True):
        hours[mapper.cell(cell)] += int(n)
    write_partition(data_dir, AGG_INVENTORY, day, out["inventory"], INVENTORY_SCHEMA, PART)
    write_partition(data_dir, AGG_MOVES, day, out["moves"], MOVES_SCHEMA, PART)
    write_partition(data_dir, AGG_TRANSITS, day, out["transits"], TRANSITS_SCHEMA, PART)
    write_partition(
        data_dir,
        AGG_CCMD_COVERAGE,
        day,
        [{"ccmd": c, "aircraft_hours": n} for c, n in hours.items()],
        CCMD_COVERAGE_SCHEMA,
        PART,
    )
    return True


def daily_counts(cache: AggregateCache, day: date) -> dict[tuple[str, str, str], int] | None:
    """(ccmd, family, series) -> count on `day`, or None if the day is missing. Transits (trips
    from the home CCMD) are keyed by destination."""
    mv = cache.get(AGG_MOVES, day)
    tr = cache.get(AGG_TRANSITS, day)
    if mv is None or tr is None:
        return None
    out: dict[tuple[str, str, str], int] = defaultdict(int)
    for m in mv:
        out[(m["dst"], m["family"], "entries")] += 1
        out[(m["src"], m["family"], "exits")] += 1
    for t in tr:
        out[(t["dest"], t["family"], "transits")] += 1
    return out


def window_sums(
    cache: AggregateCache, last: date, days: int
) -> dict[tuple[str, str, str], int] | None:
    """Sums over the `days` days ending `last`; None unless every one of them has data (a
    missing day is never a day of zeros)."""
    total: dict[tuple[str, str, str], int] = defaultdict(int)
    for back in range(days):
        counts = daily_counts(cache, last - timedelta(days=back))
        if counts is None:
            return None
        for k, v in counts.items():
            total[k] += v
    return dict(total)


def inventory_totals(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], int]:
    """(ccmd, family) -> aircraft (types summed within a family only)."""
    out: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        out[(r["ccmd"], r["family"])] += int(r["aircraft"])
    return dict(out)


def snapshot_exists(data_dir: Path, as_of: date) -> bool:
    return partition_dir(data_dir, BL_CCMD, as_of).exists()


def latest_theater_snapshot(data_dir: Path, on_or_before: date) -> date | None:
    """The newest `baselines/ccmd` snapshot date <= `on_or_before`, or None."""
    base = data_dir / BL_CCMD
    if not base.exists():
        return None
    days = [
        date.fromisoformat(p.name[6:])
        for p in base.iterdir()
        if p.name.startswith("as_of=") and p.name[6:] <= on_or_before.isoformat()
    ]
    return max(days, default=None)


def build_theater_baseline(
    as_of: date,
    data_dir: Path,
    cache: AggregateCache,
    cfg: Mapping[str, Any],
    ccmds: Sequence[str],
    families: Sequence[str],
    overwrite: bool = False,
) -> bool:
    """Write `baselines/ccmd/as_of=D` (write-once unless `overwrite`). Flows: the median/MAD of
    `baseline_blocks` earlier non-overlapping windows that are complete. Inventory: daily values
    over the baseline window, leaving out the current flow window. Coverage: per weekday, as for
    regions. Returns False when no earlier day has data."""
    if not overwrite and snapshot_exists(data_dir, as_of):
        return True
    th = cfg["theater"]
    flow_days = int(th["flow_window_days"])
    window = int(cfg["baseline"]["window_days"])
    keys = [(c, f) for c in ccmds for f in families]

    flows: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for k in range(1, int(th["baseline_blocks"]) + 1):
        sums = window_sums(cache, as_of - timedelta(days=flow_days * k), flow_days)
        if sums is None:
            continue
        for c, f in keys:
            for s in SERIES:
                flows[(c, f, s)].append(sums.get((c, f, s), 0))

    inv: dict[tuple[str, str], list[float]] = defaultdict(list)
    cov: dict[tuple[str, int], list[float]] = defaultdict(list)
    present = 0
    for back in range(1, window + 1):
        d = as_of - timedelta(days=back)
        cov_rows = cache.get(AGG_CCMD_COVERAGE, d)
        if cov_rows is None:
            continue
        present += 1
        for r in cov_rows:
            cov[(r["ccmd"], d.weekday())].append(r["aircraft_hours"])
        if back < flow_days:
            continue
        totals = inventory_totals(cache.get(AGG_INVENTORY, d) or [])
        for key in keys:
            inv[key].append(totals.get(key, 0))
    if not present:
        return False

    cfg_hash, version = effective_config_hash(cfg), code_version()

    def row(
        ccmd: str, family: str, series: str, weekday: int, values: list[float]
    ) -> dict[str, Any]:
        return {
            "ccmd": ccmd,
            "family": family,
            "series": series,
            "weekday": weekday,
            "median": median(values),
            "mad": mad(values),
            "n": len(values),
            "config_hash": cfg_hash,
            "code_version": version,
        }

    rows = [row(c, f, s, -1, v) for (c, f, s), v in sorted(flows.items())]
    rows += [row(c, f, "inventory", -1, v) for (c, f), v in sorted(inv.items())]
    rows += [row(c, "", "coverage", wd, v) for (c, wd), v in sorted(cov.items())]
    write_partition(data_dir, BL_CCMD, as_of, rows, CCMD_BL_SCHEMA, "baseline")
    return True
