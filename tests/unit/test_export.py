"""Dashboard export (M5): local files, Vercel Blob uploads, the worker's export job."""

import json
import logging
import os
from datetime import UTC, datetime, timedelta

import pytest

from tests.unit.test_views import NOW, D, history
from tests.unit.test_worker import make_worker
from tw.export.run import export
from tw.export.sinks import BLOB, BlobConfig, BlobError, BlobSink, LocalSink
from tw.views.day import ViewContext

TOKEN = "vercel_blob_rw_AbCstore123_s3cr3tvalue"
CFG = BlobConfig("https://vercel.com/api/blob", 13, 60, "tanker-watch/test")


class Recorder:
    def __init__(self, status: int = 200) -> None:
        self.calls: list[tuple[str, str, dict, bytes]] = []
        self.status = status

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        return self.status, b'{"error":{"code":"forbidden","message":"no access"}}'


class CountingLimiter:
    def __init__(self) -> None:
        self.acquired: list[str] = []

    def acquire(self, provider: str) -> None:
        self.acquired.append(provider)


def test_local_export_writes_day_files_index_and_meta(tmp_path):
    history(tmp_path / "data")
    out = tmp_path / "site"
    result = export(ViewContext(tmp_path / "data"), [D, D + timedelta(days=1)], LocalSink(out), NOW)
    assert result.days == [D] and result.missing == [D + timedelta(days=1)]
    day = json.loads((out / "days" / f"{D}.json").read_text())
    assert day["meta"]["tier"] == "official"
    assert json.loads((out / "days" / f"{D}.r6.json").read_text())["date"] == D.isoformat()
    assert json.loads((out / "index.json").read_text())["latest"] == D.isoformat()
    assert json.loads((out / "meta.json").read_text())["regions"]


def test_export_is_deterministic(tmp_path):
    history(tmp_path / "data")
    ctx = ViewContext(tmp_path / "data")
    export(ctx, [D], LocalSink(tmp_path / "a"), NOW)
    export(ctx, [D], LocalSink(tmp_path / "b"), NOW)
    for name in (f"days/{D}.json", f"days/{D}.r6.json", "index.json", "meta.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_blob_put_is_rate_limited_public_and_overwrites(tmp_path):
    history(tmp_path / "data")
    send, limiter = Recorder(), CountingLimiter()
    sink = BlobSink(TOKEN, CFG, limiter, send)
    export(ViewContext(tmp_path / "data"), [D], sink, NOW)
    assert len(send.calls) == 4 and limiter.acquired == [BLOB] * 4
    method, url, headers, _ = send.calls[0]
    assert method == "PUT"
    assert url == f"https://vercel.com/api/blob/?pathname=days%2F{D}.json"
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert headers["x-vercel-blob-store-id"] == "AbCstore123"
    assert headers["x-vercel-blob-access"] == "public"
    assert headers["x-add-random-suffix"] == "0" and headers["x-allow-overwrite"] == "1"
    assert headers["x-cache-control-max-age"] == "86400"  # official day
    assert {c[2]["x-cache-control-max-age"] for c in send.calls[2:]} == {"60", "300"}
    assert sink.base_url == "https://abcstore123.public.blob.vercel-storage.com"


def test_blob_errors_never_include_the_token():
    sink = BlobSink(TOKEN, CFG, CountingLimiter(), Recorder(status=403))
    with pytest.raises(BlobError) as err:
        sink.put("index.json", b"{}", 60)
    assert "403" in str(err.value) and "no access" in str(err.value)
    assert "s3cr3t" not in str(err.value)
    with pytest.raises(BlobError) as err:
        BlobSink("not-a-token-s3cr3t", CFG, CountingLimiter(), Recorder())
    assert "s3cr3t" not in str(err.value)


def test_worker_export_job_covers_recent_and_newly_official_days(tmp_path):
    history(tmp_path)
    w = make_worker(tmp_path)
    assert w.export(NOW) is None  # no sink: skipped
    w.sink = LocalSink(tmp_path / "site")
    w._export_pending.add(D)
    now = datetime(2026, 9, 20, 0, 15, tzinfo=UTC)
    assert w.export(now) == [D]  # 09-19 and 09-20 have no data; D was pending
    assert not w._export_pending
    assert (tmp_path / "site" / "days" / f"{D}.json").exists()


def test_worker_export_failure_is_logged_not_raised(tmp_path, caplog):
    from tw.worker import read_events

    history(tmp_path)
    w = make_worker(tmp_path)
    w.sink = BlobSink(TOKEN, CFG, CountingLimiter(), Recorder(status=500))
    w._export_pending.add(D)
    with caplog.at_level(logging.ERROR):
        w.guarded("export", w.export)()
    (event,) = [e for e in read_events(tmp_path) if e["kind"] == "job_error"]
    assert event["job"] == "export" and "HTTP 500" in event["error"]
    assert "s3cr3t" not in caplog.text and "s3cr3t" not in json.dumps(event)
    assert w._export_pending == {D}  # retried next hour


def test_dotenv_fills_missing_variables_only(tmp_path, monkeypatch):
    from tw.cli import load_dotenv

    env = tmp_path / ".env"
    env.write_text("# comment\nTW_A=from_file\nTW_B='quoted'\nTW_C=\nTW_D=file\n")
    monkeypatch.setenv("TW_D", "shell")
    for key in ("TW_A", "TW_B", "TW_C"):
        monkeypatch.delenv(key, raising=False)
    load_dotenv(env)

    assert os.environ["TW_A"] == "from_file" and os.environ["TW_B"] == "quoted"
    assert "TW_C" not in os.environ and os.environ["TW_D"] == "shell"
    for key in ("TW_A", "TW_B"):
        monkeypatch.delenv(key)
