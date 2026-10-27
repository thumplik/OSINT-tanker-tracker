"""Build `sorties/date=D/` from stored points (SPEC §5, §11).

Day D's partition holds every sortie with a point in D. It is built from the points of D and
the `lookback_days` before it, never from later days, so a past day's sorties never change
when newer data arrives. A sortie still airborne at the end of D is stored as open, with no
destination. The next day's build closes it under the same `sortie_id`, because the start is
found the same way from both days.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from tw.config import Fleet
from tw.store.parquet import SORTIE_SCHEMA, read_day, write_partition
from tw.tracks.airfields import AirfieldIndex
from tw.tracks.segment import (
    RawSortie,
    SortieRules,
    TrackPoint,
    resolve_ends,
    seen_low,
    segment,
)

PART = "sorties"


@dataclass(frozen=True)
class DayBuild:
    day: date
    sorties: int
    open: int
    both_ends: int  # closed sorties with origin and destination


def _midnight(day: date) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()


def _tracks(
    data_dir: Path, days: Sequence[date], fleet: Fleet
) -> tuple[dict[str, list[TrackPoint]], dict[str, str | None]]:
    """Points per hex (sorted by time) and each hex's type, from the given day partitions."""
    low_quality = set(fleet.low_quality_sources)
    tracks: dict[str, list[TrackPoint]] = defaultdict(list)
    types: dict[str, str | None] = {}
    for day in days:
        t = read_day(data_dir, "points", day).to_pydict()
        for i in range(len(t["hex"])):
            lat, lon = t["lat"][i], t["lon"][i]
            if lat is None or lon is None:
                continue
            hex_code = t["hex"][i]
            types[hex_code] = t["type"][i] or types.get(hex_code)
            tracks[hex_code].append(
                TrackPoint(
                    ts=t["ts"][i].timestamp(),
                    lat=lat,
                    lon=lon,
                    alt=t["alt_baro"][i],
                    on_ground=t["on_ground"][i],
                    callsign=t["callsign"][i],
                    h3_r4=t["h3_r4"][i],
                    low_quality=t["position_source"][i] in low_quality,
                )
            )
    for pts in tracks.values():
        pts.sort(key=lambda p: p.ts)
    return tracks, types


def _row(
    hex_code: str,
    type_code: str | None,
    raw: RawSortie,
    fleet: Fleet,
    ends: tuple[str | None, str | None],
    low: tuple[bool, bool],
    is_open: bool,
    truncated: bool,
) -> dict[str, Any]:
    airborne = [p for p in raw.points if p.on_ground is not True]
    first, last = airborne[0], airborne[-1]
    calls = Counter(p.callsign for p in airborne if p.callsign)
    callsign = min(calls, key=lambda c: (-calls[c], c)) if calls else None
    alts = [p.alt for p in airborne if p.alt is not None]
    start = datetime.fromtimestamp(first.ts, tz=UTC)
    return {
        "sortie_id": f"{hex_code}-{start:%Y%m%dT%H%M%SZ}",
        "hex": hex_code,
        "type": type_code,
        "families": list(fleet.families_for_type(type_code)),
        "callsign": callsign,
        "start_ts": start,
        "end_ts": datetime.fromtimestamp(last.ts, tz=UTC),
        "origin_icao": ends[0],
        "dest_icao": None if is_open else ends[1],
        "start_lat": first.lat,
        "start_lon": first.lon,
        "end_lat": last.lat,
        "end_lon": last.lon,
        "h3_cells": sorted({p.h3_r4 for p in airborne if p.h3_r4}),
        "max_alt": max(alts) if alts else None,
        "n_points": len(airborne),
        "low_quality_share": sum(p.low_quality for p in airborne) / len(airborne),
        "start_seen_low": low[0],
        "end_seen_low": low[1] and not is_open,
        "end_reason": "open" if is_open else raw.end_reason,
        "is_open": is_open,
        "start_truncated": truncated,
        "is_orbit": None,  # D3, milestone M6
        "orbit_hours": None,
    }


def build_day(
    day: date,
    data_dir: Path,
    fleet: Fleet,
    index: AirfieldIndex,
    rules: SortieRules,
    lookback_days: int,
) -> DayBuild:
    days = [day - timedelta(days=n) for n in range(lookback_days, -1, -1)]
    window_start, day_start = _midnight(days[0]), _midnight(day)
    day_end = day_start + 86400
    tracks, types = _tracks(data_dir, days, fleet)
    rows = []
    for hex_code in sorted(tracks):
        for raw in segment(tracks[hex_code], rules):
            airborne = [p for p in raw.points if p.on_ground is not True]
            if airborne[-1].ts < day_start:
                continue  # belongs to an earlier day's partition
            # Last seen close to the end of the day and not landed: may still be flying.
            is_open = raw.end_reason == "data_end" and day_end - airborne[-1].ts <= rules.max_gap_s
            if raw.end_reason == "data_end" and not is_open:
                raw = RawSortie(raw.points, raw.before, raw.after, "gap")
            truncated = raw.before is None and airborne[0].ts - window_start <= rules.max_gap_s
            ends, low = resolve_ends(raw, index, rules), seen_low(raw, index, rules)
            rows.append(_row(hex_code, types[hex_code], raw, fleet, ends, low, is_open, truncated))
    rows.sort(key=lambda r: (r["start_ts"], r["hex"]))
    write_partition(data_dir, "sorties", day, rows, SORTIE_SCHEMA, PART)
    closed = [r for r in rows if not r["is_open"]]
    return DayBuild(
        day=day,
        sorties=len(rows),
        open=len(rows) - len(closed),
        both_ends=sum(1 for r in closed if r["origin_icao"] and r["dest_icao"]),
    )


# --- Parallel builds: each process loads config and the airfield index once ---------------

_worker_state: dict[str, Any] = {}


def init_sortie_worker() -> None:
    from tw.config import load_fleet, load_yaml
    from tw.tracks.airfields import load_airfields

    cfg = load_yaml("detectors.yaml")["sortie"]
    _worker_state.update(
        fleet=load_fleet(),
        index=AirfieldIndex(load_airfields()),
        rules=SortieRules.from_config(cfg),
        lookback_days=int(cfg["lookback_days"]),
    )


def build_day_in_worker(day: date, data_dir: Path) -> DayBuild:
    s = _worker_state
    return build_day(day, data_dir, s["fleet"], s["index"], s["rules"], s["lookback_days"])


# --- Resolution report (M2 "done when": ≥ 90% of closed sorties have both ends) -------------


def latest_sorties(data_dir: Path, days: Sequence[date]) -> list[dict[str, Any]]:
    """Sorties from the given day partitions, one row per sortie_id (latest day wins, so a
    sortie open at midnight is counted once, in its closed form when available)."""
    by_id: dict[str, dict[str, Any]] = {}
    for day in sorted(days):
        for row in read_day(data_dir, "sorties", day).to_pylist():
            by_id[row["sortie_id"]] = row
    return sorted(by_id.values(), key=lambda r: (r["start_ts"], r["hex"]))


def resolution_report(
    rows: Sequence[dict[str, Any]], index: AirfieldIndex, top: int = 15
) -> list[str]:
    closed = [r for r in rows if not r["is_open"]]

    def low_share(group: Sequence[dict[str, Any]]) -> str:
        """M2 target: origins (destinations) resolved among starts (ends) seen low."""
        parts = []
        for seen, key in (("start_seen_low", "origin_icao"), ("end_seen_low", "dest_icao")):
            low = [r for r in group if r[seen]]
            hit = sum(1 for r in low if r[key])
            parts.append(f"{hit / len(low):6.1%} of {len(low):>5}" if low else "     - of     0")
        return "   ".join(parts)

    def share(group: Sequence[dict[str, Any]]) -> str:
        both = sum(1 for r in group if r["origin_icao"] and r["dest_icao"])
        return f"{both / len(group):6.1%} of {len(group):>6}" if group else "     - of      0"

    both_low = [r for r in closed if r["start_seen_low"] and r["end_seen_low"]]
    lines = [
        "M2 target: >= 90% of ends seen low get an airfield",
        f"  {'':<18} {'origin':<16}   {'destination':<16}",
        f"  {'all':<18} {low_share(closed)}",
    ]
    families = sorted({f for r in closed for f in r["families"]})
    lines += [f"  {f:<18} {low_share([r for r in closed if f in r['families']])}" for f in families]
    lines.append(f"both ends, where both were seen low: {share(both_low)}")
    lines.append(f"both ends, all closed sorties:       {share(closed)}")
    seen = sum(r["start_seen_low"] for r in closed) + sum(r["end_seen_low"] for r in closed)
    lines.append(
        f"ends seen low: {seen / max(1, 2 * len(closed)):.0%} (the rest are coverage edges)"
    )
    lines.append("by position quality:")
    mlat = [r for r in closed if r["low_quality_share"] >= 0.5]
    adsb = [r for r in closed if r["low_quality_share"] < 0.5]
    lines.append(f"  {'mostly ADS-B':<18} {low_share(adsb)}")
    lines.append(f"  {'mostly MLAT etc':<18} {low_share(mlat)}")
    reasons = Counter(r["end_reason"] for r in closed)
    lines.append("end reasons: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))

    # Where ends seen low still go unresolved, with the nearest field: finds missing airfields.
    misses: Counter[tuple[float, float]] = Counter()
    for r in closed:
        if r["start_seen_low"] and not r["origin_icao"]:
            misses[(round(r["start_lat"], 1), round(r["start_lon"], 1))] += 1
        if r["end_seen_low"] and not r["dest_icao"]:
            misses[(round(r["end_lat"], 1), round(r["end_lon"], 1))] += 1
    lines.append("top unresolved ends seen low (0.1° bins; nearest field within 50 km):")
    for (lat, lon), n in misses.most_common(top):
        hit = index.nearest(lat, lon, 50)
        near = f"{hit[0].code} {hit[0].name[:40]} {hit[1]:.0f} km" if hit else "none"
        lines.append(f"  {n:>5}  {lat:7.1f} {lon:7.1f}  {near}")
    return lines
