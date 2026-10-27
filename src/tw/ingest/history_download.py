"""Stream adsb.lol `globe_history` releases straight into the loader (no 3.5 GB temp files).

A release tar starts with ~800 MB of `./heatmap/*.bin.ttf` that we never use. Before
streaming, `find_traces_offset` walks the tar headers with small range requests and the
stream starts at the first `./traces/` entry. Any surprise falls back to streaming from byte 0.

Every HTTP request goes through the rate limiter. Tests inject a fake `Opener`.
"""

from __future__ import annotations

import io
import itertools
import string
import tarfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import IO, Any, Protocol

from tw.config import load_yaml

PROVIDER = "adsb_lol_history"
PROBE_PROVIDER = "adsb_lol_history_probe"
TAR_BLOCK = 512
# Headers that describe the *next* member (PAX and GNU long names); never skip past them.
_EXTENDED_HEADER_TYPES = {
    tarfile.XHDTYPE,
    tarfile.XGLTYPE,
    tarfile.GNUTYPE_LONGNAME,
    tarfile.GNUTYPE_LONGLINK,
}


class RangeNotSupported(Exception):
    """The server answered a range request with the whole file."""


class Opener(Protocol):
    def __call__(self, url: str, start: int = 0, length: int | None = None) -> IO[bytes] | None:
        """Open `url` from byte `start` (optionally `length` bytes); None on HTTP 404."""
        ...


class Limiter(Protocol):
    def acquire(self, provider: str) -> None: ...


@dataclass(frozen=True)
class HistoryConfig:
    base_url: str
    org: str
    repo_prefix: str
    pods: tuple[str, ...]
    tag_suffixes: tuple[str, ...]
    timeout_s: float
    max_attempts: int
    user_agent: str
    skip_heatmap: bool = True
    max_header_probes: int = 200

    @classmethod
    def load(cls) -> HistoryConfig:
        raw: dict[str, Any] = load_yaml("providers.yaml")["history"]
        return cls(
            base_url=raw["base_url"].rstrip("/"),
            org=raw["org"],
            repo_prefix=raw["repo_prefix"],
            pods=tuple(raw["pods"]),
            tag_suffixes=tuple(raw["tag_suffixes"]),
            timeout_s=float(raw["timeout_s"]),
            max_attempts=int(raw["max_attempts"]),
            user_agent=raw["user_agent"],
            skip_heatmap=bool(raw.get("skip_heatmap", True)),
            max_header_probes=int(raw.get("max_header_probes", 200)),
        )

    def candidates(self, day: date) -> Iterator[tuple[str, str]]:
        """(repo, tag) pairs to try, in preference order."""
        for pod, suffix in itertools.product(self.pods, self.tag_suffixes):
            tag = f"v{day:%Y.%m.%d}-planes-readsb-{pod}-{suffix}"
            for year in (day.year, day.year + 1):
                yield f"{self.org}/{self.repo_prefix}{year}", tag

    def part_url(self, repo: str, tag: str, part: str) -> str:
        return f"{self.base_url}/{repo}/releases/download/{tag}/{tag}.tar.{part}"


def part_suffixes() -> Iterator[str]:
    """aa, ab, ..., az, ba, ... (split(1) naming)."""
    for a, b in itertools.product(string.ascii_lowercase, repeat=2):
        yield a + b


def _is_traces_entry(name: str) -> bool:
    return name.removeprefix("./").startswith("traces")


def find_traces_offset(url: str, opener: Opener, limiter: Limiter, max_probes: int) -> int | None:
    """Byte offset of the first `traces` entry in the first part, or None if `url` is 404.

    Reads one 512-byte tar header per member. Returns 0 (stream everything) on anything
    unexpected: bad checksum, short read, no range support, or too many members.
    """
    offset = 0
    entry_start = 0  # where the current member's header chain began
    for _ in range(max_probes):
        limiter.acquire(PROBE_PROVIDER)
        try:
            fh = opener(url, start=offset, length=TAR_BLOCK)
        except RangeNotSupported:
            return 0
        if fh is None:
            return None if offset == 0 else 0
        with fh:
            block = fh.read(TAR_BLOCK)
        if len(block) < TAR_BLOCK:
            return 0
        try:
            info = tarfile.TarInfo.frombuf(block, "utf-8", "surrogateescape")
        except tarfile.HeaderError:
            return 0
        if info.type not in _EXTENDED_HEADER_TYPES and _is_traces_entry(info.name):
            return entry_start
        offset += TAR_BLOCK + -(-info.size // TAR_BLOCK) * TAR_BLOCK
        if info.type not in _EXTENDED_HEADER_TYPES:
            entry_start = offset
    return 0


class PartStream(io.RawIOBase):
    """Concatenates `<tag>.tar.aa`, `.ab`, ... into one readable stream, opening each lazily."""

    def __init__(
        self, urls: Iterator[str], open_next: Callable[[str], IO[bytes] | None], first: IO[bytes]
    ) -> None:
        self._urls = urls
        self._open_next = open_next
        self._current: IO[bytes] | None = first
        self.parts_read = 1

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while self._current is not None:
            n = self._current.readinto(buffer)  # type: ignore[attr-defined]
            if n:
                return int(n)
            self._current.close()
            self._current = self._open_next(next(self._urls))
            if self._current is not None:
                self.parts_read += 1
        return 0

    def close(self) -> None:
        if self._current is not None:
            self._current.close()
            self._current = None
        super().close()


@dataclass(frozen=True)
class DayStream:
    tag: str
    stream: PartStream
    skipped_bytes: int


def open_day(day: date, cfg: HistoryConfig, opener: Opener, limiter: Limiter) -> DayStream | None:
    """Find the release for `day` and open it, or return None if no release exists."""

    def limited(url: str, start: int = 0) -> IO[bytes] | None:
        limiter.acquire(PROVIDER)
        return opener(url, start=start)

    for repo, tag in cfg.candidates(day):
        urls = (cfg.part_url(repo, tag, s) for s in part_suffixes())
        first_url = next(urls)
        skip: int | None = 0
        if cfg.skip_heatmap:
            # The first header probe doubles as the "does this release exist" check.
            skip = find_traces_offset(first_url, opener, limiter, cfg.max_header_probes)
            if skip is None:
                continue
        first = limited(first_url, start=skip or 0)
        if first is None:
            continue
        return DayStream(tag, PartStream(urls, limited, first), skip or 0)
    return None


def http_opener(cfg: HistoryConfig) -> Opener:
    def open_url(url: str, start: int = 0, length: int | None = None) -> IO[bytes] | None:
        headers = {"User-Agent": cfg.user_agent}
        if start or length is not None:
            end = "" if length is None else str(start + length - 1)
            headers["Range"] = f"bytes={start}-{end}"
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=cfg.timeout_s)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 416:  # range starts past the end of the file
                return io.BytesIO(b"")
            raise
        if "Range" in headers and resp.status != 206:
            resp.close()
            raise RangeNotSupported(url)
        return resp  # type: ignore[no-any-return]

    return open_url
