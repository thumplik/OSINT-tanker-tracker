"""Live polling of the ADSBx v2 military endpoint (SPEC §2, §4), with provider failover.

Every request goes through the rate limiter. Each poll is written to the raw store
(`raw/date=D/<provider>-HH.jsonl.gz`, kept 14 days) and the request log
(`worker/requests/date=D.jsonl`), the evidence that rate limits were respected. Live points are
derived from raw, never kept only in memory, so a crash loses nothing that was fetched.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from tw.config import Fleet, load_yaml
from tw.ingest.adsbx_v2 import parse_v2
from tw.ingest.schema import RawPoint
from tw.normalize.points import build_registry, military_candidates, normalize
from tw.store.parquet import POINT_SCHEMA, load_registry, write_partition


class Fetch(Protocol):
    def __call__(self, url: str) -> tuple[int, bytes]: ...


class Limiter(Protocol):
    def acquire(self, provider: str) -> None: ...


@dataclass(frozen=True)
class LiveProvider:
    name: str
    url: str


@dataclass(frozen=True)
class LiveConfig:
    providers: tuple[LiveProvider, ...]  # primary first
    poll_seconds: float
    timeout_s: float
    failover_after: int
    failback_s: float
    backoff_base_s: float
    max_backoff_s: float
    raw_retention_days: int
    user_agent: str

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> LiveConfig:
        env = os.environ if env is None else env
        providers_yaml = load_yaml("providers.yaml")
        live = providers_yaml["live"]
        providers = []
        for name in live["order"]:
            spec = live["providers"][name]
            base = (env.get(spec["env"]) or spec["default_base_url"]).rstrip("/")
            providers.append(LiveProvider(name, base + spec["path"]))
        return cls(
            providers=tuple(providers),
            poll_seconds=float(live["poll_seconds"]),
            timeout_s=float(live["timeout_s"]),
            failover_after=int(live["failover_after"]),
            failback_s=float(live["failback_minutes"]) * 60,
            backoff_base_s=float(live["backoff_base_s"]),
            max_backoff_s=float(live["max_backoff_s"]),
            raw_retention_days=int(live["raw_retention_days"]),
            user_agent=str(providers_yaml["history"]["user_agent"]),
        )


@dataclass(frozen=True)
class PollResult:
    provider: str
    url: str
    fetched_at: datetime
    status: int | None  # HTTP status; None when no response arrived
    payload: dict[str, Any] | None  # set only for a valid v2 response
    error: str | None
    bytes: int
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        return self.payload is not None


def _valid_payload(body: bytes) -> dict[str, Any]:
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("now"), int | float):
        raise ValueError("not a v2 response: no numeric 'now'")
    if payload.get("ac") is not None and not isinstance(payload["ac"], list):
        raise ValueError("not a v2 response: 'ac' is not a list")
    return payload


class LiveClient:
    """Polls the active provider. After `failover_after` consecutive failures it moves to the
    next provider; `failback_s` after leaving the primary it tries the primary again (one
    failure there sends it straight back). After any failure it waits, doubling from
    `backoff_base_s` up to `max_backoff_s`, before the next request."""

    def __init__(
        self,
        cfg: LiveConfig,
        fetch: Fetch,
        limiter: Limiter,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = cfg
        self._fetch = fetch
        self._limiter = limiter
        self._clock = clock
        self.active = 0
        self.failures = 0  # consecutive, on the active provider
        self.streak = 0  # consecutive, across providers (drives the backoff)
        self.left_primary_at: float | None = None
        self.next_allowed = 0.0
        self.failovers = 0

    @property
    def provider(self) -> LiveProvider:
        return self.cfg.providers[self.active]

    def _switch(self, index: int) -> None:
        if index != self.active:
            self.failovers += 1
        self.active = index
        self.failures = 0
        self.left_primary_at = None if index == 0 else (self.left_primary_at or self._clock())

    def poll(self) -> PollResult | None:
        """One request, or None while backing off."""
        now = self._clock()
        if now < self.next_allowed:
            return None
        if self.left_primary_at is not None and now - self.left_primary_at >= self.cfg.failback_s:
            self._switch(0)
            self.failures = self.cfg.failover_after - 1
        provider = self.provider
        self._limiter.acquire(provider.name)
        started = self._clock()
        status: int | None = None
        payload: dict[str, Any] | None = None
        error: str | None = None
        size = 0
        try:
            status, body = self._fetch(provider.url)
            size = len(body)
            if status != 200:
                error = f"HTTP {status}"
            else:
                payload = _valid_payload(body)
        except (OSError, ValueError) as e:  # URLError and timeouts are OSErrors
            error = f"{type(e).__name__}: {e}"
        finished = self._clock()
        result = PollResult(
            provider=provider.name,
            url=provider.url,
            fetched_at=datetime.fromtimestamp(started, tz=UTC),
            status=status,
            payload=payload,
            error=error,
            bytes=size,
            elapsed_ms=round((finished - started) * 1000),
        )
        self._record(result, finished)
        return result

    def _record(self, result: PollResult, now: float) -> None:
        if result.ok:
            self.failures = self.streak = 0
            return
        self.failures += 1
        self.streak += 1
        wait = min(self.cfg.max_backoff_s, self.cfg.backoff_base_s * 2 ** (self.streak - 1))
        self.next_allowed = now + wait
        if self.failures >= self.cfg.failover_after:
            self._switch((self.active + 1) % len(self.cfg.providers))


def http_fetch(cfg: LiveConfig) -> Fetch:
    """urllib GET with the project User-Agent; asks for gzip and decodes it."""

    def fetch(url: str) -> tuple[int, bytes]:
        req = urllib.request.Request(
            url, headers={"User-Agent": cfg.user_agent, "Accept-Encoding": "gzip"}
        )
        try:
            with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = zlib.decompress(body, 16 + zlib.MAX_WBITS)
                return int(resp.status), body
        except urllib.error.HTTPError as e:
            return e.code, b""

    return fetch


# --- Raw store and request log ------------------------------------------------------------------


def hour_start(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def raw_path(root: Path, provider: str, hour: datetime) -> Path:
    return root / "raw" / f"date={hour.date().isoformat()}" / f"{provider}-{hour:%H}.jsonl.gz"


def append_raw(root: Path, result: PollResult) -> None:
    """One gzip member per poll, so a crash mid-write loses at most that poll."""
    if result.payload is None:
        return
    path = raw_path(root, result.provider, hour_start(result.fetched_at))
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "fetched_at": result.fetched_at.isoformat(),
        "provider": result.provider,
        "url": result.url,
        "status": result.status,
        "payload": result.payload,
    }
    with gzip.open(path, "ab") as f:
        f.write((json.dumps(line, separators=(",", ":")) + "\n").encode())


def read_raw_hour(root: Path, hour: datetime) -> Iterator[dict[str, Any]]:
    """Raw lines fetched in `hour` from every provider. A truncated final member (a crash
    mid-write) ends that file's lines; the earlier ones are kept."""
    day_dir = root / "raw" / f"date={hour.date().isoformat()}"
    for path in sorted(day_dir.glob(f"*-{hour:%H}.jsonl.gz")):
        try:
            with gzip.open(path, "rt") as f:
                for text in f:
                    try:
                        yield json.loads(text)
                    except json.JSONDecodeError:
                        break
        except (EOFError, gzip.BadGzipFile, zlib.error):
            continue


def request_log_path(root: Path, day: date) -> Path:
    return root / "worker" / "requests" / f"date={day.isoformat()}.jsonl"


def log_request(root: Path, result: PollResult) -> None:
    path = request_log_path(root, result.fetched_at.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": result.fetched_at.isoformat(),
        "provider": result.provider,
        "status": result.status,
        "ok": result.ok,
        "bytes": result.bytes,
        "ms": result.elapsed_ms,
        "error": result.error,
    }
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def read_request_log(root: Path, first: date, last: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    day = first
    while day <= last:
        path = request_log_path(root, day)
        if path.exists():
            with path.open() as f:
                for text in f:
                    try:
                        rows.append(json.loads(text))
                    except json.JSONDecodeError:
                        continue  # a torn last line after a crash
        day += timedelta(days=1)
    return rows


def prune_raw(root: Path, today: date, keep_days: int) -> list[Path]:
    """Delete raw day directories older than `keep_days`. Only `raw/` is touched."""
    base = root / "raw"
    if not base.exists():
        return []
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    removed = []
    for d in sorted(base.iterdir()):
        if d.is_dir() and d.name.startswith("date=") and d.name[5:] < cutoff:
            shutil.rmtree(d)
            removed.append(d)
    return removed


# --- Live points ---------------------------------------------------------------------------------


def live_points_for_hour(
    root: Path, hour: datetime, fleet: Fleet, registry_dir: Path
) -> list[dict[str, Any]]:
    """Tracked-fleet points timestamped in `hour`, from raw fetched in that hour and the next
    (a point's time is the poll time minus its age, so the first polls of the next hour can
    carry it). Repeated reports of one position (same hex and time) are kept once. Untyped hexes
    resolve from types seen live that hour, then the official registry as of the hour's day."""
    end = hour + timedelta(hours=1)
    raw: list[RawPoint] = []
    for h in (hour, end):
        for line in read_raw_hour(root, h):
            raw.extend(military_candidates(parse_v2(line["payload"], line["provider"]), fleet))
    inside = [p for p in raw if hour <= p.ts < end]
    registry = build_registry(inside, load_registry(registry_dir, hour.date()))
    seen: set[tuple[str, int]] = set()
    rows = []
    for p in sorted(normalize(inside, fleet, registry), key=lambda p: (p.hex, p.ts, p.provider)):
        key = (p.hex, round(p.ts.timestamp() * 10))  # v2 ages have 0.1 s resolution
        if key in seen:
            continue
        seen.add(key)
        rows.append(asdict(p))
    return rows


def flush_hour(root: Path, live_dir: Path, hour: datetime, fleet: Fleet, registry_dir: Path) -> int:
    """Rewrite `live/points/date=D/part-live-HH` from raw. Idempotent."""
    rows = live_points_for_hour(root, hour, fleet, registry_dir)
    write_partition(live_dir, "points", hour.date(), rows, POINT_SCHEMA, f"live-{hour:%H}")
    return len(rows)
