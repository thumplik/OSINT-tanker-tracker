"""Theater flows on synthetic sorties (SPEC §5, M5c): fixes, inventory, moves, transits."""

from datetime import UTC, datetime, timedelta

from tw.config import load_yaml
from tw.theater.aggregates import airfield_presence, theater_day
from tw.theater.ccmd import CcmdMapper, load_ccmds
from tw.theater.flows import fixes, inventory, moves, presence, transits

MAPPER = CcmdMapper(load_ccmds())
T0 = datetime(2026, 6, 1, tzinfo=UTC)
FIELDS = {  # (lat, lon)
    "KDOV": (39.13, -75.47),
    "ETAR": (49.44, 7.60),
    "OTBH": (25.12, 51.32),
    "LPLA": (38.76, -27.09),
    "RODN": (26.36, 127.77),
}


def sortie(hex_, start_h, hours, src, dst, family="strategic_airlift", type_="C17", **kw):
    (la0, lo0), (la1, lo1) = FIELDS[src], FIELDS[dst]
    start = T0 + timedelta(hours=start_h)
    row = {
        "sortie_id": f"{hex_}-{start_h}",
        "hex": hex_,
        "type": type_,
        "families": [family],
        "start_ts": start,
        "end_ts": start + timedelta(hours=hours),
        "start_lat": la0,
        "start_lon": lo0,
        "end_lat": la1,
        "end_lon": lo1,
        "start_seen_low": True,
        "end_seen_low": True,
        "is_open": False,
        "origin_icao": src,
        "dest_icao": dst,
    }
    return {**row, **kw}


def fx(*sorties):
    return fixes(sorties, MAPPER, min_sortie_minutes=30)


def test_overflight_is_not_an_entry():
    # Dover straight to Al Udeid crosses EUCOM, but neither end is there.
    ms = moves(fx(sortie("a", 0, 13, "KDOV", "OTBH")), T0, T0 + timedelta(days=1))
    assert [(m.src, m.dst) for m in ms] == [("northcom", "centcom")]


def test_end_quality_is_labeled():
    f = fx(
        sortie("a", 0, 8, "KDOV", "ETAR", end_seen_low=False),
        sortie("b", 0, 8, "KDOV", "ETAR", is_open=True, end_seen_low=False),
        sortie("c", 0, 0.2, "KDOV", "ETAR"),  # under 30 min: no fixes
    )
    ends = {x.hex: x.quality for x in f if x.ccmd == "eucom"}
    assert ends == {"a": "aloft", "b": "last_seen"}


def test_inventory_counts_latest_fix_and_goes_stale():
    f = fx(
        sortie("a", 0, 8, "KDOV", "ETAR"),
        sortie("a", 30, 6, "ETAR", "OTBH"),
        sortie("b", 0, 8, "KDOV", "ETAR"),
        sortie("k", 0, 8, "KDOV", "ETAR", family="tanker", type_="K35R"),
    )
    at = T0 + timedelta(days=2)
    rows = inventory(f, at, timedelta(days=14))
    assert {(r["ccmd"], r["family"], r["type"]): r["aircraft"] for r in rows} == {
        ("centcom", "strategic_airlift", "C17"): 1,
        ("eucom", "strategic_airlift", "C17"): 1,
        ("eucom", "tanker", "K35R"): 1,  # its own family: never summed with airlift
    }
    assert presence(f, T0 + timedelta(days=20), timedelta(days=14)) == {}


def test_moves_are_entries_and_exits_in_the_window():
    f = fx(sortie("a", 0, 8, "KDOV", "ETAR"), sortie("a", 30, 6, "ETAR", "OTBH"))
    day2 = moves(f, T0 + timedelta(days=1), T0 + timedelta(days=2))
    assert [(m.src, m.dst, m.dst_airfield) for m in day2] == [("eucom", "centcom", "OTBH")]


def test_transit_via_staging_resolves_after_dwell():
    f = fx(
        sortie("a", 0, 8, "KDOV", "ETAR"),  # Ramstein 20 h: a stop, not the destination
        sortie("a", 28, 6, "ETAR", "OTBH"),  # then stays at Al Udeid
    )
    (t,) = transits(f, "northcom", timedelta(hours=48), T0 + timedelta(days=5))
    assert (t.dest, t.via, t.resolution, t.dest_airfield) == (
        "centcom",
        ("eucom",),
        "stayed",
        "OTBH",
    )
    assert t.resolved_ts == T0 + timedelta(hours=34 + 48)
    (open_,) = transits(f, "northcom", timedelta(hours=48), T0 + timedelta(hours=40))
    assert open_.resolution == "en_route" and open_.resolved_ts is None


def test_out_and_back_counts_the_turnaround():
    f = fx(
        sortie("a", 0, 8, "KDOV", "ETAR"),
        sortie("a", 20, 6, "ETAR", "OTBH"),
        sortie("a", 30, 6, "OTBH", "ETAR"),
        sortie("a", 44, 9, "ETAR", "KDOV"),
    )
    (t,) = transits(f, "northcom", timedelta(hours=48), T0 + timedelta(days=5))
    assert (t.dest, t.via, t.resolution) == ("centcom", ("eucom",), "returned")
    assert t.resolved_ts == T0 + timedelta(hours=53)


def test_departure_out_of_view_is_not_a_transit():
    f = fx(sortie("a", 0, 6, "ETAR", "OTBH"))
    assert transits(f, "northcom", timedelta(hours=48), T0 + timedelta(days=5)) == []


def test_point_in_time_later_sorties_change_nothing_earlier():
    early = [sortie("a", 0, 8, "KDOV", "ETAR"), sortie("a", 28, 6, "ETAR", "OTBH")]
    later = [*early, sortie("a", 100, 6, "OTBH", "RODN")]
    at = T0 + timedelta(days=4)
    window = (T0, at)
    assert moves(fx(*early), *window) == moves([x for x in fx(*later) if x.ts <= at], *window)
    assert inventory(fx(*early), at, timedelta(days=14)) == inventory(
        [x for x in fx(*later) if x.ts <= at], at, timedelta(days=14)
    )


def test_deterministic_regardless_of_input_order():
    rows = [sortie("a", 0, 8, "KDOV", "ETAR"), sortie("a", 28, 6, "ETAR", "OTBH")]
    assert fx(*rows) == fx(*reversed(rows))


def test_positions_are_each_aircrafts_latest_fix_up_to_the_day():
    rows = [
        sortie("a", 0, 8, "KDOV", "ETAR"),
        sortie("a", 30, 6, "ETAR", "OTBH"),  # day 2: a is in CENTCOM from then on
        sortie("k", 0, 8, "KDOV", "ETAR", family="tanker", type_="K35R"),
    ]
    cfg = load_yaml("detectors.yaml")
    day1 = theater_day(rows, T0.date(), MAPPER, cfg, "northcom")["positions"]
    assert [(p["hex"], p["ccmd"], p["airfield"]) for p in day1] == [
        ("a", "eucom", "ETAR"),
        ("k", "eucom", "ETAR"),
    ]
    day2 = theater_day(rows, (T0 + timedelta(days=1)).date(), MAPPER, cfg, "northcom")
    assert {p["hex"]: p["ccmd"] for p in day2["positions"]} == {"a": "centcom", "k": "eucom"}
    totals = {(r["ccmd"], r["family"]): r["aircraft"] for r in day2["inventory"]}
    assert len(day2["positions"]) == sum(totals.values())  # one position per counted aircraft


def test_position_remembers_the_last_airfield_seen_on_the_ground():
    rows = [
        sortie("a", 0, 8, "KDOV", "ETAR"),  # landed at Ramstein
        # next flight first seen already airborne, then lost from view
        sortie(
            "a",
            30,
            6,
            "ETAR",
            "OTBH",
            origin_icao=None,
            start_seen_low=False,
            end_seen_low=False,
            dest_icao=None,
        ),
    ]
    cfg = load_yaml("detectors.yaml")
    (p,) = theater_day(rows, (T0 + timedelta(days=1)).date(), MAPPER, cfg, "northcom")["positions"]
    assert (p["quality"], p["from_airfield"], p["departed"]) == (
        "aloft",
        "ETAR",
        T0 + timedelta(hours=8),
    )


def test_airfield_presence_counts_ground_and_departures_per_family():
    rows = [
        sortie("a", 0, 8, "KDOV", "ETAR"),  # on the ground at Ramstein
        sortie("b", 0, 8, "KDOV", "ETAR", family="tanker", type_="K35R"),
        sortie("c", 0, 8, "KDOV", "OTBH", end_seen_low=False, dest_icao=None),  # lost, from KDOV
        sortie("d", 0, 10, "KDOV", "OTBH", is_open=True, end_seen_low=False),  # still flying
    ]
    cfg = load_yaml("detectors.yaml")
    positions = theater_day(rows, T0.date(), MAPPER, cfg, "northcom")["positions"]
    got = {
        (r["airfield"], r["family"]): (r["ground"], r["airborne"], r["lost"])
        for r in airfield_presence(positions)
    }
    assert got == {
        ("ETAR", "strategic_airlift"): (1, 0, 0),
        ("ETAR", "tanker"): (1, 0, 0),
        ("KDOV", "strategic_airlift"): (0, 1, 1),
    }
