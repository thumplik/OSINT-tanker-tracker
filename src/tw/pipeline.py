"""One day through the pipeline: aggregates → baseline snapshot as of the day → D1, D2 → guards.

The same functions serve live and history (CLAUDE.md rule 9): "live" is just day = today. Days
are evaluated as whole UTC days for now; the M4 worker adds rolling windows. Nothing here writes
alerts or posts. Replays and backtests are retrospective (SPEC §11).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from tw.baselines.aggregates import (
    AGG_ARRIVALS,
    AGG_COVERAGE,
    AGG_REGION_FAMILY,
    RegionMapper,
    build_aggregates,
)
from tw.baselines.build import (
    BL_AIRFIELD,
    BL_COVERAGE,
    BL_REGION_FAMILY,
    AggregateCache,
    build_baseline,
)
from tw.config import Fleet, load_fleet, load_regions, load_yaml
from tw.detectors.d1_volume_surge import detect_d1
from tw.detectors.d2_novel_destination import detect_d2
from tw.detectors.guards import apply_cooldown, apply_coverage_guard, apply_known_events
from tw.detectors.types import Alert, Arrival, KeyBaseline, RegionCount
from tw.store.parquet import read_day


@dataclass
class Context:
    data_dir: Path
    cfg: dict[str, Any] = field(default_factory=lambda: load_yaml("detectors.yaml"))
    events: list[dict[str, Any]] = field(
        default_factory=lambda: list(load_yaml("known_events.yaml").get("events") or [])
    )
    fleet: Fleet = field(default_factory=load_fleet)
    mapper: RegionMapper = field(default_factory=lambda: RegionMapper(load_regions()))
    cache: AggregateCache | None = None

    def __post_init__(self) -> None:
        if self.cache is None:
            self.cache = AggregateCache(
                self.data_dir,
                ensure=lambda d: build_aggregates(d, self.data_dir, self.fleet, self.mapper),
            )


@dataclass(frozen=True)
class DayInputs:
    """Everything the detectors see for one day; None when the day has no data."""

    day: date
    counts: list[RegionCount]
    coverage: dict[str, float]
    arrivals: list[Arrival]
    baseline: dict[tuple[str, str], KeyBaseline]
    coverage_baseline: dict[str, KeyBaseline]
    airfield_baseline: dict[tuple[str, str], int]
    history_days: int


def prepare_day(day: date, ctx: Context) -> DayInputs | None:
    """Build aggregates for `day` and its baseline snapshot, then load the detector inputs."""
    if not build_aggregates(day, ctx.data_dir, ctx.fleet, ctx.mapper):
        return None
    assert ctx.cache is not None
    build_baseline(day, ctx.data_dir, ctx.cache)
    weekday = day.weekday()
    rf_bl = read_day(ctx.data_dir, BL_REGION_FAMILY, day).to_pylist()
    baseline = {
        (r["region"], r["family"]): KeyBaseline(r["median"], r["mad"], r["n_days"])
        for r in rf_bl
        if r["weekday"] == weekday
    }
    present: dict[tuple[str, str], int] = {}
    for r in rf_bl:
        key = (r["region"], r["family"])
        present[key] = present.get(key, 0) + r["n_days"]
    coverage_baseline = {
        r["region"]: KeyBaseline(r["median"], r["mad"], r["n_days"])
        for r in read_day(ctx.data_dir, BL_COVERAGE, day).to_pylist()
        if r["weekday"] == weekday
    }
    airfield_baseline = {
        (r["airfield"], r["family"]): r["hexes"]
        for r in read_day(ctx.data_dir, BL_AIRFIELD, day).to_pylist()
    }
    counts = [
        RegionCount(r["region"], r["family"], r["sorties"], tuple(r["sortie_ids"]))
        for r in read_day(ctx.data_dir, AGG_REGION_FAMILY, day).to_pylist()
    ]
    coverage = {
        r["region"]: float(r["aircraft_hours"])
        for r in read_day(ctx.data_dir, AGG_COVERAGE, day).to_pylist()
    }
    d2_days = int(ctx.cfg["d2_novel_destination"]["window_hours"]) // 24
    arrivals = []
    for back in range(d2_days):
        for r in ctx.cache.get(AGG_ARRIVALS, day - timedelta(days=back)) or []:
            arrivals.append(
                Arrival(r["airfield"], r["region"], r["family"], r["hex"], r["sortie_id"])
            )
    return DayInputs(
        day=day,
        counts=counts,
        coverage=coverage,
        arrivals=arrivals,
        baseline=baseline,
        coverage_baseline=coverage_baseline,
        airfield_baseline=airfield_baseline,
        history_days=max(present.values(), default=0),
    )


def detect_day(
    inputs: DayInputs,
    cfg: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    recent: Sequence[Alert] = (),
) -> list[Alert]:
    """Pure: the same inputs and config always give the same alerts."""
    alerts = detect_d1(
        inputs.day,
        inputs.counts,
        inputs.baseline,
        inputs.coverage,
        inputs.coverage_baseline,
        cfg,
    ) + detect_d2(inputs.day, inputs.arrivals, inputs.airfield_baseline, inputs.history_days, cfg)
    alerts = apply_coverage_guard(alerts, inputs.coverage, inputs.coverage_baseline, cfg)
    alerts = apply_known_events(alerts, events)
    return apply_cooldown(alerts, recent, cfg)


def run_days(days: Sequence[date], ctx: Context) -> tuple[list[Alert], list[date], list[DayInputs]]:
    """(alerts, days without data, inputs) over `days` in order, with cooldown across days."""
    alerts: list[Alert] = []
    missing: list[date] = []
    prepared: list[DayInputs] = []
    for day in sorted(days):
        inputs = prepare_day(day, ctx)
        if inputs is None:
            missing.append(day)
            continue
        prepared.append(inputs)
        alerts += detect_day(inputs, ctx.cfg, ctx.events, recent=alerts[-200:])
    return alerts, missing, prepared
