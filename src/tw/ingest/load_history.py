"""Load `globe_history` dumps into date-partitioned points + coverage (milestone M1)."""

from __future__ import annotations

import io
import re
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
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
    stray_rows: int = 0  # trace rows outside the dump's day, not loaded
    corrupt_traces: tuple[str, ...] = ()  # trace files that did not decode, skipped


_TAG_DAY = re.compile(r"v(\d{4})\.(\d{2})\.(\d{2})-")


def day_from_tag(tag: str) -> date | None:
    """`v2025.10.27-planes-readsb-prod-0` -> 2025-10-27."""
    m = _TAG_DAY.match(tag)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


SECONDS_PER_DAY = 86400


def _row_in_window(row: Any, base: float, start: float) -> bool:
    if not isinstance(row, list) or not row or not isinstance(row[0], int | float):
        return False
    return start <= base + row[0] < start + SECONDS_PER_DAY


def load_docs(
    docs: Iterable[dict[str, Any]],
    data_dir: Path,
    fleet: Fleet,
    part: str,
    day: date | None = None,
) -> LoadSummary:
    """Parse trace documents, write points and coverage partitions.

    A dump holds one UTC day: `day`, or the first trace's day when not given. Only rows inside
    that day are loaded. Releases also carry rows from before midnight (negative `dt`) and
    leftover traces dated earlier days, almost all duplicates of points already in those
    days' own releases; they are counted in `stray_rows`, so each release writes one day.

    Re-loading the same dump replaces its part files, so loads are idempotent. Nothing is
    written until the whole dump has been read, so a failed download leaves no partial day.
    """
    providers = load_yaml("providers.yaml")
    coverage = CoverageAccumulator(sample_seconds=float(providers["coverage_sample_seconds"]))
    candidates: list[RawPoint] = []
    observed: dict[str, str] = {}  # hex -> type seen in this dump
    tracked = fleet.tracked_types
    traces = stray = 0
    # One pass: coverage sees every aircraft; only military-block traces become points.
    for doc in docs:
        icao, base, rows = doc.get("icao"), doc.get("timestamp"), doc.get("trace") or []
        if not isinstance(icao, str) or not isinstance(base, int | float):
            continue
        if day is None:
            day = datetime.fromtimestamp(base, tz=UTC).date()
        start = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()
        in_day = [r for r in rows if _row_in_window(r, base, start)]
        stray += len(rows) - len(in_day)
        if not in_day:
            continue
        traces += 1
        hex_code, non_icao = clean_hex(icao)
        coverage.add_trace_rows(icao.lower(), float(base), in_day)  # keep "~" distinct
        if non_icao or not fleet.in_military_block(hex_code):
            continue
        type_code = (clean_str(doc.get("t")) or "").upper() or None
        if type_code:
            observed[hex_code] = type_code
        # Untracked types only feed the registry; parse points for tracked or untyped traces.
        if type_code is None or type_code in tracked:
            # Parse the whole trace so callsigns carry forward across midnight, then trim.
            end = start + SECONDS_PER_DAY
            candidates.extend(p for p in parse_trace(doc) if start <= p.ts.timestamp() < end)

    # Resolve types from this day and earlier ones only (SPEC §11).
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
        stray_rows=stray,
    )


def load_dump(source: Path, data_dir: Path, fleet: Fleet, part: str | None = None) -> LoadSummary:
    """Load a local dump: a directory of trace files or a tar."""
    name = source.name.split(".tar")[0]
    return load_docs(iter_trace_docs(source), data_dir, fleet, part or name, day_from_tag(name))


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
    Trace files that do not decode are skipped and listed, up to `cfg.max_corrupt_traces`;
    beyond that the day fails (ValueError) without writing anything.
    """
    for attempt in range(1, cfg.max_attempts + 1):
        found = open_day(day, cfg, opener, limiter)
        if found is None:
            return None
        corrupt: list[str] = []

        def on_corrupt(name: str, corrupt: list[str] = corrupt, tag: str = found.tag) -> None:
            corrupt.append(name)
            if len(corrupt) > cfg.max_corrupt_traces:
                raise ValueError(f"{len(corrupt)} corrupt trace files in {tag}")

        try:
            with io.BufferedReader(found.stream, buffer_size=1 << 20) as buffered:
                docs = iter_trace_docs_from_stream(buffered, on_corrupt)
                summary = load_docs(docs, data_dir, fleet, found.tag, day)
            summary = replace(summary, corrupt_traces=tuple(corrupt))
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
