"""Publisher settings from `config/publisher.yaml`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tw.config import DEFAULT_CONFIG_DIR, load_yaml


@dataclass(frozen=True)
class PublisherSettings:
    auto_post: bool = False
    daily_post_cap: int = 6
    links_in_self_reply: bool = True


def load_publisher_settings(config_dir: Path = DEFAULT_CONFIG_DIR) -> PublisherSettings:
    raw = load_yaml("publisher.yaml", config_dir)
    return PublisherSettings(
        auto_post=raw.get("auto_post", False) is True,
        daily_post_cap=int(raw.get("daily_post_cap", 6)),
        links_in_self_reply=bool(raw.get("links_in_self_reply", True)),
    )


def may_post(status: str) -> bool:
    """The only gate for posting. Auto-post mode may approve alerts; it never skips this check."""
    return status == "approved"
