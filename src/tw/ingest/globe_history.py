"""Parser for readsb `globe_history` trace files (adsb.lol daily dumps).

A trace file is JSON (often gzip-compressed regardless of file name)::

    {"icao": "ae1234", "r": "...", "t": "K35R", "timestamp": <epoch s>,
     "trace": [[dt, lat, lon, alt, gs, track, flags, vrate, details, source, ...], ...]}

`details` is null or an object with fields such as `flight`; the last seen callsign is
carried forward. Fixtures in tests are synthetic; verify this layout against a real dump.
"""

from __future__ import annotations

import gzip
import json
import tarfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tw.ingest.schema import RawPoint, as_float, clean_hex, clean_str, parse_alt

PROVIDER = "adsb_lol_history"

# Trace row indices (readsb trace_full layout).
_DT, _LAT, _LON, _ALT, _GS, _TRACK, _FLAGS, _VRATE, _DETAILS, _SOURCE = range(10)


def decode_bytes(data: bytes) -> dict[str, Any]:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    doc = json.loads(data)
    if not isinstance(doc, dict):
        raise ValueError("trace file is not a JSON object")
    return doc


def parse_trace(doc: dict[str, Any], provider: str = PROVIDER) -> Iterator[RawPoint]:
    icao = doc.get("icao")
    base = doc.get("timestamp")
    if not isinstance(icao, str) or not isinstance(base, int | float):
        return
    hex_code, non_icao = clean_hex(icao)
    type_code = (clean_str(doc.get("t")) or "").upper() or None
    callsign: str | None = None
    for row in doc.get("trace") or []:
        if not isinstance(row, list) or len(row) <= _TRACK:
            continue
        details = row[_DETAILS] if len(row) > _DETAILS else None
        if isinstance(details, dict):
            callsign = clean_str(details.get("flight")) or callsign
        alt_baro, on_ground = parse_alt(row[_ALT])
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


def iter_trace_docs(path: Path) -> Iterator[dict[str, Any]]:
    """Yield trace documents from a directory tree or a (possibly split-and-joined) tar."""
    if path.is_dir():
        for file in sorted(path.rglob("trace_full_*")):
            yield decode_bytes(file.read_bytes())
        return
    with tarfile.open(path, mode="r:*") as tar:
        for member in tar:
            if not member.isfile() or "trace_full_" not in Path(member.name).name:
                continue
            fh = tar.extractfile(member)
            if fh is not None:
                yield decode_bytes(fh.read())


def iter_points(path: Path) -> Iterator[RawPoint]:
    for doc in iter_trace_docs(path):
        yield from parse_trace(doc)
