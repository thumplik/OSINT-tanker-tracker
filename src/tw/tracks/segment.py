"""Split one aircraft's points into sorties and resolve their airfields (SPEC §5).

Pure and deterministic: the same points and rules always give the same sorties.

- A sortie starts at the first airborne point. `on_ground=None` is unknown, never ground.
- It ends at a landing (a ground run of at least `min_ground_seconds`; shorter ground runs are
  touch-and-goes) or at a gap longer than `max_gap_minutes`.
- A longer gap is bridged as a coverage hole when the aircraft is high on both sides, the gap
  is not too long, and the implied ground speed is plausible.
- Origin and destination: the nearest airfield within `airfield_radius_km` to the first (last)
  point below `airfield_max_agl_ft`, including the ground point next to takeoff (landing).
  Scanning stops at the first point above that height, so a sortie first seen at cruise
  altitude has no origin.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

from tw.tracks.airfields import AirfieldIndex, haversine_km

KM_PER_NM = 1.852


class TrackPoint(NamedTuple):
    ts: float  # epoch seconds, UTC
    lat: float
    lon: float
    alt: int | None  # barometric ft; None when on the ground or not reported
    on_ground: bool | None
    callsign: str | None
    h3_r4: str | None
    low_quality: bool  # MLAT, Mode-S only, ... (fleet.yaml low_quality_sources)


@dataclass(frozen=True)
class SortieRules:
    max_gap_s: float
    min_ground_s: float
    bridge_min_alt_ft: int
    bridge_max_gap_s: float
    bridge_max_speed_kt: float
    min_points: int
    min_duration_s: float
    airfield_radius_km: float
    airfield_max_agl_ft: int

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> SortieRules:
        return cls(
            max_gap_s=float(cfg["max_gap_minutes"]) * 60,
            min_ground_s=float(cfg["min_ground_seconds"]),
            bridge_min_alt_ft=int(cfg["bridge_min_alt_ft"]),
            bridge_max_gap_s=float(cfg["bridge_max_gap_hours"]) * 3600,
            bridge_max_speed_kt=float(cfg["bridge_max_speed_kt"]),
            min_points=int(cfg["min_points"]),
            min_duration_s=float(cfg["min_minutes"]) * 60,
            airfield_radius_km=float(cfg["airfield_radius_km"]),
            airfield_max_agl_ft=int(cfg["airfield_max_agl_ft"]),
        )


@dataclass(frozen=True)
class RawSortie:
    points: tuple[TrackPoint, ...]  # airborne points (and touch-and-go ground points)
    before: TrackPoint | None  # last ground point just before takeoff
    after: TrackPoint | None  # first ground point of the landing
    end_reason: str  # "landing" | "gap" | "data_end"


def _bridgeable(a: TrackPoint, b: TrackPoint, rules: SortieRules) -> bool:
    gap = b.ts - a.ts
    if gap > rules.bridge_max_gap_s or a.on_ground or b.on_ground:
        return False
    if a.alt is None or b.alt is None or min(a.alt, b.alt) < rules.bridge_min_alt_ft:
        return False
    knots = haversine_km(a.lat, a.lon, b.lat, b.lon) / KM_PER_NM / (gap / 3600)
    return knots <= rules.bridge_max_speed_kt


class _Builder:
    def __init__(self, rules: SortieRules) -> None:
        self.rules = rules
        self.out: list[RawSortie] = []
        self.points: list[TrackPoint] = []
        self.ground_run: list[TrackPoint] = []
        self.before: TrackPoint | None = None
        self.last_ground: TrackPoint | None = None

    def close(self, reason: str, after: TrackPoint | None = None) -> None:
        airborne = [p for p in self.points if p.on_ground is not True]
        long_enough = airborne and airborne[-1].ts - airborne[0].ts >= self.rules.min_duration_s
        if len(airborne) >= self.rules.min_points and long_enough:
            self.out.append(RawSortie(tuple(self.points), self.before, after, reason))
        self.points, self.ground_run, self.before = [], [], None

    def add(self, p: TrackPoint) -> None:
        rules = self.rules
        if not self.points:
            if p.on_ground is True:
                self.last_ground = p
                return
            lg = self.last_ground
            self.before = lg if lg is not None and p.ts - lg.ts <= rules.max_gap_s else None
            self.points.append(p)
            return
        prev = self.ground_run[-1] if self.ground_run else self.points[-1]
        if p.ts - prev.ts > rules.max_gap_s:
            if self.ground_run:  # sat on the ground, then went quiet: a landing
                self.close("landing", after=self.ground_run[0])
            elif not _bridgeable(prev, p, rules):
                self.close("gap")
            if not self.points:
                self.add(p)
                return
        if p.on_ground is True:
            self.ground_run.append(p)
            self.last_ground = p
            if p.ts - self.ground_run[0].ts >= rules.min_ground_s:
                self.close("landing", after=self.ground_run[0])
            return
        if self.ground_run:  # touch-and-go
            self.points.extend(self.ground_run)
            self.ground_run = []
        self.points.append(p)

    def finish(self) -> list[RawSortie]:
        if self.points:
            if self.ground_run:
                self.close("landing", after=self.ground_run[0])
            else:
                self.close("data_end")
        return self.out


def segment(points: Iterable[TrackPoint], rules: SortieRules) -> list[RawSortie]:
    """Sorties for one aircraft. `points` must be sorted by time."""
    builder = _Builder(rules)
    for p in points:
        builder.add(p)
    return builder.finish()


def _resolve(
    candidates: Iterable[TrackPoint], index: AirfieldIndex, rules: SortieRules
) -> str | None:
    for p in candidates:
        hit = index.nearest(p.lat, p.lon, rules.airfield_radius_km)
        if p.on_ground is True:
            if hit is not None:
                return hit[0].code
            continue
        if p.alt is None:
            continue
        elevation = (hit[0].elevation_ft or 0.0) if hit is not None else 0.0
        if p.alt - elevation >= rules.airfield_max_agl_ft:
            return None  # climbed out (or first seen) above pattern height
        if hit is not None:
            return hit[0].code
    return None


def resolve_ends(
    sortie: RawSortie, index: AirfieldIndex, rules: SortieRules
) -> tuple[str | None, str | None]:
    """(origin, destination) airfield codes, None where unresolved."""
    head: Sequence[TrackPoint] = ((sortie.before,) if sortie.before else ()) + sortie.points
    tail: Sequence[TrackPoint] = ((sortie.after,) if sortie.after else ()) + sortie.points[::-1]
    return _resolve(head, index, rules), _resolve(tail, index, rules)
