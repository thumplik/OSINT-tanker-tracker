"""D2 novel destination (SPEC §8): distinct aircraft of a family arriving at an airfield that
saw at most `max_baseline_arrivals` of them in the baseline window (owner decision: aircraft,
not sorties, so one plane shuttling cannot fire it alone)."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from tw.detectors.types import Alert, Arrival, Window, alert_id, as_window

DETECTOR = "D2"


def detect_d2(
    when: date | Window,
    arrivals: Sequence[Arrival],
    baseline: Mapping[tuple[str, str], int],
    history_days: int,
    cfg: Mapping[str, Any],
) -> list[Alert]:
    """`when`: a UTC day (the window ending with it) or a window. `arrivals`: closed sorties
    arriving in the window. `baseline`: distinct aircraft per (airfield, family) seen before
    the window. `history_days`: days with data in that same pre-window interval (missing days
    excluded)."""
    rules = cfg["d2_novel_destination"]
    if history_days < int(cfg["baseline"]["min_history_days"]):
        return []
    start, end = as_window(when, float(rules["window_hours"]))
    groups: dict[tuple[str, str], list[Arrival]] = defaultdict(list)
    for a in arrivals:
        groups[(a.airfield, a.family)].append(a)
    alerts = []
    for (field, family), group in sorted(groups.items()):
        hexes = {a.hex for a in group}
        before = baseline.get((field, family), 0)
        if len(hexes) < int(rules["min_arrivals"]) or before > int(rules["max_baseline_arrivals"]):
            continue
        regions = Counter(a.region for a in group)
        region = min(regions, key=lambda r: (-regions[r], r))
        alerts.append(
            Alert(
                alert_id=alert_id(DETECTOR, region, f"{field}|{family}", start, end),
                detector=DETECTOR,
                region=region,
                window_start=start,
                window_end=end,
                observed=float(len(hexes)),
                expected=float(before),
                score=float(len(hexes) - before),
                severity="low",
                evidence={"family": family, "airfield": field, "hexes": sorted(hexes)},
                sortie_ids=tuple(sorted(a.sortie_id for a in group)),
                created_at=end,
                basis_days=tuple(sorted({a.day for a in group})),
            )
        )
    return alerts
