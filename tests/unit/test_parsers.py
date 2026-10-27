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
    assert len(tanker) == 4
    assert all(p.callsign == "PACK21" for p in tanker)  # carried forward from details
    assert tanker[0].on_ground is True and tanker[0].alt_baro is None
    assert tanker[1].alt_baro == 12000 and tanker[1].on_ground is False
    # Missing altitude is unknown, never "on the ground".
    assert tanker[3].alt_baro is None and tanker[3].on_ground is None
    assert tanker[2].position_source == "mlat"
    assert tanker[3].ts.date().isoformat() == "2026-07-02"
