"""Operator controls for joining an explicit station-network mission."""
import json
import ipaddress
from collections import Counter

import ifaddr

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from .network.missions import make_invite, read_invite
from .table_cells import set_cell


SOURCE_HELP = ("Select the telemetry feed for the rocket view, state, tables and plots. "
    "Network feeds do not require serial polling. Best connection checks once per second: "
    "Rocket packet freshness first (full quality at 60 ms or less), then LTU-XR round-trip time. "
    "Unavailable or stale feeds are not selected; manual choices stay fixed.")


def refresh_source_selector(combo, bridge):
    options = bridge.source_options()
    previous = combo.blockSignals(True)
    if options != [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]:
        combo.clear()
        for title, value in options:
            combo.addItem(title, value)
    # The selection can change independently of the list (join or other menu).
    combo.setCurrentIndex(max(0, combo.findData(bridge.source_selection)))
    combo.blockSignals(previous)
    combo.setToolTip(SOURCE_HELP)


class StationNetworkDialog(QDialog):
    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setWindowTitle("Missions")
        self.resize(820, 580)
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        mission_page, network_page = QWidget(), QWidget()
        tabs.addTab(mission_page, "Missions")
        tabs.addTab(network_page, "Network details")
        main = QVBoxLayout(mission_page)
        self.membership = QLabel()
        self.membership.setTextFormat(Qt.TextFormat.PlainText)
        main.addWidget(self.membership)
        self.create_row = QWidget()
        form = QFormLayout(self.create_row)
        self.title = QLineEdit()
        self.title.setPlaceholderText("e.g. Balius Ground Test")
        self.title.setMaxLength(64)
        create_row = QHBoxLayout()
        create_row.addWidget(self.title)
        self.create = QPushButton("Create mission")
        self.create.clicked.connect(self.new_mission)
        self.title.returnPressed.connect(self.new_mission)
        self.title.textChanged.connect(lambda value: self.create.setEnabled(bool(value.strip())))
        self.create.setEnabled(False)
        create_row.addWidget(self.create)
        form.addRow("Mission title", create_row)
        self.reopen = QPushButton("Reopen last mission")
        self.reopen.clicked.connect(self.reopen_mission)
        form.addRow(self.reopen)
        main.addWidget(self.create_row)

        self.browse = QWidget()
        browse = QFormLayout(self.browse)
        self.nearby = QComboBox()
        self.nearby.setAccessibleName("Nearby Base missions")
        self.join = QPushButton("Join mission")
        self.join.clicked.connect(self.join_mission)
        choice = QHBoxLayout()
        choice.addWidget(self.nearby, 1)
        choice.addWidget(self.join)
        browse.addRow("Nearby missions", choice)
        self.invite = QLineEdit()
        self.invite.setPlaceholderText("Paste an invite from Base")
        self.invite.setMaxLength(4096)
        self.lookup = QPushButton("Find mission")
        self.lookup.clicked.connect(self.find_invite)
        self.invite.returnPressed.connect(self.find_invite)
        self.invite.textEdited.connect(self.clear_invite)
        invite_row = QHBoxLayout()
        invite_row.addWidget(self.invite, 1)
        invite_row.addWidget(self.lookup)
        browse.addRow("Mission invite", invite_row)
        self.invited = None
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        browse.addRow(self.message)
        main.addWidget(self.browse)

        active = QHBoxLayout()
        self.copy = QPushButton("Copy invite")
        self.copy.clicked.connect(self.copy_invite)
        self.leave = QPushButton("Leave mission")
        self.leave.clicked.connect(self.leave_mission)
        active.addWidget(self.copy)
        active.addWidget(self.leave)
        active.addStretch()
        main.addLayout(active)
        main.addStretch()
        details_layout = QVBoxLayout(network_page)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        details_layout.addWidget(self.status)
        form = QFormLayout()
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
        self.source.setAccessibleName("Telemetry display source")
        self.source.setToolTip(SOURCE_HELP)
        self.source.currentIndexChanged.connect(self.source_changed)
        form.addRow("Telemetry display source", self.source)
        details_layout.addLayout(form)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Station / role", "Application", "Mission", "RTT ms", "Rocket age ms ≥", "Address / details"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.itemSelectionChanged.connect(self.show_details)
        outer.addWidget(self.table, 1)
        self.legacy_join = QPushButton("Join selected simulation or replay")
        self.legacy_join.clicked.connect(self.join_selected)
        self.legacy_join.setVisible(bridge.window.profile.station == "base")
        details_layout.addWidget(self.legacy_join)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(135)
        details_layout.addWidget(self.details)
        self.archive = QLabel()
        self.archive.setWordWrap(True)
        details_layout.addWidget(self.archive)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.addresses = self.local_addresses()
        self._rows = []
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def new_mission(self):
        self.run_action(lambda: self.bridge.publish_mission(self.title.text()))

    def reopen_mission(self):
        recent = self.bridge.config["recent_mission"]
        self.run_action(lambda: self.bridge.publish_mission(recent["title"], reopen=True))

    def leave_mission(self):
        self.run_action(self.bridge.leave_mission)

    def run_action(self, action):
        try:
            action()
            self.endpoints.setText(", ".join(self.bridge.config["peers"]))
            self.message.clear()
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "Missions", str(exc))
        self.refresh()

    def local_addresses(self):
        runtime = self.bridge.runtime
        if runtime.bind_host not in {"::", "0.0.0.0"}:
            hosts = [runtime.bind_host]
        else:
            hosts = [ip.ip for adapter in ifaddr.get_adapters() for ip in adapter.ips
                     if isinstance(ip.ip, str) and not ipaddress.ip_address(ip.ip).is_loopback]
        return list(dict.fromkeys(f"http://[{host}]:{runtime.port}" if ":" in host
                                  else f"http://{host}:{runtime.port}" for host in hosts))[:16]

    def copy_invite(self):
        addresses = self.local_addresses()
        if not addresses:
            QMessageBox.warning(self, "Missions", "Connect Base to the station network first.")
            return
        runtime = self.bridge.runtime
        QApplication.clipboard().setText(make_invite(runtime.mission_id, runtime.node_id, addresses))
        self.copy.setText("Invite copied")

    def clear_invite(self):
        self.invited = None
        self.message.clear()
        self.refresh()

    def find_invite(self):
        try:
            mission, publisher, addresses = read_invite(self.invite.text())
            self.invited = (mission, publisher)
            for address in addresses:
                self.bridge.runtime.add_endpoint(address)
        except ValueError as exc:
            self.invited = None
            self.message.setText(str(exc))
        self.refresh()

    def join_mission(self):
        key = self.nearby.currentData()
        if key:
            self.run_action(lambda: self.bridge.join_publication(tuple(key)))

    def refresh_missions(self):
        bridge = self.bridge
        runtime, config = bridge.runtime, bridge.config
        active = bool(runtime.mission_id)
        owner = bool(runtime.published_title)
        base = bridge.window.profile.station == "base"
        live = bridge.controller.mode == "LIVE"
        self.create_row.setVisible(base and live and not active)
        recent = config.get("recent_mission", {})
        self.reopen.setVisible(recent.get("publisher") == runtime.node_id
                               and recent.get("vehicle") == bridge.window.profile.vehicle)
        self.reopen.setText(f"Reopen {recent.get('title', '')}")
        self.browse.setVisible(live and not active)
        self.copy.setVisible(owner)
        self.copy.setEnabled(runtime.state == "LISTENING")
        self.leave.setVisible(active)
        if not active:
            self.copy.setText("Copy invite")
        publications = bridge.published_missions()
        if active:
            if owner:
                status = "Publishing" if runtime.state == "LISTENING" else runtime.state.title()
            elif config.get("mission_publisher"):
                peer = publications.get((runtime.mission_id, config["mission_publisher"]), {})
                status = "Connected to Base" if peer.get("synchronized") else "Waiting for Base"
            else:
                status = config.get("receive_source_mode", "live").title()
            self.membership.setText(f"{config.get('mission_title') or runtime.mission_id[:8]} · {status}")
            self.invited = None
            self.invite.clear()
            return
        self.membership.setText("Choose a mission" if live else "Switch to Live to join a Base mission")
        candidates = {k: p for k, p in publications.items() if not self.invited or k == self.invited}
        titles = Counter(p["info"]["published_mission_title"] for p in candidates.values())
        options = []
        for key, peer in sorted(candidates.items(), key=lambda kv: (kv[1]["info"]["published_mission_title"], kv[0])):
            info = peer["info"]
            title = info["published_mission_title"]
            suffix = f" · {key[0][:8]}" if titles[title] > 1 else ""
            again = " · Last joined" if recent.get("id") == key[0] and recent.get("publisher") == key[1] else ""
            options.append((f"{title} · {info['vehicle_mode'].title()} · Base {info['role']}{suffix}{again}", key))
        selected = self.nearby.currentData()
        if options != [(self.nearby.itemText(i), self.nearby.itemData(i)) for i in range(self.nearby.count())]:
            self.nearby.clear()
            for label, key in options:
                self.nearby.addItem(label, key)
            self.nearby.setCurrentIndex(next((i for i, (_, k) in enumerate(options) if k == selected),
                                             0 if len(options) == 1 else -1))
        self.nearby.setPlaceholderText("Looking for Base missions…" if not options else "Select a mission")
        self.join.setEnabled(bool(self.nearby.currentData()))
        if self.invited:
            self.message.setText("Invite found. Select Join mission." if candidates else
                                 "Looking for the invited Base. Check that it is publishing and reachable on this LAN.")

    def apply(self):
        try:
            self.bridge.apply({**self.bridge.config,
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
        if (mission and self.bridge.window.profile.station == "base"
                and peer["info"].get("source_mode") in {"simulation", "replay"}):
            try:
                self.bridge.join_peer(peer)
                self.endpoints.setText(", ".join(self.bridge.config["peers"]))
            except (ValueError, OSError, RuntimeError) as exc:
                QMessageBox.warning(self, "Station network", str(exc))
            self.refresh()

    def show_details(self):
        peer = self.selected_peer()
        self.legacy_join.setEnabled(bool(peer and (peer.get("info") or {}).get("mission_id")
                                         and peer["info"].get("source_mode") in {"simulation", "replay"}))
        if peer:
            # Latest raw telemetry is retained in the archive, not duplicated in this small status view.
            details = {key: value for key, value in peer.items() if key != "latest"}
            self.details.setPlainText(json.dumps(details, indent=2))
        else:
            self.details.clear()

    def refresh(self):
        bridge = self.bridge
        self.refresh_missions()
        info = bridge.runtime.info()
        self.status.setText(f"{bridge.window.profile.station_label} · {bridge.window.profile.role.title()}\n"
            f"{bridge.summary()}\nThis computer: "
            + ", ".join(self.addresses)
            + f" · Node {info['node_id']}")
        selected = (self.selected_peer() or {}).get("endpoint")
        self._rows = bridge.peers
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._rows))
        for row, peer in enumerate(self._rows):
            identity = peer.get("info") or {}
            station = identity.get("station", "?")
            title = "Base" if station == "launch" else station.upper()
            role = identity.get("role", "?")
            ages = [p["sample_age_ms_lower_bound"] for p in peer.get("latest", []) if p.get("sample_age_ms_lower_bound") is not None]
            mode = identity.get("source_mode", "live").upper()
            phase = (peer.get("status") or {}).get("application", {}).get("state", "")
            values = [f"{title} / {role} · {mode}", peer["state"] + (f" · {phase}" if mode == "SIMULATION" else ""), (identity.get("mission_id") or "unassigned")[:8],
                      f"{peer['rtt_ms']:.1f}" if peer.get("rtt_ms") is not None else "—",
                      f"{min(ages):.0f}" if ages else "—", peer.get("error") or peer["endpoint"]]
            for column, value in enumerate(values):
                item = set_cell(self.table, row, column, value)
                item.setToolTip(peer.get("error") or str(identity))
        if selected is not None:
            index = next((i for i, peer in enumerate(self._rows) if peer["endpoint"] == selected), -1)
            self.table.setCurrentCell(index, 0)
        self.table.blockSignals(False)
        refresh_source_selector(self.source, bridge)
        self.source.setEnabled(bridge.window.profile.role == "telemetry")
        archive = bridge.runtime.archive_status
        self.archive.setText(f"Observation archive: {archive.get('error') or archive.get('path') or 'starting'}"
                             f" · dropped {archive.get('dropped', 0)} · network queue losses {bridge.runtime.ingress_dropped}")
        self.show_details()
