import socket
import time
import uuid

import pytest

from rocket_gnc_monitor.network_bridge import network_configuration, validate_configuration
from rocket_gnc_monitor.network_ui import StationNetworkDialog
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow
from rocket_gnc_monitor.network import NetworkRuntime
from rocket_gnc_monitor.network.observation import observation_from_sample
from rocket_gnc_monitor.domain import Sample


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_mission_never_restored_implicitly(tmp_path):
    (tmp_path / "station-network.json").write_text('{"mission_id":"33333333-3333-4333-8333-333333333333", "network_port":8777}')
    assert network_configuration(tmp_path)["mission_id"] is None
    assert network_configuration(tmp_path)["network_port"] == 8777
    with pytest.raises(ValueError):
        validate_configuration(dict(mission_id="my launch", network_port=8766))


def test_real_network_ui_health_display_and_join(qtbot, tmp_path):
    mission = str(uuid.uuid4())
    config = network_configuration(tmp_path, mission_id=mission, network_bind_host="127.0.0.1",
                                   network_port=free_port(), network_discovery=False)
    window = StationWindow(tmp_path / "base", profile=StationProfile(), auto_place=False, network_config=config)
    qtbot.addWidget(window)
    window.show()
    peer = NetworkRuntime(tmp_path / "away", StationProfile(station="away3"), mission,
                          {"network_bind_host": "127.0.0.1", "network_port": 0, "network_discovery": False})
    peer.start()
    try:
        qtbot.waitUntil(lambda: bool(peer.local_endpoint), timeout=5000)
        window.station_network.runtime.add_endpoint(peer.local_endpoint)
        qtbot.waitUntil(lambda: any(p["state"] == "ONLINE" for p in window.station_network.peers), timeout=5000)
        sample = Sample(t=3.0, sequence=10, source="LIVE", received=time.monotonic(), altitude=123.0,
                        phase="PREFLIGHT", details={"state_code": 1})
        payload = observation_from_sample(sample, raw=None, raw_unavailable_reason="Explicit test fixture",
                    station="gs3", node_id=peer.node_id, boot_id=peer.boot_id, connection_id=str(uuid.uuid4()),
                    observation_counter=1, boot_monotonic=peer.boot_monotonic, vehicle_mode="balius")
        peer.publish_observation(payload)
        qtbot.waitUntil(lambda: bool(window.station_network._history.get(peer.node_id)), timeout=5000)
        window.station_network.select_source(peer.node_id)
        window.last_ui = 0
        window.refresh()
        assert window.controller.latest is None
        assert window.controller.display_latest.altitude == 123
        assert window.controller.display_latest.source == "NETWORK"
        assert not window.controller.can_command
        assert window.away_connections.health.view("away3").online
        assert "T: ONLINE" in window.away_connections.tiles["away3"].role_status
        assert window.workspace_network.isVisible()
        dialog = StationNetworkDialog(window.station_network, window)
        qtbot.addWidget(dialog)
        dialog.show()
        assert dialog.table.rowCount() == 1
        assert dialog.source.currentData() == peer.node_id
        assert "read-only" in window.controller.command_block_reason
        # A deliberate leave resets view and boot, while retaining installation identity.
        old_boot = window.station_network.runtime.boot_id
        window.station_network.apply({**config, "mission_id": None})
        assert window.station_network.runtime.boot_id != old_boot
        assert window.controller.remote_display_key is None
        assert network_configuration(tmp_path / "base")["mission_id"] is None
        dialog.close()
    finally:
        peer.stop()
        window.close()
