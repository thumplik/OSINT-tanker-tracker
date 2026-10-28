"""Combatant commands (`config/ccmds.yaml`, SPEC §6): ordered polygons, first match wins."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import h3

from tw.config import DEFAULT_CONFIG_DIR, load_yaml

Polygon = tuple[tuple[float, float], ...]  # (lon, lat) vertices


@dataclass(frozen=True)
class Ccmd:
    id: str
    name: str
    status: str
    anchor: tuple[float, float]  # (lon, lat)


@dataclass(frozen=True)
class Area:
    name: str
    ccmd: str
    polygon: Polygon


@dataclass(frozen=True)
class Ccmds:
    home: str
    fallback: str
    ccmds: tuple[Ccmd, ...]
    areas: tuple[Area, ...]
    checkpoints: dict[str, tuple[tuple[float, float], str]]  # icao -> ((lat, lon), ccmd)

    @property
    def ids(self) -> list[str]:
        return [c.id for c in self.ccmds]


def load_ccmds(config_dir: Path = DEFAULT_CONFIG_DIR) -> Ccmds:
    raw = load_yaml("ccmds.yaml", config_dir)
    ccmds = tuple(
        Ccmd(c["id"], c["name"], c["status"], (float(c["anchor"][0]), float(c["anchor"][1])))
        for c in raw["ccmds"]
    )
    ids = {c.id for c in ccmds}
    for c in ccmds:
        if c.status not in ("watch", "alerting"):
            raise ValueError(f"ccmd {c.id}: invalid status {c.status!r}")
    areas = tuple(
        Area(a["name"], a["ccmd"], tuple((float(x), float(y)) for x, y in a["polygon"]))
        for a in raw["areas"]
    )
    unknown = {a.ccmd for a in areas} | {raw["home"], raw["fallback"]}
    if not unknown <= ids:
        raise ValueError(f"ccmds.yaml: unknown ccmd ids {sorted(unknown - ids)}")
    return Ccmds(
        home=raw["home"],
        fallback=raw["fallback"],
        ccmds=ccmds,
        areas=areas,
        checkpoints={
            icao: ((float(c["at"][0]), float(c["at"][1])), c["ccmd"])
            for icao, c in (raw.get("checkpoints") or {}).items()
        },
    )


def _inside(lon: float, lat: float, polygon: Polygon) -> bool:
    """Even-odd ray casting; polygons are small and never cross the antimeridian."""
    hit = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


class CcmdMapper:
    """(lat, lon) or H3 cell -> CCMD id. Cells are cached (coverage maps a few thousand)."""

    def __init__(self, ccmds: Ccmds) -> None:
        self.config = ccmds
        self._cells: dict[str, str] = {}

    @property
    def ids(self) -> list[str]:
        return self.config.ids

    def at(self, lat: float, lon: float) -> str:
        for area in self.config.areas:
            if _inside(lon, lat, area.polygon):
                return area.ccmd
        return self.config.fallback

    def cell(self, cell: str) -> str:
        hit = self._cells.get(cell)
        if hit is None:
            hit = self.at(*h3.cell_to_latlng(cell))
            self._cells[cell] = hit
        return hit
