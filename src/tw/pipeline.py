"""One day through the pipeline: aggregates → baseline snapshot as of the day → D1, D2 → guards.

The same functions serve live and history (CLAUDE.md rule 9): "live" is just day = today. Days
are evaluated as whole UTC days for now; the M4 worker adds rolling windows. Nothing here writes
alerts or posts. Replays and backtests are retrospective (SPEC §11).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
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
from tw.provenance import code_version, effective_config_hash
from tw.store.parquet import read_day


@dataclass
class Context:
    """One run's settings. `cfg` (detectors.yaml unless given) is the single configuration used
    by aggregates, baselines and detectors alike."""

    data_dir: Path
    cfg: dict[str, Any] = field(default_factory=lambda: load_yaml("detectors.yaml"))
    events: list[dict[str, Any]] = field(
        default_factory=lambda: list(load_yaml("known_events.yaml").get("events") or [])
    )
    fleet: Fleet = field(default_factory=load_fleet)
    mapper: RegionMapper = field(default_factory=lambda: RegionMapper(load_regions()))
    rebuild_baselines: bool = False  # snapshots are write-once unless asked (SPEC §11)
    cache: AggregateCache | None = None

    def __post_init__(self) -> None:
        if self.cache is None:
            self.cache = AggregateCache(self.data_dir, ensure=self.build_aggregates)

    def build_aggregates(self, day: date) -> bool:
        minutes = float(self.cfg["counting"]["min_sortie_minutes"])
        return build_aggregates(day, self.data_dir, self.fleet, self.mapper, minutes)

    @property
    def provenance(self) -> tuple[str, str]:
        return effective_config_hash(self.cfg), code_version()


@dataclass(frozen=True)
class DayInputs:
    """Everything the detectors see for one day."""

    day: date
    counts: list[RegionCount]
    coverage: dict[str, float]
    arrivals: list[Arrival]
    baseline: dict[tuple[str, str], KeyBaseline]  # this weekday
    coverage_baseline: dict[str, KeyBaseline]  # this weekday
    airfield_baseline: dict[tuple[str, str], int]
    d2_history_days: int  # days with data in D2's own pre-window baseline interval
    coverage_by_day: dict[date, dict[str, float]]  # every day of D2's window, for the guard
    coverage_baseline_all: dict[tuple[str, int], KeyBaseline]  # (region, weekday)
    baseline_config_hash: str  # what the (write-once) snapshot was built with


def prepare_day(day: date, ctx: Context) -> DayInputs | None:
    """Build `day`'s aggregates and (if absent) its baseline snapshot, then load the detector
    inputs. None when the day has no complete data."""
    if not ctx.build_aggregates(day):
        return None
    assert ctx.cache is not None
    build_baseline(day, ctx.data_dir, ctx.cache, ctx.cfg, overwrite=ctx.rebuild_baselines)
    weekday = day.weekday()
    rf_bl = read_day(ctx.data_dir, BL_REGION_FAMILY, day).to_pylist()
    baseline = {
        (r["region"], r["family"]): KeyBaseline(r["median"], r["mad"], r["n_days"])
        for r in rf_bl
        if r["weekday"] == weekday
    }
    coverage_baseline_all = {
        (r["region"], r["weekday"]): KeyBaseline(r["median"], r["mad"], r["n_days"])
        for r in read_day(ctx.data_dir, BL_COVERAGE, day).to_pylist()
    }
    airfield_baseline = {
        (r["airfield"], r["family"]): r["hexes"]
        for r in read_day(ctx.data_dir, BL_AIRFIELD, day).to_pylist()
    }
    counts = [
        RegionCount(r["region"], r["family"], r["sorties"], tuple(r["sortie_ids"]))
        for r in read_day(ctx.data_dir, AGG_REGION_FAMILY, day).to_pylist()
    ]
    window = int(ctx.cfg["baseline"]["window_days"])
    d2_days = int(ctx.cfg["d2_novel_destination"]["window_hours"]) // 24
    arrivals = []
    coverage_by_day: dict[date, dict[str, float]] = {}
    for back in range(d2_days):
        d = day - timedelta(days=back)
        for r in ctx.cache.get(AGG_ARRIVALS, d) or []:
            arrivals.append(
                Arrival(r["airfield"], r["region"], r["family"], r["hex"], r["sortie_id"], d)
            )
        coverage_by_day[d] = {
            r["region"]: float(r["aircraft_hours"]) for r in ctx.cache.get(AGG_COVERAGE, d) or []
        }
    # D2's "seen before" set covers day-window .. day-d2_days; count the days with data there.
    d2_history = sum(
        ctx.cache.get(AGG_REGION_FAMILY, day - timedelta(days=n)) is not None
        for n in range(d2_days, window + 1)
    )
    return DayInputs(
        day=day,
        counts=counts,
        coverage=coverage_by_day[day],
        arrivals=arrivals,
        baseline=baseline,
        coverage_baseline={r: b for (r, wd), b in coverage_baseline_all.items() if wd == weekday},
        airfield_baseline=airfield_baseline,
        d2_history_days=d2_history,
        coverage_by_day=coverage_by_day,
        coverage_baseline_all=coverage_baseline_all,
        baseline_config_hash=rf_bl[0]["config_hash"] if rf_bl else "",
    )


def detect_day(
    inputs: DayInputs,
    cfg: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    recent: Sequence[Alert] = (),
    provenance: tuple[str, str] = ("", ""),
) -> list[Alert]:
    """Pure: the same inputs and config always give the same alerts."""
    alerts = detect_d1(
        inputs.day,
        inputs.counts,
        inputs.baseline,
        inputs.coverage,
        inputs.coverage_baseline,
        cfg,
    ) + detect_d2(
        inputs.day, inputs.arrivals, inputs.airfield_baseline, inputs.d2_history_days, cfg
    )
    alerts = apply_coverage_guard(alerts, inputs.coverage_by_day, inputs.coverage_baseline_all, cfg)
    alerts = apply_known_events(alerts, events)
    alerts = apply_cooldown(alerts, recent, cfg)
    config, version = provenance
    return [replace(a, config_hash=config, code_version=version) for a in alerts]


def run_days(days: Sequence[date], ctx: Context) -> tuple[list[Alert], list[date], list[DayInputs]]:
    """(alerts, days without data, inputs) over `days` in order, with cooldown across days."""
    alerts: list[Alert] = []
    missing: list[date] = []
    prepared: list[DayInputs] = []
    provenance = ctx.provenance
    for day in sorted(days):
        inputs = prepare_day(day, ctx)
        if inputs is None:
            missing.append(day)
            continue
        prepared.append(inputs)
        alerts += detect_day(inputs, ctx.cfg, ctx.events, alerts[-200:], provenance)
    return alerts, missing, prepared
