"""Stream adsb.lol `globe_history` releases straight into the loader (no 3.5 GB temp files).

Every HTTP request goes through the rate limiter. Tests inject a fake `Opener`.
"""

from __future__ import annotations

import io
import itertools
import string
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import IO, Any

from tw.config import load_yaml
from tw.ingest.ratelimit import RateLimiter

PROVIDER = "adsb_lol_history"

# Opens a URL for streaming; returns None when it does not exist (HTTP 404).
Opener = Callable[[str], IO[bytes] | None]


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


class PartStream(io.RawIOBase):
    """Concatenates `<tag>.tar.aa`, `.ab`, ... into one readable stream, opening each lazily."""

    def __init__(self, urls: Iterator[str], opener: Opener, first: IO[bytes]) -> None:
        self._urls = urls
        self._opener = opener
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
            self._current = self._opener(next(self._urls))
            if self._current is not None:
                self.parts_read += 1
        return 0

    def close(self) -> None:
        if self._current is not None:
            self._current.close()
            self._current = None
        super().close()


def open_day(
    day: date, cfg: HistoryConfig, opener: Opener, limiter: RateLimiter
) -> tuple[str, PartStream] | None:
    """Find the release for `day` and return (tag, stream), or None if no release exists."""

    def limited(url: str) -> IO[bytes] | None:
        limiter.acquire(PROVIDER)
        return opener(url)

    for repo, tag in cfg.candidates(day):
        urls = (cfg.part_url(repo, tag, s) for s in part_suffixes())
        first = limited(next(urls))
        if first is not None:
            return tag, PartStream(urls, limited, first)
    return None


def http_opener(cfg: HistoryConfig) -> Opener:
    def open_url(url: str) -> IO[bytes] | None:
        req = urllib.request.Request(url, headers={"User-Agent": cfg.user_agent})
        try:
            return urllib.request.urlopen(req, timeout=cfg.timeout_s)  # type: ignore[no-any-return]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    return open_url
