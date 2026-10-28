"""Theater flows (SPEC §5, M5c): where each aircraft is by CCMD, and how it moves between them.

Built only from where sorties start and end ("fixes"), never from the track in between, so an
aircraft crossing EUCOM on its way to CENTCOM has not entered EUCOM. Most arrivals in sparse
coverage are lost from view while still airborne, so ends seen aloft count too and are labeled:

- `ground`: the sortie end was seen on or near the ground (`start_seen_low` / `end_seen_low`);
- `aloft`: the aircraft was lost from view in the air;
- `last_seen`: the sortie is still open (airborne at the end of the data).

Everything here is pure: the same sorties always give the same fixes, moves and transits. The
caller hands in sorties of the last `theater.lookback_days` only, so a day's numbers never depend
on later data (CLAUDE.md rule 8). Families are never summed together.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from tw.theater.ccmd import CcmdMapper

EARTH_KM = 6371.0


@dataclass(frozen=True)
class Fix:
    hex: str
    type: str
    family: str
    ts: datetime
    lat: float
    lon: float
    ccmd: str
    quality: str  # ground | aloft | last_seen
    sortie_id: str
    airfield: str | None


@dataclass(frozen=True)
class Move:
    """A change of CCMD between two consecutive fixes of one aircraft: an exit and an entry."""

    hex: str
    type: str
    family: str
    ts: datetime  # the arriving fix
    src: str
    dst: str
    quality: str  # of the arriving fix
    sortie_id: str
    src_airfield: str | None
    dst_airfield: str | None


@dataclass(frozen=True)
class Transit:
    """A trip out of the home CCMD. Its destination is the CCMD of the fix farthest from where it
    left home (the turnaround); shorter stops on the way are `via`. It is resolved when the
    aircraft is back home, or has stayed `dwell_hours` in one CCMD."""

    hex: str
    type: str
    family: str
    depart_ts: datetime  # last fix at home
    arrive_ts: datetime  # the farthest fix
    resolved_ts: datetime | None  # None: still en route
    dest: str
    via: tuple[str, ...]
    resolution: str  # returned | stayed | en_route
    dest_airfield: str | None


def fixes(
    sorties: Iterable[Mapping[str, Any]], mapper: CcmdMapper, min_sortie_minutes: float
) -> list[Fix]:
    """Start and end fixes of every counted sortie, one family per fix (sorted by hex, time)."""
    out: list[Fix] = []
    for s in sorties:
        if (s["end_ts"] - s["start_ts"]).total_seconds() < min_sortie_minutes * 60:
            continue
        for family in s["families"]:
            common = {"hex": s["hex"], "type": s["type"] or "", "family": family}
            out.append(
                Fix(
                    **common,
                    ts=s["start_ts"],
                    lat=s["start_lat"],
                    lon=s["start_lon"],
                    ccmd=mapper.at(s["start_lat"], s["start_lon"]),
                    quality="ground" if s["start_seen_low"] else "aloft",
                    sortie_id=s["sortie_id"],
                    airfield=s["origin_icao"],
                )
            )
            end_quality = (
                "last_seen" if s["is_open"] else "ground" if s["end_seen_low"] else "aloft"
            )
            out.append(
                Fix(
                    **common,
                    ts=s["end_ts"],
                    lat=s["end_lat"],
                    lon=s["end_lon"],
                    ccmd=mapper.at(s["end_lat"], s["end_lon"]),
                    quality=end_quality,
                    sortie_id=s["sortie_id"],
                    airfield=s["dest_icao"],
                )
            )
    out.sort(key=lambda f: (f.hex, f.family, f.ts, f.sortie_id))
    return out


def _by_aircraft(all_fixes: Iterable[Fix]) -> dict[tuple[str, str], list[Fix]]:
    chains: dict[tuple[str, str], list[Fix]] = defaultdict(list)
    for f in all_fixes:
        chains[(f.hex, f.family)].append(f)
    for chain in chains.values():
        chain.sort(key=lambda f: (f.ts, f.sortie_id))
    return chains


def presence(
    all_fixes: Iterable[Fix], at: datetime, max_age: timedelta
) -> dict[tuple[str, str], Fix]:
    """Each aircraft's (hex, family) latest fix in (at - max_age, at]."""
    latest: dict[tuple[str, str], Fix] = {}
    for f in all_fixes:
        if at - max_age < f.ts <= at:
            key = (f.hex, f.family)
            if key not in latest or (f.ts, f.sortie_id) > (latest[key].ts, latest[key].sortie_id):
                latest[key] = f
    return latest


def inventory(all_fixes: Iterable[Fix], at: datetime, max_age: timedelta) -> list[dict[str, Any]]:
    """Distinct aircraft per (ccmd, family, type) whose latest fix lies in that CCMD."""
    hexes: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for f in presence(all_fixes, at, max_age).values():
        hexes[(f.ccmd, f.family, f.type)].add(f.hex)
    return [
        {"ccmd": c, "family": fam, "type": t, "aircraft": len(h)}
        for (c, fam, t), h in sorted(hexes.items())
    ]


def moves(all_fixes: Iterable[Fix], start: datetime, end: datetime) -> list[Move]:
    """CCMD changes whose arriving fix falls in [start, end). An aircraft's first fix in the
    data is not a move: where it came from is unknown."""
    out = []
    for chain in _by_aircraft(all_fixes).values():
        for prev, cur in pairwise(chain):
            if cur.ccmd != prev.ccmd and start <= cur.ts < end:
                out.append(
                    Move(
                        cur.hex,
                        cur.type,
                        cur.family,
                        cur.ts,
                        prev.ccmd,
                        cur.ccmd,
                        cur.quality,
                        cur.sortie_id,
                        prev.airfield,
                        cur.airfield,
                    )
                )
    return sorted(out, key=lambda m: (m.ts, m.hex, m.family))


def _km(a: Fix, b: Fix) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def _trip(depart: Fix, legs: Sequence[Fix], resolved: datetime | None, how: str) -> Transit:
    far = max(legs, key=lambda f: (_km(depart, f), f.ts))
    via: list[str] = []
    for f in legs:
        if f.ts >= far.ts:
            break
        if f.ccmd != far.ccmd and f.ccmd not in via:
            via.append(f.ccmd)
    return Transit(
        depart.hex,
        depart.type,
        depart.family,
        depart.ts,
        far.ts,
        resolved,
        far.ccmd,
        tuple(via),
        how,
        far.airfield,
    )


def transits(all_fixes: Iterable[Fix], home: str, dwell: timedelta, at: datetime) -> list[Transit]:
    """Trips out of `home` seen in the fixes up to `at`: resolved ones (`returned` when the
    aircraft is back home, `stayed` once it has spent `dwell` in one CCMD, timed at the end of
    that dwell) and unresolved ones (`en_route`). A trip whose departure from home isn't in the
    data is skipped: its origin is unknown."""
    out: list[Transit] = []
    for chain in _by_aircraft(f for f in all_fixes if f.ts <= at).values():
        depart: Fix | None = None
        legs: list[Fix] = []
        stay_since: datetime | None = None
        for f in chain:
            stayed = (
                depart is not None
                and stay_since is not None
                and legs
                and f.ccmd != legs[-1].ccmd
                and f.ts - stay_since >= dwell  # stayed long enough before leaving
            )
            if stayed:
                assert depart is not None and stay_since is not None
                out.append(_trip(depart, legs, stay_since + dwell, "stayed"))
                depart, legs, stay_since = None, [], None
            if f.ccmd == home:
                if depart is not None and legs:
                    out.append(_trip(depart, legs, f.ts, "returned"))
                depart, legs, stay_since = f, [], None
                continue
            if depart is None:
                continue  # abroad with no departure in view, or the trip already resolved
            if not legs or f.ccmd != legs[-1].ccmd:
                stay_since = f.ts
            legs.append(f)
        if depart is not None and legs and stay_since is not None:
            if at - stay_since >= dwell:
                out.append(_trip(depart, legs, stay_since + dwell, "stayed"))
            else:
                out.append(_trip(depart, legs, None, "en_route"))
    return sorted(out, key=lambda t: (t.depart_ts, t.hex, t.family))
