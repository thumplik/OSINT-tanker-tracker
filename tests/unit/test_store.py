import json
from datetime import date

from tw.config import load_fleet
from tw.ingest.load_history import load_dump
from tw.store.parquet import load_registry, partition_dir, read_day


def test_load_dump_writes_date_partitions(tmp_path, fixtures):
    summary = load_dump(fixtures / "globe_history_2026-07-01", tmp_path, load_fleet(), part="t")
    assert summary.points_per_day == {date(2026, 7, 1): 5, date(2026, 7, 2): 1}
    assert summary.kept_points == 6  # ae1234 x4 + ae0abc x2; ae9999 untyped, ae7777 untracked

    day = read_day(tmp_path, "points", date(2026, 7, 1))
    assert set(day["hex"].to_pylist()) == {"ae1234", "ae0abc"}
    assert set(day["position_source"].to_pylist()) == {"adsb_icao", "mlat"}

    coverage = read_day(tmp_path, "coverage", date(2026, 7, 1))
    assert sum(coverage["aircraft"].to_pylist()) >= 5  # civil traffic counts toward coverage
    assert load_registry(tmp_path)["ae1234"] == "K35R"


def test_reload_is_idempotent(tmp_path, fixtures):
    src = fixtures / "globe_history_2026-07-01"
    load_dump(src, tmp_path, load_fleet(), part="t")
    load_dump(src, tmp_path, load_fleet(), part="t")
    assert read_day(tmp_path, "points", date(2026, 7, 1)).num_rows == 5


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
