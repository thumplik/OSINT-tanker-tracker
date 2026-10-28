"""Synthetic stored history for pipeline tests: points, sorties and coverage partitions."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import h3

from tw.store.parquet import COVERAGE_SCHEMA, POINT_SCHEMA, SORTIE_SCHEMA, write_partition

EUROPE = h3.latlng_to_cell(50.0, 8.0, 4)
INCIRLIK = (37.0, 35.43)
EAST_MED = h3.latlng_to_cell(*INCIRLIK, 4)


def sortie(
    day: date,
    i: int,
    dest=None,
    end=(50.0, 8.0),
    hex_code=None,
    family="tanker",
    minutes: int = 180,
) -> dict:
    start = datetime(day.year, day.month, day.day, 8, tzinfo=UTC) + timedelta(minutes=i)
    hex_code = hex_code or f"ae{i:04x}"
    return {
        "sortie_id": f"{hex_code}-{start:%Y%m%dT%H%M%SZ}",
        "hex": hex_code,
        "type": "K35R",
        "families": [family],
        "callsign": None,
        "start_ts": start,
        "end_ts": start + timedelta(minutes=minutes),
        "origin_icao": "ETAR",
        "dest_icao": dest,
        "start_lat": 50.0,
        "start_lon": 8.0,
        "end_lat": end[0],
        "end_lon": end[1],
        "h3_cells": [EUROPE],
        "max_alt": 30000,
        "n_points": 180,
        "low_quality_share": 0.0,
        "start_seen_low": True,
        "end_seen_low": True,
        "end_reason": "landing",
        "is_open": False,
        "start_truncated": False,
        "is_orbit": None,
        "orbit_hours": None,
    }


def store_day(
    root: Path,
    day: date,
    sorties: list[dict],
    coverage: dict[str, int] | None = None,
    *,
    with_sorties: bool = True,
    with_coverage: bool = True,
) -> None:
    """Write one day's partitions. `coverage` maps r4 cell -> aircraft (default: Europe and the
    Eastern Med at 1000 each)."""
    coverage = coverage if coverage is not None else {EUROPE: 1000, EAST_MED: 1000}
    hour = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
    write_partition(root, "points", day, [], POINT_SCHEMA, "t")
    if with_sorties:
        write_partition(root, "sorties", day, sorties, SORTIE_SCHEMA, "sorties")
    if with_coverage:
        rows = [{"hour": hour, "h3_r4": c, "aircraft": n} for c, n in coverage.items()]
        write_partition(root, "coverage", day, rows, COVERAGE_SCHEMA, "t")


def flat_history(root: Path, days: list[date], per_day: int = 5) -> None:
    """`per_day` tanker sorties over Europe every day, normal coverage."""
    for day in days:
        store_day(root, day, [sortie(day, i, dest="ETAR") for i in range(per_day)])


def days_between(first: date, last: date) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]
