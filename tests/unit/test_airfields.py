import csv
import io

from tw.tracks.airfields import (
    AirfieldIndex,
    filter_ourairports,
    haversine_km,
    load_airfields,
    update_airfields,
)

AIRPORTS = """id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,icao_code,gps_code
1,KIAB,large_airport,McConnell Air Force Base,37.6227,-97.2673,1371,KIAB,KIAB
2,US-0001,small_airport,Short Strip,37.70,-97.30,1400,,
3,XX-0002,closed,Old Base,37.60,-97.20,1300,,
4,K3AU,small_airport,Augusta Municipal,37.6716,-97.0779,1328,,K3AU
5,0KS1,heliport,Hospital Pad,37.62,-97.26,1371,,
"""
RUNWAYS = """id,airport_ref,airport_ident,length_ft,closed
10,1,KIAB,12000,0
11,1,KIAB,12000,0
12,2,US-0001,1800,0
13,3,XX-0002,9000,0
14,4,K3AU,4200,0
15,4,K3AU,9999,1
16,5,0KS1,60,0
"""


def rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_filter_keeps_usable_open_fields_only():
    fields = filter_ourairports(
        rows(AIRPORTS), rows(RUNWAYS), ["large_airport", "small_airport"], 3000
    )
    assert [f.ident for f in fields] == ["K3AU", "KIAB"]  # strip too short, closed, heliport
    k3au = fields[0]
    assert k3au.runway_ft == 4200  # the closed 9,999 ft runway doesn't count
    assert k3au.icao is None and k3au.code == "K3AU"  # gps_code only: not an ICAO code


def test_update_writes_and_reloads(tmp_path):
    out = tmp_path / "airfields.csv"
    pages = {"a": AIRPORTS.encode(), "r": RUNWAYS.encode()}
    cfg = {
        "airports_url": "a",
        "runways_url": "r",
        "types": ["large_airport"],
        "min_runway_ft": 3000,
    }
    assert update_airfields(cfg, pages.__getitem__, out) == 1
    (kiab,) = load_airfields(out)
    assert (kiab.code, kiab.elevation_ft, kiab.runway_ft) == ("KIAB", 1371.0, 12000)


def test_nearest_within_radius(fixtures):
    index = AirfieldIndex(load_airfields(fixtures / "airfields.csv"))
    hit = index.nearest(37.63, -97.28, radius_km=10)
    assert hit is not None and hit[0].code == "KIAB" and hit[1] < 2
    hit = index.nearest(37.645, -97.41, radius_km=10)  # by the Wichita airliner field
    assert hit is not None and hit[0].code == "KICT"
    assert index.nearest(37.0, -96.0, radius_km=10) is None
    # Radius edge, across H3 cells: 9.9 km north of Ramstein is in, 10.1 km is out.
    per_km = 1 / haversine_km(0, 0, 1, 0)
    assert index.nearest(49.4369 + 9.9 * per_km, 7.6003, radius_km=10) is not None
    assert index.nearest(49.4369 + 10.1 * per_km, 7.6003, radius_km=10) is None
