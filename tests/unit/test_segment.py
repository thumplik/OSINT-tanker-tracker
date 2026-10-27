"""Sortie segmentation and origin/destination rules (SPEC §5) on synthetic tracks."""

from tw.config import load_yaml
from tw.tracks.airfields import AirfieldIndex, haversine_km, load_airfields
from tw.tracks.segment import SortieRules, TrackPoint, resolve_ends, segment

KIAB = (37.6219, -97.2682, 1371)  # McConnell AFB
KDOV = (39.1295, -75.4660, 24)  # Dover AFB
RULES = SortieRules.from_config(load_yaml("detectors.yaml")["sortie"])


def pt(t: float, lat: float, lon: float, alt: int | None, ground: bool = False) -> TrackPoint:
    return TrackPoint(
        ts=t,
        lat=lat,
        lon=lon,
        alt=None if ground else alt,
        on_ground=ground,
        callsign="REACH1",
        h3_r4="cell",
        low_quality=False,
    )


def flight(
    t0: float,
    a: tuple[float, float, int],
    b: tuple[float, float, int],
    minutes: int,
    ground: bool = True,
    cruise: int = 30000,
) -> list[TrackPoint]:
    """Ground roll at `a`, climb, cruise, descend, ground roll at `b`: one point per minute."""
    pts = []
    if ground:
        pts += [pt(t0 + s, a[0], a[1], None, ground=True) for s in (0, 30, 60, 90)]
    t0 += 120
    for m in range(minutes + 1):
        f = m / minutes
        lat, lon = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        edge = min(m, minutes - m)  # minutes from the nearer end
        alt = min(cruise, (a[2] if m < minutes / 2 else b[2]) + 300 + edge * 2000)
        pts.append(pt(t0 + m * 60, lat, lon, alt))
    if ground:
        end = t0 + minutes * 60
        pts += [pt(end + s, b[0], b[1], None, ground=True) for s in (30, 60, 90, 120)]
    return pts


def index(fixtures):
    return AirfieldIndex(load_airfields(fixtures / "airfields.csv"))


def test_takeoff_to_landing_is_one_sortie_with_both_ends(fixtures):
    (s,) = segment(flight(0, KIAB, KDOV, 150), RULES)
    assert s.end_reason == "landing"
    assert resolve_ends(s, index(fixtures), RULES) == ("KIAB", "KDOV")
    assert all(not p.on_ground for p in s.points)  # starts at the first airborne point


def test_touch_and_go_continues_the_sortie(fixtures):
    pts = flight(0, KIAB, KIAB, 40, ground=False)
    t = pts[-1].ts
    pts += [pt(t + 20, KIAB[0], KIAB[1], None, ground=True)]  # 20 s on the runway
    pts += flight(t + 40, KIAB, KIAB, 40, ground=False)[0:]
    pts.sort(key=lambda p: p.ts)
    assert len(segment(pts, RULES)) == 1


def test_landing_needs_a_ground_run(fixtures):
    first = flight(0, KIAB, KDOV, 150)
    second = flight(first[-1].ts + 600, KDOV, KIAB, 150)
    a, b = segment(first + second, RULES)
    assert resolve_ends(a, index(fixtures), RULES) == ("KIAB", "KDOV")
    assert resolve_ends(b, index(fixtures), RULES) == ("KDOV", "KIAB")


def _with_gap(minutes_gap: float, alt: int, km: float) -> list[TrackPoint]:
    """Two cruise legs at `alt` separated by a gap of `minutes_gap`, `km` apart."""
    per_km = 1 / haversine_km(0, 0, 1, 0)
    leg1 = [pt(m * 60, 40.0, -60.0 + m * 0.01, alt) for m in range(10)]
    t, lon = leg1[-1].ts + minutes_gap * 60, leg1[-1].lon
    leg2 = [pt(t + m * 60, 40.0, lon + km * per_km / 0.766 + m * 0.01, alt) for m in range(10)]
    return leg1 + leg2


def test_high_gap_is_bridged():
    assert len(segment(_with_gap(120, 32000, 1500), RULES)) == 1  # 1,500 km in 2 h ≈ 405 kt


def test_low_gap_splits():
    assert len(segment(_with_gap(120, 4000, 100), RULES)) == 2


def test_implausible_speed_splits():
    assert len(segment(_with_gap(60, 32000, 2500), RULES)) == 2  # ≈ 1,350 kt


def test_too_long_gap_splits():
    assert len(segment(_with_gap(7 * 60, 32000, 3000), RULES)) == 2


def test_short_gap_never_splits():
    assert len(segment(_with_gap(20, 4000, 50), RULES)) == 1


def test_mlat_without_ground_points_resolves_from_low_altitude(fixtures):
    (s,) = segment(flight(0, KIAB, KDOV, 150, ground=False), RULES)
    assert s.end_reason == "data_end"
    assert resolve_ends(s, index(fixtures), RULES) == ("KIAB", "KDOV")


def test_first_seen_high_has_no_origin(fixtures):
    pts = [p for p in flight(0, KIAB, KDOV, 150, ground=False) if p.ts >= 120 + 30 * 60]
    (s,) = segment(pts, RULES)
    assert resolve_ends(s, index(fixtures), RULES) == (None, "KDOV")


def test_noise_is_dropped():
    pts = [pt(m * 60, 40.0, -60.0, 20000) for m in range(3)]
    assert segment(pts, RULES) == []


def test_missing_ground_flag_is_not_ground():
    """A point with on_ground=None is not evidence of a landing."""
    pts = [pt(m * 60, 40.0, -60.0 + m * 0.01, 20000) for m in range(20)]
    pts = [p if i % 3 else p._replace(on_ground=None) for i, p in enumerate(pts)]
    assert len(segment(pts, RULES)) == 1
