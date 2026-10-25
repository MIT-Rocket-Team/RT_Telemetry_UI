import uuid
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal

from rocket_gnc_monitor.location import decode_mgrs, decode_plus_code
from rocket_gnc_monitor.network_configuration_ui import StationAssignmentsDialog
from rocket_gnc_monitor.settings import AppSettings
from rocket_gnc_monitor.settings_ui import SettingsDialog


MISSION = str(uuid.uuid4())


def peer(station="gs1", role="telem", **overrides):
    return {"endpoint": "http://192.168.1.20:8766", "state": "ONLINE", "conflict": False,
            "info": {"node_id": str(uuid.uuid4()), "boot_id": str(uuid.uuid4()),
                     "station": station, "role": role, "source_mode": "live", "mission_id": MISSION},
            **overrides}


class Bridge(QObject):
    configuration_finished = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.identity = {"station": "launch", "mission_id": MISSION}
        self.runtime = SimpleNamespace(info=lambda: self.identity)
        self.peers = [peer(), peer(role="video")]
        self.station_assignments = {}
        self.requests = []

    def request_remote_configuration(self, target, station, location):
        self.requests.append((target, station, location))
        return str(uuid.uuid4())


@pytest.fixture
def dialog(qtbot):
    bridge = Bridge()
    widget = StationAssignmentsDialog(bridge)
    qtbot.addWidget(widget)
    widget.table.selectRow(0)
    yield widget, bridge
    widget.reject()


def test_device_identity_and_roles_remain_separate_and_assignment_is_async(dialog):
    widget, bridge = dialog
    assert widget.table.rowCount() == 2
    assert widget.table.item(0, 0).text() != widget.table.item(1, 0).text()
    assert widget.send.isEnabled()
    widget.station.setCurrentIndex(widget.station.findData("gs2"))
    widget.submit()
    target, station, location = bridge.requests[-1]
    assert target["info"]["node_id"] == bridge.peers[0]["info"]["node_id"]
    assert station == "gs2" and location is None
    assert not widget.send.isEnabled() and not widget.table.isEnabled()
    assert bridge.station_assignments == {}  # No optimistic local mapping.
    bridge.configuration_finished.emit("some-other-request", {"ok": True})
    assert widget._pending
    bridge.configuration_finished.emit(widget._pending, {"ok": True, "configuration": {"station": "gs2", "location": None}})
    assert not widget._pending and widget.send.isEnabled()
    assert target["info"]["node_id"] in widget.status.text() and "GS2" in widget.status.text()


@pytest.mark.parametrize("kind,code", [("mgrs", "18TUN2291333237"), ("pluscode", "87JC9W64+4C")])
def test_coordinate_formats_send_canonical_location_and_show_applied_ack(dialog, kind, code):
    widget, bridge = dialog
    widget.update_location.setChecked(True)
    widget.location.format.setCurrentIndex(widget.location.format.findData(kind))
    field = widget.location.mgrs if kind == "mgrs" else widget.location.plus_code
    field.setText(code)
    widget.altitude.setValue(287.25)
    widget.submit()
    location = bridge.requests[-1][2]
    resolved = decode_mgrs(code) if kind == "mgrs" else decode_plus_code(code)
    assert location["lat_deg"] == resolved.latitude and location["lon_deg"] == resolved.longitude
    assert location["alt_m"] == 287.25 and location["alt_datum"] == "wgs84_ellipsoid"
    assert location["coordinate_input"] == {"format": kind, "code": code}
    bridge.configuration_finished.emit(widget._pending, {"ok": True, "station": "gs1", "location": location})
    assert "287.25" in widget.status.text()
    assert f"{resolved.latitude:.7f}" in widget.status.text()


def test_invalid_location_never_sends_and_rejection_remains_visible(dialog):
    widget, bridge = dialog
    widget.update_location.setChecked(True)
    widget.location.format.setCurrentIndex(widget.location.format.findData("mgrs"))
    widget.location.mgrs.setText("invalid")
    widget.submit()
    assert not bridge.requests
    assert "Not sent" in widget.status.text()
    widget.refresh()
    assert "Not sent" in widget.status.text()


def test_failed_remote_ack_does_not_become_applied_mapping(dialog):
    widget, bridge = dialog
    widget.submit()
    bridge.configuration_finished.emit(widget._pending, {"ok": False, "error": "Target restarted; verify boot identity"})
    assert "Not applied" in widget.status.text() and "Target restarted" in widget.status.text()
    assert not bridge.station_assignments


def test_conflicts_are_explicit_and_peer_reordering_does_not_change_target(dialog):
    widget, bridge = dialog
    target_node = bridge.peers[0]["info"]["node_id"]
    bridge.peers.append(peer("gs2", "telem"))
    widget.refresh()
    widget.station.setCurrentIndex(widget.station.findData("gs2"))
    assert widget.send.isEnabled()
    assert "duplicate GS/role" in widget.status.text() and "Devices are never merged" in widget.status.text()
    widget.station.setCurrentIndex(widget.station.findData("gs3"))
    assert widget.send.isEnabled()
    bridge.peers.reverse()
    widget.refresh()
    assert widget.selected_peer()["info"]["node_id"] == target_node
    widget.submit()
    assert bridge.requests[-1][0]["info"]["node_id"] == target_node


def test_local_base_and_matching_mission_are_required(dialog):
    widget, bridge = dialog
    bridge.identity["station"] = "gs1"
    widget.refresh()
    assert not widget.send.isEnabled() and "Base Station" in widget.status.text()
    bridge.identity["station"] = "launch"
    bridge.identity["mission_id"] = None
    widget.refresh()
    assert not widget.send.isEnabled() and "mission" in widget.status.text()
    bridge.identity["mission_id"] = str(uuid.uuid4())
    widget.refresh()
    assert not widget.send.isEnabled() and "same mission" in widget.status.text()


def test_settings_has_explicit_network_and_assignments_buttons(qtbot, tmp_path):
    opened = []
    widget = SettingsDialog(AppSettings(), tmp_path, lambda settings: None,
                            open_network=lambda: opened.append("network"),
                            open_station_assignments=lambda: opened.append("assignments"))
    qtbot.addWidget(widget)
    widget.network_button.click()
    widget.assignments_button.click()
    assert opened == ["network", "assignments"]
