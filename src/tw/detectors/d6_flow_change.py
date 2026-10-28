"""D6 flow change (SPEC §8, M5c): entries into, exits from, or transits from home to a CCMD, per
family, summed over the flow window, against that key's earlier windows.

Fires only when the count is surprising (robust z), material (`min_count`), the key has at least
`theater.min_blocks` complete earlier windows, and the count also outgrows the CCMD's receiver
coverage by `min_coverage_normalized_ratio`. As in D1, a candidate whose coverage can't be
compared is still returned with `normalized_ratio` None, so the coverage guard suppresses it on
the record instead of it vanishing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tw.baselines.stats import robust_z_from
from tw.detectors.types import Alert, FlowCount, KeyBaseline, Window, alert_id, finite

DETECTOR = "D6"


def detect_d6(
    window: Window,
    counts: Sequence[FlowCount],
    baseline: Mapping[tuple[str, str, str], KeyBaseline],
    coverage_ratio: Mapping[str, float | None],
    cfg: Mapping[str, Any],
) -> list[Alert]:
    """`coverage_ratio`: per CCMD, its traffic over the window vs its weekday baselines (None
    when it can't be compared)."""
    rules, base = cfg["d6_flow_change"], cfg["baseline"]
    min_blocks = int(cfg["theater"]["min_blocks"])
    floor = float(rules["normalization_median_floor"])
    alerts = []
    for c in counts:
        bl = baseline.get((c.ccmd, c.family, c.series))
        if bl is None or not finite(bl.median, bl.mad) or bl.median < 0 or bl.mad < 0:
            continue
        if bl.n_days < min_blocks or c.observed < int(rules["min_count"]):
            continue
        z = robust_z_from(c.observed, bl.median, bl.mad, base)
        if z < float(rules["min_z"]):
            continue
        ratio = coverage_ratio.get(c.ccmd)
        normalized = None
        if ratio is not None and finite(ratio) and ratio > 0:
            normalized = (c.observed / max(bl.median, floor)) / ratio
            if normalized < float(rules["min_coverage_normalized_ratio"]):
                continue
        alerts.append(
            Alert(
                alert_id=alert_id(DETECTOR, c.ccmd, f"{c.family}/{c.series}", *window),
                detector=DETECTOR,
                region=c.ccmd,
                window_start=window.start,
                window_end=window.end,
                observed=float(c.observed),
                expected=bl.median,
                score=round(z, 3),
                severity="medium" if z >= float(cfg["composite"]["medium_min_z"]) else "low",
                evidence={
                    "family": c.family,
                    "series": c.series,
                    "ccmd": True,
                    "mad": bl.mad,
                    "n_blocks": bl.n_days,
                    "normalized_ratio": None if normalized is None else round(normalized, 3),
                },
                sortie_ids=(),
                created_at=window.end,
                basis_days=window.days,
            )
        )
    return sorted(alerts, key=lambda a: (a.region, a.evidence["family"], a.evidence["series"]))
