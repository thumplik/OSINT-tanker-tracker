"""Detector inputs and the Alert record (SPEC §5). Plain frozen data; no I/O."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, NamedTuple

SEVERITIES = ("low", "medium", "high")


class RegionCount(NamedTuple):
    """Sorties of one family in one region on one day (from aggregates)."""

    region: str
    family: str
    sorties: int
    sortie_ids: tuple[str, ...]


class Arrival(NamedTuple):
    airfield: str
    region: str
    family: str
    hex: str
    sortie_id: str
    day: date  # UTC day of the landing


class KeyBaseline(NamedTuple):
    median: float
    mad: float
    n_days: int


@dataclass(frozen=True)
class Alert:
    alert_id: str
    detector: str
    region: str
    window_start: datetime
    window_end: datetime
    observed: float
    expected: float
    score: float
    severity: str
    evidence: dict[str, Any] = field(hash=False)
    sortie_ids: tuple[str, ...]
    created_at: datetime  # the window end, so re-runs give identical alerts
    status: str = "new"  # new | approved | rejected | posted | suppressed
    basis_days: tuple[date, ...] = ()  # days whose data the alert rests on (coverage-checked)
    config_hash: str = ""  # effective configuration of the run (SPEC §11), set by the pipeline
    code_version: str = ""


def day_window(day: date, days: int = 1) -> tuple[datetime, datetime]:
    """[start, end) covering `days` UTC days ending with `day`."""
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=UTC)
    return end - timedelta(days=days), end


def alert_id(detector: str, region: str, key: str, start: datetime, end: datetime) -> str:
    """Stable id; a JSON array keeps fields apart even when names contain delimiters."""
    raw = json.dumps([detector, region, key, start.isoformat(), end.isoformat()])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


def downgrade(severity: str) -> str:
    return SEVERITIES[max(0, SEVERITIES.index(severity) - 1)]
