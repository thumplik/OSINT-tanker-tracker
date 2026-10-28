from tw.config import config_hash, load_fleet, load_regions
from tw.publisher.settings import load_publisher_settings, may_post


def test_fleet_families():
    fleet = load_fleet()
    assert fleet.families_for_type("C30J") == ("tactical_airlift",)  # KC-130J not separable
    assert fleet.families_for_type("k35r") == ("tanker",)
    assert fleet.families_for_type("DC10") == ()  # KC-10 retired, not tracked
    assert fleet.families_for_type(None) == ()


def test_military_block_bounds():
    fleet = load_fleet()
    assert fleet.in_military_block("ae0000")
    assert fleet.in_military_block("AFFFFF")
    assert not fleet.in_military_block("adffff")
    assert not fleet.in_military_block("b00000")
    assert not fleet.in_military_block("zzz")


def test_every_region_starts_as_watch():
    regions = load_regions()
    assert all(r.status == "watch" for r in regions.regions)
    assert regions.regions[-1].id == "other"


def test_auto_post_defaults_off_and_only_approved_posts():
    assert load_publisher_settings().auto_post is False
    assert may_post("approved")
    for status in ("new", "rejected", "posted", "suppressed", "APPROVED", ""):
        assert not may_post(status)


def test_config_hash_is_stable():
    assert config_hash() == config_hash()
    assert len(config_hash()) == 16
