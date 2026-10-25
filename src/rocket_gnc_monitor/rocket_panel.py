"""Every operator control and telemetry table from the original Zephyrus UI."""

from __future__ import annotations
import time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFrame,
    QLabel,
    QPushButton,
    QLineEdit,
    QCheckBox,
    QTabWidget,
    QTableWidget,
    QHeaderView,
    QAbstractItemView,
    QMessageBox,
)
from .zephyrus import legacy_values
from .domain import finite
from .widgets import COLORS
from .table_cells import clear_cells, set_cell


def card(title):
    panel = QFrame()
    panel.setObjectName("card")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(16, 16, 16, 16)
    heading = QLabel(title)
    heading.setObjectName("section")
    layout.addWidget(heading)
    return panel, layout


def table(headers, rows=0):
    value = QTableWidget(rows, len(headers))
    value.setHorizontalHeaderLabels(headers)
    value.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    value.verticalHeader().hide()
    value.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    value.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    value.verticalHeader().setDefaultSectionSize(31)
    return value


def text(value):
    return "—" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


class RocketPanel(QWidget):
    def __init__(self, controller, guard):
        super().__init__()
        self.c, self.guard = controller, guard
        self.controls = []
        self.buttons = {}
        self.tabs = QTabWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.tabs)
        self.tabs.addTab(self.telemetry_page(), "Telemetry")
        self.tabs.addTab(self.commands_page(), "Rocket commands")
        self.tabs.addTab(self.power_page(), "Power")
        self.tabs.addTab(self.recovery_page(), "Recovery & pyros")
        self.status = QLabel("Ground station disconnected")
        outer.addWidget(self.status)

    def command_button(self, title, command, value=None, confirm=None, critical=False):
        button = QPushButton(title)
        button.clicked.connect(
            lambda: self.request(command, value() if callable(value) else value, confirm, critical)
        )
        self.controls.append(button)
        self.buttons[command] = button
        return button

    def request(self, command, value=None, confirm=None, critical=False):
        if confirm:
            prompt = confirm(value) if callable(confirm) else confirm
            function = QMessageBox.critical if critical else QMessageBox.question
            if (
                function(
                    self,
                    "Confirm " + self.buttons[command].text(),
                    prompt,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
        self.guard(lambda: self.c.send_rocket(command, value))

    def telemetry_page(self):
        page = QWidget()
        grid = QGridLayout(page)
        self.telemetry = table(["Field", "Value"])
        self.gps = table(["Field", "Value"])
        self.servos = table(["Servo", "Drive (µs)", "Angle (°)"], 4)
        self.cells = table(["Cell", "Voltage (V)"], 3)
        for widget in (self.servos, self.cells):
            widget.verticalHeader().setDefaultSectionSize(26)
        for title, widget, row, col in [
            ("Telemetry", self.telemetry, 0, 0),
            ("Rocket GPS", self.gps, 0, 1),
            ("Servos", self.servos, 1, 0),
            ("Cell voltages", self.cells, 1, 1),
        ]:
            panel, layout = card(title)
            layout.addWidget(widget)
            grid.addWidget(panel, row, col)
        grid.setRowStretch(0, 3)
        grid.setRowStretch(1, 2)
        return page

    def commands_page(self):
        page = QWidget()
        root = QVBoxLayout(page)
        panel, layout = card("Rocket commands")
        grid = QGridLayout()
        commands = [
            ("Advance State", "advance_state"),
            ("Zero P/Y/Roll", "zero_pitchYawRoll"),
            ("Zero Alt", "zero_alt"),
            ("Zero Velo", "zero_velo"),
            ("Zero Servos", "zero_servos"),
            ("PD Activate", "pd_activate"),
        ]
        for i, (title, name) in enumerate(commands):
            grid.addWidget(
                self.command_button(
                    title,
                    name,
                    confirm="Advance state will increment the onboard state machine. Are you sure?"
                    if name == "advance_state"
                    else None,
                ),
                i // 3,
                i % 3,
            )
        layout.addLayout(grid)
        root.addWidget(panel)
        panel, layout = card("Servo control")
        self.servo_inputs = {}
        for title, name in [
            ("Roll control angle (deg)", "set_roll_control_servo_angle"),
            ("Airbrakes angle (deg)", "set_airbrakes_angle"),
        ]:
            row = QHBoxLayout()
            row.addWidget(QLabel(title))
            field = QLineEdit("0.0")
            self.controls.append(field)
            self.servo_inputs[name] = field
            row.addWidget(field, 1)
            send = self.command_button("Set angle", name)
            send.clicked.disconnect()
            send.clicked.connect(
                lambda checked=False, n=name: self.guard(
                    lambda: self.c.send_rocket(n, float(self.servo_inputs[n].text()))
                )
            )
            row.addWidget(send)
            layout.addLayout(row)
        root.addWidget(panel)
        panel, layout = card("VTX power")
        row = QHBoxLayout()
        self.vtx_buttons = []
        for i, title in enumerate(["1 W", "3 W", "5 W", "8 W"]):
            control = self.command_button(title, "set_vtx_power", i)
            control.setCheckable(True)
            control.setAutoExclusive(True)
            control.setChecked(i == 0)
            self.vtx_buttons.append(control)
            row.addWidget(control)
        layout.addLayout(row)
        root.addWidget(panel)
        root.addStretch()
        return page

    def power_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        panel, column = card("Power rails")
        self.power = table(["Name", "Requested", "Enabled", "Voltage (V)", "Current (A)"], 8)
        self.power.verticalHeader().setDefaultSectionSize(25)
        self.rail_requests = []
        self.power_master = QCheckBox()
        self.power_master.setEnabled(False)
        self.power.setCellWidget(0, 1, self.power_master)
        for i, name in enumerate(["3 V", "3.3 V", "5 V", "7.4 V", "8.4 V", "28 V"]):
            set_cell(self.power, i + 1, 0, name)
            check = QCheckBox()
            check.setChecked(True)
            check.setEnabled(i != 1)
            self.rail_requests.append(check)
            self.power.setCellWidget(i + 1, 1, check)
            if i != 1:
                self.controls.append(check)
                check.clicked.connect(
                    lambda: self.guard(
                        lambda: self.c.send_rocket(
                            "update_converters", [v.isChecked() for v in self.rail_requests]
                        )
                    )
                )
        set_cell(self.power, 0, 0, "Total")
        set_cell(self.power, 7, 0, "Temperature (°C)")
        column.addWidget(self.power)
        layout.addWidget(panel, 3)
        panel, column = card("BMS protection status")
        self.protections = table(["SCD", "OCD2", "OCD1", "OCC", "COV", "CUV", "RSVD 1", "RSVD 0"], 2)
        self.protections.setVerticalHeaderLabels(["Enabled", "Triggered"])
        self.protections.verticalHeader().show()
        self.protections.verticalHeader().setDefaultSectionSize(25)
        self.protections.setMinimumHeight(87)
        panel.setMinimumHeight(151)
        column.addWidget(self.protections)
        layout.addWidget(panel, 1)
        return page

    def recovery_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        panel, column = card("Pyros")
        self.pyros = table(["Select", "#", "Status", "Armed / Fired", "Resistance (Ω)"], 6)
        self.pyro_selection = []
        for row in range(6):
            check = QCheckBox()
            self.pyro_selection.append(check)
            self.controls.append(check)
            self.pyros.setCellWidget(row, 0, check)
            set_cell(self.pyros, row, 1, str(row))
        column.addWidget(self.pyros)
        row = QHBoxLayout()
        for title, name in [("ARM", "arm_pyros"), ("FIRE", "fire_pyros")]:
            button = self.command_button(title, name)
            button.clicked.disconnect()
            button.clicked.connect(lambda checked=False, n=name, t=title: self.pyro_command(n, t))
            row.addWidget(button)
        column.addLayout(row)
        layout.addWidget(panel, 3)
        panel, column = card("Emergency recovery")
        row = QHBoxLayout()
        for title, command, prompt in [
            ("PISTON", "EMERG_DEPLOY_PISTON", "Deploy piston?"),
            ("BP WELLS", "EMERG_DEPLOY_BP_WELLS", "Fire black powder wells?"),
            ("TNDR DSNDR", "EMERG_DEPLOY_TD", "Deploy tender descender?"),
            (
                "FIRE ALL RECOVERY MEASURES",
                "EMERG_DEPLOY_ALL",
                "Fire piston, black powder wells, and tender descender?",
            ),
        ]:
            control = self.command_button(
                title,
                command,
                confirm=prompt + "\n\nDo not fire in proximity of people. Are you sure?",
                critical=True,
            )
            control.setObjectName("danger")
            row.addWidget(control)
        column.addLayout(row)
        layout.addWidget(panel, 1)
        return page

    def pyro_command(self, command, title):
        selected = [i for i, field in enumerate(self.pyro_selection) if field.isChecked()]
        if not selected:
            QMessageBox.warning(self, "No Pyros Selected", "Select at least one pyro")
            return
        self.request(command, selected, f"{title} pyros {selected}?")

    def fill(self, widget, values):
        widget.setRowCount(len(values))
        for row, (name, value) in enumerate(values):
            set_cell(widget, row, 0, name)
            display = f"{value:.7f}" if name in {"Latitude", "Longitude"} and finite(value) else text(value)
            set_cell(widget, row, 1, display)

    def set_controls_enabled(self):
        for control in self.controls:
            control.setEnabled(self.c.can_command)
            control.setToolTip(self.c.command_block_reason)
        self.status.setText(
            "Demo · commands simulated"
            if self.c.mode == "DEMO"
            and self.c.can_command
            else "Uplink board connected" if self.c.can_command and self.c.board_layout == "base"
            else "Ground station connected" if self.c.can_command
            else self.c.command_block_reason
        )

    def refresh(self):
        self.set_controls_enabled()
        s = self.c.latest
        if s is None:
            # Never retain another source's values when switching modes.
            for widget in [self.telemetry, self.gps, self.servos, self.cells, self.protections]:
                clear_cells(widget)
            for row in range(8):
                for col in (2, 3, 4):
                    set_cell(self.power, row, col, "—")
            for row in range(6):
                for col in (2, 3, 4):
                    set_cell(self.pyros, row, col, "—")
            return
        v = legacy_values(s, self.c.stats["rejected"])
        self.fill(
            self.telemetry,
            [
                (label, v.get(key))
                for label, key in [
                    ("State", "state"),
                    ("RSSI (dBm)", "rssi"),
                    ("RX RSSI (dBm)", "rxrssi"),
                    ("Baro filtered altitude (m)", "barofilteredalt"),
                    ("Baro max altitude (m)", "baro_max_alt"),
                    ("Roll gyro total (°)", "roll_gyro_int"),
                    ("Accel integrated velocity (m/s)", "accel_integrated_velo"),
                    ("Legacy tilt proxy (°)", "angleFromVertical"),
                    ("Temperature (°C)", "temp"),
                ]
            ]
            + [
                ("Last reception (ms)", max(0, time.monotonic() - s.received) * 1000),
                ("Packet number", s.sequence),
            ],
        )
        for column in (0, 1):
            self.telemetry.item(7, column).setToolTip(
                "Historical formula from independent pitch/yaw gyro integrals; not a measured angle from vertical. "
                "Retained for comparison with the legacy UI and CSV exports."
            )
        self.fill(
            self.gps,
            [
                (label, v.get(key))
                for label, key in [
                    ("GPS fix", "gps_fix"),
                    ("Latitude", "lat"),
                    ("Longitude", "lon"),
                    ("Altitude (m)", "gpsalt"),
                    ("Max altitude (m)", "gps_max_alt"),
                    ("Horizontal precision (m)", "gps_horiz_prec"),
                    ("Vertical precision (m)", "gps_vert_prec"),
                    ("Satellites", "gps_num_sat"),
                ]
            ],
        )
        for i in range(4):
            for col, value in enumerate([i, v["servos"][i], v["servos_deg"][i]]):
                set_cell(self.servos, i, col, text(value))
        for i, value in enumerate(v["cell_voltages"]):
            set_cell(self.cells, i, 0, str(i + 1))
            color = COLORS["accent" if value > 3.7 else "gold" if value > 3.5 else "red"] if finite(value) else None
            set_cell(self.cells, i, 1, text(value), color)
        for i in range(6):
            enabled, voltage, current = (
                v["enabled_status"][i],
                v["converter_voltages"][i],
                v["converter_currents"][i],
            )
            for col, value in [(2, enabled), (3, voltage), (4, current)]:
                color = (
                    COLORS["accent" if enabled == self.rail_requests[i].isChecked() else "red"]
                    if enabled is not None else None
                )
                set_cell(self.power, i + 1, col, text(value), color)
        requested = [check.isChecked() for check in self.rail_requests]
        self.power_master.setCheckState(
            Qt.CheckState.Checked
            if all(requested)
            else Qt.CheckState.Unchecked
            if not any(requested)
            else Qt.CheckState.PartiallyChecked
        )
        set_cell(self.power, 0, 2, text(all(v["enabled_status"])))
        set_cell(self.power, 0, 4, text(v["total_current"]))
        set_cell(self.power, 7, 3, text(v["bms_temp"]))
        for row, field in enumerate(["bms_protections_enabled", "bms_protection_status"]):
            bits = v[field]
            for col in range(8):
                value = ((bits >> (7 - col)) & 1) if bits is not None else None
                color = COLORS["accent" if value == (1 if row == 0 else 0) else "red"] if value is not None else None
                set_cell(self.protections, row, col, text(value), color)
        for row in range(6):
            status = v["pyros"][row]
            label = ["FAIL", "UNCONNECTED", "CONNECTED", "FIRED"][status] if status in (0, 1, 2, 3) else "—"
            color = COLORS[["red", "muted", "accent", "gold"][status]] if status in (0, 1, 2, 3) else None
            set_cell(self.pyros, row, 2, label, color)
            set_cell(self.pyros, row, 3, f"A:{v['armed_pyros'][row]} F:{v['fired_pyros'][row]}")
            set_cell(self.pyros, row, 4, text(v["pyro_resistances"][row]))
