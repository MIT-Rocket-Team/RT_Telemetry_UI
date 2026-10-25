"""Real HTTP station configuration crossing the Qt application boundary."""
import json
import socket
import urllib.request
import uuid

import pytest
from PySide6.QtWidgets import QApplication

from rocket_gnc_monitor.network_bridge import network_configuration
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture
def linked_windows(qtbot, tmp_path):
    mission = str(uuid.uuid4())
    windows = []
    for name, profile in (("base", StationProfile()), ("away", StationProfile(station="away1"))):
        directory = tmp_path / name
        config = network_configuration(directory, mission_id=mission, network_bind_host="127.0.0.1",
                                       network_port=free_port(), network_discovery=False)
        window = StationWindow(directory, profile=profile, auto_place=False, network_config=config)
        qtbot.addWidget(window)
        window.show()
        windows.append(window)
    base, away = windows
    qtbot.waitUntil(lambda: all(window.station_network.runtime.state == "LISTENING" for window in windows), timeout=5000)
    base.station_network.runtime.add_endpoint(away.station_network.runtime.local_endpoint)
    away.station_network.runtime.add_endpoint(base.station_network.runtime.local_endpoint)
    qtbot.waitUntil(lambda: all(any(peer["synchronized"] for peer in window.station_network.peers) for window in windows), timeout=5000)
    yield base, away
    replacement = getattr(QApplication.instance(), "station_window", None)
    if replacement is not None and replacement not in windows:
        replacement.close()
        QApplication.instance().station_window = None
    for window in reversed(windows):
        window.close()


def location(format="mgrs"):
    return dict(lat_deg=42.703, lon_deg=-77.194, alt_m=287.5, alt_datum="wgs84_ellipsoid",
                label="URRG test station", coordinate_input=dict(format=format,
                    code="18TUN2291333237" if format == "mgrs" else "87J4PR34+6C" if format == "pluscode" else "42.703, -77.194"))


def read_result(endpoint, request_id):
    with urllib.request.urlopen(endpoint + "/api/v1/configuration?request_id=" + request_id, timeout=3) as response:
        return json.loads(response.read(65536))


@pytest.mark.parametrize("coordinate_format", ["latlon", "mgrs", "pluscode"])
def test_base_sets_away_location_with_applied_ack_without_faking_hardware(linked_windows, qtbot, coordinate_format):
    base, away = linked_windows
    replies = []
    base.station_network.configuration_finished.connect(lambda request_id, result: replies.append((request_id, result)))
    peer = next(peer for peer in base.station_network.peers if peer["info"]["node_id"] == away.station_network.runtime.node_id)
    request_id = base.station_network.request_remote_configuration(peer, "gs1", location(coordinate_format))
    qtbot.waitUntil(lambda: bool(replies), timeout=10000)
    assert replies[0][0] == request_id and replies[0][1]["ok"], replies
    mission = away.controller.mission
    assert mission.pointer_site_configured
    assert (mission.pointer_latitude, mission.pointer_longitude, mission.pointer_altitude) == (42.703, -77.194, 287.5)
    assert away.controller.latest is None
    assert away.controller.frozen_ground is None
    assert not away.controller.ground_connected and not away.controller.can_command
    assert not any(away.controller.workers.values())
    assert base.station_network.station_assignments[away.station_network.runtime.node_id] == "gs1"
    ack = read_result(peer["endpoint"], request_id)
    assert ack["state"] == "applied"
    assert ack["result"]["configuration"]["location"]["coordinate_input"]["format"] == coordinate_format
    assert ack["result"]["restart_required"] is False


def test_renumber_restarts_identity_once_and_keeps_durable_result(linked_windows, qtbot):
    base, away = linked_windows
    old_runtime = away.station_network.runtime
    old_boot, node_id, address = old_runtime.boot_id, old_runtime.node_id, old_runtime.local_endpoint
    peer = next(peer for peer in base.station_network.peers if peer["info"]["node_id"] == node_id)
    replies = []
    base.station_network.configuration_finished.connect(lambda request_id, result: replies.append((request_id, result)))
    request_id = base.station_network.request_remote_configuration(peer, "gs4", location())
    qtbot.waitUntil(lambda: bool(replies), timeout=10000)
    assert replies[0][1]["ok"], replies
    qtbot.waitUntil(lambda: getattr(QApplication.instance(), "station_window", None) is not None, timeout=10000)
    replacement = QApplication.instance().station_window
    qtbot.addWidget(replacement)
    assert replacement is not away
    qtbot.waitUntil(lambda: replacement.station_network.runtime.state == "LISTENING", timeout=5000)
    assert replacement.profile.station == "away4"
    assert replacement.station_network.runtime.node_id == node_id
    assert replacement.station_network.runtime.boot_id != old_boot
    assert replacement.station_network.runtime.local_endpoint == address
    assert replacement.controller.mission.pointer_site_configured
    assert replacement.controller.mission.pointer_latitude == 42.703
    result = read_result(address, request_id)
    assert result["state"] == "applied" and result["result"]["restart_required"]
    assert result["result"]["configuration"]["station"] == "gs4"
    assert old_runtime.state == "STOPPED"
