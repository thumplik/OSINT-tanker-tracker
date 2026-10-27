"""Raw points → normalized, fleet-filtered points (SPEC §3, §5)."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime

import h3

from tw.config import Fleet
from tw.ingest.schema import RawPoint

H3_COARSE = 4
H3_FINE = 6


@dataclass(frozen=True)
class Point:
    ts: datetime
    hex: str
    callsign: str | None
    type: str
    lat: float | None
    lon: float | None
    alt_baro: int | None
    gs: float | None
    track: float | None
    on_ground: bool | None
    position_source: str | None
    provider: str
    h3_r4: str | None
    h3_r6: str | None


POINT_FIELDS = tuple(Point.__dataclass_fields__)


def military_candidates(raw: Iterable[RawPoint], fleet: Fleet) -> Iterator[RawPoint]:
    """Real ICAO addresses inside the military block. Type is not checked yet."""
    for p in raw:
        if not p.is_non_icao and fleet.in_military_block(p.hex):
            yield p


def build_registry(
    points: Iterable[RawPoint], prior: Mapping[str, str] | None = None
) -> dict[str, str]:
    """hex → most frequently seen type. Ties break alphabetically so the result is stable."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for p in points:
        if p.type:
            counts[p.hex][p.type] += 1
    registry = dict(prior or {})
    for hex_code, c in counts.items():
        registry[hex_code] = min(c, key=lambda t: (-c[t], t))
    return registry


def h3_cells(lat: float | None, lon: float | None) -> tuple[str | None, str | None]:
    if lat is None or lon is None:
        return None, None
    return h3.latlng_to_cell(lat, lon, H3_COARSE), h3.latlng_to_cell(lat, lon, H3_FINE)


def normalize(
    raw: Iterable[RawPoint], fleet: Fleet, registry: Mapping[str, str]
) -> Iterator[Point]:
    """Keep tracked-fleet points; fill a missing type from the registry before discarding."""
    tracked = fleet.tracked_types
    for p in military_candidates(raw, fleet):
        type_code = p.type or registry.get(p.hex)
        if type_code not in tracked:
            continue
        if p.type is None:
            p = replace(p, type=type_code)
        r4, r6 = h3_cells(p.lat, p.lon)
        yield Point(
            ts=p.ts,
            hex=p.hex,
            callsign=p.callsign,
            type=type_code,
            lat=p.lat,
            lon=p.lon,
            alt_baro=p.alt_baro,
            gs=p.gs,
            track=p.track,
            on_ground=p.on_ground,
            position_source=p.position_source,
            provider=p.provider,
            h3_r4=r4,
            h3_r6=r6,
        )
