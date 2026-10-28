"""Daily aggregates feeding baselines and detectors (SPEC §7; owner decisions Oct 2026)."""

from datetime import UTC, date, datetime, timedelta

import h3
import pytest

from tw.baselines.aggregates import (
    AGG_ARRIVALS,
    AGG_COVERAGE,
    AGG_REGION_FAMILY,
    RegionMapper,
    build_aggregates,
)
from tw.baselines.stats import mad, median, robust_z
from tw.config import load_regions, load_yaml
from tw.store.parquet import (
    COVERAGE_SCHEMA,
    POINT_SCHEMA,
    SORTIE_SCHEMA,
    partition_dir,
    read_day,
    write_partition,
)

D = date(2026, 9, 15)
CONUS_EAST, NATL, EUROPE = (39.0, -77.0), (45.0, -40.0), (50.0, 8.0)


def cell(lat_lon: tuple[float, float]) -> str:
    return h3.latlng_to_cell(lat_lon[0], lat_lon[1], 4)


def test_median_mad_and_floor():
    assert median([3, 1, 2]) == 2 and median([1, 2, 3, 4]) == 2.5
    assert mad([1, 2, 3, 4, 100]) == 1.0
    cfg = load_yaml("detectors.yaml")["baseline"]
    assert robust_z(10, [5, 5, 5, 5], cfg) == pytest.approx(5 / (1.4826 * 1.0))  # MAD 0 → floor
    assert robust_z(9, [3, 5, 7, 9, 11], cfg) == pytest.approx((9 - 7) / (1.4826 * 2))


def sortie(
    sid: str,
    hex_code: str,
    cells,
    minutes: int,
    dest=None,
    families=("tanker",),
    is_open=False,
    end=EUROPE,
):
    start = datetime(2026, 9, 15, 6, tzinfo=UTC)
    return {
        "sortie_id": sid,
        "hex": hex_code,
        "type": "K35R",
        "families": list(families),
        "callsign": None,
        "start_ts": start,
        "end_ts": start + timedelta(minutes=minutes),
        "origin_icao": None,
        "dest_icao": dest,
        "start_lat": 0.0,
        "start_lon": 0.0,
        "end_lat": end[0],
        "end_lon": end[1],
        "h3_cells": sorted(cells),
        "max_alt": 30000,
        "n_points": 100,
        "low_quality_share": 0.0,
        "start_seen_low": True,
        "end_seen_low": True,
        "end_reason": "open" if is_open else "landing",
        "is_open": is_open,
        "start_truncated": False,
        "is_orbit": None,
        "orbit_hours": None,
    }


def store_day(root, day, sorties, coverage=()):
    write_partition(root, "points", day, [], POINT_SCHEMA, "t")  # the day exists
    write_partition(root, "sorties", day, sorties, SORTIE_SCHEMA, "sorties")
    write_partition(root, "coverage", day, list(coverage), COVERAGE_SCHEMA, "t")


def test_sortie_counts_in_every_region_it_flies_in(tmp_path):
    cells = [cell(CONUS_EAST), cell(NATL), cell(EUROPE)]
    store_day(tmp_path, D, [sortie("a", "ae0001", cells, 480, dest="ETAR")])
    build_aggregates(D, tmp_path)
    rows = read_day(tmp_path, AGG_REGION_FAMILY, D).to_pylist()
    regions = {r["region"] for r in rows if r["family"] == "tanker" and r["sorties"] == 1}
    assert regions == {"conus_east", "north_atlantic", "europe"}
    (arr,) = read_day(tmp_path, AGG_ARRIVALS, D).to_pylist()
    assert (arr["airfield"], arr["region"], arr["hex"]) == ("ETAR", "europe", "ae0001")


def test_short_sorties_and_open_arrivals_do_not_count(tmp_path):
    store_day(
        tmp_path,
        D,
        [
            sortie("short", "ae0001", [cell(EUROPE)], 20, dest="ETAR"),
            sortie("open", "ae0002", [cell(EUROPE)], 90, is_open=True),
        ],
    )
    build_aggregates(D, tmp_path)
    rows = read_day(tmp_path, AGG_REGION_FAMILY, D).to_pylist()
    (eu,) = [r for r in rows if r["region"] == "europe" and r["family"] == "tanker"]
    assert eu["sorties"] == 1 and eu["sortie_ids"] == ["open"]
    assert read_day(tmp_path, AGG_ARRIVALS, D).num_rows == 0


def test_zero_rows_for_every_key_and_coverage_by_region(tmp_path):
    hour = datetime(2026, 9, 15, 3, tzinfo=UTC)
    cov = [
        {"hour": hour, "h3_r4": cell(EUROPE), "aircraft": 40},
        {"hour": hour + timedelta(hours=1), "h3_r4": cell(EUROPE), "aircraft": 2},
    ]
    store_day(tmp_path, D, [], cov)
    build_aggregates(D, tmp_path)
    rows = read_day(tmp_path, AGG_REGION_FAMILY, D).to_pylist()
    n_regions = len(load_regions().regions)
    assert len(rows) == n_regions * 3 and all(r["sorties"] == 0 for r in rows)  # 3 families
    covs = {
        r["region"]: r["aircraft_hours"] for r in read_day(tmp_path, AGG_COVERAGE, D).to_pylist()
    }
    assert covs["europe"] == 42 and covs["conus_east"] == 0


def test_missing_day_has_no_aggregates(tmp_path):
    assert build_aggregates(D, tmp_path) is False  # no points partition: missing, not zero
    assert not partition_dir(tmp_path, AGG_REGION_FAMILY, D).exists()


def test_region_mapper_falls_back_to_other():
    mapper = RegionMapper(load_regions())
    assert mapper.region(cell(EUROPE)) == "europe"
    assert mapper.region(cell((-60.0, 0.0))) == "other"  # Southern Ocean


def test_a_full_day_window_counts_like_the_daily_aggregate(tmp_path):
    """The provisional tier counts with the same code: a window over exactly one day gives the
    stored day's rows."""
    from tests.synthetic import sortie, store_day
    from tw.baselines.aggregates import window_arrivals, window_counts
    from tw.config import load_fleet

    rows = [sortie(D, i, dest="ETAR") for i in range(6)] + [sortie(D, 9, minutes=20)]
    store_day(tmp_path, D, rows)
    fleet, mapper = load_fleet(), RegionMapper(load_regions())
    assert build_aggregates(D, tmp_path, fleet, mapper, 30)
    start = datetime(2026, 9, 15, tzinfo=UTC)
    end = start + timedelta(days=1)
    stored = read_day(tmp_path, "sorties", D).to_pylist()
    assert (
        window_counts(stored, start, end, fleet, mapper, 30)
        == read_day(tmp_path, AGG_REGION_FAMILY, D).to_pylist()
    )
    assert (
        window_arrivals(stored, start, end, fleet, mapper, 30)
        == read_day(tmp_path, AGG_ARRIVALS, D).to_pylist()
    )
    # A window ending before they landed sees the flights but none of the arrivals.
    early = start + timedelta(hours=9)
    assert sum(r["sorties"] for r in window_counts(stored, start, early, fleet, mapper, 30)) > 0
    assert window_arrivals(stored, start, early, fleet, mapper, 30) == []
