from dataclasses import FrozenInstanceError

import pytest

from rocket_gnc_monitor.away_connection_health import AWAY_STATIONS, AwayConnectionHealth


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def health():
    return AwayConnectionHealth(clock=Clock())


@pytest.mark.parametrize("age,status", [
    (0, "good"), (249.999, "good"), (250, "fair"),
    (999.999, "fair"), (1000, "bad"), (None, "bad"),
])
def test_status_thresholds_follow_rocket_age_only_while_online(health, age, status):
    health.observe_rocket("away1", sample_age_ms=age)
    assert health.view("away1").status == "offline"
    health.observe_link("away1", online=True)
    view = health.view("away1")
    assert view.online and view.status == status
    assert view.ping_ms is None and view.ping_quality == 0
    assert view.sample_age_ms == pytest.approx(age) if age is not None else view.sample_age_ms is None


def test_link_heartbeat_does_not_refresh_the_rocket(health):
    health.observe_link("away1", online=True, ping_ms=5)
    health.observe_rocket("away1", sample_age_ms=100)
    health.clock.now = 0.15
    health.observe_link("away1", online=True, ping_ms=8)
    assert health.view("away1").status == "fair"
    assert health.view("away1").sample_age_ms == pytest.approx(250)
    health.clock.now = 0.9
    assert health.view("away1").status == "bad"
    assert health.view("away1").sample_age_ms == pytest.approx(1000)


def test_link_reports_expire_at_the_timeout_and_samples_keep_aging(health):
    health.observe_link("away2", online=True, ping_ms=20)
    health.observe_rocket("away2", sample_age_ms=50)
    health.clock.now = 2.999
    assert health.view("away2").online
    health.clock.now = 3
    view = health.view("away2")
    assert not view.online and view.status == "offline"
    assert view.ping_ms is None and view.sample_age_ms is None
    assert view.ping_quality == view.rocket_quality == 0
    health.observe_link("away2", online=True, ping_ms=10)
    view = health.view("away2")
    assert view.online and view.status == "bad"
    assert view.sample_age_ms == pytest.approx(3050)


@pytest.mark.parametrize("age,quality", [(0, 1), (60, 1), (120, .5), (250, .24), (1000, .06)])
def test_rocket_inverse_scale_is_bounded_and_cadence_calibrated(health, age, quality):
    health.observe_link("away3", online=True)
    health.observe_rocket("away3", sample_age_ms=age)
    assert health.view("away3").rocket_quality == pytest.approx(quality)


@pytest.mark.parametrize("ping,quality", [(None, 0), (0, 1), (10, 1), (20, .5), (100, .1)])
def test_ping_inverse_scale_accepts_zero_measurements(health, ping, quality):
    health.observe_link("away3", online=True, ping_ms=ping)
    view = health.view("away3")
    assert view.online
    assert view.ping_ms == ping
    assert view.ping_quality == pytest.approx(quality)


def test_station_isolation_disconnect_and_explicit_missing_sample(health):
    health.observe_link("away1", online=True, ping_ms=10)
    health.observe_rocket("away1", sample_age_ms=0)
    assert all(health.view(station).status == "offline" for station in AWAY_STATIONS[1:])
    health.observe_link("away1", online=False)
    assert health.view("away1").status == "offline"
    health.observe_link("away1", online=True)
    assert health.view("away1").status == "good"
    health.observe_rocket("away1", sample_age_ms=None)
    assert health.view("away1").status == "bad"
    assert health.view("away1").rocket_quality == 0
    health.clear("away1")
    health.observe_link("away1", online=True)
    assert health.view("away1").sample_age_ms is None


@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf"), True, "50", 10**1000])
def test_invalid_measurements_do_not_mutate_valid_observations(health, invalid):
    health.observe_link("away1", online=True, ping_ms=10)
    health.observe_rocket("away1", sample_age_ms=60)
    before = health.view("away1")
    with pytest.raises(ValueError):
        health.observe_link("away1", online=True, ping_ms=invalid)
    with pytest.raises(ValueError):
        health.observe_rocket("away1", sample_age_ms=invalid)
    assert health.view("away1") == before


@pytest.mark.parametrize("invalid", [None, 1, "true", []])
def test_online_requires_an_explicit_boolean(health, invalid):
    with pytest.raises(ValueError):
        health.observe_link("away1", online=invalid)


@pytest.mark.parametrize("station", ["GS1", "away0", "away5", 1, None, []])
def test_unknown_station_ids_are_rejected(health, station):
    with pytest.raises(ValueError):
        health.observe_link(station, online=True)
    with pytest.raises(ValueError):
        health.observe_rocket(station, sample_age_ms=0)
    with pytest.raises(ValueError):
        health.view(station)
    with pytest.raises(ValueError):
        health.clear(station)


def test_settings_are_configurable_and_views_are_immutable():
    clock = Clock()
    health = AwayConnectionHealth(clock, ping_full_scale_ms=5, rocket_full_scale_ms=100, link_timeout_s=1)
    health.observe_link("away4", online=True, ping_ms=10)
    health.observe_rocket("away4", sample_age_ms=200)
    view = health.view("away4")
    assert view.ping_quality == view.rocket_quality == .5
    with pytest.raises(FrozenInstanceError):
        view.status = "offline"
    clock.now = 1
    assert not health.view("away4").online
    for setting in ("ping_full_scale_ms", "rocket_full_scale_ms", "link_timeout_s"):
        for invalid in (0, -1, True, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                AwayConnectionHealth(**{setting: invalid})
