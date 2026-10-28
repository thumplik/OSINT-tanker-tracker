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


class FlowCount(NamedTuple):
    """Entries, exits or transits from home of one family for one CCMD, summed over a window."""

    ccmd: str
    family: str
    series: str  # entries | exits | transits
    observed: int


class InventoryCount(NamedTuple):
    """Aircraft of one family whose latest fix is in a CCMD at the end of a day."""

    ccmd: str
    family: str
    aircraft: int


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


class Window(NamedTuple):
    """[start, end) in UTC. Official runs use whole days; the provisional tier uses rolling
    windows ending on the hour (SPEC §8)."""

    start: datetime
    end: datetime

    @property
    def days(self) -> tuple[date, ...]:
        """UTC days the window overlaps."""
        first, last = self.start.date(), (self.end - timedelta(microseconds=1)).date()
        return tuple(first + timedelta(days=n) for n in range((last - first).days + 1))

    @property
    def weekday(self) -> int:
        """Weekday whose baseline the window is compared with: that of its midpoint."""
        return (self.start + (self.end - self.start) / 2).weekday()


def as_window(when: date | Window, hours: float) -> Window:
    """A window as given, or the `hours`-long window ending at the midnight after a day."""
    if isinstance(when, Window):
        return when
    end = datetime.combine(when + timedelta(days=1), time(), tzinfo=UTC)
    return Window(end - timedelta(hours=hours), end)


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
