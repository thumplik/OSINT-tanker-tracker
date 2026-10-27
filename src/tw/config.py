"""Typed loaders for `config/*.yaml`. Every threshold lives in YAML, never in code."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

RegionStatus = Literal["watch", "alerting"]


@dataclass(frozen=True)
class Fleet:
    hex_start: int
    hex_end: int
    families: dict[str, frozenset[str]]
    low_quality_sources: frozenset[str]

    def in_military_block(self, hex_code: str) -> bool:
        try:
            value = int(hex_code.lstrip("~"), 16)
        except ValueError:
            return False
        return self.hex_start <= value <= self.hex_end

    def families_for_type(self, type_code: str | None) -> tuple[str, ...]:
        if not type_code:
            return ()
        code = type_code.upper()
        return tuple(sorted(f for f, types in self.families.items() if code in types))

    @property
    def tracked_types(self) -> frozenset[str]:
        return frozenset().union(*self.families.values())


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    status: RegionStatus
    boxes: tuple[tuple[float, float, float, float], ...]

    def contains(self, lat: float, lon: float) -> bool:
        return any(
            lat_min <= lat <= lat_max and lon_min <= lon <= lon_max
            for lat_min, lat_max, lon_min, lon_max in self.boxes
        )


@dataclass(frozen=True)
class Regions:
    h3_resolution: int
    regions: tuple[Region, ...]

    def by_id(self, region_id: str) -> Region:
        for region in self.regions:
            if region.id == region_id:
                return region
        raise KeyError(region_id)


def _read(config_dir: Path, name: str) -> dict[str, Any]:
    with (config_dir / name).open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{name}: expected a mapping at top level")
    return data


def load_fleet(config_dir: Path = DEFAULT_CONFIG_DIR) -> Fleet:
    raw = _read(config_dir, "fleet.yaml")
    block = raw["military_hex_block"]
    return Fleet(
        hex_start=int(block["start"], 16),
        hex_end=int(block["end"], 16),
        families={
            name: frozenset(t.upper() for t in spec["types"])
            for name, spec in raw["families"].items()
        },
        low_quality_sources=frozenset(raw.get("low_quality_sources", [])),
    )


def load_regions(config_dir: Path = DEFAULT_CONFIG_DIR) -> Regions:
    raw = _read(config_dir, "regions.yaml")
    regions = []
    for r in raw["regions"]:
        if r["status"] not in ("watch", "alerting"):
            raise ValueError(f"region {r['id']}: invalid status {r['status']!r}")
        regions.append(
            Region(
                id=r["id"],
                name=r["name"],
                status=r["status"],
                boxes=tuple(tuple(float(v) for v in box) for box in r["boxes"]),  # type: ignore[misc]
            )
        )
    if not regions or regions[-1].id != "other" or regions[-1].boxes:
        raise ValueError("regions.yaml must end with a catch-all 'other' region with no boxes")
    return Regions(h3_resolution=int(raw["h3_resolution"]), regions=tuple(regions))


def load_yaml(name: str, config_dir: Path = DEFAULT_CONFIG_DIR) -> dict[str, Any]:
    return _read(config_dir, name)


def config_hash(config_dir: Path = DEFAULT_CONFIG_DIR) -> str:
    """Stable hash of all config files, recorded on every run so results are reproducible."""
    digest = hashlib.sha256()
    for path in sorted(config_dir.glob("*.yaml")):
        content = yaml.safe_load(path.read_text())
        digest.update(path.name.encode())
        digest.update(json.dumps(content, sort_keys=True, default=str).encode())
    return digest.hexdigest()[:16]
