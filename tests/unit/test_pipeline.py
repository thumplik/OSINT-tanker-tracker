"""Pipeline-level regressions from the adversarial review of PR #1."""

import copy
from datetime import date, timedelta

from tests.synthetic import days_between, flat_history, sortie, store_day
from tw.baselines.aggregates import AGG_REGION_FAMILY, build_aggregates
from tw.baselines.build import BL_REGION_FAMILY
from tw.config import load_yaml
from tw.pipeline import Context, prepare_day, run_days
from tw.store.parquet import partition_dir, read_day

D = date(2026, 9, 15)


def test_day_without_sorties_or_coverage_is_missing(tmp_path):  # finding 1
    store_day(tmp_path, D, [], with_sorties=False)
    assert build_aggregates(D, tmp_path) is False
    store_day(tmp_path, D + timedelta(days=1), [], with_coverage=False)
    assert build_aggregates(D + timedelta(days=1), tmp_path) is False
    assert not partition_dir(tmp_path, AGG_REGION_FAMILY, D).exists()


def test_snapshot_is_write_once(tmp_path):  # finding 2
    days = [d for d in days_between(D - timedelta(days=56), D) if d != D - timedelta(days=7)]
    flat_history(tmp_path, days)
    prepare_day(D, Context(tmp_path))
    before = read_day(tmp_path, BL_REGION_FAMILY, D)
    # The missing day is backfilled later with a huge count. Replaying D keeps its snapshot.
    late = D - timedelta(days=7)
    store_day(tmp_path, late, [sortie(late, i) for i in range(100)])
    prepare_day(D, Context(tmp_path))
    assert read_day(tmp_path, BL_REGION_FAMILY, D).equals(before)
    prepare_day(D, Context(tmp_path, rebuild_baselines=True))  # an explicit rebuild may change it
    assert not read_day(tmp_path, BL_REGION_FAMILY, D).equals(before)


def test_d2_history_counts_only_its_own_baseline_days(tmp_path):  # finding 3
    flat_history(tmp_path, days_between(D - timedelta(days=28), D - timedelta(days=1)))
    store_day(tmp_path, D, [sortie(D, 0, dest="ETAR")])
    inputs = prepare_day(D, Context(tmp_path))
    assert inputs is not None and inputs.d2_history_days == 26  # D-28..D-3


def test_context_config_reaches_aggregates_and_baselines(tmp_path):  # finding 8
    flat_history(tmp_path, days_between(D - timedelta(days=20), D))
    store_day(tmp_path, D, [sortie(D, i, minutes=60) for i in range(5)])
    cfg = copy.deepcopy(load_yaml("detectors.yaml"))
    cfg["counting"]["min_sortie_minutes"] = 120
    cfg["baseline"]["window_days"] = 7
    inputs = prepare_day(D, Context(tmp_path, cfg=cfg))
    assert inputs is not None
    assert sum(c.sorties for c in inputs.counts) == 0  # 60 min < 120 min
    assert all(b.n_days <= 1 for b in inputs.baseline.values())  # 7-day window


def test_alerts_and_baselines_carry_provenance(tmp_path):  # finding 13
    flat_history(tmp_path, days_between(D - timedelta(days=56), D - timedelta(days=1)))
    store_day(tmp_path, D, [sortie(D, i) for i in range(20)])
    alerts, _, _ = run_days([D], Context(tmp_path))
    assert alerts and all(a.config_hash and a.code_version for a in alerts)
    row = read_day(tmp_path, BL_REGION_FAMILY, D).to_pylist()[0]
    assert row["config_hash"] == alerts[0].config_hash and row["code_version"]
