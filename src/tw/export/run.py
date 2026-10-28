"""Write day views to a sink: `days/D.json`, `days/D.r6.json`, `index.json`, `meta.json`.

Exported files are views, not records: re-exporting a day replaces its file with what the view
gives now (an official day replaces its provisional file once the release is loaded). Stored
alerts and snapshots are never touched.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from tw.config import load_yaml
from tw.export.sinks import Sink
from tw.views.day import ViewContext, day_heat_r6, day_view, site_index, site_meta


def encode(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()


@dataclass(frozen=True)
class ExportResult:
    days: list[date]
    missing: list[date]
    bytes: int


def export(ctx: ViewContext, days: Iterable[date], sink: Sink, now: datetime) -> ExportResult:
    cache = load_yaml("export.yaml")["cache_seconds"]
    written: list[date] = []
    missing: list[date] = []
    total = 0
    for day in sorted(set(days)):
        view = day_view(ctx, day, now)
        r6 = day_heat_r6(ctx, day)
        if view is None or r6 is None:
            missing.append(day)
            continue
        max_age = int(cache[f"{view['meta']['tier']}_day"])
        for path, obj in ((f"days/{day}.json", view), (f"days/{day}.r6.json", r6)):
            body = encode(obj)
            sink.put(path, body, max_age)
            total += len(body)
        written.append(day)
    for path, obj, key in (
        ("meta.json", site_meta(ctx), "meta"),
        ("index.json", site_index(ctx), "index"),
    ):
        body = encode(obj)
        sink.put(path, body, int(cache[key]))
        total += len(body)
    return ExportResult(written, missing, total)
