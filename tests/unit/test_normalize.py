from datetime import UTC, datetime

from tw.config import load_fleet
from tw.ingest.schema import RawPoint
from tw.normalize.coverage import CoverageAccumulator
from tw.normalize.points import build_registry, normalize


def raw(hex_code: str, type_code: str | None, lat: float | None = 40.0, sec: int = 0) -> RawPoint:
    return RawPoint(
        ts=datetime(2026, 7, 1, 12, 0, sec, tzinfo=UTC),
        hex=hex_code,
        callsign=None,
        type=type_code,
        lat=lat,
        lon=-76.0 if lat is not None else None,
        alt_baro=20000,
        on_ground=False,
        gs=400.0,
        track=90.0,
        position_source="adsb_icao",
        provider="test",
    )


def test_filters_by_block_and_family():
    fleet = load_fleet()
    pts = [raw("ae1234", "K35R"), raw("a12345", "K35R"), raw("ae7777", "H60"), raw("ae0001", "C5M")]
    kept = {p.hex for p in normalize(pts, fleet, registry={})}
    assert kept == {"ae1234", "ae0001"}


def test_typeless_hex_resolved_from_registry_before_discard():
    fleet = load_fleet()
    registry = build_registry([raw("ae9999", "KC46")])
    out = list(normalize([raw("ae9999", None)], fleet, registry))
    assert len(out) == 1 and out[0].type == "KC46"
    assert list(normalize([raw("ae9999", None)], fleet, registry={})) == []


def test_registry_majority_and_stable_ties():
    reg = build_registry([raw("ae1", "C17"), raw("ae1", "C5M"), raw("ae1", "C5M")])
    assert reg["ae1"] == "C5M"
    assert build_registry([raw("ae2", "C5M"), raw("ae2", "C17")])["ae2"] == "C17"


def test_missing_position_keeps_point_without_cells():
    out = list(normalize([raw("ae1234", "K35R", lat=None)], load_fleet(), registry={}))
    assert out[0].h3_r4 is None and out[0].h3_r6 is None
    assert out[0].on_ground is False


def test_coverage_counts_distinct_aircraft_with_sampling():
    acc = CoverageAccumulator(sample_seconds=60)
    acc.add([raw("a1", "B738", sec=0), raw("a1", "B738", sec=30), raw("a2", "A320", sec=0)])
    acc.add([raw("a3", None, lat=None)])  # no position: not counted
    rows = acc.rows()
    assert len(rows) == 1 and rows[0]["aircraft"] == 2


def test_b762_is_a_tanker_only_inside_the_military_block():
    fleet = load_fleet()
    assert "tanker" in fleet.families_for_type("B762")  # KC-46A as typed by adsb.lol
    pts = [raw("ae5c01", "B762"), raw("a4b2c1", "B762")]  # military vs civil 767-200
    assert {p.hex for p in normalize(pts, fleet, registry={})} == {"ae5c01"}
