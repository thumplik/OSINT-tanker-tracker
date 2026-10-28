"""Robust statistics for baselines (SPEC §7). Pure; no means or standard deviations."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any


def median(values: Sequence[float]) -> float:
    return float(statistics.median(values))


def mad(values: Sequence[float]) -> float:
    """Median absolute deviation (unscaled)."""
    m = median(values)
    return median([abs(v - m) for v in values])


def robust_z_from(observed: float, med: float, mad_value: float, cfg: Mapping[str, Any]) -> float:
    """(observed - median) / (scale x MAD), with MAD floored so sparse keys stay finite."""
    scale, floor = float(cfg["mad_scale"]), float(cfg["mad_floor"])
    if not (math.isfinite(scale) and math.isfinite(floor) and scale > 0 and floor > 0):
        raise ValueError(
            f"baseline.mad_scale and mad_floor must be finite and > 0: {scale}, {floor}"
        )
    spread = scale * max(mad_value, floor)
    return (observed - med) / spread


def robust_z(observed: float, history: Sequence[float], cfg: Mapping[str, Any]) -> float:
    return robust_z_from(observed, median(history), mad(history), cfg)
