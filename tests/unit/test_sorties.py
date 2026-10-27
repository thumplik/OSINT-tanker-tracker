"""Building a day's sorties partition: lookback, open sorties, point-in-time (SPEC §5, §11)."""

from datetime import UTC, date, datetime, timedelta

from tw.config import load_fleet, load_yaml
from tw.store.parquet import POINT_SCHEMA, partition_dir, read_day, write_partition
from tw.tracks.airfields import AirfieldIndex, load_airfields
from tw.tracks.segment import SortieRules
from tw.tracks.sorties import build_day

KIAB = (37.6219, -97.2682, 1371)
KDOV = (39.1295, -75.4660, 24)
CFG = load_yaml("detectors.yaml")["sortie"]
RULES = SortieRules.from_config(CFG)


def ts(day: date, minute: float) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(minutes=minute)


def point(t: datetime, lat: float, lon: float, alt: int | None, ground: bool, hex_code="ae5c01"):
    return {
        "ts": t,
        "hex": hex_code,
        "callsign": "GOLD21",
        "type": "B762",
        "lat": lat,
        "lon": lon,
        "alt_baro": None if ground else alt,
        "gs": 400.0,
        "track": 90.0,
        "on_ground": ground,
        "position_source": "adsb_icao",
        "provider": "test",
        "h3_r4": "84264c3ffffffff",
        "h3_r6": None,
    }


def flight_points(day: date, start_min: float, minutes: int, a=KIAB, b=KDOV, hex_code="ae5c01"):
    """Ground at `a`, fly `minutes`, land at `b`; may run past midnight into the next day."""
    rows = [point(ts(day, start_min + s / 60), a[0], a[1], None, True, hex_code) for s in (0, 60)]
    for m in range(minutes + 1):
        f = m / minutes
        edge = min(m, minutes - m)
        alt = min(30000, (a[2] if m < minutes / 2 else b[2]) + 300 + edge * 2000)
        lat, lon = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        rows.append(point(ts(day, start_min + 2 + m), lat, lon, alt, False, hex_code))
    end = start_min + 2 + minutes
    rows += [point(ts(day, end + s / 60), b[0], b[1], None, True, hex_code) for s in (30, 120)]
    return rows


def store(data_dir, rows, part="t"):
    by_day: dict[date, list] = {}
    for r in rows:
        by_day.setdefault(r["ts"].date(), []).append(r)
    for day, day_rows in by_day.items():
        write_partition(data_dir, "points", day, day_rows, POINT_SCHEMA, part)


def build(data_dir, day, fixtures):
    index = AirfieldIndex(load_airfields(fixtures / "airfields.csv"))
    build_day(day, data_dir, load_fleet(), index, RULES, lookback_days=1)
    return read_day(data_dir, "sorties", day).to_pylist()


D, D1 = date(2026, 9, 15), date(2026, 9, 16)


def test_day_sorties_have_fields_and_ends(tmp_path, fixtures):
    store(tmp_path, flight_points(D, 600, 150))
    (s,) = build(tmp_path, D, fixtures)
    assert (s["origin_icao"], s["dest_icao"], s["end_reason"]) == ("KIAB", "KDOV", "landing")
    assert s["families"] == ["tanker"] and s["type"] == "B762" and s["callsign"] == "GOLD21"
    assert s["sortie_id"] == "ae5c01-20260915T100200Z" and not s["is_open"]
    assert s["max_alt"] == 30000 and s["h3_cells"] == ["84264c3ffffffff"]


def test_midnight_crossing_is_open_then_closed_with_same_id(tmp_path, fixtures):
    store(tmp_path, flight_points(D, 23 * 60, 150))  # 23:00 → 01:32 next day
    (open_,) = build(tmp_path, D, fixtures)
    assert open_["is_open"] and open_["dest_icao"] is None and open_["origin_icao"] == "KIAB"
    (closed,) = build(tmp_path, D1, fixtures)
    assert closed["sortie_id"] == open_["sortie_id"] and not closed["is_open"]
    assert (closed["origin_icao"], closed["dest_icao"]) == ("KIAB", "KDOV")


def test_past_day_unchanged_by_later_points(tmp_path, fixtures):
    """Point-in-time: day D's sorties use only D-1 and D, never D+1."""
    store(tmp_path, flight_points(D, 23 * 60, 150), part="d")
    before = build(tmp_path, D, fixtures)
    store(tmp_path, flight_points(D1, 600, 90, hex_code="ae0abc"), part="later")
    assert build(tmp_path, D, fixtures) == before


def test_sorties_ending_before_the_day_are_left_to_that_day(tmp_path, fixtures):
    store(tmp_path, flight_points(D, 600, 150))
    assert build(tmp_path, D1, fixtures) == []  # the D flight is read as lookback only
    assert partition_dir(tmp_path, "sorties", D1).exists()  # an empty day is still built


def test_rebuild_is_identical(tmp_path, fixtures):
    store(tmp_path, flight_points(D, 600, 150) + flight_points(D, 900, 60, KDOV, KIAB, "ae0001"))
    assert build(tmp_path, D, fixtures) == build(tmp_path, D, fixtures)
