"""Baseline snapshots `baselines/*/as_of=D/` (SPEC §7, §11)."""

from datetime import date, timedelta

from tw.baselines.aggregates import (
    AGG_ARRIVALS,
    AGG_COVERAGE,
    AGG_REGION_FAMILY,
    ARRIVALS_SCHEMA,
    COVERAGE_AGG_SCHEMA,
    REGION_FAMILY_SCHEMA,
)
from tw.baselines.build import (
    BL_AIRFIELD,
    BL_COVERAGE,
    BL_REGION_FAMILY,
    AggregateCache,
    build_baseline,
)
from tw.store.parquet import partition_dir, read_day, write_partition

AS_OF = date(2026, 9, 15)  # a Tuesday


def agg_day(root, day, tankers: int, coverage: int = 1000, arrivals=()):
    rf = [
        {
            "region": "europe",
            "family": "tanker",
            "sorties": tankers,
            "hexes": tankers,
            "sortie_ids": [f"s{i}" for i in range(tankers)],
        },
        {
            "region": "europe",
            "family": "strategic_airlift",
            "sorties": 0,
            "hexes": 0,
            "sortie_ids": [],
        },
    ]
    write_partition(root, AGG_REGION_FAMILY, day, rf, REGION_FAMILY_SCHEMA, "aggregates")
    write_partition(
        root,
        AGG_COVERAGE,
        day,
        [{"region": "europe", "aircraft_hours": coverage}],
        COVERAGE_AGG_SCHEMA,
        "aggregates",
    )
    write_partition(root, AGG_ARRIVALS, day, list(arrivals), ARRIVALS_SCHEMA, "aggregates")


def arrival(day, field, hex_code):
    from datetime import UTC, datetime

    return {
        "airfield": field,
        "region": "europe",
        "family": "tanker",
        "sortie_id": f"{hex_code}-{day}",
        "hex": hex_code,
        "end_ts": datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
    }


def build(root):
    assert build_baseline(AS_OF, root, AggregateCache(root))
    return {
        name: read_day(root, ds, AS_OF).to_pylist()
        for name, ds in (("rf", BL_REGION_FAMILY), ("cov", BL_COVERAGE), ("af", BL_AIRFIELD))
    }


def test_weekday_median_mad_from_prior_days_only(tmp_path):
    for back in range(1, 57):
        day = AS_OF - timedelta(days=back)
        agg_day(tmp_path, day, tankers=10 + (back % 7 == 0) * back)  # Tuesdays: 17, 24, ...
    agg_day(tmp_path, AS_OF, tankers=999)  # the day itself is never in its own baseline
    bl = build(tmp_path)
    (tue,) = [r for r in bl["rf"] if r["family"] == "tanker" and r["weekday"] == AS_OF.weekday()]
    assert tue["n_days"] == 8 and tue["median"] == (38 + 45) / 2  # 17..66 step 7
    (mon,) = [r for r in bl["rf"] if r["family"] == "tanker" and r["weekday"] == 0]
    assert (mon["median"], mon["mad"], mon["n_days"]) == (10, 0, 8)
    assert all(r["config_hash"] for r in bl["rf"])
    assert partition_dir(tmp_path, BL_REGION_FAMILY, AS_OF).name == "as_of=2026-09-15"


def test_missing_days_are_skipped_not_zero(tmp_path):
    for back in (7, 14, 21):  # only three Tuesdays exist
        agg_day(tmp_path, AS_OF - timedelta(days=back), tankers=10)
    (tue,) = [
        r
        for r in build(tmp_path)["rf"]
        if r["family"] == "tanker" and r["weekday"] == AS_OF.weekday()
    ]
    assert tue["n_days"] == 3 and tue["median"] == 10


def test_airfield_baseline_excludes_the_d2_window(tmp_path):
    """D2's 72 h window is D-2..D, so those days never count as 'seen before'."""
    agg_day(
        tmp_path,
        AS_OF - timedelta(days=10),
        5,
        arrivals=[arrival(AS_OF - timedelta(days=10), "LTAG", "ae0001")],
    )
    agg_day(
        tmp_path,
        AS_OF - timedelta(days=2),
        5,
        arrivals=[arrival(AS_OF - timedelta(days=2), "LTAG", "ae0002")],
    )
    (row,) = build(tmp_path)["af"]
    assert (row["airfield"], row["family"], row["hexes"]) == ("LTAG", "tanker", 1)


def test_snapshot_unchanged_by_later_days(tmp_path):
    for back in range(1, 30):
        agg_day(tmp_path, AS_OF - timedelta(days=back), tankers=back % 5)
    before = build(tmp_path)
    for ahead in range(0, 10):
        agg_day(tmp_path, AS_OF + timedelta(days=ahead), tankers=50)
    assert build(tmp_path) == before
