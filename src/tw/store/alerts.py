"""Stored alerts (SPEC §5, §11).

- `alerts/date=D/`: official alerts from the nightly run on day D's release. Write-once: a later
  run for D never replaces them (CLAUDE.md rule 8). Only these can ever reach the post queue.
- `provisional_alerts/date=D/part-HH`: hourly rolling-window alerts on live data, coverage
  unchecked. Kept for comparison with the official run; never queued, never posted.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa

from tw.detectors.types import Alert
from tw.store.parquet import partition_dir, read_day, register_schema, write_partition

OFFICIAL = "alerts"
PROVISIONAL = "provisional_alerts"
_TS = pa.timestamp("us", tz="UTC")
_FIELDS = [
    ("alert_id", pa.string()),
    ("detector", pa.string()),
    ("region", pa.string()),
    ("window_start", _TS),
    ("window_end", _TS),
    ("observed", pa.float64()),
    ("expected", pa.float64()),
    ("score", pa.float64()),
    ("severity", pa.string()),
    ("evidence", pa.string()),  # JSON
    ("sortie_ids", pa.list_(pa.string())),
    ("created_at", _TS),
    ("status", pa.string()),
    ("basis_days", pa.list_(pa.date32())),
    ("config_hash", pa.string()),
    ("code_version", pa.string()),
]
ALERT_SCHEMA = pa.schema(_FIELDS)
register_schema(OFFICIAL, ALERT_SCHEMA)
register_schema(PROVISIONAL, ALERT_SCHEMA)


def to_row(a: Alert) -> dict[str, Any]:
    row = {name: getattr(a, name) for name, _ in _FIELDS}
    row["evidence"] = json.dumps(a.evidence, sort_keys=True, default=str)
    row["sortie_ids"] = list(a.sortie_ids)
    row["basis_days"] = list(a.basis_days)
    return row


def from_row(row: dict[str, Any]) -> Alert:
    return Alert(
        **{
            **row,
            "evidence": json.loads(row["evidence"]),
            "sortie_ids": tuple(row["sortie_ids"]),
            "basis_days": tuple(row["basis_days"]),
        }
    )


def official_exists(root: Path, day: date) -> bool:
    return partition_dir(root, OFFICIAL, day).exists()


def write_official(root: Path, day: date, alerts: Sequence[Alert]) -> bool:
    """Write day D's official alerts once (an empty day too, so it reads as "ran, nothing
    fired"). Returns False, writing nothing, if D already has them."""
    if official_exists(root, day):
        return False
    write_partition(root, OFFICIAL, day, [to_row(a) for a in alerts], ALERT_SCHEMA, "official")
    return True


def write_provisional(root: Path, day: date, hour: int, alerts: Sequence[Alert]) -> None:
    rows = [to_row(a) for a in alerts]
    write_partition(root, PROVISIONAL, day, rows, ALERT_SCHEMA, f"{hour:02d}")


def read_alerts(root: Path, dataset: str, days: Iterable[date]) -> list[Alert]:
    out: list[Alert] = []
    for day in sorted(set(days)):
        out += [from_row(r) for r in read_day(root, dataset, day).to_pylist()]
    return sorted(out, key=lambda a: (a.created_at, a.alert_id))
