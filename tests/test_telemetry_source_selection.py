"""One chosen receiver drives every Base instrument, without serial polling."""

from collections import deque
import copy
import socket
import time
import uuid

import numpy as np
import pytest

from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.network import NetworkRuntime
from rocket_gnc_monitor.network.observation import observation_from_sample
from rocket_gnc_monitor.network_bridge import BEST_CONNECTION
from rocket_gnc_monitor.network_ui import StationNetworkDialog
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def window(qtbot, tmp_path):
    window = StationWindow(tmp_path / "base", profile=StationProfile(), auto_place=False,
        network_config=dict(network_bind_host="127.0.0.1", network_port=free_port(),
                            network_discovery=False, mission_id=None, peers=[]))
    qtbot.addWidget(window)
    window.show()
    yield window
    window.close()


def publisher(path, mission, station="away1", role="telemetry"):
    service = NetworkRuntime(path, StationProfile(station=station, role=role), mission,
        dict(network_bind_host="127.0.0.1", network_port=0, network_discovery=False,
             network_source_mode="simulation"))
    service.start()
    return service


def add_peer(qtbot, bridge, service):
    qtbot.waitUntil(lambda: bool(service.local_endpoint), timeout=5000)
    bridge.runtime.add_endpoint(service.local_endpoint)
    qtbot.waitUntil(lambda: any(p.get("info", {}).get("node_id") == service.node_id
                               for p in bridge.peers), timeout=5000)
    return next(p for p in bridge.peers if p.get("info", {}).get("node_id") == service.node_id)


def publish(service, altitude=120, state=2, quaternion=(1, 0, 0, 0), counter=1):
    sample = Sample(t=counter, sequence=counter, source="SIMULATION", received=time.monotonic(),
                    altitude=altitude, details={"state_code": state})
    payload = observation_from_sample(sample, raw=None, raw_unavailable_reason="Test fixture",
        station=service.station, node_id=service.node_id, boot_id=service.boot_id,
        connection_id=str(uuid.uuid4()), observation_counter=counter,
        boot_monotonic=service.boot_monotonic, vehicle_mode="balius")
    payload["values"]["orientation"] = dict(body_axes="right_handed_nose_+Z", quaternion_wxyz=list(quaternion))
    payload["values"]["flight_status"] = dict(motor_ignited=True, motor_burning=state == 2,
        parachute_deployed=state == 3, evidence_source="test")
    service.publish_observation(payload)


def test_joining_video_selects_delayed_telemetry_without_serial_polling(window, qtbot, tmp_path):
    mission = str(uuid.uuid4())
    video = publisher(tmp_path / "video", mission, role="video")
    telem = publisher(tmp_path / "telemetry", mission)
    bridge, c = window.station_network, window.controller
    try:
        peer = add_peer(qtbot, bridge, video)
        bridge.join_peer(peer)
        assert bridge.source_selection == BEST_CONNECTION
        assert c.remote_display_key == BEST_CONNECTION and c.display_latest is None
        add_peer(qtbot, bridge, telem)
        publish(telem)
        qtbot.waitUntil(lambda: c.remote_display_key == telem.node_id and c.display_latest is not None,
                        timeout=5000)
        window.last_ui = window.last_table = 0
        window.refresh()
        window.attitude.refresh()
        assert window.metrics["altitude"].text() == "120.0"
        assert window.rocket_state_badge.state_name == "FLIGHT"
        assert window.rocket_state_badge.status_label.text() == "SIMULATION · NETWORK · Telemetry"
        assert window.attitude.view.pose.attitude_known
        assert "GS1" in window.attitude.view._source_text()
        assert c.latest is None and not c.polling and not c.can_command
        assert all(worker is None for worker in c.workers.values())
        assert window.telemetry_source.currentData() == BEST_CONNECTION
    finally:
        video.stop()
        telem.stop()


def test_both_dropdowns_drive_pose_state_tables_and_preserve_apply(window, qtbot, tmp_path):
    mission = str(uuid.uuid4())
    services = [publisher(tmp_path / str(i), mission, station=f"away{i}") for i in (1, 2)]
    bridge, c = window.station_network, window.controller
    try:
        first = add_peer(qtbot, bridge, services[0])
        bridge.join_peer(first)
        add_peer(qtbot, bridge, services[1])
        publish(services[0], altitude=100, state=2)
        publish(services[1], altitude=900, state=3, quaternion=(0, 1, 0, 0))
        qtbot.waitUntil(lambda: all(bridge._history.get(s.node_id) for s in services), timeout=5000)
        dialog = StationNetworkDialog(bridge, window)
        qtbot.addWidget(dialog)
        dialog.refresh()
        assert dialog.source.currentData() == services[0].node_id
        dialog.source.setCurrentIndex(dialog.source.findData(services[1].node_id))
        window.last_ui = window.last_table = 0
        window.refresh()
        window.attitude.refresh()
        assert window.telemetry_source.currentData() == services[1].node_id
        assert window.rocket_state_badge.state_name == "APOGEE"
        assert window.metrics["altitude"].text() == "900.0"
        assert window.altitude_curve.yData.tolist() == [900]
        np.testing.assert_allclose(window.attitude.view.pose.rotation, np.diag([1, -1, -1]))
        assert window.attitude.view.pose.parachute is True and window.attitude.view.pose.motor is False
        assert "GS2" in window.attitude.view._source_text()
        # Changing the main menu also refreshes a dialog whose option list is unchanged.
        window.telemetry_source.setCurrentIndex(window.telemetry_source.findData(services[0].node_id))
        dialog.refresh()
        assert dialog.source.currentData() == services[0].node_id
        assert window.rocket_state_badge.state_name == "FLIGHT"
        np.testing.assert_allclose(window.attitude.view.pose.rotation, np.eye(3))
        assert window.altitude_curve.yData.tolist() == [100]
        assert "GS1" in window.attitude.view._source_text()
        before = c.display_latest
        bridge.apply(dict(bridge.config))
        assert bridge.source_selection == services[0].node_id and c.display_latest is before
        # A deliberate local choice must survive settings and incoming observations.
        dialog.source.setCurrentIndex(dialog.source.findData(None))
        dialog.apply()
        assert bridge.source_selection is None and c.remote_display_key is None
        bridge.poll()
        assert c.display_latest is None and not c.polling
        # A different mission must not reuse the previous mission's samples.
        bridge.select_source(services[0].node_id)
        bridge.apply({**bridge.config, "mission_id": str(uuid.uuid4())})
        assert bridge.source_selection == BEST_CONNECTION and c.display_latest is None
        assert not bridge._history
        dialog.close()
    finally:
        for service in services:
            service.stop()


def peer(mission, number, age, ping, **changes):
    value = dict(info=dict(node_id=f"node-{number}", station=f"gs{number}", role="telem",
        mission_id=mission, vehicle_mode="balius", source_mode="simulation"),
        state="ONLINE", synchronized=True, conflict=False, rtt_ms=ping,
        latest=[{"sample_age_ms_lower_bound": age}])
    value.update(changes)
    return value


def test_best_connection_scores_rocket_then_ltu_once_per_second(window):
    bridge, c = window.station_network, window.controller
    bridge.timer.stop()
    mission = str(uuid.uuid4())
    bridge.config.update(mission_id=mission, receive_source_mode="simulation")
    bridge.peers = [peer(mission, 1, 20, 8), peer(mission, 2, 50, 2), peer(mission, 3, 90, .1)]
    for i in (1, 2, 3):
        bridge._history[f"node-{i}"] = deque([Sample(t=i, sequence=i, source="NETWORK", altitude=i)])
    bridge.source_selection = BEST_CONNECTION
    bridge._select_best_source(100)
    # Both first two rocket bars are full, so the better LTU-XR link wins.
    assert c.remote_display_key == "node-2" and c.display_latest.altitude == 2
    bridge.peers[1]["latest"][0]["sample_age_ms_lower_bound"] = 700
    bridge._select_best_source(100.999)
    assert c.remote_display_key == "node-2"
    bridge._select_best_source(101)
    assert c.remote_display_key == "node-1" and c.display_latest.altitude == 1
    # Equal scores retain the selected receiver rather than flapping.
    bridge.peers[1].update(latest=[{"sample_age_ms_lower_bound": 20}], rtt_ms=8)
    bridge._select_best_source(102)
    assert c.remote_display_key == "node-1"
    bridge.select_source("node-3")
    bridge._select_best_source(103)
    assert c.remote_display_key == "node-3"


@pytest.mark.parametrize("invalid", ["mission", "vehicle", "mode", "role", "base", "conflict", "offline",
                                         "stale_link", "unsynchronized", "old_sample", "no_sample", "nan_age"])
def test_best_excludes_ineligible_receivers_and_never_falls_back_to_local(window, invalid):
    bridge, c = window.station_network, window.controller
    bridge.timer.stop()
    mission = str(uuid.uuid4())
    bridge.config.update(mission_id=mission, receive_source_mode="simulation")
    value = peer(mission, 1, 10, 1)
    if invalid in {"mission", "vehicle", "mode", "role", "base"}:
        field, replacement = {"mission": ("mission_id", str(uuid.uuid4())), "vehicle": ("vehicle_mode", "iris"),
            "mode": ("source_mode", "live"), "role": ("role", "video"), "base": ("station", "launch")}[invalid]
        value["info"][field] = replacement
    elif invalid == "conflict":
        value["conflict"] = True
    elif invalid in {"offline", "stale_link"}:
        value["state"] = "STALE" if invalid == "stale_link" else "OFFLINE"
    elif invalid == "unsynchronized":
        value["synchronized"] = False
    else:
        value["latest"] = [] if invalid == "no_sample" else [{"sample_age_ms_lower_bound":
            float("nan") if invalid == "nan_age" else 60000}]
    bridge.peers = [copy.deepcopy(value)]
    c.latest = Sample(t=1, sequence=1, source="LIVE", altitude=999)
    bridge.select_source(BEST_CONNECTION)
    assert c.remote_display_key == BEST_CONNECTION and c.display_latest is None
    assert not c.can_command
    assert c.latest.altitude == 999


def test_best_survives_same_mission_apply_but_leave_clears_it(window):
    bridge, c = window.station_network, window.controller
    bridge.apply({**bridge.config, "mission_id": str(uuid.uuid4()), "receive_source_mode": "simulation"})
    bridge.select_source(BEST_CONNECTION)
    bridge.apply(dict(bridge.config))
    assert bridge.source_selection == BEST_CONNECTION and c.remote_display_key == BEST_CONNECTION
    bridge.apply({**bridge.config, "mission_id": None, "receive_source_mode": "live"})
    assert bridge.source_selection is None and c.remote_display_key is None


@pytest.mark.parametrize("mode,label", [("live", "NETWORK"), ("simulation", "SIMULATION · NETWORK"),
                                        ("replay", "REPLAY · NETWORK")])
def test_state_badge_labels_network_source_mode_and_retains_unknown_state(window, mode, label):
    c = window.controller
    c.remote_display_key = "selected-receiver"
    c.remote_display_sample = Sample(t=1, sequence=1, source="NETWORK", received=time.monotonic(),
                                    details={"state_code": 255, "network_source_mode": mode})
    window.last_ui = 0
    window.refresh()
    assert window.rocket_state_badge.state_name == "UNKNOWN"
    assert window.rocket_state_badge.status_label.text() == label + " · Telemetry"
