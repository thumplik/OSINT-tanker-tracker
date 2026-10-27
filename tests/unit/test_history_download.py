import io
import tarfile
from datetime import date

import pytest

from tw.config import load_fleet
from tw.ingest.history_download import HistoryConfig, open_day, part_suffixes
from tw.ingest.load_history import load_day
from tw.store.parquet import read_day

CFG = HistoryConfig(
    base_url="https://example.test",
    org="adsblol",
    repo_prefix="globe_history_",
    pods=("prod",),
    tag_suffixes=("0", "0tmp"),
    timeout_s=1,
    max_attempts=2,
    user_agent="test",
)


class CountingLimiter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def acquire(self, provider: str) -> None:
        self.calls.append(provider)


def build_tar(src_dir) -> bytes:
    """A tar laid out like a real release: heatmap first, then traces/."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        heat = b"\x00" * 3000
        info = tarfile.TarInfo("./heatmap/00.bin.ttf")
        info.size = len(heat)
        tar.addfile(info, io.BytesIO(heat))
        for f in sorted(src_dir.rglob("trace_full_*")):
            tar.add(f, arcname=f"./traces/{f.parent.name}/{f.name}")
    return buf.getvalue()


def fake_server(files: dict[str, bytes]):
    requested: list[str] = []

    def opener(url: str):
        requested.append(url)
        data = files.get(url)
        return None if data is None else io.BytesIO(data)

    return opener, requested


def split_release(repo: str, tag: str, data: bytes, size: int) -> dict[str, bytes]:
    chunks = [data[i : i + size] for i in range(0, len(data), size)]
    return {
        f"https://example.test/{repo}/releases/download/{tag}/{tag}.tar.{s}": c
        for s, c in zip(part_suffixes(), chunks, strict=False)
    }


def test_part_suffixes():
    gen = part_suffixes()
    first = [next(gen) for _ in range(27)]
    assert first[:3] == ["aa", "ab", "ac"] and first[25:] == ["az", "ba"]


def test_streams_multi_part_release_into_store(tmp_path, fixtures):
    data = build_tar(fixtures / "real_2026-10-25")
    tag = "v2026.10.25-planes-readsb-prod-0"
    files = split_release("adsblol/globe_history_2026", tag, data, size=40_000)
    assert len(files) > 2  # the tar really is split across parts
    opener, requested = fake_server(files)
    limiter = CountingLimiter()

    result = load_day(date(2026, 10, 25), tmp_path, load_fleet(), CFG, opener, limiter)

    assert result is not None
    got_tag, summary = result
    assert got_tag == tag and summary.traces == 6
    assert read_day(tmp_path, "points", date(2026, 10, 25)).num_rows == 1523 + 1310 + 549
    # Every request, including the final 404 that ends the part list, was rate limited.
    assert len(limiter.calls) == len(requested) == len(files) + 1


def test_falls_back_to_next_years_repo_and_tmp_suffix():
    tag = "v2025.12.30-planes-readsb-prod-0tmp"
    files = split_release("adsblol/globe_history_2026", tag, b"x" * 10, size=100)
    opener, requested = fake_server(files)
    found = open_day(date(2025, 12, 30), CFG, opener, CountingLimiter())
    assert found is not None and found[0] == tag
    assert [u.split("/")[4] for u in requested] == [
        "globe_history_2025",
        "globe_history_2026",
        "globe_history_2025",
        "globe_history_2026",
    ]


def test_missing_day_returns_none(tmp_path):
    opener, requested = fake_server({})
    assert (
        load_day(date(2026, 1, 1), tmp_path, load_fleet(), CFG, opener, CountingLimiter()) is None
    )
    assert len(requested) == 4  # 1 pod x 2 suffixes x 2 repos
    assert not (tmp_path / "points").exists()


def test_truncated_stream_is_retried_then_raises(tmp_path, fixtures):
    data = build_tar(fixtures / "real_2026-10-25")
    tag = "v2026.10.25-planes-readsb-prod-0"
    files = split_release("adsblol/globe_history_2026", tag, data[: len(data) // 2], size=10**9)
    opener, requested = fake_server(files)
    with pytest.raises((tarfile.TarError, EOFError, OSError)):
        load_day(date(2026, 10, 25), tmp_path, load_fleet(), CFG, opener, CountingLimiter())
    assert sum(u.endswith(".tar.aa") for u in requested) == CFG.max_attempts
    assert not (tmp_path / "points").exists()  # nothing written from a failed read
