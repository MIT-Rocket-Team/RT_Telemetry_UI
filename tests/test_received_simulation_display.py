import base64
import copy
import math
import uuid

import numpy as np
import pytest

from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.mission_contracts.contracts import resource
from rocket_gnc_monitor.mission_station import MissionStation
from rocket_gnc_monitor.network.observation import observation_from_sample, observation_to_sample
from rocket_gnc_monitor.rocket_pose import telemetry_pose
from rocket_gnc_monitor.rocket_pose_view import RocketPoseView
from rocket_gnc_monitor.simulation_display import SCHEMA, received_display


def delivery():
    raw = bytes.fromhex(resource("legacy-vectors.json")["frameHex"])
    return dict(packetId="tx-1", txTimeUs="1000000", data=base64.b64encode(raw).decode(),
        unavailable=["attitude", "rates", "acceleration"], simulationDisplay=dict(schema=SCHEMA,
            packet_id="tx-1", tx_time_us="1000000", frame="body_to_ENU", body_axis="+Z",
            quaternion_wxyz=[math.sqrt(.5), math.sqrt(.5), 0, 0],
            motor_ignited=True, motor_burning=False, parachute_deployed=True))


def test_packet_display_survives_station_and_network_with_correct_pose_and_no_measurement_claim(qtbot):
    station = MissionStation(dict(id="gs3", origin=[42, -77, 0]))
    message = delivery()
    sample = Sample.from_dict(station.receive(message, 1_200_000)[0])
    assert sample.attitude is sample.rates is sample.acceleration is None
    # Pointing policy still sees the received GPS/barometer, without any overrides.
    original = copy.deepcopy(sample.to_dict())
    payload = observation_from_sample(sample, raw=base64.b64decode(message["data"]), station="gs3",
        node_id=str(uuid.uuid4()), boot_id=str(uuid.uuid4()), connection_id=str(uuid.uuid4()),
        observation_counter=1, boot_monotonic=0, vehicle_mode="balius")
    assert payload["values"]["orientation"] is None
    assert payload["values"]["flight_status"]["motor_burning"] is None
    remote = observation_to_sample(payload, received_monotonic=5, source_age_ms=200, source_label="GS3")
    remote.details.update(network_source_mode="simulation", network_station="gs3")
    pose = telemetry_pose(remote)
    assert pose.attitude_known and pose.simulated_display
    np.testing.assert_allclose(pose.rotation @ [0, 0, 1], [0, -1, 0], atol=1e-10)
    assert pose.motor is False and pose.parachute is True and pose.ignited is True
    assert "GS3" in pose.receiver_label
    view = RocketPoseView()
    qtbot.addWidget(view)
    view.set_pose(pose)
    assert view.orientation_text == "SIMULATION ATTITUDE · received packet"
    assert sample.to_dict() == original
    remote.details["network_source_mode"] = "live"
    assert not telemetry_pose(remote).attitude_known


@pytest.mark.parametrize("change", [{"packet_id": "wrong"}, {"tx_time_us": "999"},
    {"quaternion_wxyz": [0, 0, 0, 0]}, {"quaternion_wxyz": [True, 0, 0, 0]},
    {"frame": "ENU_to_body"}, {"schema": "unknown"}, {"body_axis": "+X"}])
def test_unmatched_or_invalid_display_is_ignored(change):
    data = delivery()["simulationDisplay"]
    data.update(change)
    assert received_display(data, "tx-1", "1000000") is None


def test_corrupted_receiver_packet_cannot_deliver_simulation_pose():
    message = delivery()
    raw = bytearray(base64.b64decode(message["data"]))
    raw[30] ^= 1
    message["data"] = base64.b64encode(raw).decode()
    station = MissionStation(dict(id="gs3", origin=[42, -77, 0]))
    assert station.receive(message, 1_200_000) == []
    assert station.policy.latest is None
