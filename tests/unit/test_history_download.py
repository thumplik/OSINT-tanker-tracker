import io
import tarfile
from datetime import date

import pytest

from tw.config import load_fleet
from tw.ingest.history_download import (
    HistoryConfig,
    RangeNotSupported,
    find_traces_offset,
    open_day,
    part_suffixes,
)
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
    skip_heatmap=False,
)
SKIP = HistoryConfig(**{**CFG.__dict__, "skip_heatmap": True})


class CountingLimiter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def acquire(self, provider: str) -> None:
        self.calls.append(provider)


def build_tar(src_dir, fmt: int = tarfile.PAX_FORMAT) -> bytes:
    """A tar laid out like a real release: heatmap first, then traces/."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as tar:
        heat = b"\x00" * 3000
        info = tarfile.TarInfo("./heatmap/00.bin.ttf")
        info.size = len(heat)
        tar.addfile(info, io.BytesIO(heat))
        for f in sorted(src_dir.rglob("trace_full_*")):
            tar.add(f, arcname=f"./traces/{f.parent.name}/{f.name}")
    return buf.getvalue()


def fake_server(files: dict[str, bytes], ranges: bool = True):
    """Serves `files`; records (url, start, length) for each request. Supports byte ranges."""
    requested: list[str] = []
    calls: list[tuple[str, int, int | None]] = []

    def opener(url: str, start: int = 0, length: int | None = None):
        requested.append(url)
        calls.append((url, start, length))
        data = files.get(url)
        if data is None:
            return None
        if (start or length is not None) and not ranges:
            raise RangeNotSupported(url)
        end = len(data) if length is None else start + length
        return io.BytesIO(data[start:end])

    opener.calls = calls  # type: ignore[attr-defined]
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
    got_tag, skipped, summary = result.tag, result.skipped_bytes, result.summary
    assert got_tag == tag and summary.traces == 6 and skipped == 0
    assert read_day(tmp_path, "points", date(2026, 10, 25)).num_rows == 1523 + 1310 + 549
    # Every request, including the final 404 that ends the part list, was rate limited.
    assert len(limiter.calls) == len(requested) == len(files) + 1


def heatmap_offset(data: bytes) -> int:
    """Start of the first traces member, including any extended header in front of it."""
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = tar.getmembers()
    first = next(i for i, m in enumerate(members) if m.name.startswith("./traces"))
    prev = members[first - 1]
    return prev.offset_data + -(-prev.size // 512) * 512


@pytest.mark.parametrize("fmt", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])
def test_finds_traces_offset_by_walking_headers(fixtures, fmt):
    data = build_tar(fixtures / "real_2026-10-25", fmt)
    url = "https://example.test/x.tar.aa"
    opener, _ = fake_server({url: data})
    limiter = CountingLimiter()
    offset = find_traces_offset(url, opener, limiter, max_probes=50)
    assert offset == heatmap_offset(data) > 3000
    # One 512-byte probe per member up to the traces entry, all on the probe bucket.
    assert all(length == 512 for _, _, length in opener.calls)
    assert set(limiter.calls) == {"adsb_lol_history_probe"}


def test_find_traces_offset_falls_back_to_zero():
    url = "https://example.test/x.tar.aa"
    garbage = b"not a tar header".ljust(4096, b"\x00")
    assert find_traces_offset(url, fake_server({url: garbage})[0], CountingLimiter(), 50) == 0
    no_ranges, _ = fake_server({url: garbage}, ranges=False)
    assert find_traces_offset(url, no_ranges, CountingLimiter(), 50) == 0
    assert find_traces_offset(url, fake_server({})[0], CountingLimiter(), 50) is None


def test_skip_heatmap_loads_same_points_with_fewer_bytes(tmp_path, fixtures):
    data = build_tar(fixtures / "real_2026-10-25")
    tag = "v2026.10.25-planes-readsb-prod-0"
    files = split_release("adsblol/globe_history_2026", tag, data, size=40_000)
    opener, _ = fake_server(files)

    result = load_day(date(2026, 10, 25), tmp_path, load_fleet(), SKIP, opener, CountingLimiter())

    assert result is not None and result.skipped_bytes == heatmap_offset(data)
    assert result.summary.traces == 6
    assert read_day(tmp_path, "points", date(2026, 10, 25)).num_rows == 1523 + 1310 + 549
    # The streamed read of the first part started after the heatmap.
    stream_reads = [(u, st) for u, st, ln in opener.calls if ln is None and u.endswith(".aa")]
    assert stream_reads == [(next(iter(files)), heatmap_offset(data))]


def test_falls_back_to_next_years_repo_and_tmp_suffix():
    tag = "v2025.12.30-planes-readsb-prod-0tmp"
    files = split_release("adsblol/globe_history_2026", tag, b"x" * 10, size=100)
    opener, requested = fake_server(files)
    found = open_day(date(2025, 12, 30), CFG, opener, CountingLimiter())
    assert found is not None and found.tag == tag
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
