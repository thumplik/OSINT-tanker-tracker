"""D7 inventory shift (SPEC §8, M5c): aircraft of a family last seen in a CCMD at the end of a
day, against the same key's daily values over the baseline window (the current flow window left
out). Fires in either direction when the change is surprising (robust z), material in aircraft
(`min_change`) and relative to normal (`min_rel_change`), and the key has `min_history_days` of
history. The coverage guard checks every day of the flow window (`basis_days`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tw.baselines.stats import robust_z_from
from tw.detectors.types import Alert, InventoryCount, KeyBaseline, Window, alert_id, finite

DETECTOR = "D7"


def detect_d7(
    window: Window,
    counts: Sequence[InventoryCount],
    baseline: Mapping[tuple[str, str], KeyBaseline],
    cfg: Mapping[str, Any],
) -> list[Alert]:
    """`window` is the flow window ending at the day's end; inventory is taken at its end."""
    rules, base = cfg["d7_inventory_shift"], cfg["baseline"]
    floor = float(rules["normalization_median_floor"])
    alerts = []
    for c in counts:
        bl = baseline.get((c.ccmd, c.family))
        if bl is None or not finite(bl.median, bl.mad) or bl.median < 0 or bl.mad < 0:
            continue
        if bl.n_days < int(base["min_history_days"]):
            continue
        change = c.aircraft - bl.median
        z = robust_z_from(c.aircraft, bl.median, bl.mad, base)
        if (
            abs(z) < float(rules["min_z"])
            or abs(change) < float(rules["min_change"])
            or abs(change) / max(bl.median, floor) < float(rules["min_rel_change"])
        ):
            continue
        direction = "up" if change > 0 else "down"
        alerts.append(
            Alert(
                alert_id=alert_id(DETECTOR, c.ccmd, f"{c.family}/{direction}", *window),
                detector=DETECTOR,
                region=c.ccmd,
                window_start=window.start,
                window_end=window.end,
                observed=float(c.aircraft),
                expected=bl.median,
                score=round(z, 3),
                severity="medium" if abs(z) >= float(cfg["composite"]["medium_min_z"]) else "low",
                evidence={
                    "family": c.family,
                    "series": "inventory",
                    "direction": direction,
                    "ccmd": True,
                    "mad": bl.mad,
                    "n_days": bl.n_days,
                },
                sortie_ids=(),
                created_at=window.end,
                basis_days=window.days,
            )
        )
    return sorted(alerts, key=lambda a: (a.region, a.evidence["family"]))
