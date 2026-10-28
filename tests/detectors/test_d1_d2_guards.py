"""D1, D2 and the guards on synthetic inputs (SPEC §7-8). Detectors are pure: no I/O here."""

from dataclasses import replace
from datetime import date

from tw.config import load_yaml
from tw.detectors.d1_volume_surge import detect_d1
from tw.detectors.d2_novel_destination import detect_d2
from tw.detectors.guards import apply_cooldown, apply_coverage_guard, apply_known_events
from tw.detectors.types import Arrival, KeyBaseline, RegionCount

CFG = load_yaml("detectors.yaml")
D = date(2026, 9, 15)
NORMAL = KeyBaseline(median=5, mad=1, n_days=8)
COV = {"europe": KeyBaseline(median=1000, mad=50, n_days=8)}
COV_BY_WEEKDAY = {("europe", wd): COV["europe"] for wd in range(7)}


def guard(alerts, coverage: float):
    return apply_coverage_guard(alerts, {D: {"europe": coverage}}, COV_BY_WEEKDAY, CFG)


def count(n: int, region="europe", family="tanker") -> RegionCount:
    return RegionCount(region, family, n, tuple(f"s{i}" for i in range(n)))


def d1(n: int, baseline=NORMAL, coverage=1000.0, cov_bl=COV):
    return detect_d1(
        D, [count(n)], {("europe", "tanker"): baseline}, {"europe": coverage}, cov_bl, CFG
    )


def test_injected_surge_fires_with_expected_and_observed():
    (a,) = d1(14)
    assert (a.detector, a.region, a.observed, a.expected) == ("D1", "europe", 14, 5)
    assert a.evidence["family"] == "tanker" and a.score >= 3 and len(a.sortie_ids) == 14
    assert a.status == "new" and a.severity == "medium"  # z ≈ 6 ≥ medium_min_z


def test_no_fire_below_min_count_z_or_history():
    empty = KeyBaseline(median=0, mad=0, n_days=8)
    assert d1(5, baseline=empty) == []  # z ≈ 3.4 but count < min_count
    assert d1(7) == []  # z = 2/1.48 < 3
    assert d1(14, baseline=KeyBaseline(5, 1, n_days=3)) == []  # < 4 weeks of that weekday


def test_coverage_surge_explains_the_count():
    """Receivers came back: 2.5x the usual traffic overall, so 14 vs 5 isn't news."""
    assert d1(14, coverage=2500.0) == []


def test_coverage_drop_suppresses():
    (a,) = guard(d1(14, coverage=500.0), 500.0)
    assert a.status == "suppressed" and a.evidence["suppressed_by"] == ["coverage"]
    (b,) = guard(d1(14), 1000.0)
    assert b.status == "new" and b.evidence["coverage_ratio"] == 1.0


def test_coverage_jump_suppresses():
    """Receivers came online: 5x the usual traffic. Even a count that outgrows it is suspect."""
    surge = KeyBaseline(median=0, mad=0, n_days=8)
    (a,) = d1(30, baseline=surge, coverage=5000.0)  # 30 vs 0 outgrows 5x coverage
    (b,) = guard([a], 5000.0)
    assert b.status == "suppressed" and b.evidence["coverage_ratio"] == 5.0


def arrivals(*hexes: str, field="LTAG", family="strategic_airlift"):
    return [Arrival(field, "eastern_med", family, h, f"{h}-{i}", D) for i, h in enumerate(hexes)]


def test_d2_two_aircraft_at_a_novel_field_fires():
    (a,) = detect_d2(D, arrivals("ae0001", "ae0002"), {}, history_days=56, cfg=CFG)
    assert (a.detector, a.region, a.observed, a.expected) == ("D2", "eastern_med", 2, 0)
    assert a.evidence["airfield"] == "LTAG"


def test_d2_one_aircraft_twice_or_a_known_field_does_not():
    assert detect_d2(D, arrivals("ae0001", "ae0001"), {}, 56, CFG) == []
    known = {("LTAG", "strategic_airlift"): 3}
    assert detect_d2(D, arrivals("ae0001", "ae0002"), known, 56, CFG) == []
    assert detect_d2(D, arrivals("ae0001", "ae0002"), {}, history_days=20, cfg=CFG) == []


def test_known_event_downgrades_and_labels():
    events = [
        {
            "id": "ex1",
            "regions": ["europe"],
            "families": ["tanker"],
            "from": date(2026, 9, 10),
            "to": date(2026, 9, 20),
        }
    ]
    (a,) = apply_known_events(d1(14), events)
    assert a.severity == "low" and a.evidence["known_event"] == "ex1" and a.status == "new"


def test_cooldown_only_blocks_repeats_at_same_or_lower_severity():
    (a,) = d1(14)
    later = [replace(a, alert_id="x")]
    (b,) = apply_cooldown(later, [a], CFG)
    assert b.status == "suppressed" and b.evidence["suppressed_by"] == ["cooldown"]


def test_deterministic():
    assert d1(14) == d1(14)
    assert d1(14)[0].alert_id == d1(14)[0].alert_id
