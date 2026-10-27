import json
from datetime import UTC, datetime

import pytest

from tw.ingest.adsbx_v2 import parse_v2
from tw.ingest.globe_history import iter_points


def test_v2_parse(fixtures):
    payload = json.loads((fixtures / "v2_mil_2026-07-01T0100Z.json").read_text())
    points = {p.hex: p for p in parse_v2(payload, provider="adsb_lol")}

    tanker = points["ae1234"]
    assert tanker.callsign == "PACK21"
    assert tanker.ts == datetime(2026, 7, 1, 0, 59, 58, 500000, tzinfo=UTC)
    assert tanker.position_source == "adsb_icao"
    assert tanker.on_ground is False

    mode_s = points["ae0abc"]
    assert mode_s.lat is None and mode_s.lon is None
    assert mode_s.on_ground is False  # has a baro altitude, so it is airborne
    assert mode_s.position_source == "mode_s"

    assert points["ae1111"].is_non_icao
    assert points["ae2222"].on_ground is True and points["ae2222"].alt_baro is None


def test_v2_requires_now():
    with pytest.raises(ValueError):
        list(parse_v2({"ac": []}, provider="x"))


def test_trace_parse_including_gzip(fixtures):
    points = list(iter_points(fixtures / "globe_history_2026-07-01"))
    tanker = [p for p in points if p.hex == "ae1234"]
    assert len(tanker) == 5
    assert all(p.callsign == "PACK21" for p in tanker)  # carried forward from details
    assert tanker[0].on_ground is True and tanker[0].alt_baro is None
    assert tanker[1].alt_baro == 12000 and tanker[1].on_ground is False
    assert tanker[2].position_source == "mlat"
    # flags & 8: altitude is geometric, so no baro altitude, but the aircraft is airborne.
    assert tanker[3].alt_baro is None and tanker[3].on_ground is False
    # Missing altitude is unknown, never "on the ground".
    assert tanker[4].alt_baro is None and tanker[4].on_ground is None


def test_real_dump_traces(fixtures):
    """Regression on real adsb.lol traces. Expected values were counted from the raw JSON."""
    points = list(iter_points(fixtures / "real_2026-10-25"))
    by_hex: dict[str, list] = {}
    for p in points:
        by_hex.setdefault(p.hex, []).append(p)

    assert {h: len(v) for h, v in by_hex.items()} == {
        "a99d05": 22,
        "ae29da": 1310,
        "ae07f0": 1523,
        "ae4ff0": 441,
        "ae5df0": 549,
        "2b7bf0": 21,
    }
    c17 = by_hex["ae07f0"]
    assert {p.type for p in c17} == {"C17"}
    assert sum(p.on_ground is True for p in c17) == 219
    assert {p.callsign for p in c17} == {"RCH3262"}
    assert c17[0].ts == datetime(2026, 10, 25, 13, 5, 47, 770000, tzinfo=UTC)
    assert all(p.ts.date().isoformat() == "2026-10-25" for p in points)

    hercules = by_hex["ae29da"]
    assert {p.position_source for p in hercules} == {"mlat"}
    assert hercules[0].gs is None and hercules[0].track is None

    assert all(p.is_non_icao for p in by_hex["2b7bf0"])
    assert all(not p.is_non_icao for h, v in by_hex.items() if h != "2b7bf0" for p in v)


def test_stream_stops_after_traces_subtree(fixtures):
    import io
    import tarfile

    from tw.ingest.globe_history import iter_trace_docs_from_stream

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in sorted((fixtures / "real_2026-10-25").rglob("trace_full_*")):
            tar.add(f, arcname=f"./traces/{f.parent.name}/{f.name}")
        info = tarfile.TarInfo("./heatmap/00.bin.ttf")
        info.size = 10
        tar.addfile(info, io.BytesIO(b"x" * 10))
        # A trace outside traces/ after the heatmap would not exist in a real dump; it proves
        # the reader stopped at the heatmap instead of reading on.
        tar.add(
            next((fixtures / "real_2026-10-25").rglob("trace_full_a99d05*")),
            arcname="./zzz/trace_full_a99d05.json",
        )
    buf.seek(0)
    assert len(list(iter_trace_docs_from_stream(buf))) == 6
