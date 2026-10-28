"""Worker tiers (M4): provisional rolling windows and the nightly official run. Fixtures and
synthetic history only; the history loader and HTTP are faked."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import tw.worker as worker_mod
from tests.synthetic import days_between, flat_history, sortie, store_day
from tw.baselines.build import build_baseline, snapshot_exists
from tw.ingest.history_download import HistoryConfig
from tw.ingest.live import LiveClient, LiveConfig
from tw.ingest.load_history import DayResult, LoadSummary
from tw.pipeline import Context, detect_day, prepare_window
from tw.store.alerts import OFFICIAL, PROVISIONAL, official_exists, read_alerts
from tw.store.parquet import SORTIE_SCHEMA, partition_dir, write_partition
from tw.worker import Worker, read_events

D = date(2026, 9, 15)
FIXTURE = Path(__file__).parents[1] / "fixtures" / "v2_mil_2026-07-01T0100Z.json"


def history_with_snapshot(root: Path) -> None:
    flat_history(root, days_between(D - timedelta(days=56), D - timedelta(days=1)))
    ctx = Context(root)
    build_baseline(D, root, ctx.cache, ctx.cfg)


def live_sorties(root: Path, day: date, n: int, hour: int) -> None:
    rows = []
    for i in range(n):
        s = sortie(day, i, dest="ETAR", hex_code=f"ae{i + 0x100:04x}")
        start = datetime(day.year, day.month, day.day, hour, tzinfo=UTC) + timedelta(minutes=i)
        s.update(start_ts=start, end_ts=start + timedelta(hours=3))
        s["sortie_id"] = f"{s['hex']}-{start:%Y%m%dT%H%M%SZ}"
        rows.append(s)
    write_partition(root / "live", "sorties", day, rows, SORTIE_SCHEMA, "sorties")


def test_rolling_window_across_midnight_fires_unchecked(tmp_path):
    history_with_snapshot(tmp_path)
    live_sorties(tmp_path, D - timedelta(days=1), 8, hour=20)  # evening before
    live_sorties(tmp_path, D, 8, hour=1)
    end = datetime(2026, 9, 15, 6, tzinfo=UTC)
    inputs = prepare_window(end, Context(tmp_path), tmp_path / "live")
    assert inputs is not None and inputs.provisional and inputs.baseline_as_of == D
    ctx = Context(tmp_path)
    alerts = detect_day(inputs, ctx.cfg, ctx.events, (), ctx.provenance)
    (a,) = [a for a in alerts if a.detector == "D1" and a.region == "europe"]
    assert a.observed == 16 and a.expected == 5 and a.status == "new"
    assert a.window_end == end and a.window_start == end - timedelta(hours=24)
    assert a.evidence["tier"] == "provisional" and a.evidence["coverage"] == "unchecked"
    assert "coverage_ratio" not in a.evidence


def test_window_uses_latest_snapshot_and_never_builds_one(tmp_path):
    history_with_snapshot(tmp_path)
    end = datetime(2026, 9, 17, 6, tzinfo=UTC)  # snapshots for 09-16 and 09-17 don't exist
    inputs = prepare_window(end, Context(tmp_path), tmp_path / "live")
    assert inputs is not None and inputs.baseline_as_of == D
    assert not snapshot_exists(tmp_path, date(2026, 9, 17))
    assert prepare_window(end, Context(tmp_path / "empty"), tmp_path / "live") is None


class NoFetch:
    def __call__(self, url):
        return 200, FIXTURE.read_bytes()


class NoLimit:
    def acquire(self, provider):
        pass


def make_worker(root: Path, fetch=None) -> Worker:
    cfg = LiveConfig.load(env={})
    client = LiveClient(cfg, fetch or NoFetch(), NoLimit(), clock=lambda: 1782867600.0)
    return Worker(root, client, NoLimit(), HistoryConfig.load(), history_opener=lambda *a: None)


def test_provisional_job_writes_only_provisional_alerts(tmp_path):
    history_with_snapshot(tmp_path)
    w = make_worker(tmp_path)
    w.poll(datetime(2026, 7, 1, 1, tzinfo=UTC))
    now = datetime(2026, 9, 15, 6, 5, tzinfo=UTC)
    assert w.provisional(now) is not None
    assert partition_dir(tmp_path, PROVISIONAL, D).exists()
    assert not (tmp_path / OFFICIAL).exists()
    assert (tmp_path / "live" / "sorties" / "date=2026-09-15").exists()


@pytest.fixture
def fake_release(monkeypatch):
    """`load_day` stores a synthetic day with a surge; `build_day` keeps the stored sorties."""
    released: set[date] = set()

    def load_day(day, data_dir, *args):
        if day not in released:
            return None
        store_day(data_dir, day, [sortie(day, i, dest="ETAR") for i in range(20)])
        return DayResult("tag", 0, LoadSummary(1, 20, 20, {day: 20}))

    monkeypatch.setattr(worker_mod, "load_day", load_day)
    monkeypatch.setattr(worker_mod, "build_day", lambda *a, **k: None)
    return released


def test_official_run_is_write_once_and_waits_for_the_release(tmp_path, fake_release):
    flat_history(tmp_path, days_between(D - timedelta(days=56), D - timedelta(days=1)))
    w = make_worker(tmp_path)
    w.first_start(datetime(2026, 9, 16, 1, tzinfo=UTC))
    assert w.official(datetime(2026, 9, 16, 3, 0, tzinfo=UTC)) == []  # before 03:40
    assert w.official(datetime(2026, 9, 16, 3, 45, tzinfo=UTC)) == []  # not released yet
    assert [e["kind"] for e in read_events(tmp_path)] == ["official_missing"]
    assert not official_exists(tmp_path, D)

    fake_release.add(D)
    assert w.official(datetime(2026, 9, 16, 4, 15, tzinfo=UTC)) == [D]
    alerts = read_alerts(tmp_path, OFFICIAL, [D])
    assert any(a.detector == "D1" and a.status == "new" for a in alerts)
    assert all("tier" not in a.evidence for a in alerts)
    assert snapshot_exists(tmp_path, D + timedelta(days=1))  # today's, for provisional runs

    before = sorted(p.stat().st_mtime_ns for p in partition_dir(tmp_path, OFFICIAL, D).iterdir())
    assert w.official(datetime(2026, 9, 16, 4, 45, tzinfo=UTC)) == []
    after = sorted(p.stat().st_mtime_ns for p in partition_dir(tmp_path, OFFICIAL, D).iterdir())
    assert before == after


def test_official_skips_history_before_the_worker_started(tmp_path, fake_release):
    flat_history(tmp_path, days_between(D - timedelta(days=56), D))
    w = make_worker(tmp_path)
    w.first_start(datetime(2026, 9, 16, 1, tzinfo=UTC))
    w.official(datetime(2026, 9, 18, 4, tzinfo=UTC))  # D-1 and earlier are backtest territory
    assert not official_exists(tmp_path, D - timedelta(days=1))
    assert official_exists(tmp_path, D)  # loaded before, never alerted: the worker's first day


def test_official_retries_until_give_up_then_nightly(tmp_path):
    w = make_worker(tmp_path)
    due = [
        w._official_due(D, datetime(2026, 9, 16, h, m, tzinfo=UTC))
        for h, m in ((3, 39), (3, 40), (11, 59), (12, 0))
    ]
    assert due == [False, True, True, False]
    assert w._official_due(D, datetime(2026, 9, 17, 3, 50, tzinfo=UTC))  # next night's slot
    assert not w._official_due(D, datetime(2026, 9, 17, 4, 15, tzinfo=UTC))


def test_heartbeat_and_status(tmp_path):
    w = make_worker(tmp_path)
    w.poll(datetime(2026, 7, 1, 1, tzinfo=UTC))
    w.heartbeat(datetime(2026, 7, 1, 1, 1, tzinfo=UTC))
    status = json.loads((tmp_path / "worker" / "status.json").read_text())
    assert status["polls_ok"] == 1 and status["provider"] == "adsb_lol"
    assert status["aircraft_last_poll"] == 4


def test_worker_report_reads_rate_and_uptime_from_the_logs(tmp_path):
    from tw.worker_report import build_report

    t0 = 1782867600.0
    clock = {"t": t0}
    cfg = LiveConfig.load(env={})
    client = LiveClient(cfg, NoFetch(), NoLimit(), clock=lambda: clock["t"])
    w = Worker(tmp_path, client, NoLimit(), HistoryConfig.load(), history_opener=lambda *a: None)
    for n in range(40):
        clock["t"] = t0 + 15 * n + (600 if n >= 20 else 0)  # one 10 min outage
        w.poll(datetime.fromtimestamp(clock["t"], tz=UTC))
    text, respected = build_report(tmp_path, date(2026, 7, 1), date(2026, 7, 1))
    assert respected
    assert "| adsb_lol | 40 | 40 | 1 (2) | 4 (61) |" in text
    assert "10.2 min at 07-01 01:04Z" in text
