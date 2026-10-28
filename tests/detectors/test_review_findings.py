"""Regression tests for the adversarial review of PR #1 (detectors and guards, pure)."""

import math
from dataclasses import replace
from datetime import date, timedelta

import pytest

from tw.baselines.stats import robust_z_from
from tw.config import load_yaml
from tw.detectors.d1_volume_surge import detect_d1
from tw.detectors.d2_novel_destination import detect_d2
from tw.detectors.guards import apply_cooldown, apply_coverage_guard
from tw.detectors.types import Arrival, KeyBaseline, RegionCount, alert_id, day_window

CFG = load_yaml("detectors.yaml")
D = date(2026, 9, 15)
NORMAL = KeyBaseline(median=5, mad=1, n_days=8)
COV_BL = {"europe": KeyBaseline(1000, 50, 8)}
# The coverage guard looks up baselines by (region, weekday) for every day an alert rests on.
COV_BL_ALL = {("europe", wd): KeyBaseline(1000, 50, 8) for wd in range(7)} | {
    ("eastern_med", wd): KeyBaseline(1000, 50, 8) for wd in range(7)
}


def d1(n=14, baseline=NORMAL, coverage=1000.0, cov_bl=COV_BL, cfg=CFG):
    counts = [RegionCount("europe", "tanker", n, tuple(f"s{i}" for i in range(n)))]
    return detect_d1(D, counts, {("europe", "tanker"): baseline}, {"europe": coverage}, cov_bl, cfg)


def guard(alerts, coverage_by_day):
    return apply_coverage_guard(alerts, coverage_by_day, COV_BL_ALL, CFG)


def test_coverage_guard_checks_every_day_d2_rests_on():  # finding 4
    arrivals = [
        Arrival("LTAG", "eastern_med", "strategic_airlift", h, f"{h}-s", D - timedelta(days=2))
        for h in ("ae0001", "ae0002")
    ]
    (a,) = detect_d2(D, arrivals, {}, history_days=56, cfg=CFG)
    assert a.basis_days == (D - timedelta(days=2),)
    by_day = {D - timedelta(days=2): {"eastern_med": 5000.0}, D: {"eastern_med": 1000.0}}
    (b,) = guard([a], by_day)
    assert b.status == "suppressed" and b.evidence["coverage_ratio"] == 5.0


@pytest.mark.parametrize("cov", [599.6, 1670.4])
def test_coverage_bounds_compare_unrounded(cov):  # finding 5
    (a,) = guard(d1(), {D: {"europe": cov}})
    assert a.status == "suppressed"


def test_d1_keeps_candidates_without_coverage_as_suppressed():  # finding 6
    (a,) = d1(coverage=0.0)
    assert a.evidence["normalized_ratio"] is None
    (b,) = guard([a], {D: {"europe": 0.0}})
    assert b.status == "suppressed" and b.evidence["suppressed_by"] == ["coverage"]
    (c,) = d1(cov_bl={})
    assert guard([replace(c, region="nowhere")], {D: {}})[0].status == "suppressed"


def test_cooldown_keeps_earlier_reasons_and_catches_repeats_in_a_batch():  # finding 7
    (a,) = d1()
    (sup,) = guard([a], {D: {"europe": 100.0}})
    prior = replace(a, alert_id="prior")
    (kept,) = apply_cooldown([sup], [prior], CFG)
    assert kept.evidence["suppressed_by"] == ["coverage", "cooldown"]
    strong, weak = replace(a, alert_id="x", score=9.0), replace(a, alert_id="y", score=4.0)
    out = apply_cooldown([weak, strong], [], CFG)
    assert [o.status for o in out] == ["suppressed", "new"]  # input order kept; stronger wins


def test_non_finite_inputs_never_alert():  # finding 10
    assert d1(baseline=KeyBaseline(math.nan, 1, 8)) == []
    assert d1(baseline=KeyBaseline(-math.inf, 1, 8)) == []
    (a,) = guard(d1(), {D: {"europe": math.nan}})
    assert a.status == "suppressed"
    with pytest.raises(ValueError):
        robust_z_from(10, 5, 0, {"mad_scale": 1.4826, "mad_floor": 0})


def test_min_history_rounds_up():  # finding 11
    cfg = {**CFG, "baseline": {**CFG["baseline"], "min_history_days": 29}}
    assert d1(baseline=KeyBaseline(5, 1, n_days=4), cfg=cfg) == []
    assert d1(baseline=KeyBaseline(5, 1, n_days=5), cfg=cfg) != []


def test_alert_ids_have_no_delimiter_collisions():  # finding 12
    start, end = day_window(D)
    assert alert_id("D2", "a|b", "c", start, end) != alert_id("D2", "a", "b|c", start, end)


def test_normalization_floor_is_configured():  # finding 14
    base = KeyBaseline(median=0.5, mad=0, n_days=8)
    cfg = {**CFG, "d1_volume_surge": {**CFG["d1_volume_surge"], "normalization_median_floor": 0.5}}
    (a,) = d1(n=6, baseline=base, cfg=cfg)
    assert a.evidence["normalized_ratio"] == 12.0
