import json
from datetime import date

from tw.config import load_fleet
from tw.ingest.load_history import load_dump
from tw.store.parquet import load_registry, partition_dir, read_day


def test_load_dump_writes_date_partitions(tmp_path, fixtures):
    summary = load_dump(fixtures / "globe_history_2026-07-01", tmp_path, load_fleet(), part="t")
    assert summary.points_per_day == {date(2026, 7, 1): 7}
    assert summary.kept_points == 7  # ae1234 x5 + ae0abc x2; ae9999 untyped, ae7777 untracked

    day = read_day(tmp_path, "points", date(2026, 7, 1))
    assert set(day["hex"].to_pylist()) == {"ae1234", "ae0abc"}
    assert set(day["position_source"].to_pylist()) == {"adsb_icao", "mlat"}

    coverage = read_day(tmp_path, "coverage", date(2026, 7, 1))
    assert sum(coverage["aircraft"].to_pylist()) >= 5  # civil traffic counts toward coverage
    assert load_registry(tmp_path, date(2026, 7, 1))["ae1234"] == "K35R"
    assert load_registry(tmp_path, date(2026, 6, 30)) == {}  # not known before that day


def test_reload_is_idempotent(tmp_path, fixtures):
    src = fixtures / "globe_history_2026-07-01"
    load_dump(src, tmp_path, load_fleet(), part="t")
    load_dump(src, tmp_path, load_fleet(), part="t")
    assert read_day(tmp_path, "points", date(2026, 7, 1)).num_rows == 7


def test_past_partition_unchanged_by_later_load(tmp_path, fixtures):
    """Point-in-time: loading a later day's dump must not change an earlier day's partition."""
    data = tmp_path / "data"
    load_dump(fixtures / "globe_history_2026-07-01", data, load_fleet(), part="d1")
    before = read_day(data, "points", date(2026, 7, 1))
    files_before = sorted(partition_dir(data, "points", date(2026, 7, 1)).iterdir())

    later = tmp_path / "globe_history_2026-07-03" / "traces" / "34"
    later.mkdir(parents=True)
    doc = {
        "icao": "ae1234",
        "t": "K35R",
        "timestamp": 1783036800,  # 2026-07-03T00:00Z
        "trace": [[0, 40.0, -76.0, 20000, 400.0, 90.0, 0, 0, None, "adsb_icao"]],
    }
    (later / "trace_full_ae1234.json").write_text(json.dumps(doc))
    load_dump(later.parents[1], data, load_fleet(), part="d3")

    assert read_day(data, "points", date(2026, 7, 1)).equals(before)
    assert sorted(partition_dir(data, "points", date(2026, 7, 1)).iterdir()) == files_before
    assert read_day(data, "points", date(2026, 7, 3)).num_rows == 1


def test_empty_day(tmp_path):
    assert read_day(tmp_path, "points", date(2020, 1, 1)).num_rows == 0


def test_load_real_traces(tmp_path, fixtures):
    summary = load_dump(fixtures / "real_2026-10-25", tmp_path, load_fleet(), part="real")
    assert summary.traces == 6
    day = read_day(tmp_path, "points", date(2026, 10, 25))
    # C-17 and both C-130Js are tracked; the UH-60, civil and non-ICAO traces are not.
    assert sorted(set(day["hex"].to_pylist())) == ["ae07f0", "ae29da", "ae5df0"]
    assert day.num_rows == 1523 + 1310 + 549
    coverage = read_day(tmp_path, "coverage", date(2026, 10, 25))
    assert coverage.num_rows > 0


def test_registry_resolves_from_earlier_days_only(tmp_path):
    """A type learned on a later day must not resolve an earlier day's untyped hex."""

    def dump(name: str, ts: int, type_code: str | None):
        d = tmp_path / name / "traces" / "99"
        d.mkdir(parents=True)
        doc = {
            "icao": "ae9999",
            "t": type_code,
            "timestamp": ts,
            "trace": [[0, 26.0, 51.0, 24000, 400.0, 0.0, 0, 0, None, "adsb_icao"]],
        }
        (d / "trace_full_ae9999.json").write_text(json.dumps(doc))
        return d.parents[1]

    data = tmp_path / "data"
    fleet = load_fleet()
    # Day 3 knows ae9999 is a KC46; days 2 and 4 see it untyped.
    load_dump(dump("d3", 1783036800, "KC46"), data, fleet, part="d3")  # 2026-07-03
    load_dump(dump("d2", 1782950400, None), data, fleet, part="d2")  # 2026-07-02
    load_dump(dump("d4", 1783123200, None), data, fleet, part="d4")  # 2026-07-04
    assert read_day(data, "points", date(2026, 7, 2)).num_rows == 0
    assert read_day(data, "points", date(2026, 7, 4))["type"].to_pylist() == ["KC46"]


def test_untracked_military_types_feed_registry_without_points(tmp_path, fixtures):
    load_dump(fixtures / "real_2026-10-25", tmp_path, load_fleet(), part="real")
    registry = load_registry(tmp_path, date(2026, 10, 25))
    assert registry["ae4ff0"] == "H60"  # untracked, but its type is still recorded
    day = read_day(tmp_path, "points", date(2026, 10, 25))
    assert "ae4ff0" not in set(day["hex"].to_pylist())


def _write_trace(root, hex_code: str, timestamp: int, dts: list[float], **extra) -> None:
    folder = root / "traces" / hex_code[-2:]
    folder.mkdir(parents=True, exist_ok=True)
    rows = [[dt, 40.0, -76.0, 20000, 400.0, 90.0, 0, 0, None, "adsb_icao"] for dt in dts]
    if "flight" in extra:
        rows[0][8] = {"flight": extra["flight"]}
    doc = {"icao": hex_code, "t": "K35R", "timestamp": timestamp, "trace": rows}
    (folder / f"trace_full_{hex_code}.json").write_text(json.dumps(doc))


def test_release_only_loads_its_own_day(tmp_path):
    """Seen in 2025 adsb.lol releases: rows before midnight (negative dt) and leftover traces
    dated earlier days, almost all duplicates of the earlier day's own release. A release
    tagged D keeps only rows inside day D, even when a leftover trace is read first."""
    src = tmp_path / "v2026.07.03-planes-readsb-prod-0"
    _write_trace(src, "ae0001", 1782864000, [60, 120])  # dated 2026-07-01, sorts first
    # Starts 2026-07-02 23:00Z, callsign only on the pre-midnight row.
    _write_trace(src, "ae1234", 1783036800, [-3600, 60, 90000], flight="TEAL71  ")
    data = tmp_path / "data"
    summary = load_dump(src, data, load_fleet())
    assert summary.traces == 1 and summary.stray_rows == 4
    assert summary.points_per_day == {date(2026, 7, 3): 1}
    day = read_day(data, "points", date(2026, 7, 3))
    assert day["callsign"].to_pylist() == ["TEAL71"]  # carried forward across midnight
    for other in (date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 4)):
        assert not partition_dir(data, "points", other).exists()
        assert not partition_dir(data, "coverage", other).exists()
    assert load_registry(data, date(2026, 7, 3)) == {"ae1234": "K35R"}
