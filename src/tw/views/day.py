"""One day as the dashboard shows it (SPEC §10, §11). The exporter writes exactly these dicts as
JSON, so the deployed site and local development see the same data (CLAUDE.md rule 9).

Point-in-time: a day's view reads that day's partitions, partitions of earlier days, and the
newest baseline snapshot dated on or before it. Nothing dated later is read, so adding later data
never changes an earlier day's view. Views never write (no aggregates, no snapshots).

Tiers:
- `official`: the day's release is loaded (`points/date=D` exists). Counts come from the stored
  aggregates; coverage is known.
- `provisional`: only live data exists (`live/points/date=D`), i.e. today, or yesterday before
  the nightly run. Counts use the same `count_sorties` over live sorties; coverage is unknown.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import duckdb
import h3

from tw.baselines.aggregates import (
    AGG_COVERAGE,
    AGG_REGION_FAMILY,
    RegionMapper,
    count_sorties,
)
from tw.baselines.build import BL_COVERAGE, BL_REGION_FAMILY, AggregateCache, latest_snapshot
from tw.baselines.stats import robust_z_from
from tw.config import Fleet, Regions, load_fleet, load_regions, load_yaml
from tw.detectors.types import Alert
from tw.provenance import code_version, effective_config_hash
from tw.store.alerts import OFFICIAL, PROVISIONAL, read_alerts
from tw.store.parquet import partition_dir, read_day
from tw.theater.aggregates import (
    AGG_CCMD_COVERAGE,
    AGG_INVENTORY,
    AGG_MOVES,
    AGG_TRANSITS,
    BL_CCMD,
    SERIES,
    daily_counts,
    inventory_totals,
    latest_theater_snapshot,
    lookback_sorties,
    theater_day,
)
from tw.theater.ccmd import CcmdMapper, Ccmds, load_ccmds

Tier = Literal["official", "provisional"]
ALL = "all"  # pseudo-family: any tracked type, each aircraft once
SCHEMA_VERSION = 2  # 2: theaters (M5c)


@dataclass
class ViewContext:
    data_dir: Path
    cfg: dict[str, Any] = field(default_factory=lambda: load_yaml("detectors.yaml"))
    fleet: Fleet = field(default_factory=load_fleet)
    regions: Regions = field(default_factory=load_regions)
    ccmds: Ccmds = field(default_factory=load_ccmds)

    def __post_init__(self) -> None:
        self.mapper = RegionMapper(self.regions)
        self.ccmd_mapper = CcmdMapper(self.ccmds)
        self.cache = AggregateCache(self.data_dir)  # no `ensure`: views never write

    @property
    def live_dir(self) -> Path:
        return self.data_dir / "live"

    @property
    def families(self) -> list[str]:
        return sorted(self.fleet.families)


def day_tier(ctx: ViewContext, day: date) -> Tier | None:
    if partition_dir(ctx.data_dir, "points", day).exists():
        return "official"
    if partition_dir(ctx.live_dir, "points", day).exists():
        return "provisional"
    return None


def available_days(ctx: ViewContext) -> dict[date, Tier]:
    """Every day with official or live points (official wins)."""
    days: dict[date, Tier] = {}
    for root, tier in ((ctx.live_dir, "provisional"), (ctx.data_dir, "official")):
        base = root / "points"
        if base.exists():
            for p in base.iterdir():
                if p.name.startswith("date="):
                    days[date.fromisoformat(p.name[5:])] = tier  # type: ignore[assignment]
    return dict(sorted(days.items()))


def _iso(ts: datetime | None) -> str | None:
    return None if ts is None else ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _type_families(ctx: ViewContext) -> list[tuple[str, str]]:
    pairs = [(t, f) for f, types in ctx.fleet.families.items() for t in sorted(types)]
    return sorted(pairs) + sorted((t, ALL) for t in ctx.fleet.tracked_types)


def _heat(ctx: ViewContext, points_dir: Path, cell_col: str, hourly: bool) -> dict[str, Any]:
    """Distinct aircraft per (cell[, hour], family) from one day's points. Compact: cells are
    listed once and rows refer to them by index."""
    families = [*ctx.families, ALL]
    files = sorted(str(f) for f in points_dir.glob("part-*.parquet"))
    if not files:
        return {"families": families, "cells": [], "rows": []}
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE tf (type VARCHAR, family VARCHAR)")
        con.executemany("INSERT INTO tf VALUES (?, ?)", _type_families(ctx))
        hour = "CAST(hour(p.ts AT TIME ZONE 'UTC') AS INTEGER)" if hourly else "0"
        rows = con.execute(
            f"""
            SELECT p.{cell_col} AS cell, {hour} AS h, tf.family, COUNT(DISTINCT p.hex) AS n
            FROM read_parquet($files, union_by_name = true) p
            JOIN tf ON upper(p.type) = tf.type
            WHERE p.{cell_col} IS NOT NULL
            GROUP BY ALL
            ORDER BY cell, h, tf.family
            """,
            {"files": files},
        ).fetchall()
    finally:
        con.close()
    cells = sorted({r[0] for r in rows})
    cell_ix = {c: i for i, c in enumerate(cells)}
    fam_ix = {f: i for i, f in enumerate(families)}
    out = (
        [[cell_ix[c], h, fam_ix[f], n] for c, h, f, n in rows]
        if hourly
        else [[cell_ix[c], fam_ix[f], n] for c, _, f, n in rows]
    )
    return {"families": families, "cells": cells, "rows": out}


def _region_rows(
    ctx: ViewContext, day: date, tier: Tier, sorties: list[dict[str, Any]], as_of: date | None
) -> list[dict[str, Any]]:
    if tier == "official" and partition_dir(ctx.data_dir, AGG_REGION_FAMILY, day).exists():
        counts = read_day(ctx.data_dir, AGG_REGION_FAMILY, day).to_pylist()
    else:  # live day, or an official day the pipeline hasn't aggregated: count the same way
        minutes = float(ctx.cfg["counting"]["min_sortie_minutes"])
        counts = count_sorties(sorties, ctx.fleet, ctx.mapper, minutes)[0]
    baseline = _baseline(ctx, BL_REGION_FAMILY, as_of, day, ("region", "family"))
    bl_cfg = ctx.cfg["baseline"]
    min_samples = _min_samples(ctx)
    status = {r.id: r.status for r in ctx.regions.regions}
    rows = []
    for r in counts:
        b = baseline.get((r["region"], r["family"]))
        enough = b is not None and b["n_days"] >= min_samples
        rows.append(
            {
                "region": r["region"],
                "family": r["family"],
                "status": status.get(r["region"]),
                "observed": r["sorties"],
                "aircraft": r["hexes"],
                "median": b["median"] if b else None,
                "mad": b["mad"] if b else None,
                "n_days": b["n_days"] if b else 0,
                "z": round(robust_z_from(r["sorties"], b["median"], b["mad"], bl_cfg), 2)
                if b and enough
                else None,
            }
        )
    return rows


def _min_samples(ctx: ViewContext) -> int:
    """Same-weekday samples a key needs before it is judged (as D1 does)."""
    return math.ceil(int(ctx.cfg["baseline"]["min_history_days"]) / 7)


def _baseline(
    ctx: ViewContext, dataset: str, as_of: date | None, day: date, key: tuple[str, ...]
) -> dict[Any, dict[str, Any]]:
    """This weekday's rows of the snapshot taken as of `as_of`."""
    if as_of is None:
        return {}
    rows = read_day(ctx.data_dir, dataset, as_of).to_pylist()
    return {
        (tuple(r[k] for k in key) if len(key) > 1 else r[key[0]]): r
        for r in rows
        if r["weekday"] == day.weekday()
    }


def _coverage_rows(
    ctx: ViewContext, day: date, tier: Tier, as_of: date | None
) -> list[dict[str, Any]]:
    if tier != "official":
        return []
    baseline = _baseline(ctx, BL_COVERAGE, as_of, day, ("region",))
    rows = []
    for r in read_day(ctx.data_dir, AGG_COVERAGE, day).to_pylist():
        b = baseline.get(r["region"])
        med = b["median"] if b else None
        rows.append(
            {
                "region": r["region"],
                "aircraft_hours": r["aircraft_hours"],
                "median": med,
                "ratio": round(r["aircraft_hours"] / med, 3) if med else None,
            }
        )
    return rows


def _alert(a: Alert, tier: Tier) -> dict[str, Any]:
    return {
        "alert_id": a.alert_id,
        "tier": tier,
        "detector": a.detector,
        "region": a.region,
        "window_start": _iso(a.window_start),
        "window_end": _iso(a.window_end),
        "observed": a.observed,
        "expected": a.expected,
        "score": round(a.score, 2),
        "severity": a.severity,
        "status": a.status,
        "evidence": a.evidence,
        "sortie_ids": list(a.sortie_ids),
        "created_at": _iso(a.created_at),
    }


def _alerts(ctx: ViewContext, day: date) -> list[dict[str, Any]]:
    out = []
    for dataset, tier in ((OFFICIAL, "official"), (PROVISIONAL, "provisional")):
        alerts = read_alerts(ctx.data_dir, dataset, [day])
        out += [_alert(a, tier) for a in alerts]  # type: ignore[arg-type]
    return out


def _sortie(s: Mapping[str, Any], ctx: ViewContext, min_minutes: float) -> dict[str, Any]:
    minutes = (s["end_ts"] - s["start_ts"]).total_seconds() / 60
    return {
        "sortie_id": s["sortie_id"],
        "hex": s["hex"],
        "type": s["type"],
        "families": list(s["families"]),
        "callsign": (s["callsign"] or "").strip() or None,
        "start": _iso(s["start_ts"]),
        "end": _iso(s["end_ts"]),
        "minutes": round(minutes),
        "origin": s["origin_icao"],
        "dest": s["dest_icao"],
        "regions": sorted({ctx.mapper.region(c) for c in s["h3_cells"]}),
        "max_alt": s["max_alt"],
        "end_reason": s["end_reason"],
        "is_open": bool(s["is_open"]),
        "counted": minutes >= min_minutes,
    }


def _ts_rows(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{**r, **{k: _iso(r[k]) for k in keys}} for r in rows]


def _theaters(ctx: ViewContext, day: date, tier: Tier) -> dict[str, Any]:
    """CCMD inventory, entries/exits and transits from home (M5c). Official days read the
    stored theater aggregates; a live day computes its own the same way (`theater_day`) over
    official sorties where they exist and live ones after. Earlier days of the flow window always
    come from aggregates. Families are never summed together."""
    th = ctx.cfg["theater"]
    flow_days = int(th["flow_window_days"])
    families = ctx.families
    if tier == "official":
        today = {
            "inventory": ctx.cache.get(AGG_INVENTORY, day) or [],
            "moves": ctx.cache.get(AGG_MOVES, day) or [],
            "transits": ctx.cache.get(AGG_TRANSITS, day) or [],
        }
        sorties = lookback_sorties([ctx.data_dir], day, int(th["lookback_days"]))
        en_route = theater_day(sorties, day, ctx.ccmd_mapper, ctx.cfg, ctx.ccmds.home)["en_route"]
        built = ctx.cache.get(AGG_INVENTORY, day) is not None
    else:
        sorties = lookback_sorties([ctx.data_dir, ctx.live_dir], day, int(th["lookback_days"]))
        live = theater_day(sorties, day, ctx.ccmd_mapper, ctx.cfg, ctx.ccmds.home)
        today, en_route, built = live, live["en_route"], True

    # Flow sums over the window: today from `today`, earlier days from aggregates.
    sums: dict[tuple[str, str, str], int] = defaultdict(int)
    complete = built
    for m in today["moves"]:
        sums[(m["dst"], m["family"], "entries")] += 1
        sums[(m["src"], m["family"], "exits")] += 1
    for t in today["transits"]:
        sums[(t["dest"], t["family"], "transits")] += 1
    recent_transits = list(today["transits"])
    for back in range(1, flow_days):
        d = day - timedelta(days=back)
        counts = daily_counts(ctx.cache, d)
        if counts is None:
            complete = False
            continue
        for k, v in counts.items():
            sums[k] += v
        recent_transits += ctx.cache.get(AGG_TRANSITS, d) or []

    as_of = latest_theater_snapshot(ctx.data_dir, day)
    bl: dict[tuple[str, str, str], dict[str, Any]] = {}
    cov_bl: dict[str, dict[str, Any]] = {}
    if as_of is not None:
        for r in read_day(ctx.data_dir, BL_CCMD, as_of).to_pylist():
            if r["series"] == "coverage":
                if r["weekday"] == day.weekday():
                    cov_bl[r["ccmd"]] = r
            else:
                bl[(r["ccmd"], r["family"], r["series"])] = r
    min_blocks = int(th["min_blocks"])
    min_days = int(ctx.cfg["baseline"]["min_history_days"])
    base_cfg = ctx.cfg["baseline"]

    def judged(observed: float, key: tuple[str, str, str], need: int) -> dict[str, Any]:
        b = bl.get(key)
        ok = b is not None and b["n"] >= need
        return {
            "observed": observed,
            "median": b["median"] if b else None,
            "n": b["n"] if b else 0,
            "z": round(robust_z_from(observed, b["median"], b["mad"], base_cfg), 2)
            if b and ok
            else None,
        }

    by_type: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for r in today["inventory"]:
        by_type[(r["ccmd"], r["family"])][r["type"]] = int(r["aircraft"])
    totals = inventory_totals(today["inventory"])
    rows = []
    for c in ctx.ccmds.ids:
        for f in families:
            row: dict[str, Any] = {
                "ccmd": c,
                "family": f,
                "by_type": dict(sorted(by_type[(c, f)].items())),
                "inventory": judged(totals.get((c, f), 0), (c, f, "inventory"), min_days),
            }
            for s in SERIES:
                row[s] = judged(sums.get((c, f, s), 0), (c, f, s), min_blocks) if complete else None
            rows.append(row)

    history: dict[str, list[int | None]] = {}
    span = int(ctx.cfg["baseline"]["window_days"])
    for back in range(span - 1, -1, -1):
        d = day - timedelta(days=back)
        inv = today["inventory"] if back == 0 else ctx.cache.get(AGG_INVENTORY, d)
        tot = None if inv is None else inventory_totals(inv)
        for c in ctx.ccmds.ids:
            for f in families:
                value = None if tot is None else tot.get((c, f), 0)
                history.setdefault(f"{c}|{f}", []).append(value)

    coverage = []
    for r in (ctx.cache.get(AGG_CCMD_COVERAGE, day) or []) if tier == "official" else []:
        cb = cov_bl.get(r["ccmd"])
        med = cb["median"] if cb else None
        coverage.append(
            {
                "ccmd": r["ccmd"],
                "aircraft_hours": r["aircraft_hours"],
                "median": med,
                "ratio": round(r["aircraft_hours"] / med, 3) if med else None,
            }
        )
    return {
        "home": ctx.ccmds.home,
        "baseline_as_of": as_of.isoformat() if as_of else None,
        "window_days": flow_days,
        "window_complete": complete,
        "max_age_days": int(th["max_age_days"]),
        "dwell_hours": float(th["dwell_hours"]),
        "min_blocks": min_blocks,
        "rows": rows,
        "history": history,
        "coverage": coverage,
        "moves": _ts_rows(sorted(today["moves"], key=lambda m: (m["ts"], m["hex"])), ("ts",)),
        "transits": _ts_rows(
            sorted(recent_transits, key=lambda t: (t["resolved_ts"], t["hex"])),
            ("depart_ts", "arrive_ts", "resolved_ts"),
        ),
        "en_route": _ts_rows(en_route, ("depart_ts", "arrive_ts", "resolved_ts")),
    }


def day_view(ctx: ViewContext, day: date, now: datetime) -> dict[str, Any] | None:
    """The dashboard's view of `day`, or None when no data exists for it."""
    tier = day_tier(ctx, day)
    if tier is None:
        return None
    root = ctx.data_dir if tier == "official" else ctx.live_dir
    sorties = read_day(root, "sorties", day).to_pylist()
    sorties.sort(key=lambda s: (s["start_ts"], s["hex"]))
    as_of = latest_snapshot(ctx.data_dir, day)
    min_minutes = float(ctx.cfg["counting"]["min_sortie_minutes"])
    guards = ctx.cfg["guards"]
    return {
        "meta": {
            "schema": SCHEMA_VERSION,
            "date": day.isoformat(),
            "tier": tier,
            "baseline_as_of": as_of.isoformat() if as_of else None,
            "weekday": day.weekday(),
            "min_sortie_minutes": min_minutes,
            "min_baseline_samples": _min_samples(ctx),
            "baseline_window_days": int(ctx.cfg["baseline"]["window_days"]),
            "d1_min_z": float(ctx.cfg["d1_volume_surge"]["min_z"]),
            "coverage_min_ratio": float(guards["coverage_min_ratio"]),
            "coverage_max_ratio": float(guards["coverage_max_ratio"]),
            "generated_at": _iso(now),
            "config_hash": effective_config_hash(ctx.cfg),
            "code_version": code_version(),
        },
        "heat": _heat(ctx, partition_dir(root, "points", day), "h3_r4", hourly=True),
        "regions": _region_rows(ctx, day, tier, sorties, as_of),
        "coverage": _coverage_rows(ctx, day, tier, as_of),
        "alerts": _alerts(ctx, day),
        "sorties": [_sortie(s, ctx, min_minutes) for s in sorties],
        "theaters": _theaters(ctx, day, tier),
    }


def day_heat_r6(ctx: ViewContext, day: date) -> dict[str, Any] | None:
    """Whole-day r6 heat (loaded only when the local view is chosen; ~40k cells a day)."""
    tier = day_tier(ctx, day)
    if tier is None:
        return None
    root = ctx.data_dir if tier == "official" else ctx.live_dir
    heat = _heat(ctx, partition_dir(root, "points", day), "h3_r6", hourly=False)
    return {"date": day.isoformat(), "tier": tier, **heat}


def site_meta(ctx: ViewContext) -> dict[str, Any]:
    """Static lookups: regions (boxes, status), families and their types, data attribution."""
    providers = load_yaml("providers.yaml")
    cells = ccmd_cells(ctx)
    used = {"adsb_lol", "ourairports", *providers["live"]["order"]}  # history, airfields, live
    return {
        "schema": SCHEMA_VERSION,
        "regions": [
            {"id": r.id, "name": r.name, "status": r.status, "boxes": [list(b) for b in r.boxes]}
            for r in ctx.regions.regions
        ],
        "families": {f: sorted(ctx.fleet.families[f]) for f in ctx.families},
        "ccmds": [
            {
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "anchor": list(c.anchor),
                "cells": cells[c.id],
            }
            for c in ctx.ccmds.ccmds
        ],
        "attribution": sorted({providers["attribution"][p] for p in used}),
        "official_alerts_from": _official_alerts_from(ctx),
    }


CCMD_MAP_RESOLUTION = 2  # ~150 km cells: CCMD areas on the map, which never overlap


def ccmd_cells(ctx: ViewContext) -> dict[str, list[str]]:
    """Every H3 cell at `CCMD_MAP_RESOLUTION` by the CCMD holding its centre. The configured
    polygons overlap (first match wins), so the map draws these cells instead."""
    out: dict[str, list[str]] = {c: [] for c in ctx.ccmds.ids}
    for base in sorted(h3.get_res0_cells()):
        for cell in sorted(h3.cell_to_children(base, CCMD_MAP_RESOLUTION)):
            out[ctx.ccmd_mapper.cell(cell)].append(cell)
    return out


def _official_alerts_from(ctx: ViewContext) -> str | None:
    """The first day with official alerts: the day before the worker first started (M4)."""
    path = ctx.data_dir / "worker" / "state.json"
    if not path.exists():
        return None
    first = date.fromisoformat(json.loads(path.read_text())["first_start"])
    return (first - timedelta(days=1)).isoformat()


def site_index(ctx: ViewContext) -> dict[str, Any]:
    """Days with data, their tier and alert counts. The date picker reads this."""
    rows = []
    for day, tier in available_days(ctx).items():
        official = read_alerts(ctx.data_dir, OFFICIAL, [day])
        provisional = read_alerts(ctx.data_dir, PROVISIONAL, [day])
        rows.append(
            {
                "date": day.isoformat(),
                "tier": tier,
                "alerts": sum(a.status != "suppressed" for a in official),
                "provisional_alerts": sum(a.status != "suppressed" for a in provisional),
            }
        )
    return {"schema": SCHEMA_VERSION, "days": rows, "latest": rows[-1]["date"] if rows else None}
