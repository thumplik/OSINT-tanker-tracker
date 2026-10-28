"""D1 volume surge (SPEC §7-8): region x family sorties in a day, against that weekday's baseline.

Fires only when all four hold: the count is surprising (robust z), real (it outgrows regional
coverage by `min_coverage_normalized_ratio`), material (`min_count`), and the key has at least
`baseline.min_history_days` of same-weekday history.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from tw.baselines.stats import robust_z_from
from tw.detectors.types import Alert, KeyBaseline, RegionCount, alert_id, day_window, finite

DETECTOR = "D1"


def detect_d1(
    day: date,
    counts: Sequence[RegionCount],
    baseline: Mapping[tuple[str, str], KeyBaseline],
    coverage: Mapping[str, float],
    coverage_baseline: Mapping[str, KeyBaseline],
    cfg: Mapping[str, Any],
) -> list[Alert]:
    """`baseline` and `coverage_baseline` are for `day`'s weekday, from the snapshot as of `day`.

    When coverage can't be compared (no baseline, or no traffic seen), the coverage-normalized
    check can't run: the candidate is still returned, with `normalized_ratio` None, so the
    coverage guard suppresses it on the record instead of it vanishing."""
    rules, base = cfg["d1_volume_surge"], cfg["baseline"]
    min_samples = math.ceil(int(base["min_history_days"]) / 7)
    median_floor = float(rules["normalization_median_floor"])
    start, end = day_window(day)
    alerts = []
    for c in counts:
        bl, cov_bl = baseline.get((c.region, c.family)), coverage_baseline.get(c.region)
        if bl is None or not finite(bl.median, bl.mad) or bl.median < 0 or bl.mad < 0:
            continue  # no (or malformed) baseline: "insufficient baseline", never an alert
        if bl.n_days < min_samples or c.sorties < int(rules["min_count"]):
            continue
        z = robust_z_from(c.sorties, bl.median, bl.mad, base)
        if z < float(rules["min_z"]):
            continue
        cov_obs = coverage.get(c.region, 0.0)
        normalized: float | None = None
        cov_ratio: float | None = None
        comparable = cov_bl is not None and finite(cov_bl.median, cov_obs) and cov_bl.median > 0
        if cov_bl is not None and comparable and cov_obs > 0:
            cov_ratio = cov_obs / cov_bl.median
            normalized = (c.sorties / max(bl.median, median_floor)) / cov_ratio
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
                    "normalized_ratio": None if normalized is None else round(normalized, 3),
                },
                sortie_ids=tuple(sorted(c.sortie_ids)),
                created_at=end,
                basis_days=(day,),
            )
        )
    return sorted(alerts, key=lambda a: (a.region, a.evidence["family"]))
