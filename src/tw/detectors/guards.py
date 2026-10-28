"""Always-on guards (SPEC §8). Each takes alerts and returns new alerts; nothing is dropped:
suppressed alerts keep their record with the reason in `evidence`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from tw.detectors.types import SEVERITIES, Alert, KeyBaseline, downgrade


def _suppress(alert: Alert, reason: str, **detail: Any) -> Alert:
    return replace(
        alert, status="suppressed", evidence={**alert.evidence, "suppressed_by": reason, **detail}
    )


def apply_coverage_guard(
    alerts: Sequence[Alert],
    coverage: Mapping[str, float],
    coverage_baseline: Mapping[str, KeyBaseline],
    cfg: Mapping[str, Any],
) -> list[Alert]:
    """Suppress when the region's total traffic is outside [`coverage_min_ratio`,
    `coverage_max_ratio`] of its weekday median, or when there is no coverage baseline."""
    floor = float(cfg["guards"]["coverage_min_ratio"])
    ceiling = float(cfg["guards"]["coverage_max_ratio"])
    out = []
    for a in alerts:
        bl = coverage_baseline.get(a.region)
        if bl is None or bl.median <= 0:
            out.append(_suppress(a, "coverage", coverage_ratio=None))
            continue
        ratio = round(coverage.get(a.region, 0.0) / bl.median, 3)
        if ratio < floor or ratio > ceiling:
            out.append(_suppress(a, "coverage", coverage_ratio=ratio))
        else:
            out.append(replace(a, evidence={**a.evidence, "coverage_ratio": ratio}))
    return out


def apply_known_events(alerts: Sequence[Alert], events: Sequence[Mapping[str, Any]]) -> list[Alert]:
    """Downgrade (never hide) alerts that overlap an announced exercise or event."""
    out = []
    for a in alerts:
        first = a.window_start.date()
        last = (a.window_end - timedelta(microseconds=1)).date()
        match = next(
            (
                e
                for e in events
                if a.region in e.get("regions", [])
                and a.evidence.get("family") in e.get("families", [])
                and _as_date(e["from"]) <= last
                and first <= _as_date(e["to"])
            ),
            None,
        )
        if match is None:
            out.append(a)
        else:
            evidence = {**a.evidence, "known_event": match["id"]}
            out.append(replace(a, severity=downgrade(a.severity), evidence=evidence))
    return out


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def apply_cooldown(
    alerts: Sequence[Alert], recent: Sequence[Alert], cfg: Mapping[str, Any]
) -> list[Alert]:
    """Suppress a repeat of the same detector and region within `cooldown_hours` of an earlier
    unsuppressed alert, unless its severity is higher."""
    window = timedelta(hours=float(cfg["guards"]["cooldown_hours"]))
    out = []
    for a in alerts:
        blocking = [
            r
            for r in recent
            if r.alert_id != a.alert_id
            and r.status != "suppressed"
            and r.detector == a.detector
            and r.region == a.region
            and timedelta(0) <= a.created_at - r.created_at <= window
            and SEVERITIES.index(a.severity) <= SEVERITIES.index(r.severity)
        ]
        out.append(_suppress(a, "cooldown", after=blocking[0].alert_id) if blocking else a)
    return out
