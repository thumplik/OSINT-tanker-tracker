"""D1 volume surge (SPEC §7-8): region x family sorties in a day, against that weekday's baseline.

Fires only when all four hold: the count is surprising (robust z), real (it outgrows regional
coverage by `min_coverage_normalized_ratio`), material (`min_count`), and the key has at least
`baseline.min_history_days` of same-weekday history.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from tw.baselines.stats import robust_z_from
from tw.detectors.types import Alert, KeyBaseline, RegionCount, alert_id, day_window

DETECTOR = "D1"


def detect_d1(
    day: date,
    counts: Sequence[RegionCount],
    baseline: Mapping[tuple[str, str], KeyBaseline],
    coverage: Mapping[str, float],
    coverage_baseline: Mapping[str, KeyBaseline],
    cfg: Mapping[str, Any],
) -> list[Alert]:
    """`baseline` and `coverage_baseline` are for `day`'s weekday, from the snapshot as of `day`."""
    rules, base = cfg["d1_volume_surge"], cfg["baseline"]
    min_samples = int(base["min_history_days"]) // 7
    start, end = day_window(day)
    alerts = []
    for c in counts:
        bl, cov_bl = baseline.get((c.region, c.family)), coverage_baseline.get(c.region)
        if bl is None or bl.n_days < min_samples or c.sorties < int(rules["min_count"]):
            continue
        z = robust_z_from(c.sorties, bl.median, bl.mad, base)
        if z < float(rules["min_z"]):
            continue
        cov_obs = coverage.get(c.region, 0.0)
        if cov_bl is None or cov_bl.n_days < min_samples or cov_bl.median <= 0 or cov_obs <= 0:
            continue  # no coverage baseline: cannot tell receivers from aircraft
        cov_ratio = cov_obs / cov_bl.median
        normalized = (c.sorties / max(bl.median, 1.0)) / cov_ratio
        if normalized < float(rules["min_coverage_normalized_ratio"]):
            continue
        medium = z >= float(cfg["composite"]["medium_min_z"])
        alerts.append(
            Alert(
                alert_id=alert_id(DETECTOR, c.region, c.family, start, end),
                detector=DETECTOR,
                region=c.region,
                window_start=start,
                window_end=end,
                observed=float(c.sorties),
                expected=bl.median,
                score=round(z, 3),
                severity="medium" if medium else "low",
                evidence={
                    "family": c.family,
                    "weekday": day.weekday(),
                    "mad": bl.mad,
                    "n_days": bl.n_days,
                    "coverage_ratio": round(cov_ratio, 3),
                    "normalized_ratio": round(normalized, 3),
                },
                sortie_ids=tuple(sorted(c.sortie_ids)),
                created_at=end,
            )
        )
    return sorted(alerts, key=lambda a: (a.region, a.evidence["family"]))
