"""Live client, raw store and live points (M4). Fake fetch and clock only: no live calls."""

import gzip
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tw.config import load_fleet
from tw.ingest.live import (
    LiveClient,
    LiveConfig,
    PollResult,
    append_raw,
    flush_hour,
    live_points_for_hour,
    log_request,
    prune_raw,
    raw_path,
    read_raw_hour,
    read_request_log,
)
from tw.ingest.ratelimit import RateLimiter, TokenBucket
from tw.store.parquet import read_day, save_registry
from tw.worker_report import max_in_window, rate_check

FIXTURE = Path(__file__).parents[1] / "fixtures" / "v2_mil_2026-07-01T0100Z.json"
PAYLOAD = json.loads(FIXTURE.read_text())
NOW = PAYLOAD["now"] / 1000  # 2026-07-01T01:00:00Z
HOUR = datetime(2026, 7, 1, 0, tzinfo=UTC)  # the fixture's positions are seconds before 01:00
FLEET = load_fleet()


class Clock:
    def __init__(self, t: float = NOW) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeLimiter:
    def __init__(self, clock: Clock) -> None:
        self.calls: list[tuple[str, float]] = []
        self.clock = clock

    def acquire(self, provider: str) -> None:
        self.calls.append((provider, self.clock()))


def config(**over) -> LiveConfig:
    return LiveConfig.load(env={}).__class__(**{**LiveConfig.load(env={}).__dict__, **over})


def client(responses, clock=None, **over):
    """`responses`: provider -> list of (status, body) returned in order (last one repeats)."""
    clock = clock or Clock()
    limiter = FakeLimiter(clock)
    cfg = config(**over)
    by_url = {p.url: p.name for p in cfg.providers}

    def fetch(url):
        queue = responses[by_url[url]]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return LiveClient(cfg, fetch, limiter, clock), limiter, clock


GOOD = (200, json.dumps(PAYLOAD).encode())


def test_config_reads_env_overrides():
    cfg = LiveConfig.load(env={"ADSBLOL_BASE_URL": "http://localhost:9/"})
    assert cfg.providers[0].url == "http://localhost:9/v2/mil"
    assert [p.name for p in cfg.providers] == ["adsb_lol", "adsb_fi"]


def test_every_request_acquires_the_bucket():
    c, limiter, _ = client({"adsb_lol": [GOOD]})
    for _ in range(3):
        assert c.poll().ok
    assert [p for p, _ in limiter.calls] == ["adsb_lol"] * 3


def test_failover_backoff_and_failback():
    c, limiter, clock = client({"adsb_lol": [(503, b"")], "adsb_fi": [GOOD]}, failover_after=2)
    first = c.poll()
    assert not first.ok and first.error == "HTTP 503"
    assert c.poll() is None  # backing off: no request made
    clock.t += c.cfg.backoff_base_s
    assert not c.poll().ok  # second failure → fail over
    assert c.provider.name == "adsb_fi" and c.failovers == 1
    clock.t = c.next_allowed
    assert c.poll().ok and c.poll().ok
    clock.t += c.cfg.failback_s
    assert not c.poll().ok  # failback tries the primary once ...
    assert c.provider.name == "adsb_fi"  # ... and one failure sends it back
    assert len(limiter.calls) == 5  # none while backing off


def test_backoff_doubles_up_to_the_cap_and_429_waits():
    c, _, clock = client({"adsb_lol": [(429, b"")]}, failover_after=99, max_backoff_s=40)
    waits = []
    for _ in range(4):
        before = clock.t
        assert not c.poll().ok
        waits.append(c.next_allowed - before)
        clock.t = c.next_allowed
    assert waits == [15, 30, 40, 40]


def test_bad_bodies_are_failures():
    for body in (b"not json", b"[]", b'{"ac": []}', b'{"now": 1, "ac": {}}'):
        c, _, _ = client({"adsb_lol": [(200, body)]})
        r = c.poll()
        assert not r.ok and r.error


def test_real_limiter_keeps_polls_within_the_rate(tmp_path):
    """Ten simulated minutes of back-to-back polls stay within 1 req/s on the request log."""
    clock = Clock()
    limiter = RateLimiter({})
    limiter._buckets["adsb_lol"] = TokenBucket(1.0, 1, clock=clock, sleep=clock.sleep)
    c = LiveClient(config(), lambda url: GOOD, limiter, clock)
    while clock.t < NOW + 600:
        r = c.poll()
        log_request(tmp_path, r)
    rows = read_request_log(tmp_path, date(2026, 7, 1), date(2026, 7, 1))
    times = [datetime.fromisoformat(r["ts"]).timestamp() for r in rows]
    assert rate_check(times, 1.0, 1, 1) == (1, 2)
    assert max_in_window(times, 60) <= 61 and len(times) >= 590


def result(provider="adsb_lol", payload=PAYLOAD, at=NOW) -> PollResult:
    return PollResult(provider, "u", datetime.fromtimestamp(at, tz=UTC), 200, payload, None, 1, 1)


def test_raw_store_survives_a_torn_write(tmp_path):
    append_raw(tmp_path, result())
    append_raw(tmp_path, result(at=NOW + 15))
    path = raw_path(tmp_path, "adsb_lol", HOUR + timedelta(hours=1))
    with path.open("ab") as f:
        f.write(gzip.compress(b'{"partial":')[:-6])  # crash mid-member
    assert len(list(read_raw_hour(tmp_path, HOUR + timedelta(hours=1)))) == 2


def test_live_points_are_deduped_idempotent_and_typed_from_registry(tmp_path):
    untyped = {
        **PAYLOAD,
        "ac": [
            *PAYLOAD["ac"],
            {"hex": "ae9999", "lat": 1.0, "lon": 2.0, "alt_baro": 9000, "seen_pos": 0},
        ],
    }
    for n in range(3):  # same positions reported in three polls (ages grow with `now`)
        later = {
            **untyped,
            "now": PAYLOAD["now"] - 60_000 + n * 15000,  # 00:59:00, then every 15 s
            "ac": [
                {**a, **{k: a[k] + n * 15 for k in ("seen", "seen_pos") if k in a}}
                for a in untyped["ac"]
            ],
        }
        append_raw(tmp_path, result(payload=later, at=NOW - 60 + n * 15))
    rows = live_points_for_hour(tmp_path, HOUR, FLEET, tmp_path)
    # One row per aircraft: the ~ track is not ICAO; the untyped ae9999 has no registry yet.
    assert sorted(r["hex"] for r in rows) == ["ae0abc", "ae1234", "ae2222"]
    save_registry(tmp_path, date(2026, 6, 30), {"ae9999": "C17"}, part="t")
    flush_hour(tmp_path, tmp_path / "live", HOUR, FLEET, tmp_path)
    first = read_day(tmp_path / "live", "points", HOUR.date())
    flush_hour(tmp_path, tmp_path / "live", HOUR, FLEET, tmp_path)
    again = read_day(tmp_path / "live", "points", HOUR.date())
    assert first.equals(again)
    assert "ae9999" in first["hex"].to_pylist()
    assert first.num_rows == len(
        set(zip(first["hex"].to_pylist(), first["ts"].to_pylist(), strict=True))
    )


def test_points_fetched_next_hour_belong_to_their_own_hour(tmp_path):
    aged = {
        **PAYLOAD,
        "now": PAYLOAD["now"] + 3600_000 + 2000,
        "ac": [{**PAYLOAD["ac"][0], "seen_pos": 5}],
    }  # fetched 02:00:02, position 01:59:57
    append_raw(tmp_path, result(payload=aged, at=NOW + 3602))
    one = HOUR + timedelta(hours=1)
    rows = live_points_for_hour(tmp_path, one, FLEET, tmp_path)
    assert [r["ts"] for r in rows] == [one + timedelta(minutes=59, seconds=57)]


def test_retention_deletes_only_old_raw(tmp_path):
    for d in ("2026-06-01", "2026-06-17", "2026-06-30"):
        (tmp_path / "raw" / f"date={d}").mkdir(parents=True)
    (tmp_path / "points" / "date=2026-06-01").mkdir(parents=True)
    removed = prune_raw(tmp_path, date(2026, 7, 1), 14)
    assert [p.name for p in removed] == ["date=2026-06-01"]
    assert (tmp_path / "raw" / "date=2026-06-17").exists()
    assert (tmp_path / "points" / "date=2026-06-01").exists()
