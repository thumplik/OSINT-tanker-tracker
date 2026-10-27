"""Raw point as parsed from any ADSBx-v2-compatible provider, before filtering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RawPoint:
    ts: datetime  # UTC
    hex: str  # lower-case, no "~" prefix
    callsign: str | None
    type: str | None  # ICAO type designator, upper-case
    lat: float | None
    lon: float | None
    alt_baro: int | None  # feet; None when unknown or on the ground
    on_ground: bool | None  # None = unknown. A missing position is NOT "on the ground".
    gs: float | None
    track: float | None
    position_source: str | None  # ADSBx v2 `type`: adsb_icao, mlat, mode_s, ...
    provider: str
    is_non_icao: bool = False  # "~" hex: TIS-B/ADS-R track file, not a real ICAO address


def clean_hex(raw: str) -> tuple[str, bool]:
    raw = raw.strip().lower()
    return raw.lstrip("~"), raw.startswith("~")


def clean_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def parse_alt(value: object) -> tuple[int | None, bool | None]:
    """ADSBx v2 `alt_baro` is an int (ft), the string "ground", or absent."""
    if value == "ground":
        return None, True
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int(value), False
    return None, None


def as_float(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None
