"""Load `globe_history` dumps into date-partitioned points + coverage (milestone M1)."""

from __future__ import annotations

import io
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from tw.config import Fleet, load_yaml
from tw.ingest.globe_history import iter_trace_docs, iter_trace_docs_from_stream, parse_trace
from tw.ingest.history_download import HistoryConfig, Opener, open_day
from tw.ingest.ratelimit import RateLimiter
from tw.ingest.schema import RawPoint, clean_hex
from tw.normalize.coverage import CoverageAccumulator
from tw.normalize.points import build_registry, normalize
from tw.store.parquet import load_registry, save_registry, write_coverage, write_points


@dataclass(frozen=True)
class LoadSummary:
    traces: int
    military_points: int
    kept_points: int
    points_per_day: dict[date, int]


def load_docs(
    docs: Iterable[dict[str, Any]], data_dir: Path, fleet: Fleet, part: str
) -> LoadSummary:
    """Parse trace documents, write points and coverage partitions.

    Re-loading the same dump replaces its part files, so loads are idempotent. Nothing is
    written until the whole dump has been read, so a failed download leaves no partial day.
    """
    providers = load_yaml("providers.yaml")
    coverage = CoverageAccumulator(sample_seconds=float(providers["coverage_sample_seconds"]))
    candidates: list[RawPoint] = []
    traces = 0
    # One pass: coverage sees every aircraft; only military-block traces become points.
    for doc in docs:
        icao, base, rows = doc.get("icao"), doc.get("timestamp"), doc.get("trace") or []
        if not isinstance(icao, str) or not isinstance(base, int | float):
            continue
        traces += 1
        hex_code, non_icao = clean_hex(icao)
        coverage.add_trace_rows(icao.lower(), float(base), rows)  # keep "~" distinct
        if not non_icao and fleet.in_military_block(hex_code):
            candidates.extend(parse_trace(doc))

    registry = build_registry(candidates, prior=load_registry(data_dir))
    points = list(normalize(candidates, fleet, registry))
    per_day = write_points(data_dir, points, part=part)
    write_coverage(data_dir, coverage.rows(), part=part)
    save_registry(data_dir, registry)
    return LoadSummary(
        traces=traces,
        military_points=len(candidates),
        kept_points=len(points),
        points_per_day=per_day,
    )


def load_dump(source: Path, data_dir: Path, fleet: Fleet, part: str | None = None) -> LoadSummary:
    """Load a local dump: a directory of trace files or a tar."""
    return load_docs(iter_trace_docs(source), data_dir, fleet, part or source.name.split(".")[0])


def load_day(
    day: date,
    data_dir: Path,
    fleet: Fleet,
    cfg: HistoryConfig,
    opener: Opener,
    limiter: RateLimiter,
) -> tuple[str, LoadSummary] | None:
    """Stream one day's release from adsb.lol and load it. Returns None if no release exists.

    A failed stream is retried from the start; nothing is written until a full read succeeds.
    """
    for attempt in range(1, cfg.max_attempts + 1):
        found = open_day(day, cfg, opener, limiter)
        if found is None:
            return None
        tag, stream = found
        try:
            with io.BufferedReader(stream, buffer_size=1 << 20) as buffered:
                return tag, load_docs(iter_trace_docs_from_stream(buffered), data_dir, fleet, tag)
        except (OSError, EOFError, tarfile.TarError):
            if attempt == cfg.max_attempts:
                raise
    raise AssertionError("unreachable")
