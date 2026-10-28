"""What a day's numbers would flag (SPEC §10, M5d): the dashboard's "What stands out".

The pure detectors D1, D6 and D7 and the coverage guard run on the same inputs the official run
uses, read from that day's aggregates and the snapshot dated on or before it, so a past day shows
what was knowable then (CLAUDE.md rule 8). Nothing here is stored or posted: these are not alerts
(rule 1). Days before the worker started have no stored alerts, so this is how history shows its
unusual days. D2 needs the airfield history and isn't run here; cooldown and known events aren't
applied (one day, read-only).

A live (provisional) day has no coverage yet: its candidates skip the guard and say so, as
provisional alerts do.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from typing import Any

from tw.detectors.d1_volume_surge import detect_d1
from tw.detectors.d6_flow_change import detect_d6
from tw.detectors.d7_inventory_shift import detect_d7
from tw.detectors.guards import apply_coverage_guard
from tw.detectors.types import Alert, KeyBaseline, RegionCount
from tw.pipeline import TheaterInputs


def day_indicators(
    day: date,
    provisional: bool,
    counts: Sequence[RegionCount],
    baseline: Mapping[tuple[str, str], KeyBaseline],
    coverage: Mapping[str, float],
    coverage_baseline: Mapping[str, KeyBaseline],
    theater: TheaterInputs | None,
    cfg: Mapping[str, Any],
) -> list[Alert]:
    """Pure: D1 on the day's region counts, D6/D7 on the flow window ending with it, each family
    on its own. Guarded like the official run unless `provisional`."""
    d1 = detect_d1(day, counts, baseline, coverage, coverage_baseline, cfg)
    th: list[Alert] = []
    if theater is not None:
        th = detect_d6(
            theater.window,
            theater.flows,
            theater.flow_baseline,
            theater.coverage_ratio(),
            cfg,
        ) + detect_d7(theater.window, theater.inventory, theater.inventory_baseline, cfg)
    if provisional:
        unchecked = {"tier": "provisional", "coverage": "unchecked"}
        return [replace(a, evidence={**a.evidence, **unchecked}) for a in d1 + th]
    d1 = apply_coverage_guard(
        d1,
        {day: coverage},
        {(r, day.weekday()): b for r, b in coverage_baseline.items()},
        cfg,
    )
    if theater is not None:
        th = apply_coverage_guard(th, theater.coverage_by_day, theater.coverage_baseline_all, cfg)
    return d1 + th
