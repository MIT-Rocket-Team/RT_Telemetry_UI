"""Angle chirality, source provenance and confirmed-effect behavior."""

import math
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from rocket_gnc_monitor.demo import DemoFlight
from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.flight_scene import FlightFrame, FlightScene, quaternion_matrix
from rocket_gnc_monitor.rocket_pose import RocketPose, rotation_from_rpy, simulation_pose, telemetry_pose
from rocket_gnc_monitor.trajectory import Trajectory


def sample(angles=(0, 0, 0), **kwargs):
    return Sample(t=12, sequence=1, source="LIVE", attitude=angles, **kwargs)


def frame(rotation=None, **kwargs):
    values = dict(time=2.5, position=np.zeros(3), velocity=np.zeros(3),
                  rotation=np.eye(3) if rotation is None else rotation,
                  attitude="Simulation attitude", powered=False, recovery=False,
                  inflation=0, state="COAST / PAD")
    values.update(kwargs)
    return FlightFrame(**values)


@pytest.mark.parametrize("angles", [
    (90, 0, 0), (0, 90, 0), (0, 0, 90), (450, -400, 721),
])
def test_gyro_integrals_are_preserved_but_never_treated_as_euler_orientation(angles):
    pose = telemetry_pose(sample(angles))
    assert pose.angles == angles and not pose.attitude_known
    assert pose.angle_kind == "gyro_integrals"
    np.testing.assert_array_equal(pose.rotation, np.eye(3))
    assert "Orientation unavailable · gyro integrals are not attitude" in pose.status


def test_reported_angles_are_not_normalized_or_replaced_by_simulation():
    values = (450, -400, 721)
    pose = telemetry_pose(sample(values))
    assert pose.angles == values and pose.source == "LIVE"
    np.testing.assert_array_equal(pose.rotation, np.eye(3))
    assert pose.motor is pose.parachute is pose.ignited is None


@pytest.mark.parametrize("station", ["GS1", "GS2", "GS3"])
@pytest.mark.parametrize("seconds_after_launch", [2, 10])
def test_real_zephyrus_climb_data_does_not_make_false_sideways_rocket(station, seconds_after_launch):
    demo = DemoFlight(station)
    stamp = demo.flight_zero + seconds_after_launch
    index = min(range(len(demo.rows)), key=lambda i: abs(float(demo.rows[i]["flight_time"]) / 1000 - stamp))
    value = demo.sample(index)
    raw = tuple(float(demo.rows[index][key]) for key in ("roll_gyro_int", "pitch_gyro_int", "yaw_gyro_int"))
    assert value.phase == "Flight" and value.altitude > demo.sample(demo.launch_index).altitude + 500
    assert any(abs(angle) > 90 for angle in raw)
    pose = telemetry_pose(value, "DEMO")
    assert tuple(value.attitude) == raw == pose.angles
    assert pose.angle_kind == "gyro_integrals" and not pose.attitude_known
    assert pose.source == "DEMO" and "gyro integrals are not attitude" in pose.status
    np.testing.assert_array_equal(pose.rotation, np.eye(3))


def orientation(quaternion, **kwargs):
    return dict(frame="body_to_ENU", body_axis="+Z", quaternion_wxyz=quaternion, **kwargs)


@pytest.mark.parametrize("quaternion,axis,expected", [
    ((1, 0, 0, 0), (0, 0, 1), (0, 0, 1)),
    ((math.sqrt(0.5), 0, 0, math.sqrt(0.5)), (1, 0, 0), (0, 1, 0)),
    ((math.sqrt(0.5), 0, 0, math.sqrt(0.5)), (0, 0, 1), (0, 0, 1)),
    ((math.sqrt(0.5), 0, math.sqrt(0.5), 0), (0, 0, 1), (1, 0, 0)),
    ((math.sqrt(0.5), math.sqrt(0.5), 0, 0), (0, 0, 1), (0, -1, 0)),
])
def test_explicit_quaternion_orientation_has_right_handed_chirality(quaternion, axis, expected):
    value = sample((450, -400, 721), details={"orientation": orientation(quaternion)})
    pose = telemetry_pose(value)
    assert pose.attitude_known and pose.angle_kind == "orientation"
    assert value.attitude == (450, -400, 721)  # The adapter never rewrites raw telemetry.
    np.testing.assert_allclose(pose.rotation @ axis, expected, atol=1e-12)
    np.testing.assert_allclose(pose.rotation, quaternion_matrix(quaternion), atol=1e-12)
    np.testing.assert_allclose(rotation_from_rpy(*pose.angles), pose.rotation, atol=1e-12)
    np.testing.assert_allclose(pose.rotation.T @ pose.rotation, np.eye(3), atol=1e-12)
    assert np.linalg.det(pose.rotation) == pytest.approx(1)
    assert "Orientation quaternion" in pose.status and not pose.rotation.flags.writeable


def test_unit_quaternion_rounding_and_sign_are_supported_without_mutating_input():
    quaternion = np.array([0.7, 0.2, -0.3, 0.5])
    quaternion /= np.linalg.norm(quaternion)
    quaternion *= 1.0005
    value = sample(details={"orientation": orientation(quaternion)})
    original = quaternion.copy()
    positive = telemetry_pose(value)
    value.details["orientation"]["quaternion_wxyz"] = -quaternion
    negative = telemetry_pose(value)
    assert positive.attitude_known and negative.attitude_known
    np.testing.assert_array_equal(quaternion, original)
    np.testing.assert_allclose(positive.rotation, negative.rotation)
    quaternion[:] = 0
    assert positive.attitude_known and np.linalg.det(positive.rotation) == pytest.approx(1)


def test_orientation_contract_does_not_require_legacy_gyro_totals_and_remains_stale():
    value = sample(None, details={"orientation": orientation([0, 0, 1, 0]), "flight_status": {
        "motor_burning": False, "parachute_deployed": True,
    }})
    pose = telemetry_pose(value, fresh=False)
    assert pose.attitude_known and pose.angle_kind == "orientation"
    assert pose.stale and "STALE" in pose.status
    assert pose.motor is False and pose.parachute is True
    np.testing.assert_allclose(pose.rotation @ [0, 0, 1], [0, 0, -1], atol=1e-12)
    assert value.attitude is None


@pytest.mark.parametrize("metadata", [
    None, True, [], {},
    {"frame": "ENU_to_body", "body_axis": "+Z", "quaternion_wxyz": [1, 0, 0, 0]},
    {"frame": "body_to_ENU", "body_axis": "+X", "quaternion_wxyz": [1, 0, 0, 0]},
    {"frame": "body_to_ENU", "quaternion_wxyz": [1, 0, 0, 0]},
    {"body_axis": "+Z", "quaternion_wxyz": [1, 0, 0, 0]},
    {"frame": ["body_to_ENU"], "body_axis": "+Z", "quaternion_wxyz": [1, 0, 0, 0]},
    orientation(None), orientation("1,0,0,0"), orientation([0, 0, 0, 0]),
    orientation([2, 0, 0, 0]), orientation([1.01, 0, 0, 0]), orientation([0.5, 0, 0, 0]),
    orientation([1, 0, 0]), orientation([1, 0, 0, 0, 0]), orientation([[1, 0, 0, 0]]),
    orientation([math.nan, 0, 0, 0]), orientation([1, math.inf, 0, 0]),
    orientation([1, True, 0, 0]), orientation([1, np.bool_(False), 0, 0]),
    orientation(["1", 0, 0, 0]), orientation([10**400, 0, 0, 0]),
    orientation([1e308, 1e308, 1e308, 1e308]), orientation(np.eye(4)),
])
def test_malformed_orientation_never_falls_back_to_legacy_euler_angles(metadata):
    pose = telemetry_pose(sample((30, 60, 90), details={"orientation": metadata}))
    assert pose.angles == (30, 60, 90) and pose.angle_kind == "gyro_integrals"
    assert not pose.attitude_known and "Invalid orientation metadata" in pose.status
    np.testing.assert_array_equal(pose.rotation, np.eye(3))


@pytest.mark.parametrize("values,reported", [
    (None, (None, None, None)),
    ((10, math.nan, 30), (10, None, 30)),
    ((10, 20, math.inf), (10, 20, None)),
    ((10, True, 30), (10, None, 30)),
    ((10, "20", 30), (10, None, 30)),
    ((10, 20), (None, None, None)),
    (np.zeros((3, 3)), (None, None, None)),
    ((10**400, 0, 0), (None, 0, 0)),
])
def test_missing_or_malformed_angles_keep_neutral_model_without_claiming_zero(values, reported):
    pose = telemetry_pose(sample(values))
    assert pose.angles == reported and not pose.attitude_known
    np.testing.assert_array_equal(pose.rotation, np.eye(3))
    assert "Orientation unavailable" in pose.status


def test_empty_pose_and_missing_telemetry_are_explicitly_unknown():
    for pose in (RocketPose(), telemetry_pose(None), simulation_pose(None)):
        assert pose.angles == (None, None, None)
        assert pose.motor is pose.parachute is pose.ignited is None
        assert not pose.attitude_known
        assert pose.angle_kind == "unknown"
        np.testing.assert_array_equal(pose.rotation, np.eye(3))
        assert not pose.rotation.flags.writeable
    assert "No telemetry" in telemetry_pose(None).status
    assert simulation_pose(None).source == "SIMULATION"


def test_legacy_phase_and_pyro_firing_do_not_confirm_motor_or_parachute():
    live = sample((12, 20, 30), phase="Flight", details={
        "state_code": 2, "fired_bits": 255, "armed_bits": 255,
        "pyro_continuity": [3] * 6, "motor_ignited": True, "parachute_deployed": True,
    })
    pose = telemetry_pose(live)
    assert pose.motor is pose.parachute is pose.ignited is None
    live.phase = "Main"
    live.details["state_code"] = 4
    assert telemetry_pose(live).parachute is None


@pytest.mark.parametrize("flag", [True, False])
def test_normalized_adapter_flags_preserve_confirmed_on_and_off(flag):
    pose = telemetry_pose(sample(details={"flight_status": {
        "motor_ignited": flag, "motor_burning": flag, "parachute_deployed": flag,
    }}))
    assert pose.motor is flag and pose.parachute is flag and pose.ignited is flag
    assert pose.inflation == (1 if flag else 0)


@pytest.mark.parametrize("flag", [0, 1, "true", "false", None, [], {}, np.bool_(True)])
def test_adapter_flags_are_strict_booleans(flag):
    pose = telemetry_pose(sample(details={"flight_status": {
        "motor_ignited": flag, "motor_burning": flag, "parachute_deployed": flag,
    }}))
    assert pose.motor is pose.parachute is pose.ignited is None


def test_historical_ignition_does_not_mean_continuous_flame():
    pose = telemetry_pose(sample(details={"flight_status": {"motor_ignited": True}}))
    assert pose.ignited is True and pose.motor is None
    assert "current burn unknown" in pose.status
    burnt_out = telemetry_pose(sample(details={"flight_status": {"motor_ignited": True, "motor_burning": False}}))
    assert burnt_out.ignited is True and burnt_out.motor is False


@pytest.mark.parametrize("mode,sample_source,source,stale", [
    ("LIVE", "LIVE", "LIVE", True),
    ("DEMO", "LIVE", "DEMO", False),
    ("REPLAY", "LIVE", "REPLAY", False),
    ("LIVE", "DEMO", "DEMO", False),
    ("LIVE", "LEGACY_CSV", "REPLAY", False),
])
def test_stale_source_provenance_is_explicit_and_pose_remains_last_known(mode, sample_source, source, stale):
    value = sample((10, 20, 30))
    value.source = sample_source
    pose = telemetry_pose(value, mode=mode, fresh=False)
    assert pose.source == source and pose.stale is stale
    assert pose.angles == (10, 20, 30)
    assert ("STALE" in pose.status) is stale
    assert pose.time == 12


def test_invalid_metadata_does_not_create_confirmed_effects():
    for details in (None, [], {"flight_status": True}):
        value = SimpleNamespace(attitude=[1, 2, 3], details=details, source=[], t=math.nan)
        pose = telemetry_pose(value)
        assert pose.motor is pose.parachute is pose.ignited is None
        assert pose.time is None


def test_simulation_uses_existing_quaternion_rotation_without_axis_conversion_or_mutation():
    q = np.asarray([0.7, 0.2, -0.3, 0.5])
    q /= np.linalg.norm(q)
    original = quaternion_matrix(q)
    pose = simulation_pose(frame(original, powered=True))
    np.testing.assert_array_equal(pose.rotation, original)
    np.testing.assert_allclose(rotation_from_rpy(*pose.angles), original, atol=1e-12)
    assert pose.source == "SIMULATION" and pose.attitude_known and pose.angle_kind == "orientation"
    assert pose.motor is True and pose.ignited is True and pose.parachute is False
    assert not pose.stale and pose.time == 2.5
    original[0, 0] = -12
    assert pose.rotation[0, 0] != -12 and not pose.rotation.flags.writeable


@pytest.mark.parametrize("pitch", [90, -90])
def test_simulation_angle_labels_handle_display_euler_singularity(pitch):
    rotation = rotation_from_rpy(37, pitch, -25)
    pose = simulation_pose(frame(rotation))
    np.testing.assert_allclose(rotation_from_rpy(*pose.angles), rotation, atol=1e-12)


def test_simulation_path_alignment_is_preserved_but_never_claims_reported_angles():
    rotation = rotation_from_rpy(0, 45, 30)
    pose = simulation_pose(frame(rotation, attitude="Path-aligned illustration · attitude unavailable"))
    np.testing.assert_array_equal(pose.rotation, rotation)
    assert not pose.attitude_known and pose.angles == (None, None, None)
    assert pose.angle_kind == "unknown"
    assert "Path-aligned illustration" in pose.status


@pytest.mark.parametrize("matrix", [np.diag([1, 1, -1]), np.diag([2, 1, 1]), np.ones((2, 2)), np.full((3, 3), math.nan)])
def test_invalid_simulation_rotation_is_neutral_and_unknown(matrix):
    pose = simulation_pose(frame(matrix))
    assert not pose.attitude_known and pose.angles == (None, None, None)
    np.testing.assert_array_equal(pose.rotation, np.eye(3))
    assert "Orientation unavailable" in pose.status


def test_simulation_missing_events_do_not_turn_absence_into_confirmed_off():
    known = frame()
    pose = simulation_pose(known)
    assert pose.motor is False and pose.parachute is False
    for pose in (simulation_pose(known, events_available=False),
                 simulation_pose(replace(known, state="FLIGHT EVENTS UNAVAILABLE")),
                 simulation_pose(replace(known, powered=True, recovery=True), events_available=False)):
        assert pose.motor is pose.parachute is pose.ignited is None
        assert "Flight events unavailable" in pose.status


@pytest.mark.parametrize("motor_available,chute_available,motor,chute", [
    (True, True, True, True),
    (False, True, None, True),
    (True, False, True, None),
    (False, False, None, None),
    (1, "yes", None, None),
])
def test_per_effect_event_availability_gates_confirmation_independently(motor_available, chute_available, motor, chute):
    value = frame(powered=True, recovery=True, inflation=1)
    pose = simulation_pose(value, motor_events_available=motor_available,
                           parachute_events_available=chute_available)
    assert pose.motor is motor and pose.parachute is chute
    assert pose.ignited is (True if motor is True else None)
    assert pose.inflation == (1 if chute is True else 0)
    assert ("Motor event history unavailable" in pose.status) is (motor is None)
    assert ("Parachute event history unavailable" in pose.status) is (chute is None)


def test_global_missing_event_timeline_overrides_per_effect_flags():
    value = frame(powered=True, recovery=True, inflation=1)
    for pose in (simulation_pose(value, events_available=False, motor_events_available=True,
                                 parachute_events_available=True),
                 simulation_pose(replace(value, state="FLIGHT EVENTS UNAVAILABLE"),
                                 motor_events_available=True, parachute_events_available=True)):
        assert pose.motor is pose.parachute is pose.ignited is None
        assert pose.inflation == 0 and "Flight events unavailable" in pose.status


@pytest.mark.parametrize("events,stamp,motor,chute", [
    ([dict(time=3, type="APOGEE")], 1, None, None),
    ([dict(time=4, type="RECOVERY_DEVICE_DEPLOYMENT")], 1, None, False),
    ([dict(time=4, type="RECOVERY_DEVICE_DEPLOYMENT")], 5, None, True),
    ([dict(time=0, type="IGNITION"), dict(time=2, type="BURNOUT")], 1, True, None),
    ([dict(time=0, type="IGNITION"), dict(time=2, type="BURNOUT")], 3, False, None),
])
def test_partial_reference_event_lists_do_not_invent_missing_effect_history(events, stamp, motor, chute):
    scene = FlightScene(Trajectory(
        [[0, 0, 0, 0], [2, 0, 0, 100], [4, 10, 20, 80], [6, 20, 30, 0]],
        dict(schema_version=1, frame="ENU", units="m,s", origin=[42, -77, 300], flight_events=events),
    ))
    kinds = {event["type"] for event in scene.events}
    pose = simulation_pose(scene.frame(stamp), events_available=bool(scene.events),
                           motor_events_available=bool(kinds & {"IGNITION", "BURNOUT"}),
                           parachute_events_available="RECOVERY_DEVICE_DEPLOYMENT" in kinds)
    assert pose.motor is motor and pose.parachute is chute


def test_real_flight_scene_event_boundaries_and_eventless_reference():
    manifest = dict(schema_version=1, frame="ENU", units="m,s", origin=[42, -77, 300])
    points = [[0, 0, 0, 0], [2, 0, 0, 100], [4, 10, 20, 80], [6, 20, 30, 0]]
    scene = FlightScene(Trajectory(points, dict(manifest, flight_events=[
        dict(time=0, type="IGNITION"), dict(time=2, type="BURNOUT"),
        dict(time=4, type="RECOVERY_DEVICE_DEPLOYMENT"),
    ])))
    assert simulation_pose(scene.frame(1)).motor is True
    assert simulation_pose(scene.frame(2)).motor is False
    assert simulation_pose(scene.frame(3.99)).parachute is False
    deployed = simulation_pose(scene.frame(4.7))
    assert deployed.parachute is True and deployed.inflation == pytest.approx(1)
    old = simulation_pose(FlightScene(Trajectory(points, manifest)).frame(1))
    assert old.motor is old.parachute is None
