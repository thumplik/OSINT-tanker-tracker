"""Where exported files go: a local directory (development) or Vercel Blob (the deployed site).
Both receive the same bytes, so the local site shows exactly what the deployed one will."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tw.config import load_yaml

BLOB = "vercel_blob"  # rate-limit bucket (providers.yaml)


class Sink(Protocol):
    def put(self, path: str, body: bytes, max_age: int) -> None: ...


class Limiter(Protocol):
    def acquire(self, provider: str) -> None: ...


class LocalSink:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, path: str, body: bytes, max_age: int) -> None:
        out = self.root / path
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(out)


# (method, url, headers, body) -> (status, response body)
Send = Callable[[str, str, Mapping[str, str], bytes], tuple[int, bytes]]


class BlobError(RuntimeError):
    pass


@dataclass(frozen=True)
class BlobConfig:
    api_url: str
    api_version: int
    timeout_s: float
    user_agent: str

    @classmethod
    def load(cls) -> BlobConfig:
        providers = load_yaml("providers.yaml")
        blob = providers["blob"]
        return cls(
            api_url=str(blob["api_url"]).rstrip("/"),
            api_version=int(blob["api_version"]),
            timeout_s=float(blob["timeout_s"]),
            user_agent=str(providers["history"]["user_agent"]),
        )


def blob_token(env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    return env.get("BLOB_READ_WRITE_TOKEN") or None


def http_send(timeout_s: float) -> Send:
    def send(method: str, url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return int(resp.status), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    return send


class BlobSink:
    """Public, fixed-path uploads that overwrite (no random suffix), through the rate limiter.
    The token goes only into the Authorization header; errors never include it."""

    def __init__(self, token: str, cfg: BlobConfig, limiter: Limiter, send: Send) -> None:
        parts = token.split("_")
        if len(parts) < 5 or not parts[3]:
            raise BlobError("BLOB_READ_WRITE_TOKEN is not a read-write token")
        self._token = token
        self._store_id = parts[3]
        self.cfg = cfg
        self._limiter = limiter
        self._send = send

    @property
    def base_url(self) -> str:
        """Where the files are served from (the site's VITE_DATA_BASE_URL)."""
        return f"https://{self._store_id.lower()}.public.blob.vercel-storage.com"

    def put(self, path: str, body: bytes, max_age: int) -> None:
        self._limiter.acquire(BLOB)
        url = f"{self.cfg.api_url}/?{urllib.parse.urlencode({'pathname': path})}"
        headers = {
            "authorization": f"Bearer {self._token}",
            "x-api-version": str(self.cfg.api_version),
            "x-vercel-blob-store-id": self._store_id,
            "x-vercel-blob-access": "public",
            "x-content-type": "application/json",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
            "x-cache-control-max-age": str(max_age),
            "user-agent": self.cfg.user_agent,
        }
        status, resp = self._send("PUT", url, headers, body)
        if status != 200:
            raise BlobError(f"blob PUT {path}: HTTP {status} {_message(resp)}")


def _message(resp: bytes) -> str:
    try:
        err = json.loads(resp).get("error", {})
        return str(err.get("message") or err.get("code") or "")[:200]
    except (ValueError, AttributeError):
        return ""
