"""Explicit per-device station assignments and acknowledged remote locations."""

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from .domain import Mission
from .location_ui import LaunchLocation
from .site_presets import URRG_STATION_MGRS
from .widgets import ComboBox


class StationLocationEditor(LaunchLocation):
    """Reuse established offline conversions without the launch-site preset."""

    def __init__(self, parent=None):
        super().__init__(Mission(), parent)
        self.preset.hide()
        self.layout().itemAt(1).widget().hide()
        self.format.setAccessibleName("Station location coordinate format")
        self.latitude.setAccessibleName("Station latitude in degrees")
        self.longitude.setAccessibleName("Station longitude in degrees")
        self.mgrs.setAccessibleName("Station MGRS coordinate")
        self.plus_code.setAccessibleName("Station Plus Code")
        self.preview.setAccessibleName("Resolved station coordinates")

    def update_preview(self):
        super().update_preview()
        self.preview.setText(self.preview.text().replace("Launch site:", "Station location:"))


class StationAssignmentsDialog(QDialog):
    """A device is always identified by its node UUID, never only a GS label."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setWindowTitle("Station assignments & locations")
        self.resize(1000, 800)
        self._rows = []
        self._pending = None
        self._pending_target = None
        self._selection = None
        self._completed_text = ""
        outer = QVBoxLayout(self)
        description = QLabel(
            "Select one discovered computer by its node ID. Telemetry and Video computers are "
            "configured independently, even when they belong to the same away station."
        )
        description.setWordWrap(True)
        outer.addWidget(description)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Device / node ID", "Bonjour / network address", "Station", "Role", "Connection"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(170)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        outer.addWidget(self.table, 1)
        self.target = QLabel("Select a discovered away-station computer")
        self.target.setTextFormat(Qt.TextFormat.PlainText)
        self.target.setWordWrap(True)
        outer.addWidget(self.target)
        form = QFormLayout()
        self.station = ComboBox()
        for index in range(1, 5):
            self.station.addItem(f"GS{index} · Away Station {index}", f"gs{index}")
        self.station.setAccessibleName("Assign selected computer to ground station")
        form.addRow("Assigned station", self.station)
        self.update_location = QCheckBox("Update station location")
        form.addRow(self.update_location)
        self.location = StationLocationEditor()
        form.addRow(self.location)
        self.altitude = QDoubleSpinBox()
        self.altitude.setRange(-1000, 100000)
        self.altitude.setDecimals(2)
        self.altitude.setSuffix(" m")
        self.altitude.setAccessibleName("Station altitude above WGS84 ellipsoid in metres")
        form.addRow("WGS84 ellipsoid altitude", self.altitude)
        preset = QPushButton("Use URRG location for selected GS")
        preset.clicked.connect(self.use_urrg)
        form.addRow(preset)
        outer.addLayout(form)
        note = QLabel(
            "Changing the station number restarts the remote view and disconnects its local devices. "
            "A location-only update pauses pointer tracking. Coordinate conversion sets latitude and "
            "longitude; enter the ellipsoid altitude separately."
        )
        note.setWordWrap(True)
        outer.addWidget(note)
        self.send = QPushButton("Send to selected station")
        self.send.clicked.connect(self.submit)
        outer.addWidget(self.send)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.update_location.toggled.connect(self.enable_location)
        self.enable_location(False)
        self.station.currentIndexChanged.connect(self.update_send_state)
        bridge.configuration_finished.connect(self.configuration_finished)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    @staticmethod
    def peer_key(peer):
        return (peer.get("info", {}).get("node_id"), peer.get("endpoint"))

    def selected_peer(self):
        row = self.table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def refresh(self):
        selected = self._selection
        self._rows = [peer for peer in self.bridge.peers if isinstance(peer.get("info"), dict)]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._rows))
        selected_row = -1
        for row, peer in enumerate(self._rows):
            info = peer["info"]
            node = info.get("node_id", "Unknown node")
            role = {"telem": "Telemetry", "video": "Video"}.get(info.get("role"), "Unknown")
            values = [node, peer.get("endpoint", ""), info.get("station", "?").upper(), role,
                      "CONFLICT · select a different GS" if peer.get("conflict") else peer.get("state", "Unknown")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(f"Node {node}\n{peer.get('error') or peer.get('endpoint', '')}")
                self.table.setItem(row, column, item)
            if self.peer_key(peer) == selected:
                selected_row = row
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        else:
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        self.update_send_state()

    def selection_changed(self):
        if self._pending:
            return
        peer = self.selected_peer()
        self._selection = self.peer_key(peer) if peer else None
        self.update_location.setChecked(False)
        if peer:
            info = peer["info"]
            station = info.get("station")
            mappings = getattr(self.bridge, "station_assignments", {})
            assigned = mappings.get(info.get("node_id"))
            if isinstance(assigned, dict):
                assigned = assigned.get("station")
            self.station.setCurrentIndex(max(0, self.station.findData(assigned or station)))
            self.target.setText(f"Selected node: {info.get('node_id')}\n{peer.get('endpoint')} · {info.get('role')} · currently {station}")
        else:
            self.target.setText("Select a discovered away-station computer")
        self.update_send_state()

    def enable_location(self, enabled):
        self.location.setEnabled(enabled and not bool(self._pending))
        self.altitude.setEnabled(enabled and not bool(self._pending))

    def use_urrg(self):
        index = self.station.currentData()[-1]
        code = URRG_STATION_MGRS[f"away{index}"]
        self.update_location.setChecked(True)
        self.location.format.setCurrentIndex(self.location.format.findData("mgrs"))
        self.location.mgrs.setText(code)

    def blocked_reason(self):
        if self._pending:
            return "Waiting for the selected station to apply this request…"
        info = self.bridge.runtime.info()
        if info.get("station") != "launch":
            return "Assign stations and send locations from a Base Station computer."
        if not info.get("mission_id"):
            return "Join or create a station-network mission first."
        peer = self.selected_peer()
        if not peer:
            return "Select one discovered away-station computer."
        target = peer.get("info", {})
        if target.get("station") not in {"gs1", "gs2", "gs3", "gs4"}:
            return "The selected computer is not an away station."
        if target.get("source_mode") != "live":
            return "The selected station must be running live."
        if target.get("mission_id") != info["mission_id"]:
            return "Both computers must join the same mission before configuration can be sent."
        if peer.get("state") in {"OFFLINE", "UNREACHABLE"}:
            return "The selected station is offline."
        return ""

    def conflict_warning(self):
        target = (self.selected_peer() or {}).get("info", {})
        for other in self._rows:
            other_info = other.get("info", {})
            if (other_info.get("node_id") != target.get("node_id")
                    and other_info.get("station") == self.station.currentData()
                    and other_info.get("role") == target.get("role")
                    and other.get("state") not in {"OFFLINE", "UNREACHABLE"}):
                return ("This assignment creates a duplicate GS/role; data from conflicting publishers "
                        "is blocked until the other device is reassigned. Devices are never merged.")
        return ""

    def update_send_state(self):
        reason = self.blocked_reason()
        self.send.setEnabled(not reason)
        self.send.setToolTip(reason)
        self.table.setEnabled(not bool(self._pending))
        self.station.setEnabled(not bool(self._pending))
        self.update_location.setEnabled(not bool(self._pending))
        self.enable_location(self.update_location.isChecked())
        if reason:
            self.status.setText(reason if not self._completed_text else self._completed_text + "\n" + reason)
        else:
            status = self._completed_text or "Ready. The mapping is saved only after the selected device confirms it applied the request."
            warning = self.conflict_warning()
            self.status.setText(status + ("\n" + warning if warning else ""))

    def submit(self):
        if self.blocked_reason():
            self.update_send_state()
            return
        peer = self.selected_peer()
        location = None
        try:
            if self.update_location.isChecked():
                resolved = self.location.value()
                location = {
                    "lat_deg": resolved.latitude, "lon_deg": resolved.longitude,
                    "alt_m": self.altitude.value(), "alt_datum": "wgs84_ellipsoid",
                    "label": f"{self.station.currentText().split(' · ')[0]} station location",
                    "coordinate_input": {"format": self.location.format.currentData(), "code": resolved.code},
                }
            station = self.station.currentData()
            self._pending_target = (peer["info"]["node_id"], station)
            self._pending = self.bridge.request_remote_configuration(peer, station, location)
        except (ValueError, OSError, RuntimeError) as exc:
            self._pending_target = None
            self._completed_text = f"Not sent: {exc}"
            self.status.setText(self._completed_text)
            return
        self._completed_text = ""
        self.update_send_state()

    def configuration_finished(self, request_id, result):
        if request_id != self._pending:
            return
        target = self._pending_target
        self._pending = self._pending_target = None
        if not isinstance(result, dict) or not (result.get("ok") is True or result.get("state") == "applied"):
            error = result.get("error", "Request did not receive an applied acknowledgement") if isinstance(result, dict) else str(result)
            self._completed_text = f"Not applied: {error}"
        else:
            config = result.get("configuration") or result.get("result") or result
            if "configuration" in config:
                config = config["configuration"]
            station = config.get("station", target[1])
            self._completed_text = f"Applied by {target[0]} · {station.upper()}"
            location = config.get("location")
            if isinstance(location, dict):
                self._completed_text += (f"\n{location['lat_deg']:.7f}°, {location['lon_deg']:.7f}°"
                                         f" · {location['alt_m']:.2f} m above WGS84 ellipsoid")
        self.status.setText(self._completed_text)
        self.update_send_state()

    def done(self, result):
        self.timer.stop()
        try:
            self.bridge.configuration_finished.disconnect(self.configuration_finished)
        except (RuntimeError, TypeError):
            pass
        super().done(result)
