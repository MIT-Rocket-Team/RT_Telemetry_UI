"""Operator controls for joining an explicit station-network mission."""
import json
import uuid

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout)


class StationNetworkDialog(QDialog):
    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setWindowTitle("Station network")
        self.resize(1050, 730)
        outer = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        form = QFormLayout()
        self.mission = QLineEdit(bridge.config["mission_id"] or "")
        self.mission.setPlaceholderText("Create a mission here, or paste the UUID from another station")
        mission_row = QHBoxLayout()
        mission_row.addWidget(self.mission, 1)
        for title, callback in (("New mission", self.new_mission), ("Copy", self.copy_mission)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            mission_row.addWidget(button)
        form.addRow("Mission UUID", mission_row)
        hint = QLabel("Use the same mission UUID on all ten computers. A matching mission name or Wi-Fi network is not enough. Mission UUID is not remembered on restart.")
        hint.setWordWrap(True)
        form.addRow(hint)
        address = QHBoxLayout()
        self.host = QLineEdit(bridge.config["network_bind_host"])
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(bridge.config["network_port"])
        address.addWidget(self.host, 1)
        address.addWidget(self.port)
        form.addRow("Listen address / port", address)
        self.discovery = QCheckBox("Discover stations on this local network")
        self.discovery.setChecked(bridge.config["network_discovery"])
        form.addRow(self.discovery)
        self.endpoints = QLineEdit(", ".join(bridge.config["peers"]))
        self.endpoints.setPlaceholderText("http://192.168.1.10:8766, http://[IPv6-address]:8766")
        form.addRow("Manual peer addresses", self.endpoints)
        self.apply_button = QPushButton("Apply network configuration")
        self.apply_button.clicked.connect(self.apply)
        form.addRow(self.apply_button)
        self.source = QComboBox()
        self.source.currentIndexChanged.connect(self.source_changed)
        form.addRow("Telemetry display source", self.source)
        outer.addLayout(form)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Station / role", "Application", "Mission", "RTT ms", "Rocket age ms ≥", "Address / details"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.itemSelectionChanged.connect(self.show_details)
        outer.addWidget(self.table, 1)
        join = QPushButton("Join selected station's mission")
        join.clicked.connect(self.join_selected)
        outer.addWidget(join)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(135)
        outer.addWidget(self.details)
        self.archive = QLabel()
        self.archive.setWordWrap(True)
        outer.addWidget(self.archive)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._rows = []
        self._source_options = None
        import ifaddr
        self.addresses = [ip.ip for adapter in ifaddr.get_adapters() for ip in adapter.ips
                          if isinstance(ip.ip, str) and not ip.ip.startswith("127.")]
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def new_mission(self):
        self.mission.setText(str(uuid.uuid4()))

    def copy_mission(self):
        QApplication.clipboard().setText(self.mission.text().strip())

    def apply(self):
        try:
            self.bridge.apply({**self.bridge.config, "mission_id": self.mission.text().strip() or None,
                "network_bind_host": self.host.text().strip(), "network_port": self.port.value(),
                "network_discovery": self.discovery.isChecked(),
                "peers": [value.strip() for value in self.endpoints.text().split(",") if value.strip()]})
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "Station network", str(exc))
        self.refresh()

    def source_changed(self):
        self.bridge.select_source(self.source.currentData())

    def selected_peer(self):
        row = self.table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def join_selected(self):
        peer = self.selected_peer()
        mission = (peer or {}).get("info", {}).get("mission_id")
        if mission:
            self.mission.setText(mission)
            self.apply()

    def show_details(self):
        peer = self.selected_peer()
        if peer:
            # Latest raw telemetry is retained in the archive, not duplicated in this small status view.
            details = {key: value for key, value in peer.items() if key != "latest"}
            self.details.setPlainText(json.dumps(details, indent=2))

    def refresh(self):
        bridge = self.bridge
        info = bridge.runtime.info()
        self.status.setText(f"{bridge.window.profile.station_label} · {bridge.window.profile.role.title()}\n"
            f"{bridge.summary()}\nThis computer: "
            + ", ".join(f"http://{ip}:{bridge.runtime.port}" for ip in self.addresses)
            + f" · Node {info['node_id']}")
        self._rows = bridge.peers
        selected = self.table.currentRow()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._rows))
        options = [("Local telemetry board", None)]
        for row, peer in enumerate(self._rows):
            identity = peer.get("info") or {}
            station = identity.get("station", "?")
            title = "Base" if station == "launch" else station.upper()
            role = identity.get("role", "?")
            ages = [p["sample_age_ms_lower_bound"] for p in peer.get("latest", []) if p.get("sample_age_ms_lower_bound") is not None]
            values = [f"{title} / {role}", peer["state"], (identity.get("mission_id") or "unassigned")[:8],
                      f"{peer['rtt_ms']:.1f}" if peer.get("rtt_ms") is not None else "—",
                      f"{min(ages):.0f}" if ages else "—", peer.get("error") or peer["endpoint"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(peer.get("error") or str(identity))
                self.table.setItem(row, column, item)
            if role == "telem" and peer.get("synchronized") and not peer.get("conflict"):
                options.append((f"{title} · network · READ ONLY", identity["node_id"]))
        if selected >= 0 and selected < len(self._rows):
            self.table.selectRow(selected)
        self.table.blockSignals(False)
        chosen = bridge.controller.remote_display_key
        if chosen and chosen not in [value for _, value in options]:
            options.append(("Selected remote source · unavailable · READ ONLY", chosen))
        if options != self._source_options:
            self.source.blockSignals(True)
            self.source.clear()
            for title, value in options:
                self.source.addItem(title, value)
            self.source.setCurrentIndex(max(0, self.source.findData(chosen)))
            self.source.blockSignals(False)
            self._source_options = options
        self.source.setEnabled(bridge.window.profile.role == "telemetry")
        archive = bridge.runtime.archive_status
        self.archive.setText(f"Observation archive: {archive.get('error') or archive.get('path') or 'starting'}"
                             f" · dropped {archive.get('dropped', 0)} · network queue losses {bridge.runtime.ingress_dropped}")
        self.show_details()
