"""Always-on guards (SPEC §8). Each takes alerts and returns new alerts; nothing is dropped:
suppressed alerts keep their record, with every reason listed in `evidence["suppressed_by"]`."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from tw.detectors.types import SEVERITIES, Alert, KeyBaseline, downgrade, finite


def _suppress(alert: Alert, reason: str, **detail: Any) -> Alert:
    """Mark suppressed, adding `reason` to any earlier ones (all reasons stay on the record)."""
    reasons = [*alert.evidence.get("suppressed_by", []), reason]
    evidence = {**alert.evidence, **detail, "suppressed_by": reasons}
    return replace(alert, status="suppressed", evidence=evidence)


def apply_coverage_guard(
    alerts: Sequence[Alert],
    coverage_by_day: Mapping[date, Mapping[str, float]],
    coverage_baseline: Mapping[tuple[str, int], KeyBaseline],
    cfg: Mapping[str, Any],
) -> list[Alert]:
    """Suppress when, on any day the alert rests on (`basis_days`), the region's total traffic
    is outside [`coverage_min_ratio`, `coverage_max_ratio`] of that weekday's median, or can't
    be compared (no baseline, non-finite values). Bounds compare the exact ratio."""
    floor = float(cfg["guards"]["coverage_min_ratio"])
    ceiling = float(cfg["guards"]["coverage_max_ratio"])
    out = []
    for a in alerts:
        days = a.basis_days or ((a.window_end - timedelta(microseconds=1)).date(),)
        ratios: list[float] = []
        comparable = True
        for d in days:
            bl = coverage_baseline.get((a.region, d.weekday()))
            observed = coverage_by_day.get(d, {}).get(a.region, 0.0)
            if bl is None or not finite(bl.median, observed) or bl.median <= 0:
                comparable = False
                break
            ratios.append(observed / bl.median)
        if not comparable:
            out.append(_suppress(a, "coverage", coverage_ratio=None))
            continue
        worst = max(ratios, key=lambda r: max(floor / r if r > 0 else math.inf, r / ceiling))
        shown = round(worst, 3)
        if worst < floor or worst > ceiling:
            out.append(_suppress(a, "coverage", coverage_ratio=shown))
        else:
            out.append(replace(a, evidence={**a.evidence, "coverage_ratio": shown}))
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


def cooldown_key(a: Alert) -> tuple[str, ...]:
    """What makes two alerts repeats of each other: the same detector, region, family (a tanker
    alert never silences an airlift one) and, for D6, flow series. D2 airfields in one region
    still share a cooldown: keying by airfield too added ~1.1 D2 alerts a day in the backtest."""
    return (
        a.detector,
        a.region,
        str(a.evidence.get("family", "")),
        str(a.evidence.get("series", "")),
    )


def apply_cooldown(
    alerts: Sequence[Alert], recent: Sequence[Alert], cfg: Mapping[str, Any]
) -> list[Alert]:
    """Suppress a repeat of the same key (`cooldown_key`) within the cooldown of an earlier
    unsuppressed alert, unless its severity is higher. Alerts in this batch count too: the
    strongest (severity, then score, then id) is kept and the rest are repeats. Output keeps
    the input order.

    The cooldown is `guards.cooldown_hours`, or `guards.cooldown_hours_by_detector[detector]`
    where set (multi-day windows would otherwise repeat every day)."""
    hours = float(cfg["guards"]["cooldown_hours"])
    by_detector = cfg["guards"].get("cooldown_hours_by_detector") or {}
    history = [r for r in recent if r.status != "suppressed"]
    order = sorted(
        range(len(alerts)),
        key=lambda i: (-SEVERITIES.index(alerts[i].severity), -alerts[i].score, alerts[i].alert_id),
    )
    out: dict[int, Alert] = {}
    for i in order:
        a = alerts[i]
        blocking = next(
            (
                r
                for r in history
                if r.alert_id != a.alert_id
                and cooldown_key(r) == cooldown_key(a)
                and timedelta(0)
                <= a.created_at - r.created_at
                <= timedelta(hours=float(by_detector.get(a.detector, hours)))
                and SEVERITIES.index(a.severity) <= SEVERITIES.index(r.severity)
            ),
            None,
        )
        if blocking is not None:
            out[i] = _suppress(a, "cooldown", after=blocking.alert_id)
        else:
            out[i] = a
            if a.status != "suppressed":
                history.append(a)
    return [out[i] for i in range(len(alerts))]
