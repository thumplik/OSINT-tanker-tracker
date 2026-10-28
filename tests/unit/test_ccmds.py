"""CCMD polygons (config/ccmds.yaml): every pinned airfield maps to its command."""

import pytest

from tw.theater.ccmd import CcmdMapper, load_ccmds

CCMDS = load_ccmds()
MAPPER = CcmdMapper(CCMDS)


@pytest.mark.parametrize("icao", sorted(CCMDS.checkpoints))
def test_checkpoint_maps_to_its_ccmd(icao):
    (lat, lon), expected = CCMDS.checkpoints[icao]
    assert MAPPER.at(lat, lon) == expected


def test_config_is_consistent():
    assert CCMDS.home == "northcom" and CCMDS.fallback in CCMDS.ids
    assert {c.status for c in CCMDS.ccmds} == {"watch"}  # promotion is a manual config edit
    assert len(CCMDS.checkpoints) >= 25


def test_cells_use_their_centre():
    import h3

    assert MAPPER.cell(h3.latlng_to_cell(25.117, 51.315, 4)) == "centcom"
