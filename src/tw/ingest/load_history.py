"""Load `globe_history` dumps into date-partitioned points + coverage (milestone M1)."""

from __future__ import annotations

import io
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from tw.config import Fleet, load_yaml
from tw.ingest.globe_history import iter_trace_docs, iter_trace_docs_from_stream, parse_trace
from tw.ingest.history_download import HistoryConfig, Limiter, Opener, open_day
from tw.ingest.schema import RawPoint, clean_hex, clean_str
from tw.normalize.coverage import CoverageAccumulator
from tw.normalize.points import normalize
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
    observed: dict[str, str] = {}  # hex -> type seen in this dump
    tracked = fleet.tracked_types
    dump_day: date | None = None
    traces = 0
    # One pass: coverage sees every aircraft; only military-block traces become points.
    for doc in docs:
        icao, base, rows = doc.get("icao"), doc.get("timestamp"), doc.get("trace") or []
        if not isinstance(icao, str) or not isinstance(base, int | float):
            continue
        traces += 1
        if dump_day is None:
            dump_day = datetime.fromtimestamp(base, tz=UTC).date()
        hex_code, non_icao = clean_hex(icao)
        coverage.add_trace_rows(icao.lower(), float(base), rows)  # keep "~" distinct
        if non_icao or not fleet.in_military_block(hex_code):
            continue
        type_code = (clean_str(doc.get("t")) or "").upper() or None
        if type_code:
            observed[hex_code] = type_code
        # Untracked types only feed the registry; parse points for tracked or untyped traces.
        if type_code is None or type_code in tracked:
            candidates.extend(parse_trace(doc))

    # A dump holds one UTC day; resolve types from that day and earlier ones only (SPEC §11).
    day = dump_day
    registry = {**load_registry(data_dir, day), **observed} if day else observed
    points = list(normalize(candidates, fleet, registry))
    per_day = write_points(data_dir, points, part=part)
    write_coverage(data_dir, coverage.rows(), part=part)
    if day:
        save_registry(data_dir, day, observed, part=part)
    return LoadSummary(
        traces=traces,
        military_points=len(candidates),
        kept_points=len(points),
        points_per_day=per_day,
    )


def load_dump(source: Path, data_dir: Path, fleet: Fleet, part: str | None = None) -> LoadSummary:
    """Load a local dump: a directory of trace files or a tar."""
    return load_docs(iter_trace_docs(source), data_dir, fleet, part or source.name.split(".")[0])


@dataclass(frozen=True)
class DayResult:
    tag: str
    skipped_bytes: int
    summary: LoadSummary


def load_day(
    day: date,
    data_dir: Path,
    fleet: Fleet,
    cfg: HistoryConfig,
    opener: Opener,
    limiter: Limiter,
) -> DayResult | None:
    """Stream one day's release from adsb.lol and load it. Returns None if no release exists.

    A failed stream is retried from the start; nothing is written until a full read succeeds.
    """
    for attempt in range(1, cfg.max_attempts + 1):
        found = open_day(day, cfg, opener, limiter)
        if found is None:
            return None
        try:
            with io.BufferedReader(found.stream, buffer_size=1 << 20) as buffered:
                docs = iter_trace_docs_from_stream(buffered)
                summary = load_docs(docs, data_dir, fleet, found.tag)
            return DayResult(found.tag, found.skipped_bytes, summary)
        except (OSError, EOFError, tarfile.TarError):
            if attempt == cfg.max_attempts:
                raise
    raise AssertionError("unreachable")


# --- Parallel backfill -------------------------------------------------------------------------
# Each process loads whole days. Every process gets 1/N of each rate limit, so the pool as a
# whole stays within the configured limits.

_worker_state: dict[str, Any] = {}


def init_worker(share: int) -> None:
    from tw.config import load_fleet
    from tw.ingest.history_download import http_opener
    from tw.ingest.ratelimit import RateLimiter

    cfg = HistoryConfig.load()
    _worker_state.update(
        cfg=cfg,
        fleet=load_fleet(),
        opener=http_opener(cfg),
        limiter=RateLimiter.from_config(share=share),
    )


def load_day_in_worker(day: date, data_dir: Path) -> tuple[date, DayResult | None, str | None]:
    """Returns (day, result, error). Errors are returned, not raised, so one bad day doesn't
    stop the rest of the backfill."""
    s = _worker_state
    try:
        return day, load_day(day, data_dir, s["fleet"], s["cfg"], s["opener"], s["limiter"]), None
    except Exception as e:
        return day, None, f"{type(e).__name__}: {e}"
