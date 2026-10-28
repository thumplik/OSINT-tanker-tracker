"""Dashboard day views (M5): tiers, counts vs baseline, heat, alerts, point-in-time."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import h3

from tests.synthetic import EUROPE, days_between, flat_history, sortie, store_day
from tw.baselines.aggregates import build_aggregates
from tw.baselines.build import build_baseline
from tw.detectors.types import Alert
from tw.pipeline import Context
from tw.store.alerts import write_official, write_provisional
from tw.store.parquet import POINT_SCHEMA, SORTIE_SCHEMA, write_partition
from tw.views.day import ViewContext, day_heat_r6, day_view, site_index, site_meta

D = date(2026, 9, 15)
NOW = datetime(2026, 9, 20, tzinfo=UTC)


def history(root: Path, last: date = D) -> None:
    """56 flat days before D, plus D with 9 sorties; D's aggregates and snapshot built."""
    flat_history(root, days_between(D - timedelta(days=56), D - timedelta(days=1)))
    store_day(root, D, [sortie(D, i, dest="ETAR") for i in range(9)])
    ctx = Context(root)
    build_baseline(D, root, ctx.cache, ctx.cfg)
    assert build_aggregates(D, root)


def point(day: date, hex_code: str, hour: int, type_code: str = "K35R", lat: float = 50.0):
    return {
        "ts": datetime(day.year, day.month, day.day, hour, 10, tzinfo=UTC),
        "hex": hex_code,
        "callsign": None,
        "type": type_code,
        "lat": lat,
        "lon": 8.0,
        "alt_baro": 30000,
        "gs": 400.0,
        "track": 90.0,
        "on_ground": False,
        "position_source": "adsb_icao",
        "provider": "test",
        "h3_r4": h3.latlng_to_cell(lat, 8.0, 4),
        "h3_r6": h3.latlng_to_cell(lat, 8.0, 6),
    }


def alert(day: date, alert_id: str, status: str = "new") -> Alert:
    end = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)
    return Alert(
        alert_id=alert_id,
        detector="D1",
        region="europe",
        window_start=end - timedelta(days=1),
        window_end=end,
        observed=9,
        expected=5,
        score=4.0,
        severity="medium",
        evidence={"family": "tanker"},
        sortie_ids=("a",),
        created_at=end,
        status=status,
    )


def test_official_day_counts_against_its_own_snapshot(tmp_path):
    history(tmp_path)
    v = day_view(ViewContext(tmp_path), D, NOW)
    assert v is not None
    assert v["meta"]["tier"] == "official" and v["meta"]["baseline_as_of"] == "2026-09-15"
    (row,) = [r for r in v["regions"] if r["region"] == "europe" and r["family"] == "tanker"]
    assert (row["observed"], row["median"], row["n_days"]) == (9, 5.0, 8)
    assert row["z"] is not None and row["z"] > 0
    (cov,) = [c for c in v["coverage"] if c["region"] == "europe"]
    assert cov["ratio"] == 1.0
    assert len(v["sorties"]) == 9 and all(s["counted"] for s in v["sorties"])
    assert v["sorties"][0]["regions"] == ["europe"]


def test_day_without_data_is_absent(tmp_path):
    history(tmp_path)
    assert day_view(ViewContext(tmp_path), D + timedelta(days=3), NOW) is None


def test_past_view_ignores_later_data(tmp_path):
    """SPEC §11: adding later days, a newer snapshot and later alerts leaves day D unchanged."""
    history(tmp_path)
    before = day_view(ViewContext(tmp_path), D, NOW)
    later = D + timedelta(days=1)
    store_day(tmp_path, later, [sortie(later, i, dest="ETAR") for i in range(30)])
    ctx = Context(tmp_path)
    build_baseline(later, tmp_path, ctx.cache, ctx.cfg)
    write_official(tmp_path, later, [alert(later, "x")])
    assert day_view(ViewContext(tmp_path), D, NOW) == before


def test_live_day_is_provisional_with_unknown_coverage(tmp_path):
    history(tmp_path)
    today = D + timedelta(days=1)
    live = tmp_path / "live"
    rows = [sortie(today, i, dest="ETAR", hex_code=f"ae{i + 0x200:04x}") for i in range(3)]
    write_partition(live, "sorties", today, rows, SORTIE_SCHEMA, "sorties")
    write_partition(live, "points", today, [point(today, "ae0201", 9)], POINT_SCHEMA, "live-09")
    v = day_view(ViewContext(tmp_path), today, NOW)
    assert v is not None
    assert v["meta"]["tier"] == "provisional" and v["meta"]["baseline_as_of"] == "2026-09-15"
    assert v["coverage"] == []
    (row,) = [r for r in v["regions"] if r["region"] == "europe" and r["family"] == "tanker"]
    assert row["observed"] == 3
    assert v["heat"]["rows"]
    idx = site_index(ViewContext(tmp_path))
    assert idx["latest"] == today.isoformat()
    assert {d["date"]: d["tier"] for d in idx["days"]}[today.isoformat()] == "provisional"


def test_heat_counts_distinct_aircraft_per_cell_hour_family(tmp_path):
    history(tmp_path)
    pts = [
        point(D, "ae0001", 9),
        point(D, "ae0001", 9),  # same aircraft again: counted once
        point(D, "ae0002", 9),
        point(D, "ae0003", 9, type_code="C17"),
        point(D, "ae0004", 14),
        point(D, "ae0005", 9, type_code="B738"),  # untracked type: ignored
    ]
    write_partition(tmp_path, "points", D, pts, POINT_SCHEMA, "t")
    heat = day_view(ViewContext(tmp_path), D, NOW)["heat"]  # type: ignore[index]
    fam = heat["families"]
    got = {(heat["cells"][c], h, fam[f]): n for c, h, f, n in heat["rows"]}
    assert got == {
        (EUROPE, 9, "tanker"): 2,
        (EUROPE, 9, "strategic_airlift"): 1,
        (EUROPE, 9, "all"): 3,
        (EUROPE, 14, "tanker"): 1,
        (EUROPE, 14, "all"): 1,
    }
    r6 = day_heat_r6(ViewContext(tmp_path), D)
    assert r6 is not None
    totals = {r6["families"][f]: n for _, f, n in r6["rows"]}
    assert totals == {"tanker": 3, "strategic_airlift": 1, "all": 4}


def test_alerts_are_tagged_by_tier(tmp_path):
    history(tmp_path)
    write_official(tmp_path, D, [alert(D, "off"), alert(D, "sup", status="suppressed")])
    write_provisional(tmp_path, D, 6, [alert(D, "prov")])
    v = day_view(ViewContext(tmp_path), D, NOW)
    assert [(a["alert_id"], a["tier"]) for a in v["alerts"]] == [  # type: ignore[index]
        ("off", "official"),
        ("sup", "official"),
        ("prov", "provisional"),
    ]
    assert v["alerts"][0]["window_end"] == "2026-09-16T00:00:00Z"  # type: ignore[index]
    (day,) = [d for d in site_index(ViewContext(tmp_path))["days"] if d["date"] == "2026-09-15"]
    assert (day["alerts"], day["provisional_alerts"]) == (1, 1)


def test_site_meta_lists_regions_families_and_attribution(tmp_path):
    meta = site_meta(ViewContext(tmp_path))
    assert meta["regions"][-1]["id"] == "other"
    assert "C17" in meta["families"]["strategic_airlift"]
    assert any("ODbL" in a for a in meta["attribution"])


def theater_history(root: Path, tankers: int = 9) -> None:
    """history() with D's theater aggregates and snapshot, plus two C-17s from Dover into Al
    Udeid on D (another family than the Europe tankers)."""
    history(root)
    for day in days_between(D - timedelta(days=56), D - timedelta(days=1)):
        assert Context(root).build_theater(day)
    rows = [sortie(D, i, dest="ETAR") for i in range(tankers)] + [
        {
            **sortie(D, 50 + i, dest="OTBH", end=(25.12, 51.32), hex_code=f"ae0c1{i}"),
            "type": "C17",
            "families": ["strategic_airlift"],
            "start_lat": 39.13,
            "start_lon": -75.47,
            "origin_icao": "KDOV",
        }
        for i in range(2)
    ]
    store_day(root, D, rows)
    ctx = Context(root)
    assert ctx.build_theater(D) and ctx.build_aggregates(D)


def test_theaters_keep_families_apart_and_judge_against_the_snapshot(tmp_path):
    theater_history(tmp_path)
    from tw.pipeline import prepare_theater

    assert prepare_theater(D, Context(tmp_path)) is not None  # builds the snapshot
    th = day_view(ViewContext(tmp_path), D, NOW)["theaters"]
    rows = {(r["ccmd"], r["family"]): r for r in th["rows"]}
    tankers, airlift = rows[("eucom", "tanker")], rows[("centcom", "strategic_airlift")]
    assert tankers["inventory"]["observed"] == 9 and tankers["by_type"] == {"K35R": 9}
    assert tankers["inventory"]["median"] == 5.0 and tankers["inventory"]["z"] > 0
    assert airlift["inventory"]["observed"] == 2 and airlift["by_type"] == {"C17": 2}
    assert rows[("centcom", "tanker")]["inventory"]["observed"] == 0
    assert airlift["entries"]["observed"] == 2  # one exit from NORTHCOM, one entry, per flight
    assert rows[("northcom", "strategic_airlift")]["exits"]["observed"] == 2
    assert [(m["src"], m["dst"]) for m in th["moves"]] == [("northcom", "centcom")] * 2
    assert len(th["history"]["eucom|tanker"]) == 180 and th["history"]["eucom|tanker"][-1] == 9
    assert th["history"]["eucom|tanker"][0] is None  # before the data: missing, never zero
    spots = {(p["ccmd"], p["family"]): p for p in th["positions"]}
    assert len(th["positions"]) == 11
    c17 = spots[("centcom", "strategic_airlift")]
    assert (c17["airfield"], c17["from_airfield"], c17["quality"]) == ("OTBH", "KDOV", "ground")
    assert c17["departed"] < c17["ts"]
    assert th["airfields"]["OTBH"]["ccmd"] == "centcom"  # every named airfield is described
    assert {p["airfield"] for p in th["positions"]} | {"KDOV"} <= set(th["airfields"])
    assert th["window_complete"] and th["baseline_as_of"] == D.isoformat()
    card = {(r["airfield"], r["family"]): r for r in th["airfield_stats"]}[("ETAR", "tanker")]
    assert card["ground"] == 9 and card["days"] == 56 and len(card["series"]) == 56
    assert card["min"] <= card["mean"] <= card["max"]
    dover = {(r["airfield"], r["family"]): r for r in th["airfield_stats"]}
    assert ("KDOV", "strategic_airlift") not in dover  # both C-17s landed: nothing pending there
    assert set(card) >= {"min", "max", "mean"} and "ETAR" in th["airfields"]


def test_theaters_ignore_later_data(tmp_path):
    theater_history(tmp_path)
    before = day_view(ViewContext(tmp_path), D, NOW)["theaters"]
    later = D + timedelta(days=1)
    store_day(
        tmp_path, later, [sortie(later, i, dest="ETAR", hex_code=f"af{i:04x}") for i in range(30)]
    )
    assert Context(tmp_path).build_theater(later)
    assert day_view(ViewContext(tmp_path), D, NOW)["theaters"] == before


def test_indicators_run_the_detectors_without_storing_anything(tmp_path):
    theater_history(tmp_path, tankers=14)
    from tw.pipeline import prepare_theater
    from tw.store.alerts import OFFICIAL, read_alerts

    assert prepare_theater(D, Context(tmp_path)) is not None  # builds the snapshot
    view = day_view(ViewContext(tmp_path), D, NOW)
    d7 = [a for a in view["indicators"] if a["detector"] == "D7"]
    assert [(a["region"], a["evidence"]["family"], a["observed"]) for a in d7] == [
        ("eucom", "tanker", 14)
    ]
    assert all(a["tier"] == "indicator" for a in view["indicators"])
    assert view["alerts"] == [] and read_alerts(tmp_path, OFFICIAL, [D]) == []
    assert day_view(ViewContext(tmp_path), D, NOW)["indicators"] == view["indicators"]
