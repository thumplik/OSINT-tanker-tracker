"""Detection over a window: aggregates → baseline snapshot → D1, D2 → guards.

The same functions serve live and history (CLAUDE.md rule 9). Official runs evaluate whole UTC
days of release data (`prepare_day`). The provisional tier (M4 worker) evaluates rolling windows
of live data ending on the hour (`prepare_window`) against the latest official snapshot, through
the same counting, detectors and guards, except the coverage guard: the live feed has no civil
traffic to measure coverage with (SPEC §8). Nothing here writes alerts or posts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from tw.baselines.aggregates import (
    AGG_ARRIVALS,
    AGG_COVERAGE,
    AGG_REGION_FAMILY,
    RegionMapper,
    build_aggregates,
    window_arrivals,
    window_counts,
)
from tw.baselines.build import (
    BL_AIRFIELD,
    BL_COVERAGE,
    BL_REGION_FAMILY,
    AggregateCache,
    build_baseline,
    latest_snapshot,
)
from tw.config import Fleet, load_fleet, load_regions, load_yaml
from tw.detectors.d1_volume_surge import detect_d1
from tw.detectors.d2_novel_destination import detect_d2
from tw.detectors.guards import apply_cooldown, apply_coverage_guard, apply_known_events
from tw.detectors.types import Alert, Arrival, KeyBaseline, RegionCount, Window, as_window
from tw.provenance import code_version, effective_config_hash
from tw.store.parquet import read_day
from tw.tracks.sorties import latest_sorties


@dataclass
class Context:
    """One run's settings. `cfg` (detectors.yaml unless given) is the single configuration used
    by aggregates, baselines and detectors alike. The cache remembers missing days, so a
    long-running process makes a fresh Context per run."""

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
    d1_window: Window
    d2_window: Window
    provisional: bool = False  # live data, coverage unchecked: never official, never posted
    baseline_as_of: date | None = None  # snapshot used; for official runs this is `day`


def prepare_day(day: date, ctx: Context) -> DayInputs | None:
    """Build `day`'s aggregates and (if absent) its baseline snapshot, then load the detector
    inputs. None when the day has no complete data."""
    if not ctx.build_aggregates(day):
        return None
    assert ctx.cache is not None
    build_baseline(day, ctx.data_dir, ctx.cache, ctx.cfg, overwrite=ctx.rebuild_baselines)
    weekday = day.weekday()
    baseline, coverage_baseline_all, airfield_baseline, bl_hash = _snapshot(ctx, day, weekday)
    counts = [
        RegionCount(r["region"], r["family"], r["sorties"], tuple(r["sortie_ids"]))
        for r in read_day(ctx.data_dir, AGG_REGION_FAMILY, day).to_pylist()
    ]
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
    return DayInputs(
        day=day,
        counts=counts,
        coverage=coverage_by_day[day],
        arrivals=arrivals,
        baseline=baseline,
        coverage_baseline={r: b for (r, wd), b in coverage_baseline_all.items() if wd == weekday},
        airfield_baseline=airfield_baseline,
        d2_history_days=_d2_history_days(ctx, day),
        coverage_by_day=coverage_by_day,
        coverage_baseline_all=coverage_baseline_all,
        baseline_config_hash=bl_hash,
        d1_window=as_window(day, float(ctx.cfg["d1_volume_surge"]["window_hours"])),
        d2_window=as_window(day, float(ctx.cfg["d2_novel_destination"]["window_hours"])),
        baseline_as_of=day,
    )


def _d2_history_days(ctx: Context, as_of: date) -> int:
    """D2's "seen before" set covers as_of-window .. as_of-d2_days: the days with data there."""
    assert ctx.cache is not None
    window = int(ctx.cfg["baseline"]["window_days"])
    d2_days = int(ctx.cfg["d2_novel_destination"]["window_hours"]) // 24
    return sum(
        ctx.cache.get(AGG_REGION_FAMILY, as_of - timedelta(days=n)) is not None
        for n in range(d2_days, window + 1)
    )


def _snapshot(
    ctx: Context, as_of: date, weekday: int
) -> tuple[
    dict[tuple[str, str], KeyBaseline],
    dict[tuple[str, int], KeyBaseline],
    dict[tuple[str, str], int],
    str,
]:
    rf_bl = read_day(ctx.data_dir, BL_REGION_FAMILY, as_of).to_pylist()
    baseline = {
        (r["region"], r["family"]): KeyBaseline(r["median"], r["mad"], r["n_days"])
        for r in rf_bl
        if r["weekday"] == weekday
    }
    coverage_all = {
        (r["region"], r["weekday"]): KeyBaseline(r["median"], r["mad"], r["n_days"])
        for r in read_day(ctx.data_dir, BL_COVERAGE, as_of).to_pylist()
    }
    airfield = {
        (r["airfield"], r["family"]): r["hexes"]
        for r in read_day(ctx.data_dir, BL_AIRFIELD, as_of).to_pylist()
    }
    return baseline, coverage_all, airfield, rf_bl[0]["config_hash"] if rf_bl else ""


def prepare_window(end: datetime, ctx: Context, live_dir: Path) -> DayInputs | None:
    """Provisional inputs for the rolling windows ending at `end`: live sorties from `live_dir`
    against the latest official snapshot as of `end`'s date or earlier.

    Never builds a snapshot: one built before the previous day's release is loaded would miss
    that day for good (snapshots are write-once). None when no snapshot exists yet."""
    as_of = latest_snapshot(ctx.data_dir, (end - timedelta(microseconds=1)).date())
    if as_of is None:
        return None
    assert ctx.cache is not None
    cfg = ctx.cfg
    minutes = float(cfg["counting"]["min_sortie_minutes"])
    d1_window = Window(end - timedelta(hours=float(cfg["d1_volume_surge"]["window_hours"])), end)
    d2_window = Window(
        end - timedelta(hours=float(cfg["d2_novel_destination"]["window_hours"])), end
    )
    sorties = latest_sorties(live_dir, d2_window.days)
    counts = [
        RegionCount(r["region"], r["family"], r["sorties"], tuple(r["sortie_ids"]))
        for r in window_counts(sorties, *d1_window, ctx.fleet, ctx.mapper, minutes)
    ]
    arrivals = [
        Arrival(
            r["airfield"], r["region"], r["family"], r["hex"], r["sortie_id"], r["end_ts"].date()
        )
        for r in window_arrivals(sorties, *d2_window, ctx.fleet, ctx.mapper, minutes)
    ]
    baseline, coverage_all, airfield, bl_hash = _snapshot(ctx, as_of, d1_window.weekday)
    return DayInputs(
        day=d1_window.days[-1],
        counts=counts,
        coverage={},
        arrivals=arrivals,
        baseline=baseline,
        coverage_baseline={},
        airfield_baseline=airfield,
        d2_history_days=_d2_history_days(ctx, as_of),
        coverage_by_day={},
        coverage_baseline_all=coverage_all,
        baseline_config_hash=bl_hash,
        d1_window=d1_window,
        d2_window=d2_window,
        provisional=True,
        baseline_as_of=as_of,
    )


def detect_day(
    inputs: DayInputs,
    cfg: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    recent: Sequence[Alert] = (),
    provenance: tuple[str, str] = ("", ""),
) -> list[Alert]:
    """Pure: the same inputs and config always give the same alerts.

    Provisional inputs skip only the coverage guard, which needs coverage the live feed lacks;
    their alerts say so in evidence (`tier`, `coverage`)."""
    alerts = detect_d1(
        inputs.d1_window,
        inputs.counts,
        inputs.baseline,
        inputs.coverage,
        inputs.coverage_baseline,
        cfg,
    ) + detect_d2(
        inputs.d2_window, inputs.arrivals, inputs.airfield_baseline, inputs.d2_history_days, cfg
    )
    if inputs.provisional:
        tier = {
            "tier": "provisional",
            "coverage": "unchecked",
            "baseline_as_of": inputs.baseline_as_of,
        }
        alerts = [replace(a, evidence={**a.evidence, **tier}) for a in alerts]
    else:
        alerts = apply_coverage_guard(
            alerts, inputs.coverage_by_day, inputs.coverage_baseline_all, cfg
        )
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
