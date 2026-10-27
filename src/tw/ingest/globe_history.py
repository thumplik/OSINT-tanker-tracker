"""Parser for readsb `globe_history` trace files (adsb.lol daily dumps).

Verified against `v2026.10.25-planes-readsb-prod-0` (readsb 3.16.15). Each dump is a plain tar
of `./heatmap/*.bin.ttf` followed by `./traces/<last 2 hex>/trace_full_<hex>.json`, where every
trace file is gzip-compressed JSON despite the `.json` name::

    {"icao": "ae07f0", "r": "96-0006", "t": "C17", "dbFlags": 1, "desc": "...",
     "version": "readsb ...", "timestamp": <00:00 UTC of the dump day>,
     "trace": [[dt, lat, lon, alt, gs, track, flags, vrate, details, source,
                alt_geom, geom_rate, ias, roll], ...]}

- `dt` is seconds after `timestamp`, in [0, 86400): a dump holds exactly one UTC day.
- `alt` is an int (ft), "ground" or null. When `flags & 8` it is geometric, not barometric.
- `details` is null or an object with fields such as `flight` (space-padded callsign), only
  present on some rows, so the last seen callsign is carried forward.
- `t` is missing for some aircraft; normalize resolves those through the registry.
- `icao` starting with "~" is a non-ICAO (TIS-B/ADS-R) track.
"""

from __future__ import annotations

import gzip
import json
import tarfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from tw.ingest.schema import RawPoint, as_float, clean_hex, clean_str, parse_alt

PROVIDER = "adsb_lol_history"

# Trace row indices (readsb trace_full layout).
_DT, _LAT, _LON, _ALT, _GS, _TRACK, _FLAGS, _VRATE, _DETAILS, _SOURCE = range(10)

# Row flag bits (readsb).
FLAG_ALT_GEOMETRIC = 8


def decode_bytes(data: bytes) -> dict[str, Any]:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    doc = json.loads(data)
    if not isinstance(doc, dict):
        raise ValueError("trace file is not a JSON object")
    return doc


def _row_alt(row: list[Any]) -> tuple[int | None, bool | None]:
    alt, on_ground = parse_alt(row[_ALT])
    flags = row[_FLAGS] if isinstance(row[_FLAGS], int) else 0
    if alt is not None and flags & FLAG_ALT_GEOMETRIC:
        return None, on_ground  # airborne, but no barometric altitude on this row
    return alt, on_ground


def parse_trace(doc: dict[str, Any], provider: str = PROVIDER) -> Iterator[RawPoint]:
    icao = doc.get("icao")
    base = doc.get("timestamp")
    if not isinstance(icao, str) or not isinstance(base, int | float):
        return
    hex_code, non_icao = clean_hex(icao)
    type_code = (clean_str(doc.get("t")) or "").upper() or None
    callsign: str | None = None
    for row in doc.get("trace") or []:
        if not isinstance(row, list) or len(row) <= _FLAGS:
            continue
        details = row[_DETAILS] if len(row) > _DETAILS else None
        if isinstance(details, dict):
            callsign = clean_str(details.get("flight")) or callsign
        alt_baro, on_ground = _row_alt(row)
        source = row[_SOURCE] if len(row) > _SOURCE else None
        yield RawPoint(
            ts=datetime.fromtimestamp(base + row[_DT], tz=UTC),
            hex=hex_code,
            callsign=callsign,
            type=type_code,
            lat=as_float(row[_LAT]),
            lon=as_float(row[_LON]),
            alt_baro=alt_baro,
            on_ground=on_ground,
            gs=as_float(row[_GS]),
            track=as_float(row[_TRACK]),
            position_source=clean_str(source),
            provider=provider,
            is_non_icao=non_icao,
        )


def _is_trace_member(name: str) -> bool:
    return Path(name).name.startswith("trace_full_")


def iter_trace_docs_from_tar(tar: tarfile.TarFile) -> Iterator[dict[str, Any]]:
    for member in tar:
        if not member.isfile() or not _is_trace_member(member.name):
            continue
        fh = tar.extractfile(member)
        if fh is not None:
            yield decode_bytes(fh.read())


def iter_trace_docs_from_stream(stream: IO[bytes]) -> Iterator[dict[str, Any]]:
    """Read a tar from a non-seekable stream (e.g. concatenated HTTP parts) in one pass."""
    with tarfile.open(fileobj=stream, mode="r|*") as tar:
        yield from iter_trace_docs_from_tar(tar)


def iter_trace_docs(path: Path) -> Iterator[dict[str, Any]]:
    """Yield trace documents from a directory tree or a tar file."""
    if path.is_dir():
        for file in sorted(path.rglob("trace_full_*")):
            yield decode_bytes(file.read_bytes())
        return
    with tarfile.open(path, mode="r:*") as tar:
        yield from iter_trace_docs_from_tar(tar)


def iter_points(path: Path) -> Iterator[RawPoint]:
    for doc in iter_trace_docs(path):
        yield from parse_trace(doc)
