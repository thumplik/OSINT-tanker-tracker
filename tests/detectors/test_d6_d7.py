"""D6 flow change and D7 inventory shift on synthetic inputs (SPEC §8, M5c). Pure: no I/O."""

from datetime import UTC, date, datetime, timedelta

from tw.config import load_yaml
from tw.detectors.d6_flow_change import detect_d6
from tw.detectors.d7_inventory_shift import detect_d7
from tw.detectors.guards import apply_coverage_guard
from tw.detectors.types import FlowCount, InventoryCount, KeyBaseline, Window

CFG = load_yaml("detectors.yaml")
D = date(2026, 6, 29)
END = datetime(2026, 6, 30, tzinfo=UTC)
WINDOW = Window(END - timedelta(days=7), END)
FLOW_BL = {("centcom", "tanker", "entries"): KeyBaseline(median=2, mad=1, n_days=8)}
INV_BL = {("centcom", "tanker"): KeyBaseline(median=5, mad=1, n_days=49)}


def d6(n, baseline=FLOW_BL, ratio=1.0):
    return detect_d6(
        WINDOW, [FlowCount("centcom", "tanker", "entries", n)], baseline, {"centcom": ratio}, CFG
    )


def d7(n, baseline=INV_BL):
    return detect_d7(WINDOW, [InventoryCount("centcom", "tanker", n)], baseline, CFG)


def test_d6_flow_surge_fires_with_observed_and_expected():
    (a,) = d6(20)
    assert (a.detector, a.region, a.observed, a.expected) == ("D6", "centcom", 20, 2)
    assert a.evidence["family"] == "tanker" and a.evidence["series"] == "entries"
    assert a.basis_days == WINDOW.days and len(a.basis_days) == 7


def test_d6_needs_count_z_blocks_and_coverage_normalized_growth():
    assert d6(4) == []  # under min_count
    assert d6(20, {k: KeyBaseline(2, 1, 3) for k in FLOW_BL}) == []  # too few blocks
    assert d6(20, ratio=12.0) == []  # coverage grew faster than the flow
    (a,) = d6(20, ratio=None)  # can't compare: kept for the coverage guard to suppress
    assert a.evidence["normalized_ratio"] is None


def test_d7_fires_both_directions():
    (up,) = d7(30)
    assert up.evidence["direction"] == "up" and up.observed == 30 and up.expected == 5
    (down,) = d7(0, {("centcom", "tanker"): KeyBaseline(median=30, mad=2, n_days=49)})
    assert down.evidence["direction"] == "down" and down.score < 0


def test_d7_needs_history_and_material_change():
    assert d7(30, {("centcom", "tanker"): KeyBaseline(5, 1, 20)}) == []  # under 28 days
    assert d7(8) == []  # +3 aircraft is under min_change


def test_coverage_jump_suppresses_theater_alerts():
    alerts = d6(20) + d7(30)
    cov_bl = {("centcom", d.weekday()): KeyBaseline(1000, 50, 8) for d in WINDOW.days}
    jumped = {d: {"centcom": 6000.0} for d in WINDOW.days}  # receivers came online
    out = apply_coverage_guard(alerts, jumped, cov_bl, CFG)
    assert [a.status for a in out] == ["suppressed", "suppressed"]
    steady = {d: {"centcom": 1000.0} for d in WINDOW.days}
    assert [a.status for a in apply_coverage_guard(alerts, steady, cov_bl, CFG)] == ["new", "new"]


def test_families_are_separate_keys():
    counts = [FlowCount("centcom", "strategic_airlift", "entries", 20)]
    assert detect_d6(WINDOW, counts, FLOW_BL, {"centcom": 1.0}, CFG) == []  # no airlift baseline


def test_deterministic():
    assert d6(20) == d6(20) and d7(30) == d7(30)
    assert d6(20)[0].alert_id != d7(30)[0].alert_id
