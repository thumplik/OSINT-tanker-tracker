"""Coverage denominator: distinct aircraft (civil included) per H3 r4 cell per UTC hour."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

import h3

from tw.ingest.schema import RawPoint
from tw.normalize.points import H3_COARSE


@dataclass
class CoverageAccumulator:
    sample_seconds: float
    _seen: dict[tuple[datetime, str], set[str]] = field(default_factory=lambda: defaultdict(set))
    _last_sample: dict[str, float] = field(default_factory=dict)

    def add(self, points: Iterable[RawPoint]) -> None:
        for p in points:
            if p.lat is None or p.lon is None:
                continue
            t = p.ts.timestamp()
            last = self._last_sample.get(p.hex)
            if last is not None and 0 <= t - last < self.sample_seconds:
                continue
            self._last_sample[p.hex] = t
            hour = p.ts.replace(minute=0, second=0, microsecond=0)
            self._seen[(hour, h3.latlng_to_cell(p.lat, p.lon, H3_COARSE))].add(p.hex)

    def rows(self) -> list[dict[str, object]]:
        return [
            {"hour": hour, "h3_r4": cell, "aircraft": len(hexes)}
            for (hour, cell), hexes in sorted(self._seen.items())
        ]
