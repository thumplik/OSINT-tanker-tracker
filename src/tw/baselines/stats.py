"""Robust statistics for baselines (SPEC §7). Pure; no means or standard deviations."""

from __future__ import annotations

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
    spread = float(cfg["mad_scale"]) * max(mad_value, float(cfg["mad_floor"]))
    return (observed - med) / spread


def robust_z(observed: float, history: Sequence[float], cfg: Mapping[str, Any]) -> float:
    return robust_z_from(observed, median(history), mad(history), cfg)
