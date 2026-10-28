"""Run provenance (SPEC §11): which configuration and code produced a baseline or an alert."""

from __future__ import annotations

import functools
import hashlib
import json
import subprocess
from collections.abc import Mapping
from typing import Any

from tw import __version__
from tw.config import DEFAULT_CONFIG_DIR, config_hash


def effective_config_hash(detector_cfg: Mapping[str, Any]) -> str:
    """Hash of the on-disk config plus the detector settings actually used (which a backtest
    sweep or a test may override), so two runs with different settings never share a hash."""
    digest = hashlib.sha256(config_hash().encode())
    digest.update(json.dumps(detector_cfg, sort_keys=True, default=str).encode())
    return digest.hexdigest()[:16]


@functools.cache
def code_version() -> str:
    """Package version plus the git commit (with `-dirty` for uncommitted changes) if known."""
    repo = DEFAULT_CONFIG_DIR.parent
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo), "describe", "--always", "--dirty", "--abbrev=12"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = "unknown"
    return f"{__version__}+{sha}"
