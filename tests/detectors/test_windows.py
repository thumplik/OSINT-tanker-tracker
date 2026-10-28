"""Rolling windows (M4 provisional tier): the detectors take a window or a day, and a day is just
the window ending at the next midnight, so official results don't change."""

from datetime import UTC, date, datetime, timedelta

from tw.config import load_yaml
from tw.detectors.d1_volume_surge import detect_d1
from tw.detectors.d2_novel_destination import detect_d2
from tw.detectors.types import Arrival, KeyBaseline, RegionCount, Window, as_window

CFG = load_yaml("detectors.yaml")
D = date(2026, 9, 15)  # a Tuesday
COUNTS = [RegionCount("europe", "tanker", 14, tuple(f"s{i}" for i in range(14)))]
BASELINE = {("europe", "tanker"): KeyBaseline(5, 1, 8)}
COV_BL = {"europe": KeyBaseline(1000, 50, 8)}


def d1(when):
    return detect_d1(when, COUNTS, BASELINE, {"europe": 1000.0}, COV_BL, CFG)


def test_a_day_is_the_window_ending_at_the_next_midnight():
    window = as_window(D, 24)
    assert window == Window(datetime(2026, 9, 15, tzinfo=UTC), datetime(2026, 9, 16, tzinfo=UTC))
    assert window.days == (D,) and window.weekday == D.weekday()
    assert d1(D) == d1(window)
    arrivals = [Arrival("LTAG", "eastern_med", "tanker", h, f"{h}-s", D) for h in ("a", "b")]
    assert detect_d2(D, arrivals, {}, 56, CFG) == detect_d2(as_window(D, 72), arrivals, {}, 56, CFG)


def test_rolling_window_across_midnight():
    end = datetime(2026, 9, 16, 6, tzinfo=UTC)
    window = Window(end - timedelta(hours=24), end)
    assert window.days == (D, D + timedelta(days=1))
    assert window.weekday == D.weekday()  # midpoint 2026-09-15 18:00
    (a,) = d1(window)
    assert (a.window_start, a.window_end, a.created_at) == (window.start, end, end)
    assert a.basis_days == window.days and a.evidence["weekday"] == D.weekday()
    assert a.alert_id != d1(D)[0].alert_id  # each window is its own alert


def test_window_hours_come_from_config():
    cfg = {**CFG, "d1_volume_surge": {**CFG["d1_volume_surge"], "window_hours": 48}}
    (a,) = detect_d1(D, COUNTS, BASELINE, {"europe": 1000.0}, COV_BL, cfg)
    assert a.window_end - a.window_start == timedelta(hours=48)
