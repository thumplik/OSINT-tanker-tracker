"""Coverage denominator: distinct aircraft (civil included) per H3 r4 cell per UTC hour."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import h3

from tw.ingest.schema import RawPoint
from tw.normalize.points import H3_COARSE


@dataclass
class CoverageAccumulator:
    """Counts each aircraft at most once per `sample_seconds`, keyed by (epoch hour, r4 cell)."""

    sample_seconds: float
    _seen: dict[tuple[int, str], set[str]] = field(default_factory=lambda: defaultdict(set))
    _last_sample: dict[str, float] = field(default_factory=dict)

    def _add(self, hex_code: str, t: float, lat: float, lon: float) -> None:
        last = self._last_sample.get(hex_code)
        if last is not None and 0 <= t - last < self.sample_seconds:
            return
        self._last_sample[hex_code] = t
        cell = h3.latlng_to_cell(lat, lon, H3_COARSE)
        self._seen[(int(t // 3600), cell)].add(hex_code)

    def add(self, points: Iterable[RawPoint]) -> None:
        for p in points:
            if p.lat is not None and p.lon is not None:
                self._add(p.hex, p.ts.timestamp(), p.lat, p.lon)

    def add_trace_rows(self, hex_code: str, base: float, rows: Sequence[Any]) -> None:
        """Fast path for trace rows (`[dt, lat, lon, ...]`) without building RawPoints.

        Used for the bulk of a dump (civil traffic), which only feeds coverage.
        """
        for row in rows:
            lat, lon = row[1], row[2]
            if isinstance(lat, int | float) and isinstance(lon, int | float):
                self._add(hex_code, base + row[0], float(lat), float(lon))

    def rows(self) -> list[dict[str, object]]:
        return [
            {
                "hour": datetime.fromtimestamp(hour * 3600, tz=UTC),
                "h3_r4": cell,
                "aircraft": len(hexes),
            }
            for (hour, cell), hexes in sorted(self._seen.items())
        ]
