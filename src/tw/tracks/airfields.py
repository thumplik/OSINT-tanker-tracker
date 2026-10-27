"""Airfield reference data (OurAirports) and nearest-airfield lookup (SPEC §5).

`tw airfields update` filters OurAirports' `airports.csv` and `runways.csv` to fields a tanker
or airlifter could use and writes `config/airfields.csv`. `AirfieldIndex` buckets them by H3
cell so a nearest lookup only measures the few fields in nearby cells.
"""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h3

from tw.config import DEFAULT_CONFIG_DIR

AIRFIELDS_PATH = DEFAULT_CONFIG_DIR / "airfields.csv"
PROVIDER = "ourairports"
FIELDS = ("ident", "icao", "name", "lat", "lon", "elevation_ft", "runway_ft")
EARTH_RADIUS_KM = 6371.0088
_INDEX_RES = 5  # ~10 km cell edge; search rings are derived from the radius


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class Airfield:
    ident: str
    icao: str | None
    name: str
    lat: float
    lon: float
    elevation_ft: float | None
    runway_ft: int

    @property
    def code(self) -> str:
        """ICAO code where one exists, else the OurAirports ident."""
        return self.icao or self.ident


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def filter_ourairports(
    airports: Iterable[Mapping[str, str]],
    runways: Iterable[Mapping[str, str]],
    types: Sequence[str],
    min_runway_ft: int,
) -> list[Airfield]:
    """Open airports of the given types whose longest open runway is at least `min_runway_ft`."""
    longest: dict[str, int] = defaultdict(int)
    for r in runways:
        length = _float(r.get("length_ft"))
        if length is not None and r.get("closed") != "1":
            ref = r.get("airport_ref", "")
            longest[ref] = max(longest[ref], int(length))
    out = []
    for a in airports:
        lat, lon = _float(a.get("latitude_deg")), _float(a.get("longitude_deg"))
        runway = longest.get(a.get("id", ""), 0)
        if a.get("type") not in types or lat is None or lon is None or runway < min_runway_ft:
            continue
        icao = (a.get("icao_code") or "").strip().upper() or None
        out.append(
            Airfield(
                ident=a["ident"].strip().upper(),
                icao=icao if icao and len(icao) == 4 and icao.isalnum() else None,
                name=a.get("name", "").strip(),
                lat=lat,
                lon=lon,
                elevation_ft=_float(a.get("elevation_ft")),
                runway_ft=runway,
            )
        )
    return sorted(out, key=lambda f: f.ident)


def write_airfields(path: Path, airfields: Iterable[Airfield]) -> int:
    rows = 0
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for f in airfields:
            elev = "" if f.elevation_ft is None else f"{f.elevation_ft:g}"
            writer.writerow([f.ident, f.icao or "", f.name, f.lat, f.lon, elev, f.runway_ft])
            rows += 1
    tmp.replace(path)
    return rows


def load_airfields(path: Path = AIRFIELDS_PATH) -> list[Airfield]:
    with path.open(newline="") as fh:
        return [
            Airfield(
                ident=r["ident"],
                icao=r["icao"] or None,
                name=r["name"],
                lat=float(r["lat"]),
                lon=float(r["lon"]),
                elevation_ft=_float(r["elevation_ft"]),
                runway_ft=int(r["runway_ft"]),
            )
            for r in csv.DictReader(fh)
        ]


def update_airfields(
    cfg: Mapping[str, Any], fetch: Callable[[str], bytes], out: Path = AIRFIELDS_PATH
) -> int:
    """Download OurAirports through `fetch` (rate limited by the caller) and rewrite `out`."""

    def rows(url: str) -> list[dict[str, str]]:
        return list(csv.DictReader(io.StringIO(fetch(url).decode("utf-8"))))

    airfields = filter_ourairports(
        rows(cfg["airports_url"]),
        rows(cfg["runways_url"]),
        types=tuple(cfg["types"]),
        min_runway_ft=int(cfg["min_runway_ft"]),
    )
    return write_airfields(out, airfields)


class AirfieldIndex:
    def __init__(self, airfields: Iterable[Airfield]) -> None:
        self._cells: dict[str, list[Airfield]] = defaultdict(list)
        for f in airfields:
            self._cells[h3.latlng_to_cell(f.lat, f.lon, _INDEX_RES)].append(f)
        self._spacing_km = math.sqrt(3) * h3.average_hexagon_edge_length(_INDEX_RES, unit="km")

    def nearest(self, lat: float, lon: float, radius_km: float) -> tuple[Airfield, float] | None:
        """Nearest airfield within `radius_km`. Ties: longer runway, then ident."""
        rings = math.ceil(radius_km / self._spacing_km) + 1
        best: tuple[float, int, str, Airfield] | None = None
        for cell in h3.grid_disk(h3.latlng_to_cell(lat, lon, _INDEX_RES), rings):
            for f in self._cells.get(cell, ()):
                d = haversine_km(lat, lon, f.lat, f.lon)
                key = (d, -f.runway_ft, f.ident, f)
                if d <= radius_km and (best is None or key[:3] < best[:3]):
                    best = key
        return (best[3], best[0]) if best else None
