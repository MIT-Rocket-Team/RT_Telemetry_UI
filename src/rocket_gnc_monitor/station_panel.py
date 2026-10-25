"""Compact board-layout companion: local APRS and explicitly connected LAN peer.

Instantiate ``StationNetworkPanel(controller)``; call ``refresh()`` when desired
and ``shutdown()`` before controller teardown. A 500 ms timer also updates it.
``remote_snapshot`` is read-only display data, never controller telemetry.
Preferences live in station_network.json; connections never restart on launch.
"""

from __future__ import annotations

import json
import socket
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget

from .domain import write_json
from .station_io import APRSReceiver, StationLink, controller_snapshot


def _label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _field(value, placeholder, tip, width=None):
    field = QLineEdit(value)
    field.setPlaceholderText(placeholder)
    field.setToolTip(tip)
    field.setMaxLength(253)
    field.setMinimumWidth(35)
    if width:
        field.setMaximumWidth(width)
    return field


def _port(value):
    field = QSpinBox()
    field.setRange(1, 65535)
    field.setValue(value)
    field.setMaximumWidth(79)
    field.setToolTip("TCP port")
    return field


class StationNetworkPanel(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.aprs = APRSReceiver()
        self.lan = StationLink()
        self.preferences_path = controller.data_dir / "station_network.json"
        self.preference_error = ""
        self.remote_snapshot = None
        self._closed = False
        self._last_publish = 0
        prefs = self._load_preferences()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(4)
        heading = _label("STATION NETWORK · OFFLINE LAN")
        heading.setObjectName("section")
        outer.addWidget(heading)

        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(_label("APRS"))
        self.aprs_host = _field(
            prefs["aprs_host"], "Dire Wolf host", "Dire Wolf KISS TCP host; audio is decoded by Dire Wolf"
        )
        self.aprs_port = _port(prefs["aprs_port"])
        self.aprs_callsign = _field(
            prefs["aprs_callsign"],
            "Callsign",
            "Optional exact callsign / SSID filter; empty shows any station",
            90,
        )
        self.aprs_callsign.setMaxLength(10)
        self.aprs_button = QPushButton("Connect")
        self.aprs_button.clicked.connect(self.toggle_aprs)
        for widget in (self.aprs_host, self.aprs_port, self.aprs_callsign, self.aprs_button):
            row.addWidget(widget)
        outer.addLayout(row)
        self.aprs_status = _label()
        self.aprs_status.setMinimumHeight(30)
        outer.addWidget(self.aprs_status)

        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(_label("LAN"))
        self.station_name = _field(
            prefs["station_name"], "Station name", "Name shared with the other station", 90
        )
        self.station_name.setMaxLength(80)
        self.peer_host = _field(
            prefs["peer_host"], "Peer IP", "Connect to the listening station's local-network IP address"
        )
        self.peer_port = _port(prefs["peer_port"])
        self.listen_button = QPushButton("Listen")
        self.listen_button.setToolTip(
            "Listen on all local IPv4 interfaces at this port; one read-only peer at a time"
        )
        self.listen_button.clicked.connect(self.listen)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.connect_peer)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_peer)
        for widget in (
            self.station_name,
            self.peer_host,
            self.peer_port,
            self.listen_button,
            self.connect_button,
            self.stop_button,
        ):
            row.addWidget(widget)
        outer.addLayout(row)
        self.peer_status = _label()
        self.peer_status.setMinimumHeight(30)
        outer.addWidget(self.peer_status)
        self.topology = _label(
            "USB: telemetry PCB + antenna PCB · Digital / Analog USB video receivers\n"
            "LTU-XR via Ethernet / Wi-Fi AP · LTU metrics and analog VRX control: protocol needed"
        )
        self.topology.setToolTip(
            "The physical USB camera previews and serial controls remain independent. "
            "Dire Wolf is an external audio modem. LAN sharing carries status and telemetry only; "
            "it does not carry video, audio calls or commands. No LTU or VRX control protocol was supplied."
        )
        outer.addWidget(self.topology)
        self.error = _label(self.preference_error)
        self.error.setVisible(bool(self.preference_error))
        outer.addWidget(self.error)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def _load_preferences(self):
        defaults = {
            "aprs_host": "127.0.0.1",
            "aprs_port": 8001,
            "aprs_callsign": "",
            "station_name": socket.gethostname()[:80],
            "peer_host": "",
            "peer_port": 8765,
        }
        try:
            if self.preferences_path.exists():
                if self.preferences_path.stat().st_size > 8192:
                    raise ValueError("Station preferences file is too large")
                data = json.loads(self.preferences_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("Invalid station preferences")
                for key, value in defaults.items():
                    saved = data.get(key, value)
                    if type(saved) is not type(value):
                        raise ValueError("Invalid station preference type")
                    if isinstance(saved, str) and (len(saved) > 253 or any(ord(c) < 32 for c in saved)):
                        raise ValueError("Invalid station preference text")
                    if isinstance(saved, int) and not 1 <= saved <= 65535:
                        raise ValueError("Invalid station port")
                    defaults[key] = saved
        except (OSError, ValueError) as exc:
            self.preference_error = str(exc)
        return defaults

    def _save_preferences(self):
        write_json(
            self.preferences_path,
            {
                "aprs_host": self.aprs_host.text().strip(),
                "aprs_port": self.aprs_port.value(),
                "aprs_callsign": self.aprs_callsign.text().strip().upper(),
                "station_name": self.station_name.text().strip(),
                "peer_host": self.peer_host.text().strip(),
                "peer_port": self.peer_port.value(),
            },
        )

    def _action(self, operation):
        try:
            self._save_preferences()
            operation()
            self.error.clear()
            self.error.hide()
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc))
            self.error.show()
        self.refresh()

    def toggle_aprs(self):
        if self.aprs.active:
            self.aprs.stop()
            self.refresh()
        else:
            self._action(
                lambda: self.aprs.start(
                    self.aprs_host.text().strip(), self.aprs_port.value(), self.aprs_callsign.text()
                )
            )

    def listen(self):
        self._action(lambda: self.lan.listen(port=self.peer_port.value()))

    def connect_peer(self):
        self._action(lambda: self.lan.connect(self.peer_host.text().strip(), self.peer_port.value()))

    def stop_peer(self):
        self.lan.stop()
        self.refresh()

    def refresh(self):
        if self._closed:
            return
        aprs = self.aprs.snapshot()
        fix = aprs["latest"]
        aprs_connected = aprs["state"] == "Connected"
        self.aprs_button.setText("Disconnect" if self.aprs.active else "Connect")
        for field in (self.aprs_host, self.aprs_port, self.aprs_callsign):
            field.setEnabled(not self.aprs.active)
        if fix:
            age = max(0, time.monotonic() - fix.received)
            altitude = f" · {fix.altitude_m:.0f} m" if fix.altitude_m is not None else ""
            prefix = "APRS" if aprs_connected else "LAST APRS · disconnected"
            self.aprs_status.setText(
                f"{prefix} · {fix.callsign} · {fix.latitude:.5f}, {fix.longitude:.5f}{altitude} · {age:.0f}s old"
            )
        else:
            detail = aprs["error"] or (
                f"{aprs['frames']} packets · awaiting an uncompressed position"
                if aprs_connected
                else "Dire Wolf audio modem → KISS TCP · receive only"
            )
            self.aprs_status.setText(f"{aprs['state']} · {detail}")
        now = time.monotonic()
        if now - self._last_publish >= 0.4:
            try:
                # A disconnected APRS feed must not be advertised as a current fix.
                self.lan.publish(
                    controller_snapshot(
                        self.c, self.station_name.text().strip(), fix if aprs_connected else None
                    )
                )
            except ValueError as exc:
                self.error.setText(str(exc))
                self.error.show()
            self._last_publish = now
        lan = self.lan.snapshot()
        self.remote_snapshot = lan["remote"] if lan["state"] == "Connected" else None
        for widget in (self.peer_host, self.peer_port, self.listen_button, self.connect_button):
            widget.setEnabled(not self.lan.active)
        self.stop_button.setEnabled(self.lan.active)
        peer = self.remote_snapshot
        if peer:
            sample = peer["telemetry"] or {}
            altitude = sample.get("altitude")
            altitude = f"{altitude:.1f} m" if altitude is not None else "—"
            age = peer["sample_age_s"]
            age = f" · sample {(age + lan['age_s']):.1f}s old" if age is not None else ""
            self.peer_status.setText(
                f"PEER · {peer['name']} · {peer['mode']} · {peer['links'].get('rocket', 'Unknown')} · read only\n"
                f"Altitude {altitude} · {sample.get('phase', 'No telemetry')}{age}"
            )
        elif lan["state"] == "Listening":
            self.peer_status.setText(f"Listening on port {lan['port']} · awaiting station · read only")
        else:
            detail = lan["error"] or "Listen here, then connect from the other station · read only"
            self.peer_status.setText(f"{lan['state']} · {detail}")

    def shutdown(self):
        self._closed = True
        self.timer.stop()
        self.aprs.stop()
        self.lan.stop()
