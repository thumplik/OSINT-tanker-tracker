"""Parser for ADSBx v2 `/v2/...` responses (adsb.lol, airplanes.live, adsb.fi)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from tw.ingest.schema import RawPoint, as_float, clean_hex, clean_str, parse_alt


def _now_seconds(payload: dict[str, Any]) -> float:
    now = payload.get("now")
    if not isinstance(now, int | float):
        raise ValueError("v2 payload has no numeric 'now'")
    # adsb.lol and airplanes.live report milliseconds; readsb aircraft.json reports seconds.
    return now / 1000.0 if now > 1e11 else float(now)


def parse_v2(payload: dict[str, Any], provider: str) -> Iterator[RawPoint]:
    now = _now_seconds(payload)
    for ac in payload.get("ac") or []:
        hex_raw = ac.get("hex")
        if not isinstance(hex_raw, str):
            continue
        hex_code, non_icao = clean_hex(hex_raw)
        lat, lon = as_float(ac.get("lat")), as_float(ac.get("lon"))
        age = ac.get("seen_pos") if lat is not None else ac.get("seen")
        ts = now - (age if isinstance(age, int | float) else 0.0)
        alt_baro, on_ground = parse_alt(ac.get("alt_baro"))
        yield RawPoint(
            ts=datetime.fromtimestamp(ts, tz=UTC),
            hex=hex_code,
            callsign=clean_str(ac.get("flight")),
            type=(clean_str(ac.get("t")) or "").upper() or None,
            lat=lat,
            lon=lon,
            alt_baro=alt_baro,
            on_ground=on_ground,
            gs=as_float(ac.get("gs")),
            track=as_float(ac.get("track")),
            position_source=clean_str(ac.get("type")),
            provider=provider,
            is_non_icao=non_icao,
        )
