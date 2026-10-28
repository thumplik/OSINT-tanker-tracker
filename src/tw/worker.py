"""The always-on worker (SPEC §4): live polling, the provisional tier, and the nightly official run.

Jobs are plain methods taking the current UTC time, so tests drive them with a fake clock and
fixtures; `run()` puts them on an APScheduler. Every job is wrapped: an exception is logged to
`worker/events.jsonl` and the worker keeps running.

- poll (every `poll_seconds`): one rate-limited request → raw store + request log.
- flush (every `flush_minutes`): re-derive this hour's and last hour's live points from raw.
- provisional (hourly): live sorties, then rolling-window D1/D2 against the latest official
  snapshot → `provisional_alerts/`. Never queued or posted.
- official (every `official_retry_minutes`): load each released day not yet loaded, build its
  sorties, run the day pipeline and write `alerts/date=D/` once. Then today's snapshot.
- retention (daily): drop raw older than `raw_retention_days`.
- heartbeat: `worker/status.json`.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from tw.baselines.build import build_baseline
from tw.config import Fleet, load_fleet, load_yaml
from tw.ingest.history_download import HistoryConfig, Opener
from tw.ingest.live import (
    Limiter,
    LiveClient,
    append_raw,
    flush_hour,
    hour_start,
    log_request,
    prune_raw,
)
from tw.ingest.load_history import load_day
from tw.pipeline import Context, detect_day, prepare_day, prepare_window
from tw.store.alerts import (
    OFFICIAL,
    PROVISIONAL,
    official_exists,
    read_alerts,
    write_official,
    write_provisional,
)
from tw.store.parquet import partition_dir
from tw.tracks.airfields import AirfieldIndex, load_airfields
from tw.tracks.segment import SortieRules
from tw.tracks.sorties import build_day

log = logging.getLogger("tw.worker")


def _hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


@dataclass(frozen=True)
class WorkerConfig:
    flush_minutes: float
    provisional_minute: int
    official_at: time
    official_retry_minutes: float
    official_give_up_hour: int
    catchup_days: int
    retention_at: time
    heartbeat_seconds: float

    @classmethod
    def load(cls) -> WorkerConfig:
        raw = load_yaml("worker.yaml")
        return cls(
            flush_minutes=float(raw["flush_minutes"]),
            provisional_minute=int(raw["provisional_minute"]),
            official_at=_hhmm(str(raw["official_at"])),
            official_retry_minutes=float(raw["official_retry_minutes"]),
            official_give_up_hour=int(raw["official_give_up_hour"]),
            catchup_days=int(raw["catchup_days"]),
            retention_at=_hhmm(str(raw["retention_at"])),
            heartbeat_seconds=float(raw["heartbeat_seconds"]),
        )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, default=str))
    tmp.replace(path)


def log_event(data_dir: Path, now: datetime, kind: str, **detail: Any) -> None:
    path = data_dir / "worker" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"ts": now.isoformat(), "kind": kind, **detail}, default=str) + "\n")


def read_events(data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / "worker" / "events.jsonl"
    if not path.exists():
        return []
    rows = []
    for text in path.read_text().splitlines():
        try:
            rows.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return rows


@dataclass
class Worker:
    data_dir: Path
    client: LiveClient
    limiter: Limiter
    history_cfg: HistoryConfig
    history_opener: Opener
    cfg: WorkerConfig = field(default_factory=WorkerConfig.load)
    fleet: Fleet = field(default_factory=load_fleet)
    detectors: dict[str, Any] = field(default_factory=lambda: load_yaml("detectors.yaml"))
    index: AirfieldIndex | None = None
    status: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)  # live point/sortie writes

    @property
    def live_dir(self) -> Path:
        return self.data_dir / "live"

    def _sortie_args(self) -> tuple[AirfieldIndex, SortieRules, int]:
        if self.index is None:
            self.index = AirfieldIndex(load_airfields())
        rules = self.detectors["sortie"]
        return self.index, SortieRules.from_config(rules), int(rules["lookback_days"])

    def first_start(self, now: datetime) -> date:
        """The day the worker first ran here. Official alerts start the day before it: earlier
        days are history, whose would-have-fired alerts belong to the backtest."""
        path = self.data_dir / "worker" / "state.json"
        if path.exists():
            return date.fromisoformat(json.loads(path.read_text())["first_start"])
        _write_json(path, {"first_start": now.date().isoformat()})
        return now.date()

    # --- jobs ---------------------------------------------------------------------------------

    def poll(self, now: datetime) -> None:
        result = self.client.poll()
        if result is None:
            return
        append_raw(self.data_dir, result)
        log_request(self.data_dir, result)
        key = "polls_ok" if result.ok else "polls_failed"
        self.status[key] = self.status.get(key, 0) + 1
        self.status["provider"] = self.client.provider.name
        self.status["failovers"] = self.client.failovers
        if result.ok:
            self.status["last_ok_poll"] = result.fetched_at.isoformat()
            self.status["aircraft_last_poll"] = len(result.payload.get("ac") or [])  # type: ignore[union-attr]
        else:
            self.status["last_error"] = f"{result.fetched_at.isoformat()} {result.error}"

    def flush(self, now: datetime) -> None:
        hour = hour_start(now)
        with self._lock:
            for h in (hour - timedelta(hours=1), hour):
                flush_hour(self.data_dir, self.live_dir, h, self.fleet, self.data_dir)

    def provisional(self, now: datetime) -> int | None:
        """Rolling windows ending at the start of this hour. Returns alerts written, or None
        when there is no official snapshot to compare with yet."""
        end = hour_start(now)
        index, rules, lookback = self._sortie_args()
        with self._lock:
            for h in (end - timedelta(hours=2), end - timedelta(hours=1)):
                flush_hour(self.data_dir, self.live_dir, h, self.fleet, self.data_dir)
            for day in sorted({(end - timedelta(hours=1)).date(), end.date()}):
                build_day(day, self.live_dir, self.fleet, index, rules, lookback)
        ctx = Context(self.data_dir, cfg=self.detectors)
        inputs = prepare_window(end, ctx, self.live_dir)
        if inputs is None:
            log_event(self.data_dir, now, "provisional_skipped", reason="no official snapshot")
            return None
        recent_days = [end.date() - timedelta(days=n) for n in (1, 0)]
        recent = read_alerts(self.data_dir, PROVISIONAL, recent_days)
        alerts = detect_day(inputs, ctx.cfg, ctx.events, recent, ctx.provenance)
        last_hour = end - timedelta(hours=1)
        write_provisional(self.data_dir, last_hour.date(), last_hour.hour, alerts)
        live = sum(1 for a in alerts if a.status != "suppressed")
        log_event(
            self.data_dir, now, "provisional", window_end=end, alerts=len(alerts), unsuppressed=live
        )
        self.status["last_provisional"] = end.isoformat()
        return len(alerts)

    def _official_due(self, day: date, now: datetime) -> bool:
        """Day D's release is expected from `official_at` on D+1. Until `official_give_up_hour`
        it is retried every run; after that only in the first run past `official_at` each day."""
        released = datetime.combine(day + timedelta(days=1), self.cfg.official_at, tzinfo=UTC)
        if now < released:
            return False
        give_up = datetime.combine(
            day + timedelta(days=1), time(self.cfg.official_give_up_hour), tzinfo=UTC
        )
        if now < give_up:
            return True
        slot = datetime.combine(now.date(), self.cfg.official_at, tzinfo=UTC)
        return slot <= now < slot + timedelta(minutes=self.cfg.official_retry_minutes)

    def official(self, now: datetime) -> list[date]:
        """Returns the days whose official alerts were written in this run."""
        first = max(
            self.first_start(now) - timedelta(days=1),
            now.date() - timedelta(days=self.cfg.catchup_days),
        )
        written = []
        day = first
        while day < now.date():
            pending = not official_exists(self.data_dir, day) and self._official_due(day, now)
            if pending and self._official_day(day, now):
                written.append(day)
            day += timedelta(days=1)
        yesterday = now.date() - timedelta(days=1)
        if partition_dir(self.data_dir, "points", yesterday).exists():
            ctx = Context(self.data_dir, cfg=self.detectors)
            ctx.build_aggregates(yesterday)
            build_baseline(now.date(), self.data_dir, ctx.cache, ctx.cfg)  # write-once
        return written

    def _official_day(self, day: date, now: datetime) -> bool:
        if not partition_dir(self.data_dir, "points", day).exists():
            result = load_day(
                day, self.data_dir, self.fleet, self.history_cfg, self.history_opener, self.limiter
            )
            if result is None:
                log_event(self.data_dir, now, "official_missing", day=day)
                return False
            log_event(
                self.data_dir,
                now,
                "official_loaded",
                day=day,
                tag=result.tag,
                points=result.summary.kept_points,
                corrupt=len(result.summary.corrupt_traces),
            )
        index, rules, lookback = self._sortie_args()
        build_day(day, self.data_dir, self.fleet, index, rules, lookback)
        ctx = Context(self.data_dir, cfg=self.detectors)
        inputs = prepare_day(day, ctx)
        if inputs is None:
            log_event(self.data_dir, now, "official_incomplete", day=day)
            return False
        recent = read_alerts(self.data_dir, OFFICIAL, [day - timedelta(days=1)])
        alerts = detect_day(inputs, ctx.cfg, ctx.events, recent, ctx.provenance)
        write_official(self.data_dir, day, alerts)
        log_event(
            self.data_dir,
            now,
            "official_alerts",
            day=day,
            alerts=len(alerts),
            unsuppressed=sum(1 for a in alerts if a.status != "suppressed"),
        )
        self.status["last_official_day"] = day.isoformat()
        return True

    def retention(self, now: datetime) -> None:
        removed = prune_raw(self.data_dir, now.date(), self.client.cfg.raw_retention_days)
        if removed:
            log_event(self.data_dir, now, "retention", removed=[p.name for p in removed])

    def heartbeat(self, now: datetime) -> None:
        _write_json(
            self.data_dir / "worker" / "status.json", {**self.status, "heartbeat": now.isoformat()}
        )

    # --- scheduling ---------------------------------------------------------------------------

    def guarded(self, name: str, job: Callable[[datetime], Any]) -> Callable[[], None]:
        def run() -> None:
            now = datetime.now(UTC)
            try:
                job(now)
            except Exception as e:  # keep the worker alive; the report lists every failure
                log.exception("job %s failed", name)
                log_event(
                    self.data_dir, now, "job_error", job=name, error=f"{type(e).__name__}: {e}"
                )
                self.status[f"{name}_errors"] = self.status.get(f"{name}_errors", 0) + 1

        return run

    def run(self) -> None:
        from apscheduler.schedulers.blocking import BlockingScheduler

        sched = BlockingScheduler(
            timezone=UTC,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
        )
        c = self.cfg
        sched.add_job(
            self.guarded("poll", self.poll), "interval", seconds=self.client.cfg.poll_seconds
        )
        sched.add_job(self.guarded("flush", self.flush), "interval", minutes=c.flush_minutes)
        sched.add_job(
            self.guarded("provisional", self.provisional), "cron", minute=c.provisional_minute
        )
        sched.add_job(
            self.guarded("official", self.official),
            "interval",
            minutes=c.official_retry_minutes,
            next_run_time=datetime.now(UTC) + timedelta(seconds=30),
        )
        sched.add_job(
            self.guarded("retention", self.retention),
            "cron",
            hour=c.retention_at.hour,
            minute=c.retention_at.minute,
        )
        sched.add_job(
            self.guarded("heartbeat", self.heartbeat), "interval", seconds=c.heartbeat_seconds
        )
        signal.signal(signal.SIGTERM, lambda *_: sched.shutdown(wait=False))
        now = datetime.now(UTC)
        log_event(self.data_dir, now, "start", first_start=self.first_start(now))
        try:
            sched.start()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            stop = datetime.now(UTC)
            self.guarded("flush", self.flush)()
            self.heartbeat(stop)
            log_event(self.data_dir, stop, "stop")
