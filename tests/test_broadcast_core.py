from dataclasses import replace

import pytest
from PySide6.QtGui import QImage

from rocket_gnc_monitor.broadcast_model import (
    BroadcastScope,
    BroadcastTelemetry,
    CameraKey,
    BroadcastFrame,
    FrameView,
)
from rocket_gnc_monitor.broadcast_timing import (
    FrameHistory,
    TelemetryHistory,
    TimingConfig,
    Calibration,
    BroadcastClock,
    EventLedger,
    next_deadline,
)
from rocket_gnc_monitor.broadcast_renderer import compose, mission_clock
from rocket_gnc_monitor.broadcast_settings import BroadcastSettings
from rocket_gnc_monitor.display_units import DisplayUnits
from rocket_gnc_monitor.network_bridge import best_telemetry_node
from rocket_gnc_monitor.rocket_pose import RocketPose
from test_network_observation import observation, NODE, BOOT


def event(counter=1, arrival=10):
    return dict(
        peer=dict(
            mission_id="mission",
            vehicle_mode="balius",
            source_mode="live",
            role="telem",
            station="gs1",
            node_id=NODE,
            boot_id=BOOT,
        ),
        payload=observation(observation_counter=counter),
        received_local_mono=arrival,
        source_age_ms=20,
        timing_basis="event",
    )


def test_canonical_unknown_fields_and_scope_are_never_invented():
    scope = BroadcastScope("mission")
    item = event()
    record = BroadcastTelemetry.from_event(item, scope)
    assert record.altitude is None and record.vertical_speed is None
    assert not record.pose.attitude_known
    assert BroadcastTelemetry.from_event(item, replace(scope, vehicle="sustainer")) is None
    assert BroadcastTelemetry.from_event(item, replace(scope, mode="simulation")) is None
    item["payload"]["values"]["baro_altitude"]["datum"] = "agl_launch"
    qualified = BroadcastTelemetry.from_event(item, scope, vertical_contract=True)
    assert qualified.altitude is not None and qualified.datum == "above_launch"
    assert qualified.vertical_speed is not None


def test_history_uses_actual_arrival_dedupes_and_never_selects_future():
    history = TelemetryHistory()
    old = BroadcastTelemetry.from_event(event(), BroadcastScope("mission"))
    assert history.offer(old)
    assert not history.offer(replace(old, arrival=100))
    assert history.at(NODE, 9.8, 0.1) is None
    assert history.at(NODE, 9.9, 0.1) == old
    assert history.at(NODE, 100, None) is None
    fresh = replace(old, observation_id="other", arrival=11, boot="new")
    history.offer(fresh)
    assert history.at(NODE, 10, 0) is None


def test_buffer_limit_no_future_and_epoch_retirement():
    key = CameraKey("m", "balius", "live", "n", "b", "gs1", "c", "e")
    history = FrameHistory(retention=1.1)
    for n in range(40):
        history.offer(BroadcastFrame(key, bytes(6), n / 30, 2, 1))
    assert history.at(key, 1).arrival == 1
    assert history.at(key, 0) is None
    assert history.continuous(key, 1.3)
    assert history.at(key, 1, None) is None
    history.retain([replace(key, epoch="new")])
    assert history.bytes == 0
    history.limit = 5
    assert not history.offer(BroadcastFrame(key, bytes(6), 2, 2, 1))


def test_timing_admission_includes_candidates_and_measurement_basis():
    path = Calibration(0.4, 0.1, "flash", "2026-11-03", 30)
    with pytest.raises(ValueError, match="cover"):
        TimingConfig(0.5, True, {"camera": path}).validate()
    TimingConfig(0.6, True, {"camera": path}).validate(["camera"])
    with pytest.raises(ValueError, match="memory"):
        TimingConfig(4).validate(["one", "two", "candidate"])
    assert next_deadline(0, 0.101) == pytest.approx(4 / 30)


def test_countdown_zero_hold_resume_actual_launch_and_event_time():
    clock = BroadcastClock()
    clock.revise(1, "countdown", anchor=11)
    clock.hold(5)
    assert clock.at(6)[:2] == (-6, "hold")
    clock.resume(8)
    assert clock.at(14)[1] == "awaiting_launch"
    clock.revise(15, "elapsed", anchor=15, basis="observed now")
    assert clock.at(14.5)[1] == "awaiting_launch"
    assert clock.at(16)[0] == 1
    ledger = EventLedger()
    ledger.mark("APOGEE", 20, "received")
    assert ledger.at(19)[0] == ()
    assert ledger.at(21)[0] == ("APOGEE",)
    assert not ledger.mark("APOGEE", 25, "repeated")
    assert mission_clock(59.99) == "T+00:01:00.0"


def test_best_connection_is_vehicle_partitioned_and_tie_stable():
    def peer(node, vehicle, age, rtt):
        return dict(
            info=dict(node_id=node, station="gs1"),
            synchronized=True,
            state="ONLINE",
            rtt_ms=rtt,
            latest=[dict(vehicle=dict(id=vehicle), sample_age_ms_lower_bound=age)],
        )

    peers = [peer("wrong", "booster", 1, 1), peer("a", "balius", 20, 4), peer("b", "balius", 30, 3)]
    assert best_telemetry_node(peers, 500, vehicle_id="balius") == "b"
    peers[1]["rtt_ms"] = 3
    assert best_telemetry_node(peers, 500, current="b", vehicle_id="balius") == "b"


def test_settings_never_restore_on_air_state(tmp_path):
    path = tmp_path / "broadcast.json"
    settings = BroadcastSettings(
        timing=TimingConfig(0.6, True, {"x": Calibration(0.3, 0.1, "flash", "today", 20)})
    )
    settings.save(path)
    assert not BroadcastSettings.load(path).timing.calibrated
    assert "mission" not in path.read_text()


@pytest.mark.parametrize("units", ["imperial", "metric"])
def test_renderer_all_scenes_and_pose(qapp, units):
    from rocket_gnc_monitor.broadcast_model import SCENES

    for scene in SCENES:
        result = compose(
            None,
            FrameView(scene=scene, mode="SIMULATION", paused=True),
            DisplayUnits(units),
            QImage(),
            pose=RocketPose(attitude_known=True, motor=True),
        )
        assert (result.width(), result.height()) == (1920, 1080)
        assert not result.isNull()


def test_telemetry_history_bounds_receiver_churn():
    history = TelemetryHistory()
    base = BroadcastTelemetry.from_event(event(), BroadcastScope("mission"))
    for i in range(20):
        history.offer(replace(base, node=str(i), observation_id=str(i)))
    assert len(history.histories) == 8
    for i in range(2100):
        history.offer(replace(base, node="19", observation_id="last:" + str(i), arrival=10 + i / 100))
    assert len(history.histories["19"]) == 2000


def test_path_retention_matches_memory_admission():
    timing = TimingConfig(2, True, {"camera": Calibration(1, 0.1, "fixture", "today", 10)})
    assert timing.retention("camera") < timing.delay
    timing.validate(["camera"])
