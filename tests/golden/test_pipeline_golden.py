"""Golden file: a fixed synthetic history gives fixed alerts (SPEC §12). If this changes, the
commit message must say why. Regenerate with TW_UPDATE_GOLDEN=1 after reviewing the diff."""

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import h3

from tw.pipeline import Context, run_days
from tw.store.parquet import COVERAGE_SCHEMA, POINT_SCHEMA, SORTIE_SCHEMA, write_partition

GOLDEN = Path(__file__).parent / "pipeline_surge.json"
START, SURGE = date(2026, 7, 1), date(2026, 8, 31)
EUROPE = h3.latlng_to_cell(50.0, 8.0, 4)
INCIRLIK = (37.0, 35.43)
EAST_MED = h3.latlng_to_cell(*INCIRLIK, 4)


def sortie(day: date, i: int, dest=None, end=(50.0, 8.0), hex_code=None, family="tanker"):
    start = datetime(day.year, day.month, day.day, 8, tzinfo=UTC) + timedelta(minutes=i)
    return {
        "sortie_id": f"{hex_code or f'ae{i:04x}'}-{start:%Y%m%dT%H%M%SZ}",
        "hex": hex_code or f"ae{i:04x}",
        "type": "K35R",
        "families": [family],
        "callsign": None,
        "start_ts": start,
        "end_ts": start + timedelta(hours=3),
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


def history(root: Path) -> list[date]:
    days = [START + timedelta(days=n) for n in range((SURGE - START).days + 1)]
    for day in days:
        n = 14 if day == SURGE else 5 + (day.toordinal() % 2)  # 5 or 6 a day, then 14
        rows = [sortie(day, i, dest="ETAR") for i in range(n)]
        if day >= SURGE - timedelta(days=1):  # two C-17s into a field never used before
            hex_code = "ae0c17" if day < SURGE else "ae0c18"
            rows.append(sortie(day, 99, "LTAG", INCIRLIK, hex_code, "strategic_airlift"))
        hour = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
        write_partition(root, "points", day, [], POINT_SCHEMA, "t")
        write_partition(root, "sorties", day, rows, SORTIE_SCHEMA, "sorties")
        coverage = [{"hour": hour, "h3_r4": cell, "aircraft": 1000} for cell in (EUROPE, EAST_MED)]
        write_partition(root, "coverage", day, coverage, COVERAGE_SCHEMA, "t")
    return days


def summarize(alerts) -> list[dict]:
    return [
        {
            "id": a.alert_id,
            "detector": a.detector,
            "region": a.region,
            "day": str(a.window_end.date() - timedelta(days=1)),
            "observed": a.observed,
            "expected": a.expected,
            "score": a.score,
            "severity": a.severity,
            "status": a.status,
            "evidence": {k: v for k, v in sorted(a.evidence.items())},
        }
        for a in alerts
    ]


def test_pipeline_golden(tmp_path):
    days = history(tmp_path)
    alerts, missing, _ = run_days(days, Context(tmp_path))
    got = summarize(alerts)
    if os.environ.get("TW_UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
    assert missing == []
    assert got == json.loads(GOLDEN.read_text())
    # And run again from scratch: identical (determinism).
    again, _, _ = run_days(days, Context(tmp_path))
    assert summarize(again) == got
