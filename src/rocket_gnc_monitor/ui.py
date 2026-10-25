"""Operator workspaces for flight, antenna, GNC, mission and session analysis."""

from __future__ import annotations
from dataclasses import asdict, replace
from datetime import datetime, timezone
import copy
import json
import math
import sys
from pathlib import Path
import time
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QImage, QPixmap, QIcon, QKeySequence, QFont
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QStackedWidget,
    QListWidget,
    QLineEdit,
    QDoubleSpinBox,
    QSpinBox,
    QFileDialog,
    QMessageBox,
    QTableWidget,
    QHeaderView,
    QSlider,
    QPlainTextEdit,
    QCheckBox,
    QFormLayout,
    QScrollArea,
    QDialog,
    QDialogButtonBox,
    QTabWidget,
    QSizePolicy,
    QAbstractItemView,
    QTextBrowser,
    QApplication,
)
from .controller import Controller
from .domain import Mission, finite, validate_wind, wind_from
from .devices import ports, serial_device_key
from .media import WIDTH, HEIGHT, camera_devices
from .trajectory import Trajectory, weather_profile
from .widgets import STYLE, COLORS, MountView, ComboBox as QComboBox
from .rocket_pose_panel import RocketPosePanel
from .rocket_panel import RocketPanel
from .zephyrus import legacy_values
from .fonts import FONT_FAMILY, configure_fonts
from .location_ui import LaunchLocation, PointerLocation
from .settings_ui import SettingsDialog
from .virtual_pointer import VIRTUAL_POINTER_DEVICE
from .table_cells import set_cell


def label(text, name=None):
    item = QLabel(text)
    if name:
        item.setObjectName(name)
    return item


def button(text, callback, primary=False):
    item = QPushButton(text)
    if primary:
        item.setObjectName("primary")
    item.clicked.connect(callback)
    return item


def card(title=None):
    widget = QFrame()
    widget.setObjectName("card")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    if title:
        layout.addWidget(label(title, "section"))
    return widget, layout


def table(headers):
    item = QTableWidget(0, len(headers))
    item.setHorizontalHeaderLabels(headers)
    item.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    item.verticalHeader().hide()
    item.setAlternatingRowColors(False)
    return item


def plot(ylabel, xlabel="Flight time", unit="s"):
    item = pg.PlotWidget(background=COLORS["panel"])
    item.setLabel("left", ylabel, color=COLORS["muted"], **{"font-family": f"'{FONT_FAMILY}'"})
    item.setLabel("bottom", xlabel, units=unit, color=COLORS["muted"], **{"font-family": f"'{FONT_FAMILY}'"})
    item.showGrid(x=True, y=True, alpha=0.12)
    item.getPlotItem().setMenuEnabled(False)
    for axis in ("left", "bottom"):
        item.getAxis(axis).setTextPen(COLORS["muted"])
        item.getAxis(axis).setPen(COLORS["line"])
        item.getAxis(axis).setTickFont(QFont(FONT_FAMILY, 10))
    return item


class FlightScrollArea(QScrollArea):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Qt's wrapped-label height-for-width calculation otherwise expands
        # both plot rows to their preferred heights, creating excessive scroll.
        if self.widget():
            self.widget().setMaximumHeight(max(720, self.viewport().height()))


class MissionDialog(QDialog):
    def __init__(self, mission, parent=None):
        super().__init__(parent)
        configure_fonts(QApplication.instance())
        self.setWindowTitle("Mission configuration")
        self.resize(690, 760)
        self.mission = copy.deepcopy(mission)
        remote_only = getattr(getattr(parent, "controller", None), "board_layout", None) == "launch"
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)
        self.fields = {}
        definitions = [
            (
                "Mission",
                [
                    ("name", "Mission name", "text"),
                    ("site_configured", "Launch origin established", "bool"),
                    ("launch_location", "Launch location", "location"),
                    ("altitude", "Launch altitude, WGS84 ellipsoid (m)", (-1000, 10000, 2)),
                    ("altitude_msl", "Launch elevation, mean sea level (m)", (-1000, 10000, 2)),
                    ("legacy_altitude", "Legacy height interpretation", "altitude"),
                    ("canard_count", "Canards in vehicle profile", (0, 16, 0)),
                    ("freshness", "Maximum target age (s)", (0.1, 60, 1)),
                    ("low_battery", "Low battery alert (V)", (0, 100, 2)),
                    ("rail_length", "Launch rail length (m)", (0.1, 100, 2)),
                    ("rail_tilt", "Rail tilt from vertical (°)", (0, 89, 1)),
                    ("rail_heading", "Rail heading, true north (°)", (0, 359.99, 2)),
                    ("simulation_index", "Saved .ork simulation index (0-based)", (0, 1000, 0)),
                    ("seed", "Simulation random seed", (0, 2147483647, 0)),
                    ("video_offset", "Replay video offset, positive delays video (s)", (-600, 600, 2)),
                ],
            ),
            (
                "Antenna pointer",
                [
                    ("pointer_site_configured", "Antenna location established", "bool"),
                    ("pointer_location", "Antenna location", "pointer_location"),
                ],
            ),
        ]
        for page_title, fields in definitions:
            if remote_only and page_title == "Antenna pointer":
                continue
            page = QWidget()
            form = QFormLayout(page)
            form.setSpacing(11)
            for key, title, kind in fields:
                if kind == "location":
                    self.location = LaunchLocation(mission)
                    self.location.preset_selected.connect(
                        lambda: self.fields["site_configured"].setChecked(True)
                    )
                    form.addRow(title, self.location)
                    continue
                if kind == "pointer_location":
                    self.pointer_location = PointerLocation(
                        mission, self.launch_origin,
                        station=getattr(getattr(parent, "profile", None), "station", None),
                    )
                    self.pointer_location.preset_selected.connect(
                        lambda: self.fields["pointer_site_configured"].setChecked(True)
                    )
                    form.addRow(self.pointer_location)
                    continue
                value = getattr(mission, key)
                if kind == "text":
                    editor = QLineEdit(value)
                elif kind == "bool":
                    editor = QCheckBox()
                    editor.setChecked(value)
                elif kind == "altitude":
                    editor = QComboBox()
                    for title_, key_ in [
                        ("Unknown — trajectory position unavailable", "unknown"),
                        ("GPS zeroed at launch origin", "gps_agl"),
                        ("Filtered barometer relative to launch", "barometric_agl"),
                        ("GPS absolute ellipsoid altitude", "ellipsoid"),
                    ]:
                        editor.addItem(title_, key_)
                    editor.setCurrentIndex(max(0, editor.findData(value)))
                else:
                    low, high, decimals = kind
                    editor = QDoubleSpinBox() if decimals else QSpinBox()
                    if decimals:
                        editor.setDecimals(decimals)
                    editor.setRange(low, high)
                    editor.setValue(value)
                editor.setAccessibleName(title)
                self.fields[key] = editor
                form.addRow(title, editor)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            tabs.addTab(scroll, page_title)
        if not remote_only:
            for field in (self.location.latitude, self.location.longitude, self.fields["altitude"]):
                field.valueChanged.connect(self.pointer_location.update_preview)
            for field in (self.location.mgrs, self.location.plus_code, self.location.reference_lat, self.location.reference_lon):
                field.textChanged.connect(self.pointer_location.update_preview)
            self.fields["site_configured"].toggled.connect(self.pointer_location.update_preview)
            self.location.preset_selected.connect(self.pointer_location.update_preview)
            self.location.preset.currentIndexChanged.connect(
                lambda: self.pointer_location.set_urrg_available(self.location.preset.currentData() == "URRG")
            )
        note = label(
            "Launch station has no local antenna pointer. Select an away-station pointer in the communication panel; its location is configured at that away station."
            if remote_only else
            "Set the antenna location for virtual trajectory tracking. Physical-board tracking retains the ground GPS set with Send to AntPtr.",
            "muted",
        )
        note.setWordWrap(True)
        root.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def launch_origin(self):
        if not self.fields["site_configured"].isChecked():
            raise ValueError("Establish the launch origin in the Mission tab to use a relative antenna location")
        point = self.location.value()
        return point.latitude, point.longitude, self.fields["altitude"].value()

    def accept(self):
        try:
            for key, editor in self.fields.items():
                value = (
                    editor.isChecked()
                    if isinstance(editor, QCheckBox)
                    else editor.currentData()
                    if isinstance(editor, QComboBox)
                    else editor.text()
                    if isinstance(editor, QLineEdit)
                    else editor.value()
                )
                setattr(self.mission, key, value)
            location = self.location.value()
            self.mission.latitude, self.mission.longitude = location.latitude, location.longitude
            self.mission.launch_location_format = self.location.format.currentData()
            self.mission.launch_location_code = location.code
            self.mission.launch_site_name = self.location.preset.currentData()
            if hasattr(self, "pointer_location"):
                self.pointer_location.apply(self.mission)
            self.mission.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Mission settings", str(exc))
            return
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, data_dir, video_channels=None, video_labels=None, board_layout="legacy", vehicle="balius", initial_mission=None):
        super().__init__()
        configure_fonts(QApplication.instance())
        self.setWindowTitle("Rocket GNC Monitor")
        self.resource_root = (
            Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
        )
        self.setWindowIcon(QIcon(str(self.resource_root / "resources" / "icon.svg")))
        self.resize(1480, 980)
        self.setMinimumSize(1120, 800)
        self.setStyleSheet(STYLE)
        self.controller = c = Controller(data_dir, video_channels=video_channels, video_labels=video_labels,
                                         board_layout=board_layout, vehicle=vehicle, initial_mission=initial_mission)
        self.settings_dialog = None
        self.flight_3d_dialog = None
        self.events = []
        self.last_ui = 0
        self.last_table = 0
        self.port_widgets = {}
        self.serial_devices = []
        self.port_choice_signature = None
        self.camera_devices = []
        self.camera_choice_signature = None
        self.video_widgets = {}
        self.last_pixmaps = {stream: None for stream in c.video_channels}
        self.disconnect_buttons = {}
        self.refresh_buttons = {}
        self.live_controls = []
        self.pointer_controls = []
        self.metrics = {}
        self.last_pixmap = None
        self.last_video_error = ""
        self.last_sample_key = None
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 16, 18, 12)
        outer.setSpacing(13)
        header = QHBoxLayout()
        title = QVBoxLayout()
        title.addWidget(label("MIT ROCKET TEAM / GROUND SYSTEMS", "eyebrow"))
        title.addWidget(label("GNC flight monitor", "heading"))
        header.addLayout(title)
        header.addStretch()
        self.mission_label = label(c.mission.name, "section")
        header.addWidget(self.mission_label)
        self.mode = QComboBox()
        self.mode.addItems(["LIVE", "DEMO", "REPLAY"])
        self.mode.setAccessibleName("Data source mode")
        self.mode.currentTextChanged.connect(lambda value: self.guard(lambda: c.switch_mode(value)))
        header.addWidget(self.mode)
        self.poll_button = button("Start Polling", self.toggle_polling)
        header.addWidget(self.poll_button)
        self.record_button = button("Start Logging", self.record, True)
        header.addWidget(self.record_button)
        outer.addLayout(header)
        self.serial_bar = QWidget()
        connections = QHBoxLayout(self.serial_bar)
        connections.setContentsMargins(0, 0, 0, 0)
        for role, title in c.serial_labels.items():
            panel, layout = card()
            heading = QHBoxLayout()
            heading.addWidget(label(title, "section"))
            heading.addStretch()
            status = label("Disconnected", "muted")
            heading.addWidget(status)
            layout.addLayout(heading)
            row = QHBoxLayout()
            combo = QComboBox()
            combo.setMinimumWidth(110)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.setAccessibleName(f"{title} serial port")
            row.addWidget(combo, 1)
            refresh = button("Refresh", self.refresh_ports)
            action = button("Connect", lambda checked=False, r=role: self.connect_role(r))
            disconnect = button("Disconnect", lambda checked=False, r=role: self.disconnect_role(r))
            for control in [refresh, action, disconnect]:
                row.addWidget(control)
            layout.addLayout(row)
            connections.addWidget(panel, 1)
            self.port_widgets[role] = (combo, action, status)
            self.disconnect_buttons[role] = disconnect
            self.refresh_buttons[role] = refresh
        outer.addWidget(self.serial_bar)
        self.demo_bar = QWidget()
        demo_row = QHBoxLayout(self.demo_bar)
        demo_row.setContentsMargins(0, 0, 0, 0)
        demo_row.addWidget(label("Zephyrus test flight", "section"))
        self.demo_station = QComboBox()
        self.demo_station.addItems(["GS1", "GS2", "GS3"])
        self.demo_station.setCurrentText(c.demo_station)
        self.demo_station.setAccessibleName("Demo ground-station recording")
        self.demo_station.currentTextChanged.connect(
            lambda station: self.guard(lambda: c.select_demo(station))
        )
        demo_row.addWidget(self.demo_station)
        self.demo_play = button("Pause", lambda: self.guard(c.play_demo))
        demo_row.addWidget(self.demo_play)
        self.demo_launch = button("Launch −5s", lambda: self.restart_demo(True))
        self.demo_start = button("Full start", lambda: self.restart_demo(False))
        demo_row.addWidget(self.demo_launch)
        demo_row.addWidget(self.demo_start)
        self.demo_speed = QComboBox()
        for speed in (0.25, 0.5, 1, 2, 4):
            self.demo_speed.addItem(f"{speed:g}×", speed)
        self.demo_speed.setCurrentIndex(2)
        self.demo_speed.setAccessibleName("Demo playback speed")
        self.demo_speed.currentIndexChanged.connect(
            lambda: setattr(c, "demo_speed", self.demo_speed.currentData())
        )
        demo_row.addWidget(self.demo_speed)
        self.demo_slider = QSlider(Qt.Orientation.Horizontal)
        self.demo_slider.setSingleStep(1000)
        self.demo_slider.setPageStep(10000)
        self.demo_slider.setAccessibleName("Zephyrus recording position")
        self.demo_slider.sliderReleased.connect(
            lambda: self.guard(lambda: c.seek_demo(self.demo_slider.value() / 1000))
        )
        self.demo_slider.valueChanged.connect(
            lambda value: (
                None if self.demo_slider.isSliderDown() else self.guard(lambda: c.seek_demo(value / 1000))
            )
        )
        demo_row.addWidget(self.demo_slider, 1)
        self.demo_clock = label("", "muted")
        demo_row.addWidget(self.demo_clock)
        outer.addWidget(self.demo_bar)
        body = QHBoxLayout()
        navigation = QListWidget()
        navigation.setObjectName("navigation")
        navigation.addItems(
            [
                "Control panel",
                "Flight overview",
                "Antenna pointer",
                "GNC & actuators",
                "Mission & wind",
                "Sessions & replay",
                "Diagnostics",
                "Rocket controls",
            ]
        )
        navigation.setFixedWidth(155)
        self.pages = QStackedWidget()
        body.addWidget(navigation)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)
        for builder in (
            self.control_page,
            self.flight_page,
            self.antenna_page,
            self.gnc_page,
            self.mission_page,
            self.sessions_page,
            self.diagnostics_page,
            self.rocket_page,
        ):
            self.pages.addWidget(builder())
        navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.pages.currentChanged.connect(navigation.setCurrentRow)
        navigation.setCurrentRow(0)
        self.banner = label("LIVE · Controls locked · connect the ground station", "muted")
        outer.addWidget(self.banner)
        self.statusBar().showMessage("Ready · offline-capable")
        self.wall_clock = label("", "muted")
        self.statusBar().addPermanentWidget(self.wall_clock)
        c.event.connect(self.add_event)
        c.changed.connect(self.refresh)
        c.video_frame.connect(self.show_frame)
        c.video_reset.connect(self.reset_video_frame)
        c.task_done.connect(self.task_done)
        self.refresh_ports()
        self.wind_to_table()
        self.install_shortcuts()
        menu = self.menuBar().addMenu("Mission")
        for title, callback in [
            ("Configure…", self.edit_mission),
            ("Open mission…", self.load_mission),
            ("Save mission…", self.save_mission),
            ("Open session…", self.open_session),
        ]:
            action = QAction(title, self)
            # Cocoa otherwise treats "Configure…" as Preferences and steals
            # Command-comma from the explicit Settings action.
            action.setMenuRole(QAction.MenuRole.NoRole)
            action.triggered.connect(callback)
            menu.addAction(action)
        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction("3D flight…", self.open_flight_3d)
        self.daylight_action = daylight = QAction("Daylight theme", self)
        daylight.setCheckable(True)
        daylight.toggled.connect(lambda enabled: self.guard(lambda: self.set_daylight(enabled)))
        view_menu.addAction(daylight)
        help_menu = self.menuBar().addMenu("Help")
        for title, filename in [
            ("Operator guide", "OPERATOR_GUIDE.md"),
            ("Legacy feature parity and shortcuts", "LEGACY_PARITY.md"),
            ("Implementation status", "IMPLEMENTATION_STATUS.md"),
            ("Wire protocol", "PROTOCOL.md"),
            ("Third-party notices", "THIRD_PARTY_NOTICES.md"),
        ]:
            action = QAction(title, self)
            action.triggered.connect(lambda checked=False, f=filename: self.show_help(f))
            help_menu.addAction(action)
        self.set_daylight(c.settings.daylight, persist=False)
        self.refresh()
        if c.settings_error:
            self.add_event(c.settings_error)
            self.statusBar().showMessage(
                "Settings could not be loaded; defaults are active. Open Settings to repair."
            )

    def restart_demo(self, launch):
        def restart():
            c = self.controller
            if c.demo:
                c.seek_demo(c.demo.cue if launch else 0)
                c.play_demo(True)

        self.guard(restart)

    def rocket_page(self):
        self.rocket_panel = RocketPanel(self.controller, self.guard)
        return self.rocket_panel

    def forget_startup_setup(self):
        from .station_profile import forget_startup_choices

        def forget():
            forget_startup_choices(self.controller.data_dir)
            self.statusBar().showMessage("Setup forgotten. The next launch will use fresh wizard defaults.", 10000)

        self.guard(forget)

    def install_shortcuts(self):
        file_menu = self.menuBar().addMenu("File")
        self.flight_actions = {}
        for key, title, shortcut, callback in [
            ("open", "Open flight…", QKeySequence.StandardKey.Open, self.open_flight),
            ("save", "Save flight…", QKeySequence.StandardKey.Save, self.save_flight),
        ]:
            action = QAction(title, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            file_menu.addAction(action)
            self.flight_actions[key] = action
        file_menu.addSeparator()
        self.settings_action = QAction("Settings…", self)
        self.settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        self.settings_action.setShortcut(QKeySequence("Ctrl+,"))
        self.settings_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(self.settings_action)
        self.forget_setup_action = QAction("Forget setup on next launch", self)
        self.forget_setup_action.setMenuRole(QAction.MenuRole.NoRole)
        self.forget_setup_action.triggered.connect(self.forget_startup_setup)
        file_menu.addAction(self.forget_setup_action)
        menu = self.menuBar().addMenu("Serial controls")
        self.legacy_actions = {}
        definitions = [
            ("refresh", "Refresh ports", "Ctrl+R", self.refresh_ports),
            ("ground", "Connect Ground Station", "Ctrl+Alt+C", lambda: self.toggle_connection("telemetry")),
            (
                "pointer",
                "Connect Antenna Pointer",
                "Shift+Ctrl+Alt+C",
                lambda: self.toggle_connection("pointer"),
            ),
            ("poll", "Start Polling", "Ctrl+Return", self.toggle_polling),
            ("log", "Start Logging", "Ctrl+L", self.record),
            ("up", "Pointer UP", "Ctrl+Up", lambda: self.guard(lambda: self.controller.jog(0, 5))),
            ("down", "Pointer DOWN", "Ctrl+Down", lambda: self.guard(lambda: self.controller.jog(0, -5))),
            ("right", "Pointer RIGHT", "Ctrl+Right", lambda: self.guard(lambda: self.controller.jog(5, 0))),
            ("left", "Pointer LEFT", "Ctrl+Left", lambda: self.guard(lambda: self.controller.jog(-5, 0))),
            ("zero", "Pointer ZERO", "Ctrl+0", lambda: self.guard(self.controller.reference_zero)),
        ]
        for key, title, shortcut, callback in definitions:
            action = QAction(title, self)
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(callback)
            menu.addAction(action)
            self.legacy_actions[key] = action

    def toggle_connection(self, role):
        if self.controller.states[role] in {"Connected", "Connecting"}:
            self.disconnect_role(role)
        else:
            self.connect_role(role)

    def show_help(self, filename):
        dialog = QDialog(self)
        dialog.setWindowTitle("Rocket GNC Monitor · Help")
        dialog.resize(850, 720)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setMarkdown((self.resource_root / "docs" / filename).read_text(encoding="utf-8"))
        layout.addWidget(browser)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def open_settings(self):
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(
                self.controller.settings, self.controller.data_dir, self.apply_settings, self
            )
            self.settings_dialog.finished.connect(self.settings_closed)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def settings_closed(self):
        self.settings_dialog.deleteLater()
        self.settings_dialog = None

    def apply_settings(self, settings):
        self.controller.save_settings(settings)
        self.set_daylight(settings.daylight, persist=False)
        self.statusBar().showMessage("Settings saved", 5000)

    def set_daylight(self, enabled, *, persist=True):
        from .widgets import DARK_COLORS, LIGHT_COLORS

        if persist and enabled != self.controller.settings.daylight:
            self.controller.save_settings(replace(self.controller.settings, daylight=enabled))
        self.daylight_action.blockSignals(True)
        self.daylight_action.setChecked(enabled)
        self.daylight_action.blockSignals(False)
        palette = LIGHT_COLORS if enabled else DARK_COLORS
        COLORS.update(palette)
        style = STYLE
        if enabled:
            # Replace exact tokens in one pass so replacements cannot cascade.
            import re

            replacements = {DARK_COLORS[key]: value for key, value in LIGHT_COLORS.items()}
            replacements.update(
                {
                    "#172333": "#ffffff",
                    "#34485e": "#bac8d2",
                    "#23374b": "#d9e8ed",
                    "#0e1722": "#e8eef2",
                    "#213c42": "#c8e6df",
                    "#9ceddf": "#115d53",
                    "#617287": "#7b858e",
                    "#263344": "#c1c9d0",
                    "#aabfd2": "#354e61",
                    "#264b55": "#b8dfd5",
                    "#243348": "#d1dce2",
                    "#091715": "#ffffff",
                }
            )
            style = re.sub(r"#[0-9a-f]{6}", lambda m: replacements.get(m[0], m[0]), STYLE)
        self.setStyleSheet(style)
        for graph in (self.trajectory_plot, self.altitude_plot, self.rate_plot, self.angle_plot):
            graph.setBackground(COLORS["panel"])
            for axis in ("left", "bottom"):
                graph.getAxis(axis).setTextPen(COLORS["muted"])
                graph.getAxis(axis).setPen(COLORS["line"])
                graph.getAxis(axis).setLabel(color=COLORS["muted"])
        for curve, key in [
            (self.reference_curve, "muted"),
            (self.altitude_reference, "muted"),
            (self.actual_curve, "accent"),
            (self.altitude_curve, "accent"),
        ]:
            pen = curve.opts["pen"]
            pen.setColor(COLORS[key])
            curve.setPen(pen)
        for curves in (self.rate_curves, self.angle_curves):
            for curve, key in zip(curves, ("accent", "gold", "violet")):
                curve.setPen(pg.mkPen(COLORS[key], width=1.5))
        self.actual_marker.setSymbolBrush(COLORS["accent"])
        self.reference_marker.setSymbolBrush(COLORS["gold"])
        self.virtual_mount_marker.setSymbolBrush(COLORS["violet"])
        self.mount.update()
        self.attitude.view.update()

    def guard(self, function):
        try:
            return function()
        except Exception as exc:
            self.add_event(str(exc))
            self.statusBar().showMessage(str(exc), 12000)
            QMessageBox.warning(self, "Rocket GNC Monitor", str(exc))

    def control_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        panel, column = card()
        column.addWidget(label("CONNECTION CONTROL", "eyebrow"))
        self.connection_headline = label("Ground station disconnected", "heading")
        self.connection_headline.setWordWrap(True)
        column.addWidget(self.connection_headline)
        self.connection_help = label("Connect the ground station using the serial selector above.", "muted")
        self.connection_help.setWordWrap(True)
        column.addWidget(self.connection_help)
        layout.addWidget(panel)
        row = QHBoxLayout()
        self.connection_tiles = {}
        for key, number, title, detail in [
            ("ground", "01 / USB", "Mac → ground station", "Serial connection to the telemetry board"),
            ("rocket", "02 / RADIO", "Rocket → ground station", "Fresh, checksum-valid Zephyrus telemetry"),
            ("pointer", "03 / USB", "Mac → antenna pointer", "Separate serial connection for mount control"),
        ]:
            tile, items = card()
            items.addWidget(label(number, "eyebrow"))
            items.addWidget(label(title, "section"))
            state = label("● DISCONNECTED", "section")
            state.setWordWrap(True)
            items.addWidget(state)
            description = label(detail, "muted")
            description.setWordWrap(True)
            items.addWidget(description)
            row.addWidget(tile, 1)
            self.connection_tiles[key] = state
        layout.addLayout(row)
        panel, column = card("Rocket link monitor")
        row = QHBoxLayout()
        self.link_metrics = {}
        for key, title in [
            ("age", "LAST VALID PACKET"),
            ("rssi", "RADIO SIGNAL"),
            ("accepted", "VALID PACKETS"),
            ("rejected", "REJECTED PACKETS"),
        ]:
            group = QVBoxLayout()
            group.addWidget(label(title, "muted"))
            value = label("—", "metric")
            group.addWidget(value)
            row.addLayout(group, 1)
            self.link_metrics[key] = value
        column.addLayout(row)
        self.radio_explanation = label(
            "A connected USB board alone does not establish the rocket radio link.", "muted"
        )
        self.radio_explanation.setWordWrap(True)
        column.addWidget(self.radio_explanation)
        layout.addWidget(panel)
        panel, column = card("Operating controls")
        self.control_readiness = label("LOCKED · connect the ground station", "section")
        self.control_readiness.setWordWrap(True)
        column.addWidget(self.control_readiness)
        note = label(
            "Connect the ground station for telemetry and recording. Connect the antenna pointer for manual control. Start polling to receive rocket telemetry.",
            "muted",
        )
        note.setWordWrap(True)
        column.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(button("Flight overview", lambda: self.pages.setCurrentIndex(1)))
        row.addWidget(button("Antenna pointer", lambda: self.pages.setCurrentIndex(2)))
        row.addWidget(button("Mission configuration…", self.edit_mission))
        row.addStretch()
        column.addLayout(row)
        layout.addWidget(panel)
        layout.addStretch()
        page.setMinimumHeight(620)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    def flight_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        metrics = QHBoxLayout()
        for key, title, unit in [
            ("altitude", "ALTITUDE", "m"),
            ("velocity", "VELOCITY", "m/s"),
            ("flight", "FLIGHT TIME", "s"),
            ("battery", "BATTERY", "V"),
            ("rssi", "LINK RSSI", "dBm"),
            ("age", "SAMPLE AGE", "s"),
        ]:
            panel, column = card()
            column.addWidget(label(title, "muted"))
            value = label("—", "metric")
            column.addWidget(value)
            column.addWidget(label(unit, "muted"))
            metrics.addWidget(panel, 1)
            self.metrics[key] = value
        layout.addLayout(metrics)
        middle = QHBoxLayout()
        for stream, title in self.controller.video_labels.items():
            panel, column = card(title)
            view = label(f"{title.upper()} · USB CAMERA NOT STARTED")
            view.setAlignment(Qt.AlignmentFlag.AlignCenter)
            view.setMinimumSize(140, 100)
            view.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            column.addWidget(view, 1)
            status = label("Camera not started", "muted")
            status.setWordWrap(True)
            status.setFixedHeight(32)
            status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            column.addWidget(status)
            controls = QHBoxLayout()
            camera = QComboBox()
            camera.addItem("Choose USB camera…", "")
            camera.setAccessibleName(f"{title} USB camera")
            camera.setMinimumWidth(0)
            camera.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            controls.addWidget(camera, 1)
            find = button("Find", lambda: self.controller.submit("cameras", camera_devices))
            find.setToolTip("Find USB cameras for the active video inputs")
            controls.addWidget(find)
            column.addLayout(controls)
            row = QHBoxLayout()
            start = button(
                "Start", lambda checked=False, ch=stream: self.guard(lambda: self.start_camera(ch)), True
            )
            file = button("File…", lambda checked=False, ch=stream: self.video_file(ch))
            stop = button(
                "Stop", lambda checked=False, ch=stream: self.controller.stop_video(ch, pause_replay=True)
            )
            for control in (start, file, stop):
                row.addWidget(control)
            self.live_controls.extend((start, file))
            column.addLayout(row)
            middle.addWidget(panel, 1)
            self.video_widgets[stream] = dict(
                image=view, status=status, camera=camera, start=start, file=file, stop=stop
            )
            camera.currentIndexChanged.connect(self.update_camera_choices)
        # Keep first-stream widget names available to existing integrations.
        digital = self.video_widgets["digital"]
        self.video_image, self.video_status, self.camera = (
            digital["image"],
            digital["status"],
            digital["camera"],
        )
        panel, column = card("Flight trajectory")
        row = QHBoxLayout()
        self.view = QComboBox()
        self.view.addItems(["Plan · East / North", "Side · East / Up", "Perspective"])
        self.view.currentIndexChanged.connect(lambda: self.refresh_plots(force=True))
        row.addWidget(self.view)
        row.addWidget(button("Import reference…", self.load_reference))
        row.addWidget(button("3D…", self.open_flight_3d))
        column.addLayout(row)
        self.trajectory_plot = plot("North (m)", "East", "m")
        self.trajectory_legend = self.trajectory_plot.addLegend(offset=(10, 10))
        self.reference_curve = self.trajectory_plot.plot(
            pen=pg.mkPen(COLORS["muted"], width=1.5, style=Qt.PenStyle.DashLine), name="Reference"
        )
        self.actual_curve = self.trajectory_plot.plot(
            pen=pg.mkPen(COLORS["accent"], width=2), name="Actual / demo"
        )
        self.actual_marker = self.trajectory_plot.plot(
            pen=None, symbol="o", symbolBrush=COLORS["accent"], symbolSize=10
        )
        self.reference_marker = self.trajectory_plot.plot(
            pen=None, symbol="d", symbolBrush=COLORS["gold"], symbolSize=8
        )
        self.virtual_mount_marker = self.trajectory_plot.plot(
            pen=None, symbol="t", symbolBrush=COLORS["violet"], symbolSize=11
        )
        column.addWidget(self.trajectory_plot, 1)
        self.reference_label = label("No reference selected", "muted")
        self.reference_label.setWordWrap(True)
        self.reference_label.setFixedHeight(48)
        self.reference_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.reference_label)
        middle.addWidget(panel, 1)
        layout.addLayout(middle, 4)
        bottom = QHBoxLayout()
        panel, column = card("Vertical flight profile")
        self.altitude_plot = plot("Altitude (m)")
        self.altitude_curve = self.altitude_plot.plot(pen=pg.mkPen(COLORS["accent"], width=2))
        self.altitude_reference = self.altitude_plot.plot(
            pen=pg.mkPen(COLORS["muted"], style=Qt.PenStyle.DashLine)
        )
        column.addWidget(self.altitude_plot)
        bottom.addWidget(panel, 3)
        panel, column = card("Rocket orientation & flight status")
        self.attitude = RocketPosePanel(self.controller, self.rocket_pose_reference, self.open_flight_3d)
        column.addWidget(self.attitude, 1)
        bottom.addWidget(panel, 1)
        layout.addLayout(bottom, 2)
        # Keep both camera controls and plots usable when the connection bar
        # reduces the available height on a compact display.
        page.setMinimumHeight(680)
        scroll = FlightScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    def antenna_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        panel, column = card("Antenna assembly")
        row = QHBoxLayout()
        for name in ["Perspective", "Front", "Side", "Top"]:
            row.addWidget(button(name, lambda checked=False, v=name: self.mount_camera(v)))
        row.addStretch()
        column.addLayout(row)
        self.mount = MountView()
        self.mount.pose_provider = lambda: (
            self.controller.virtual_pointer.pose if self.controller.virtual_pointer else None
        )
        column.addWidget(self.mount, 1)
        note = label("Yagi · grid reflector · Avenger XR18 — all mounted on the elevation beam", "muted")
        note.setWordWrap(True)
        column.addWidget(note)
        layout.addWidget(panel, 3)
        panel, column = card("Pointing control")
        panel.setMaximumWidth(390)
        self.pointer_pose = label("MANUAL CONTROL", "eyebrow")
        column.addWidget(self.pointer_pose)
        self.virtual_connect = button(
            "Connect virtual pointer",
            lambda: self.guard(lambda: self.controller.connect("pointer", VIRTUAL_POINTER_DEVICE)),
        )
        column.addWidget(self.virtual_connect)
        self.virtual_panel = QWidget()
        self.virtual_panel.setObjectName("transparent")
        virtual = QVBoxLayout(self.virtual_panel)
        virtual.setContentsMargins(0, 0, 0, 8)
        self.virtual_location = label("Set the antenna location in Mission configuration", "muted")
        self.virtual_location.setWordWrap(True)
        virtual.addWidget(self.virtual_location)
        virtual.addWidget(button("Configure antenna location…", self.edit_pointer_mission))
        self.virtual_follow = button("Follow trajectory", self.toggle_virtual_trajectory, True)
        virtual.addWidget(self.virtual_follow)
        row = QHBoxLayout()
        self.virtual_reset = button("Rewind", lambda: self.guard(self.rewind_virtual_trajectory))
        row.addWidget(self.virtual_reset)
        self.virtual_speed = QComboBox()
        for rate in (0.25, 0.5, 1, 2, 4):
            self.virtual_speed.addItem(f"{rate:g}×", rate)
        self.virtual_speed.setCurrentIndex(2)
        self.virtual_speed.currentIndexChanged.connect(self.set_virtual_speed)
        row.addWidget(self.virtual_speed)
        virtual.addLayout(row)
        self.virtual_slider = QSlider(Qt.Orientation.Horizontal)
        self.virtual_slider.setAccessibleName("Virtual trajectory time")
        self.virtual_slider.sliderPressed.connect(lambda: self.controller.hold("Virtual trajectory paused"))
        self.virtual_slider.valueChanged.connect(self.seek_virtual_slider)
        self.virtual_slider.sliderReleased.connect(self.seek_virtual_slider)
        virtual.addWidget(self.virtual_slider)
        self.virtual_clock = label("Run OpenRocket or load a trajectory in Mission & wind", "muted")
        self.virtual_clock.setWordWrap(True)
        virtual.addWidget(self.virtual_clock)
        self.virtual_pose = label("", "section")
        self.virtual_pose.setWordWrap(True)
        virtual.addWidget(self.virtual_pose)
        description = label("SIMULATED POSE · illustrative slew: azimuth 90°/s, elevation 60°/s", "muted")
        description.setWordWrap(True)
        virtual.addWidget(description)
        column.addWidget(self.virtual_panel)
        self.azimuth = QLineEdit("0.0")
        self.elevation = QLineEdit("0.0")
        for title, control in [("Azimuth (deg)", self.azimuth), ("Elevation (deg)", self.elevation)]:
            column.addWidget(label(title, "muted"))
            column.addWidget(control)
        self.point_button = button(
            "Send",
            lambda: self.guard(
                lambda: self.controller.manual_point(float(self.azimuth.text()), float(self.elevation.text()))
            ),
            True,
        )
        column.addWidget(self.point_button)
        self.pointer_controls.extend([self.azimuth, self.elevation, self.point_button])
        row = QHBoxLayout()
        for title, az, el in [("←", -5, 0), ("→", 5, 0), ("↓", 0, -5), ("↑", 0, 5)]:
            control = button(
                title, lambda checked=False, a=az, e=el: self.guard(lambda: self.controller.jog(a, e))
            )
            row.addWidget(control)
            self.pointer_controls.append(control)
        column.addLayout(row)
        self.track_button = button("Track live rocket", lambda: self.guard(self.controller.start_tracking))
        column.addWidget(self.track_button)
        self.hold_button = button("Stop tracking", lambda: self.controller.hold())
        column.addWidget(self.hold_button)
        self.zero_button = button("ZERO", lambda: self.guard(self.controller.reference_zero))
        column.addWidget(self.zero_button)
        self.pointer_sent = label("Last sent: —", "section")
        column.addWidget(self.pointer_sent)
        self.pointer_status = label("Disconnected", "muted")
        self.pointer_status.setWordWrap(True)
        column.addWidget(self.pointer_status)
        self.ground_gps_title = label("Ground station GPS", "section")
        column.addWidget(self.ground_gps_title)
        self.ground_gps = label("Awaiting telemetry", "muted")
        self.ground_gps.setWordWrap(True)
        column.addWidget(self.ground_gps)
        self.freeze_gps = button("Send to AntPtr", lambda: self.guard(self.controller.freeze_ground_station))
        column.addWidget(self.freeze_gps)
        column.addStretch()
        panel.setMinimumHeight(535)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(350)
        scroll.setMaximumWidth(390)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(scroll, 1)
        return page

    def gnc_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.rate_plot = plot("Angular rate (°/s)")
        self.rate_plot.addLegend()
        self.angle_plot = plot("Integrated rotation (°)")
        self.angle_plot.addLegend()
        self.rate_curves = []
        self.angle_curves = []
        for name, color in zip(
            ["X / roll", "Y / pitch", "Z / yaw"], [COLORS["accent"], COLORS["gold"], COLORS["violet"]]
        ):
            self.rate_curves.append(self.rate_plot.plot(pen=pg.mkPen(color, width=1.5), name=name))
            self.angle_curves.append(self.angle_plot.plot(pen=pg.mkPen(color, width=1.5), name=name))
        row.addWidget(self.rate_plot)
        row.addWidget(self.angle_plot)
        layout.addLayout(row, 1)
        panel, column = card("Actuator channels")
        self.actuators = table(["Channel", "Requested (°)", "Measured (°)", "Drive (legacy µs)", "Status"])
        self.actuators.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        column.addWidget(self.actuators)
        note = label(
            "Zephyrus reports four legacy servo drive values. Mapping to new canards/tabs and measured deflection is unavailable in this protocol.",
            "muted",
        )
        note.setWordWrap(True)
        column.addWidget(note)
        layout.addWidget(panel, 1)
        return page

    def mission_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        for title, callback in [
            ("Configure mission…", self.edit_mission),
            ("Open mission…", self.load_mission),
            ("Save mission…", self.save_mission),
            ("Open flight…", self.open_flight),
            ("Save flight…", self.save_flight),
        ]:
            row.addWidget(button(title, callback))
        row.addStretch()
        layout.addLayout(row)
        self.mission_summary = label("Launch site not configured", "muted")
        self.mission_summary.setWordWrap(True)
        layout.addWidget(self.mission_summary)
        body = QHBoxLayout()
        panel, column = card("Wind profile")
        self.wind_table = table(["Height above launch (m)", "Speed (m/s)", "Direction from (°)"])
        column.addWidget(self.wind_table, 1)
        row = QHBoxLayout()
        row.addWidget(button("Add layer", lambda: self.add_wind_row(1000, 0, 0)))
        row.addWidget(button("Remove selected", self.remove_wind_row))
        row.addWidget(button("Apply wind", lambda: self.guard(self.apply_wind), True))
        column.addLayout(row)
        self.wind_status = label("Manual · calm", "muted")
        self.wind_status.setWordWrap(True)
        column.addWidget(self.wind_status)
        row = QHBoxLayout()
        row.addWidget(button("Import wind…", self.import_wind))
        row.addWidget(button("Export wind…", self.export_wind))
        column.addLayout(row)
        body.addWidget(panel, 1)
        panel, column = card("Weather & simulation")
        column.addWidget(label("Weather valid time (UTC ISO 8601)", "muted"))
        self.weather_time = QLineEdit(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00+00:00"))
        column.addWidget(self.weather_time)
        column.addWidget(label("Launch elevation above mean sea level (m)", "muted"))
        self.weather_msl = QDoubleSpinBox()
        self.weather_msl.setRange(-500, 10000)
        self.weather_msl.setDecimals(1)
        column.addWidget(self.weather_msl)
        column.addWidget(button("Retrieve wind · Open-Meteo", self.fetch_weather))
        note = label(
            "Profile uses forecast/historical forecast data. Below/above coverage, the simulation holds the nearest layer. Review before use. Open-Meteo data attribution is retained with the session.",
            "muted",
        )
        note.setWordWrap(True)
        column.addWidget(note)
        self.model_path = QLineEdit()
        self.model_path.setPlaceholderText("Select the team's .ork file")
        column.addWidget(self.model_path)
        column.addWidget(button("Choose OpenRocket model…", self.choose_model))
        self.motor_status = label("Bundled motor database + MITRT N8406", "muted")
        self.motor_status.setWordWrap(True)
        column.addWidget(self.motor_status)
        column.addWidget(button("Select custom motor files…", self.choose_motors))
        column.addWidget(button("Run nominal simulation", lambda: self.guard(self.start_simulation), True))
        column.addWidget(
            button(
                "Cancel simulation",
                lambda: self.controller.simulation.cancel() if self.controller.simulation else None,
            )
        )
        self.sim_status = label("Nominal reference · three-axis controlled model unqualified", "muted")
        self.sim_status.setWordWrap(True)
        column.addWidget(self.sim_status)
        column.addStretch()
        self.simulation_scroll = QScrollArea()
        self.simulation_scroll.setWidgetResizable(True)
        self.simulation_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.simulation_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.simulation_scroll.setWidget(panel)
        self.simulation_failed = False
        scroll_bar = self.simulation_scroll.verticalScrollBar()
        scroll_bar.rangeChanged.connect(
            lambda minimum, maximum: scroll_bar.setValue(maximum) if self.simulation_failed else None
        )
        body.addWidget(self.simulation_scroll, 1)
        layout.addLayout(body, 1)
        return page

    def sessions_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        panel, column = card("Flight files")
        row = QHBoxLayout()
        row.addWidget(button("Open flight…", self.open_flight))
        row.addWidget(button("Save flight…", self.save_flight, True))
        row.addStretch()
        column.addLayout(row)
        self.flight_file_status = label(
            "One .rktflight file keeps mission settings, rocket model, simulation and available recordings together.",
            "muted",
        )
        self.flight_file_status.setWordWrap(True)
        column.addWidget(self.flight_file_status)
        layout.addWidget(panel)
        panel, column = card("Session replay")
        row = QHBoxLayout()
        row.addWidget(button("Open recorded session…", self.open_session))
        row.addWidget(button("Import Zephyrus CSV…", self.import_legacy_csv))
        row.addWidget(button("Export samples to CSV…", self.export_session))
        row.addStretch()
        column.addLayout(row)
        self.session_status = label("Record a DEMO or LIVE session, then open its folder here.", "muted")
        self.session_status.setWordWrap(True)
        column.addWidget(self.session_status)
        self.replay_slider = QSlider(Qt.Orientation.Horizontal)
        self.replay_slider.setRange(0, 0)
        self.replay_slider.setAccessibleName("Replay position")
        self.replay_slider.sliderReleased.connect(
            lambda: self.guard(lambda: self.controller.seek(self.replay_slider.value() / 1000))
        )
        column.addWidget(self.replay_slider)
        row = QHBoxLayout()
        self.play = button("Play / pause", self.toggle_replay)
        row.addWidget(self.play)
        self.step_button = button(
            "Step 0.1 s", lambda: self.guard(lambda: self.controller.seek(self.controller.replay_time + 0.1))
        )
        row.addWidget(self.step_button)
        self.speed = QComboBox()
        self.speed.addItems(["0.25×", "0.5×", "1×", "2×", "4×"])
        self.speed.setCurrentIndex(2)
        self.speed.currentIndexChanged.connect(self.change_speed)
        row.addWidget(self.speed)
        self.replay_clock = label("0.00 / 0.00 s", "section")
        row.addWidget(self.replay_clock)
        row.addStretch()
        column.addLayout(row)
        row = QHBoxLayout()
        self.align_time_button = button(
            "Set displayed sample to flight t = 0", lambda: self.guard(self.controller.set_zero)
        )
        row.addWidget(self.align_time_button)
        row.addStretch()
        column.addLayout(row)
        note = label(
            "Replay cannot open the physical pointer transport. Video uses host timing plus the mission's manual offset; camera exposure synchronization is not measured.",
            "muted",
        )
        note.setWordWrap(True)
        column.addWidget(note)
        layout.addWidget(panel)
        panel, column = card("Event timeline")
        self.timeline = QListWidget()
        column.addWidget(self.timeline)
        layout.addWidget(panel, 1)
        return page

    def diagnostics_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.link_stats = label("No live packets", "section")
        layout.addWidget(self.link_stats)
        self.alert_status = label("No active alerts", "muted")
        layout.addWidget(self.alert_status)
        layout.addWidget(button("Acknowledge active alerts", self.controller.acknowledge_alerts))
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        layout.addWidget(self.log_view, 1)
        return page

    def add_event(self, text):
        line = time.strftime("%H:%M:%S") + "  " + text
        self.events.append(line)
        self.events = self.events[-500:]
        self.log_view.appendPlainText(line)
        self.timeline.addItem(line)
        if self.timeline.count() > 500:
            self.timeline.takeItem(0)
        self.timeline.scrollToBottom()
        self.statusBar().showMessage(text, 8000)

    def refresh_ports(self):
        self.serial_devices = ports() if self.controller.serial_labels else []
        self.update_port_choices(force=True)

    def update_port_choices(self, force=False):
        c = self.controller
        active = {
            role: worker.device
            for role, worker in c.workers.items()
            if worker and c.states[role] in {"Connected", "Connecting"}
        }
        signature = tuple(active.items())
        if not force and signature == self.port_choice_signature:
            return
        self.port_choice_signature = signature
        for role, (combo, _, _) in self.port_widgets.items():
            selected = active.get(role, combo.currentData())
            excluded = {serial_device_key(device) for other, device in active.items() if other != role}
            choices = [
                d["device"] for d in self.serial_devices if serial_device_key(d["device"]) not in excluded
            ]
            if role == "pointer":
                choices.append(VIRTUAL_POINTER_DEVICE)
            if role in active and active[role] not in choices:
                choices.insert(0, active[role])
            combo.blockSignals(True)
            combo.clear()
            for device in choices:
                combo.addItem(
                    "Virtual antenna pointer" if device == VIRTUAL_POINTER_DEVICE else device, device
                )
            if not choices:
                combo.addItem("No available serial devices", "")
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)

    def update_camera_choices(self, *_):
        selected = {stream: w["camera"].currentData() or "" for stream, w in self.video_widgets.items()}
        reservations = {
            stream: frozenset(state.reserved_cameras)
            for stream, state in self.controller.video_streams.items()
        }
        signature = (tuple(self.camera_devices), tuple(selected.items()), tuple(reservations.items()))
        if signature == self.camera_choice_signature:
            return
        for stream, widgets in self.video_widgets.items():
            combo = widgets["camera"]
            excluded = set().union(*(values for other, values in reservations.items() if other != stream))
            excluded.update(value for other, value in selected.items() if other != stream and value)
            state = self.controller.video_streams[stream]
            current = state.config[1] if state.config and state.config[0] == "camera" else selected[stream]
            choices = [(label, source) for label, source in self.camera_devices if source not in excluded]
            # Keep a connected camera visible in its own dropdown during rescans.
            if current and current not in excluded and current not in {source for _, source in choices}:
                choices.append((f"Camera {current}", current))
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Choose USB camera…" if self.camera_devices else "Find USB cameras…", "")
            for description, source in choices:
                duplicates = sum(name == description for name, _ in self.camera_devices)
                combo.addItem(f"{description} · {source}" if duplicates > 1 else description, source)
            combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.blockSignals(False)
        self.camera_choice_signature = (
            tuple(self.camera_devices),
            tuple((stream, w["camera"].currentData() or "") for stream, w in self.video_widgets.items()),
            tuple(reservations.items()),
        )

    def connect_role(self, role):
        if role not in self.port_widgets:
            return
        if self.controller.states[role] not in {"Connected", "Connecting"}:
            self.guard(lambda: self.controller.connect(role, self.port_widgets[role][0].currentData()))
        self.last_ui = 0
        self.refresh()

    def disconnect_role(self, role):
        self.controller.disconnect(role)
        self.last_ui = 0
        self.refresh()

    def toggle_polling(self):
        self.guard(lambda: self.controller.set_polling(not self.controller.polling))
        self.last_ui = 0
        self.refresh()

    def show_frame(self, stream, data):
        image = QImage(data, WIDTH, HEIGHT, WIDTH * 3, QImage.Format.Format_RGB888).copy()
        pixmap = self.last_pixmaps[stream] = QPixmap.fromImage(image)
        if stream == "digital":
            self.last_pixmap = pixmap
        view = self.video_widgets[stream]["image"]
        view.setPixmap(
            pixmap.scaled(
                view.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )

    def reset_video_frame(self, stream):
        self.last_pixmaps[stream] = None
        if stream == "digital":
            self.last_pixmap = None
        self.video_widgets[stream]["image"].setText(f"{self.controller.video_labels[stream].upper()} · NO VIDEO")

    def start_camera(self, stream="digital"):
        self.controller.require_ground_station()
        source = self.video_widgets[stream]["camera"].currentData()
        if source is None or source == "":
            raise ValueError("Find and select a USB camera first")
        self.controller.start_video("camera", source, stream=stream)
        self.update_camera_choices()

    def video_file(self, stream="digital"):
        if self.controller.mode == "LIVE" and not self.controller.ground_connected:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"{self.controller.video_labels[stream]} video source",
            "",
            "Video (*.mp4 *.mkv *.mov *.avi *.ts);;All files (*)",
        )
        if path:
            self.controller.start_video("file", path, stream=stream)

    def mount_camera(self, name):
        self.mount.set_camera(name)

    def record(self):
        c = self.controller
        if c.recorder:
            c.stop_recording()
        else:
            result = self.guard(lambda: c.start_recording(c.settings.sessions_path(c.data_dir)))
            if result:
                self.add_event(f"Session: {result}")
        self.last_ui = 0
        self.refresh()

    def edit_mission(self, checked=False, *, pointer_tab=False):
        if self.controller.recorder:
            return self.guard(
                lambda: (_ for _ in ()).throw(
                    ValueError("Stop recording before changing mission configuration")
                )
            )
        dialog = MissionDialog(self.controller.mission, self)
        if pointer_tab:
            dialog.findChild(QTabWidget).setCurrentIndex(1)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.controller.hold("Mission configuration changed")
            self.controller.mission = dialog.mission
            self.controller.history.clear()
            self.controller.track.clear()
            self.controller.log("Mission configuration changed", asdict(dialog.mission))
            self.wind_to_table()
            self.weather_msl.setValue(dialog.mission.altitude_msl)

    def edit_pointer_mission(self):
        self.edit_mission(pointer_tab=True)

    def toggle_virtual_trajectory(self):
        def toggle():
            c = self.controller
            if c.virtual_flight and c.virtual_flight.playing:
                c.hold("Virtual trajectory paused")
            else:
                c.start_virtual_trajectory()
                self.set_virtual_speed()

        self.guard(toggle)

    def rewind_virtual_trajectory(self):
        flight = self.controller.prepare_virtual_flight()
        self.controller.seek_virtual_trajectory(flight.start)
        self.set_virtual_speed()

    def set_virtual_speed(self):
        if self.controller.virtual_flight:
            self.controller.virtual_flight.speed = self.virtual_speed.currentData()

    def seek_virtual_slider(self):
        if not self.virtual_slider.isSliderDown():
            self.guard(lambda: self.controller.seek_virtual_trajectory(self.virtual_slider.value() / 1000))

    def refresh_virtual_pointer(self):
        c = self.controller
        worker, flight = c.virtual_pointer, c.virtual_flight
        self.virtual_connect.setVisible(worker is None)
        self.virtual_connect.setEnabled(c.board_layout != "launch" and c.mode == "LIVE" and not c.pointer_connected)
        self.virtual_panel.setVisible(worker is not None)
        for control in (self.ground_gps_title, self.ground_gps, self.freeze_gps, self.track_button):
            control.setVisible(worker is None)
        if worker is None:
            return
        m = c.mission
        self.virtual_location.setText(
            f"Antenna: {m.pointer_latitude:.7f}°, {m.pointer_longitude:.7f}°\n{m.pointer_altitude:.1f} m ellipsoid"
            if m.pointer_site_configured
            else "Set the antenna location in Mission configuration"
        )
        ready = bool(
            c.pointer_connected
            and m.pointer_site_configured
            and c.reference
            and not c.reference.manifest.get("synthetic")
        )
        self.virtual_follow.setEnabled(ready)
        self.virtual_reset.setEnabled(ready)
        self.virtual_speed.setEnabled(ready)
        self.virtual_slider.setEnabled(ready)
        self.virtual_follow.setText(
            "Pause trajectory"
            if flight and flight.playing
            else "Replay trajectory"
            if flight and flight.time >= flight.end
            else "Resume trajectory"
            if flight and flight.time > flight.start
            else "Follow trajectory"
        )
        self.virtual_slider.blockSignals(True)
        if c.reference:
            self.virtual_slider.setRange(
                round(c.reference.points[0, 0] * 1000), round(c.reference.points[-1, 0] * 1000)
            )
        if flight and not self.virtual_slider.isSliderDown():
            self.virtual_slider.setValue(round(flight.time * 1000))
        self.virtual_slider.blockSignals(False)
        self.virtual_clock.setText(
            f"{'PLAYING' if flight.playing else 'ENDED' if flight.time >= flight.end else 'PAUSED'} · "
            f"{flight.time:.2f} / {flight.end:.2f} s\nRange {flight.distance:,.1f} m"
            + (" · direction undefined at antenna origin" if flight.angles is None else "")
            if flight
            else "Ready to follow the reference trajectory"
            if ready
            else "Set the antenna location and run OpenRocket or load a trajectory in Mission & wind"
        )
        self.virtual_pose.setText(f"Simulated pose\nAZ {worker.pose[0]:.1f}° · EL {worker.pose[1]:.1f}°")
        self.pointer_pose.setText("VIRTUAL ANTENNA POINTER")
        self.mount.set_pose(*worker.pose)

    def load_mission(self):
        if self.controller.recorder:
            return self.guard(
                lambda: (_ for _ in ()).throw(ValueError("Stop recording before loading another mission"))
            )
        path, _ = QFileDialog.getOpenFileName(self, "Open mission", "", "Mission (*.json)")
        if path:
            mission = self.guard(lambda: Mission.load(path))
            if mission:
                self.controller.hold("Mission loaded")
                self.controller.mission = mission
                self.controller.history.clear()
                self.controller.track.clear()
                self.controller.reference = None
                self.model_path.setText(mission.model)
                self.wind_to_table()
                self.weather_msl.setValue(mission.altitude_msl)
                self.motor_status.setText(f"Bundled motors + {len(mission.motor_files)} custom files")

    def save_mission(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save mission", "mission.json", "JSON (*.json)")
        if path:
            self.guard(lambda: self.controller.mission.save(path))

    def open_flight(self):
        c = self.controller
        if c.flight_busy or c.recorder:
            message = (
                "Wait for the current flight file operation"
                if c.flight_busy
                else "Stop logging before opening another flight"
            )
            return self.guard(lambda: (_ for _ in ()).throw(ValueError(message)))
        path, _ = QFileDialog.getOpenFileName(
            self, "Open flight", str(c.last_flight_path or c.data_dir), "Rocket flight (*.rktflight)"
        )
        if path:
            self.guard(lambda: c.open_flight(path))
            if c.flight_busy:
                self.flight_file_status.setText("Opening flight…")
                self.statusBar().showMessage("Opening flight…")

    def save_flight(self):
        c = self.controller
        if c.flight_busy:
            return self.guard(
                lambda: (_ for _ in ()).throw(ValueError("Wait for the current flight file operation"))
            )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save flight",
            str(c.last_flight_path or c.data_dir / "flight.rktflight"),
            "Rocket flight (*.rktflight)",
        )
        if path:
            if not path.lower().endswith(".rktflight"):
                path += ".rktflight"
            self.guard(lambda: c.save_flight(path))
            if c.flight_busy:
                self.flight_file_status.setText("Saving flight… Logging continues while the file is written.")
                self.statusBar().showMessage("Saving flight…")

    def add_wind_row(self, height, speed, direction):
        row = self.wind_table.rowCount()
        self.wind_table.insertRow(row)
        for col, value in enumerate([height, speed, direction]):
            set_cell(self.wind_table, row, col, f"{value:.2f}")

    def remove_wind_row(self):
        row = self.wind_table.currentRow()
        if row >= 0:
            self.wind_table.removeRow(row)

    def wind_to_table(self):
        self.wind_table.setRowCount(0)
        for layer in self.controller.mission.wind:
            direction = math.degrees(math.atan2(-layer["east"], -layer["north"])) % 360
            self.add_wind_row(layer["height"], math.hypot(layer["east"], layer["north"]), direction)
        self.wind_status.setText(self.controller.mission.wind_source)

    def apply_wind(self):
        layers = []
        for row in range(self.wind_table.rowCount()):
            height, speed, direction = [float(self.wind_table.item(row, col).text()) for col in range(3)]
            if not 0 <= speed <= 150 or not 0 <= direction <= 360:
                raise ValueError("Wind speed must be 0–150 m/s; direction 0–360°")
            east, north = wind_from(speed, direction)
            layers.append(dict(height=height, east=east, north=north))
        validate_wind(layers)
        self.controller.mission.wind = layers
        self.controller.mission.wind_source = "Manual profile · endpoint wind held outside layer coverage"
        self.wind_status.setText(self.controller.mission.wind_source)
        self.controller.log("Wind profile updated", {"layers": layers})

    def import_wind(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import wind profile", "", "JSON (*.json)")
        if path:

            def load():
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                validate_wind(data["layers"])
                self.controller.mission.wind = data["layers"]
                self.controller.mission.wind_source = data.get("source", "Imported manual wind")
                self.wind_to_table()

            self.guard(load)

    def export_wind(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export wind", "wind.json", "JSON (*.json)")
        if path:
            from .domain import write_json

            self.guard(
                lambda: write_json(
                    path,
                    dict(
                        schema_version=1,
                        layers=self.controller.mission.wind,
                        source=self.controller.mission.wind_source,
                    ),
                )
            )

    def fetch_weather(self):
        m = copy.deepcopy(self.controller.mission)
        if not m.site_configured:
            self.guard(lambda: (_ for _ in ()).throw(ValueError("Configure the launch site first")))
            return
        self.wind_status.setText("Retrieving weather…")
        when = self.weather_time.text()
        height = self.weather_msl.value()
        self.controller.mission.altitude_msl = height
        self.controller.submit("weather", lambda: weather_profile(m.latitude, m.longitude, height, when))

    def choose_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "OpenRocket model", "", "OpenRocket (*.ork)")
        if path:
            self.model_path.setText(path)
            self.controller.mission.model = path

    def choose_motors(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Additional motor curves (cancel keeps existing selection)",
            "",
            "Motor curves (*.eng *.rse)",
        )
        if paths:
            self.controller.mission.motor_files = paths
            self.motor_status.setText(f"Bundled motors + {len(paths)} custom files · saved with mission")

    def start_simulation(self):
        self.simulation_failed = False
        self.controller.mission.model = self.model_path.text()
        self.controller.run_simulation()
        self.sim_status.setText("Running isolated OpenRocket job…")

    def load_reference(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Normalized ENU reference (with matching .json manifest)", "", "CSV (*.csv)"
        )
        if path:
            self.controller.submit("reference", lambda: Trajectory.load(path))

    def set_reference(self, reference):
        c = self.controller
        if c.recorder:
            raise ValueError("Stop recording before changing the session's reference baseline")
        origin = reference.manifest.get("origin")
        if c.mode == "LIVE":
            if reference.manifest.get("synthetic") or not origin or not c.mission.site_configured:
                raise ValueError(
                    "Live comparison requires a georeferenced reference and configured launch origin"
                )
            expected = [c.mission.latitude, c.mission.longitude, c.mission.altitude]
            if any(abs(a - b) > tol for a, b, tol in zip(origin, expected, [1e-6, 1e-6, 0.1])):
                raise ValueError("Reference launch origin differs from this mission")
        c.reference = reference
        c.log("Reference selected: " + reference.manifest.get("name", "Imported trajectory"))
        self.refresh_plots(force=True)

    def import_legacy_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Documented Zephyrus CSV", "", "CSV (*.csv)")
        if not path:
            return
        parent = QFileDialog.getExistingDirectory(
            self, "Parent folder for converted session", str(self.controller.data_dir)
        )
        if parent:
            from .legacy import import_legacy

            mission = copy.deepcopy(self.controller.mission)
            self.controller.submit("legacy_import", lambda: import_legacy(path, parent, mission))
            self.add_event("Importing legacy CSV; raw packet quality is unavailable")

    def open_session(self):
        path = QFileDialog.getExistingDirectory(
            self, "Recorded session folder", str(self.controller.data_dir)
        )
        if path:
            self.guard(lambda: self.controller.open_replay(path))
            self.mode.setCurrentText(self.controller.mode)

    def export_session(self):
        if not self.controller.reader:
            return self.guard(lambda: (_ for _ in ()).throw(ValueError("Open a recorded session first")))
        path, _ = QFileDialog.getSaveFileName(self, "Export session", "telemetry.csv", "CSV (*.csv)")
        if path:
            self.guard(lambda: self.controller.reader.export_csv(path))

    def toggle_replay(self):
        c = self.controller
        if c.mode != "REPLAY" or not c.reader:
            return
        c.replay_playing = not c.replay_playing
        if c.replay_playing:
            c.replay_video()
        else:
            c.stop_video()

    def change_speed(self):
        self.controller.replay_speed = [0.25, 0.5, 1, 2, 4][self.speed.currentIndex()]
        if self.controller.replay_playing:
            self.controller.replay_video()

    def task_done(self, name, result):
        if name == "simulation":
            self.simulation_failed = isinstance(result, Exception)
        if isinstance(result, Exception):
            if name == "weather":
                self.wind_status.setText(str(result))
            elif name == "simulation":
                self.sim_status.setText(str(result))
                scroll_bar = self.simulation_scroll.verticalScrollBar()
                scroll_bar.setValue(scroll_bar.maximum())
            elif name in {"flight_loaded", "flight_saved"}:
                self.flight_file_status.setText(str(result))
                self.statusBar().showMessage(str(result))
                QMessageBox.warning(self, "Flight file", str(result))
            return
        if name == "flight_loaded":
            c = self.controller
            self.model_path.setText(c.mission.model)
            self.weather_msl.setValue(c.mission.altitude_msl)
            self.motor_status.setText(f"Bundled motors + {len(c.mission.motor_files)} custom files")
            self.wind_to_table()
            self.sim_status.setText(
                "Saved simulation reference restored" if c.reference else "No saved simulation reference"
            )
            self.flight_file_status.setText(f"Opened {result.path.name} · {result.metadata['scope']}")
            self.statusBar().showMessage(f"Opened {result.path}")
            self.setWindowTitle(f"Rocket GNC Monitor · {result.path.name}")
            self.pages.setCurrentIndex(5 if c.reader else 4)
            self.refresh_plots(force=True)
        elif name == "flight_saved":
            message = f"Saved {Path(result['path']).name} · {result['samples']:,} samples · {result['scope']}"
            if result["video_scope"].startswith("Finalized"):
                message += " · Video includes finished segments; stop logging and save again to include the last segment."
            self.flight_file_status.setText(message)
            self.statusBar().showMessage(f"Saved {result['path']}")
            self.setWindowTitle(f"Rocket GNC Monitor · {Path(result['path']).name}")
        if name == "cameras":
            self.camera_devices = list(
                dict.fromkeys((description, str(source)) for description, source in result)
            )
            self.camera_choice_signature = None
            self.update_camera_choices()
        elif name == "weather":
            request = result["request"]
            current = self.controller.mission
            if (request["latitude"], request["longitude"]) != (current.latitude, current.longitude):
                self.wind_status.setText("Site changed during retrieval; request weather again")
                return
            self.controller.mission.wind = result["layers"]
            self.controller.mission.weather_raw = result
            self.controller.mission.wind_source = result["source"]
            self.wind_to_table()
            self.controller.log("Weather profile retrieved", {"source": result["source"]})
        elif name in {"simulation", "reference"}:
            self.guard(lambda: self.set_reference(result))
            if name == "simulation":
                self.sim_status.setText(
                    "Nominal simulation complete · controlled-vehicle physics unqualified"
                )
        elif name == "recording_closed":
            _, error, path = result
            self.add_event(error or f"Session saved: {path}")
        elif name == "legacy_import":
            self.guard(lambda: self.controller.open_replay(result))

    def refresh_connections(self, now):
        c = self.controller
        state, age = c.rocket_link_state(now)
        simulated = c.mode == "DEMO"
        live_ready = simulated or c.ground_connected
        pointer_ready = c.board_layout != "launch" and (simulated or c.pointer_connected)
        descriptions = {
            "DISCONNECTED": (
                "Ground station disconnected",
                "Select the ground-station port and click Connect.",
                "muted",
            ),
            "PAUSED": (
                "Ground station connected",
                "Click Start Polling to receive rocket telemetry.",
                "gold",
            ),
            "WAITING": (
                "Waiting for the rocket",
                "Polling · waiting for rocket telemetry.",
                "gold",
            ),
            "RECEIVING": (
                "Rocket telemetry live",
                "The ground station is receiving fresh, valid telemetry from the rocket.",
                "accent",
            ),
            "STALE": (
                "Rocket telemetry lost / stale",
                "Rocket telemetry has stopped arriving.",
                "red",
            ),
            "DEMO": (
                "Zephyrus test flight · " + c.demo_station,
                "Recorded telemetry; simulated commands and video test pattern. No physical board connections.",
                "gold",
            ),
            "REPLAY": (
                "Recorded session",
                "Reviewing saved telemetry and video.",
                "muted",
            ),
        }
        title, detail, color = descriptions[state]
        self.connection_headline.setText(title)
        self.connection_headline.setStyleSheet(f"color: {COLORS[color]};")
        self.connection_help.setText(detail)
        for key, role in [("ground", "telemetry"), ("pointer", "pointer")]:
            text = (
                "VIRTUAL / SIMULATED"
                if role == "pointer" and c.virtual_pointer
                else "SIMULATED"
                if simulated
                else "OFFLINE / REPLAY"
                if c.mode == "REPLAY"
                else c.states.get(role, "Remote transport pending").upper()
            )
            shade = (
                "accent"
                if text == "CONNECTED"
                else "gold"
                if text in {"CONNECTING", "SIMULATED", "VIRTUAL / SIMULATED"}
                else "muted"
            )
            self.connection_tiles[key].setText("● " + text)
            self.connection_tiles[key].setStyleSheet(f"color: {COLORS[shade]}; padding: 9px 0;")
        self.connection_tiles["rocket"].setText(
            "● "
            + {
                "RECEIVING": "TELEMETRY LIVE",
                "PAUSED": "POLLING STOPPED",
                "WAITING": "AWAITING TELEMETRY",
                "STALE": "LOST / STALE",
                "DEMO": "RECORDED TELEMETRY",
                "REPLAY": "RECORDED",
                "DISCONNECTED": "NOT CONNECTED",
            }[state]
        )
        self.connection_tiles["rocket"].setStyleSheet(f"color: {COLORS[color]}; padding: 9px 0;")
        self.link_metrics["age"].setText(f"{age:.1f} s ago" if age is not None else "—")
        rssi = c.latest.rssi if c.latest and age is not None else None
        self.link_metrics["rssi"].setText(f"{rssi:.0f} dBm" if finite(rssi) else "—")
        for key in ["accepted", "rejected"]:
            self.link_metrics[key].setText(str(c.stats[key]) if c.mode == "LIVE" else "—")
        self.radio_explanation.setText("Polling started" if c.polling else "Polling stopped")
        readiness = (
            "Virtual pointer connected" + (" · Ground station connected" if c.ground_connected else "")
            if c.virtual_pointer
            else "Demo controls enabled"
            if simulated
            else "Replay"
            if c.mode == "REPLAY"
            else "Ground station connected · Antenna pointer connected"
            if c.ground_connected and c.pointer_connected
            else "Antenna pointer connected"
            if c.pointer_connected
            else "Ground station connected"
            if c.ground_connected
            else "Select a serial port to connect"
        )
        self.control_readiness.setText(readiness)
        self.control_readiness.setStyleSheet(f"color: {COLORS['accent' if live_ready else 'gold']};")
        for control in self.live_controls:
            control.setEnabled(live_ready)
            control.setToolTip(
                "" if live_ready else "Connect the ground station in Live mode to unlock this control."
            )
        for control in self.pointer_controls:
            control.setEnabled(pointer_ready and (not c.pointer_pending or c.virtual_pointer is not None))
            control.setToolTip("" if pointer_ready else "Connect the antenna pointer.")
        self.zero_button.setEnabled(
            pointer_ready and (not c.pointer_pending or c.virtual_pointer is not None)
        )
        can_track = False
        if pointer_ready and not c.virtual_pointer and (simulated or (c.ground_connected and c.polling)):
            try:
                c.tracking_target()
                can_track = True
            except ValueError:
                pass
        self.track_button.setText("Start tracking")
        self.track_button.setEnabled(can_track and not c.tracking and not c.pointer_pending)
        # Hold remains available to cancel queued work even while other controls relock.
        self.hold_button.setEnabled(
            c.tracking or c.pointer_pending is not None or c.virtual_pointer is not None
        )
        self.hold_button.setText("Hold virtual pointer" if c.virtual_pointer else "Stop tracking")
        self.poll_button.setEnabled(c.ground_connected)
        self.poll_button.setText("Stop Polling" if c.polling else "Start Polling")
        self.record_button.setEnabled(bool(c.recorder) or (c.mode != "REPLAY" and live_ready))
        self.freeze_gps.setEnabled(
            c.mode == "LIVE" and c.ground_connected and c.pointer_connected and c.latest is not None
        )
        self.freeze_gps.setText("Unfreeze GPS" if c.frozen_ground else "Send to AntPtr")
        if c.frozen_ground:
            gps = c.frozen_ground
            self.ground_gps.setText(
                f"FROZEN · {'FIX' if gps['gnd_fix'] else 'NO FIX'}\n{gps['gnd_lat']:.5f}°, {gps['gnd_lon']:.5f}°\nAltitude {gps['gnd_alt']} m"
            )
        elif c.latest:
            gps = legacy_values(c.latest)
            self.ground_gps.setText(
                f"{'FIX' if gps['gnd_fix'] else 'NO FIX'}\n{gps['gnd_lat']}°, {gps['gnd_lon']}°\nAltitude {gps['gnd_alt']} m"
            )
        else:
            self.ground_gps.setText("Awaiting telemetry")
        self.rocket_panel.set_controls_enabled()
        if hasattr(self, "legacy_actions"):
            for key, role in [("ground", "telemetry"), ("pointer", "pointer")]:
                if role not in self.port_widgets:
                    self.legacy_actions[key].setEnabled(False)
                    continue
                active = c.states[role] in {"Connected", "Connecting"}
                self.legacy_actions[key].setEnabled(
                    c.mode == "LIVE" and (active or bool(self.port_widgets[role][0].currentData()))
                )
                self.legacy_actions[key].setText(
                    ("Disconnect " if active else "Connect ")
                    + ("Ground Station" if key == "ground" else "Antenna Pointer")
                )
            self.legacy_actions["refresh"].setEnabled(c.mode == "LIVE")
            for key, control in [("poll", self.poll_button), ("log", self.record_button)]:
                self.legacy_actions[key].setEnabled(control.isEnabled())
                self.legacy_actions[key].setText(control.text())
            for key in ("up", "down", "left", "right", "zero"):
                self.legacy_actions[key].setEnabled(
                    pointer_ready and (not c.pointer_pending or c.virtual_pointer is not None)
                )
        self.align_time_button.setEnabled(c.latest is not None and (c.mode == "REPLAY" or live_ready))
        for control in [self.play, self.step_button, self.replay_slider, self.speed]:
            control.setEnabled(c.mode == "REPLAY" and c.reader is not None)

    def refresh(self):
        now = time.monotonic()
        if now - self.last_ui < 0.1:
            return
        self.last_ui = now
        c = self.controller
        s = c.latest
        if hasattr(self, "flight_actions"):
            self.flight_actions["open"].setEnabled(not c.flight_busy and c.recorder is None)
            self.flight_actions["save"].setEnabled(not c.flight_busy)
        self.wall_clock.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-4])
        self.mission_label.setText(c.mission.name)
        self.mode.blockSignals(True)
        self.mode.setCurrentText(c.mode)
        self.mode.blockSignals(False)
        self.demo_bar.setVisible(c.mode == "DEMO")
        self.serial_bar.setVisible(c.mode != "DEMO")
        if c.demo:
            self.demo_station.blockSignals(True)
            self.demo_station.setCurrentText(c.demo_station)
            self.demo_station.blockSignals(False)
            self.demo_play.setText("Pause" if c.demo_playing else "Play")
            for control in (self.demo_station, self.demo_launch, self.demo_start, self.demo_slider):
                control.setEnabled(c.recorder is None)
                control.setToolTip("Stop logging to seek or change recordings" if c.recorder else "")
            self.demo_slider.blockSignals(True)
            self.demo_slider.setMaximum(math.ceil(c.demo.duration * 1000))
            if not self.demo_slider.isSliderDown():
                self.demo_slider.setValue(round(c.demo_time * 1000))
            self.demo_slider.blockSignals(False)
            self.demo_clock.setText(f"{c.demo_time:.1f} / {c.demo.duration:.1f} s")
        self.update_port_choices()
        self.update_camera_choices()
        for role, (combo, action, status) in self.port_widgets.items():
            active = c.states[role] in {"Connected", "Connecting"}
            status.setText(
                "Virtual / simulated" if role == "pointer" and c.virtual_pointer else c.states[role]
            )
            status.setStyleSheet("color: " + COLORS["accent" if c.states[role] == "Connected" else "muted"])
            combo.setEnabled(c.mode == "LIVE" and not active)
            action.setEnabled(c.mode == "LIVE" and not active and bool(combo.currentData()))
            self.disconnect_buttons[role].setEnabled(c.mode == "LIVE" and active)
            self.refresh_buttons[role].setEnabled(c.mode == "LIVE" and not active)
        self.record_button.setText("Stop Logging" if c.recorder else "Start Logging")
        self.refresh_connections(now)
        age = max(0, now - s.received) if s else None
        values = dict(
            altitude=s.altitude if s else None,
            velocity=s.velocity if s else None,
            flight=s.t - c.flight_zero if s and c.time_aligned else None,
            battery=s.battery if s else None,
            rssi=s.rssi if s else None,
            age=age,
        )
        for key, value in values.items():
            self.metrics[key].setText(f"{value:,.1f}" if finite(value) else "—")
        issues = []
        if c.mode == "DEMO":
            state = "PLAYING" if c.demo_playing else "ENDED" if c.demo_time >= c.demo.duration else "PAUSED"
            issues.append(f"DEMO · Zephyrus {c.demo_station} · {state} · simulated commands")
            if s and s.details.get("position_warning"):
                issues.append(s.details["position_warning"])
        elif c.mode == "REPLAY":
            issues.append("REPLAY · physical pointer disabled")
        else:
            issues.append("LIVE · " + (s.phase if s else "awaiting telemetry"))
            if c.virtual_pointer:
                issues.append("VIRTUAL POINTER · SIMULATED MOTION")
            if not c.ground_connected:
                issues.append("GROUND STATION DISCONNECTED")
        if (c.mode != "LIVE" or c.polling) and (
            age is None
            or (age > c.mission.freshness and c.mode != "REPLAY" and (c.mode != "DEMO" or c.demo_playing))
        ):
            issues.append("TELEMETRY STALE / ABSENT")
        if s and s.battery is not None and s.battery < c.mission.low_battery:
            issues.append("LOW BATTERY")
        if c.recorder:
            issues.append(c.recorder.error or "RECORDING")
        if c.ui_drops:
            issues.append(f"DISPLAY BUFFER LOSS: {c.ui_drops}")
        self.banner.setText("  ·  ".join(issues))
        self.alert_status.setText(
            "  ·  ".join(
                a["message"] + (" (acknowledged)" if a["acknowledged"] else " (new)")
                for a in c.alerts.values()
            )
            or "No active alerts"
        )
        self.banner.setStyleSheet(
            "color: "
            + (COLORS["red"] if any(not a["acknowledged"] for a in c.alerts.values()) else COLORS["muted"])
        )
        self.pointer_sent.setText(
            "Last target: " + (" / ".join(f"{v:.1f}°" for v in c.pointer_sent) if c.pointer_sent else "—")
        )
        self.pointer_status.setText(c.pointer_status)
        if (c.tracking or c.mode == "REPLAY") and c.pointer_sent:
            self.azimuth.blockSignals(True)
            self.elevation.blockSignals(True)
            self.azimuth.setText(str(c.pointer_sent[0]))
            self.elevation.setText(str(c.pointer_sent[1]))
            self.azimuth.blockSignals(False)
            self.elevation.blockSignals(False)
            self.mount.set_pose(*c.pointer_sent)
        self.pointer_pose.setText("TRACKING" if c.tracking else "MANUAL CONTROL")
        if c.pointer_sent is not None:
            self.mount.set_pose(*c.pointer_sent)
        if c.mode == "REPLAY":
            self.pointer_pose.setText("REPLAY")
            if not c.pointer_sent:
                self.mount.set_pose(0, 0)
        self.refresh_virtual_pointer()
        for stream, channel in c.video_streams.items():
            widgets = self.video_widgets[stream]
            worker = channel.worker
            if worker:
                frame_age = now - worker.last_frame if worker.last_frame else None
                state = worker.error or (
                    f"{'TEST PATTERN' if worker.kind == 'demo' else worker.kind.upper()} · frame age {frame_age:.1f}s"
                    if frame_age is not None
                    else "Starting video…"
                )
                if frame_age is not None and frame_age > 2:
                    state += " · FROZEN / STALE"
            else:
                state = channel.error or (
                    "Starting video…"
                    if channel.config
                    else "Stopping camera…"
                    if channel.reserved_cameras
                    else "Video stopped · last frame retained"
                    if self.last_pixmaps[stream]
                    else "Camera not started"
                )
            widgets["status"].setText(state)
            widgets["status"].setToolTip(state)
            busy = bool(channel.config or channel.reserved_cameras)
            widgets["camera"].setEnabled(c.mode != "REPLAY" and not busy)
            widgets["start"].setEnabled(
                c.mode != "REPLAY"
                and (c.mode == "DEMO" or c.ground_connected)
                and bool(widgets["camera"].currentData())
                and not busy
            )
            widgets["stop"].setEnabled(bool(channel.config))
        self.attitude.refresh()
        if now - self.last_table > 0.5:
            self.last_table = now
            self.refresh_actuators()
            self.rocket_panel.refresh()
            self.details.setPlainText(json.dumps(s.to_dict() if s else {}, indent=2))
        self.link_stats.setText("  ·  ".join(f"{k.title()}: {v}" for k, v in c.stats.items()))
        m = c.mission
        self.mission_summary.setText(
            f"{m.name} · "
            + (f"{m.launch_site_name} · " if m.launch_site_name else "")
            + (
                f"Launch {m.latitude:.6f}°, {m.longitude:.6f}° / {m.altitude:.1f} m ellipsoid"
                if m.site_configured
                else "Launch origin not configured"
            )
            + f" · Legacy height: {m.legacy_altitude}"
        )
        if c.reader:
            self.replay_slider.setMaximum(int(c.reader.duration * 1000))
            if not self.replay_slider.isSliderDown():
                self.replay_slider.setValue(int(c.replay_time * 1000))
            self.replay_clock.setText(f"{c.replay_time:.2f} / {c.reader.duration:.2f} s")
            self.session_status.setText(
                f"{c.reader.path} · {c.reader.count} samples · "
                + (
                    "recording snapshot"
                    if c.reader.manifest.get("snapshot")
                    else "complete"
                    if c.reader.manifest.get("complete")
                    else "incomplete / recovered"
                )
            )
        self.refresh_plots()

    def refresh_actuators(self):
        c = self.controller
        channels = {f"Canard {i + 1}": {} for i in range(c.mission.canard_count)}
        channels.update({f"Tab {i + 1}": {} for i in range(4)})
        if c.latest:
            channels.update(c.latest.actuators)
        self.actuators.setRowCount(len(channels))
        for row, (name, values) in enumerate(channels.items()):
            cells = [name] + [
                f"{values[k]:.2f}" if finite(values.get(k)) else "—" for k in ("demand", "measured", "drive")
            ]
            cells.append(
                "Recorded drive"
                if c.mode == "DEMO" and "drive" in values
                else "Drive only"
                if "drive" in values
                else "Unavailable"
            )
            for col, text in enumerate(cells):
                set_cell(self.actuators, row, col, text)

    def refresh_plots(self, force=False):
        if not hasattr(self, "trajectory_plot"):
            return
        c = self.controller
        key = (
            c.mode,
            c.latest.sequence if c.latest else None,
            c.latest.utc if c.latest else None,
            c.flight_zero,
            id(c.reference),
            (id(c.virtual_flight), c.virtual_flight.time) if c.virtual_flight else None,
            self.view.currentIndex(),
        )
        if key == self.last_sample_key and not force:
            return
        self.last_sample_key = key
        history = list(c.history)[-1200:]
        times = [s.t - c.flight_zero for s in history]
        self.altitude_curve.setData(times, [s.altitude if finite(s.altitude) else np.nan for s in history])
        for i in range(3):
            self.rate_curves[i].setData(times, [s.rates[i] if s.rates else np.nan for s in history])
            self.angle_curves[i].setData(times, [s.attitude[i] if s.attitude else np.nan for s in history])
        view = self.view.currentIndex()

        def project(points):
            points = np.asarray(points, dtype=float)
            if points.size == 0:
                return [], []
            if view == 0:
                return points[:, 0], points[:, 1]
            if view == 1:
                return points[:, 0], points[:, 2]
            return points[:, 0] * 0.85 - points[:, 1] * 0.52, points[:, 2] * 0.94 - (
                points[:, 0] * 0.52 + points[:, 1] * 0.85
            ) * 0.34

        self.trajectory_plot.setLabel(
            "left", ["North (m)", "Up (m)", "Projected up (m)"][view], color=COLORS["muted"]
        )
        self.trajectory_plot.setLabel(
            "bottom", ["East (m)", "East (m)", "Projected horizontal (m)"][view], color=COLORS["muted"]
        )
        x, y = project(list(c.track))
        self.actual_curve.setData(x, y)
        self.actual_marker.setData(x[-1:] if len(x) else [], y[-1:] if len(y) else [])
        self.trajectory_legend.setVisible(c.reference is not None)
        self.virtual_mount_marker.setData([], [])
        if c.reference:
            reference = c.reference
            x, y = project(reference.points[:, 1:])
            self.reference_curve.setData(x, y)
            self.altitude_reference.setData(reference.points[:, 0], reference.points[:, 3])
            mark = reference.at(c.latest.t - c.flight_zero) if c.latest and c.time_aligned else None
            virtual = c.virtual_flight if c.virtual_pointer else None
            if virtual and virtual.reference is reference:
                mark = virtual.position
                px, py = project([virtual.mount_position])
                self.virtual_mount_marker.setData(px, py)
            x, y = project([mark] if mark else [])
            self.reference_marker.setData(x, y)
            text = reference.manifest.get("name", "Reference")
            if virtual and virtual.reference is reference:
                text += f" · VIRTUAL {virtual.time:.2f} s · gold: target · purple: antenna"
            if c.latest and c.latest.enu and mark and not virtual:
                residual = np.linalg.norm(np.asarray(c.latest.enu) - np.asarray(mark))
                text += f" · same-time position residual {residual:.1f} m"
            if not c.time_aligned and not virtual:
                text += " · flight time not aligned"
            self.reference_label.setText(text)
        else:
            self.reference_curve.setData([], [])
            self.reference_marker.setData([], [])
            self.altitude_reference.setData([], [])
            self.reference_label.setText(
                "Recorded GPS offsets from launch + reported barometric altitude · no simulation reference"
                if c.latest and c.latest.details.get("demo_station")
                else "No reference selected · actual position requires a verified origin and altitude convention"
            )

    def rocket_pose_reference(self):
        dialog = self.flight_3d_dialog
        if (getattr(self, "profile_locked", False)
                and getattr(self, "profile", None).layout == "away"):
            dialog = getattr(self, "away_flight_dialog", None)
        if (dialog is not None and dialog.reference is self.controller.reference
                and dialog.scene is not None and dialog.view.frame is not None):
            state = "Playing" if dialog.playback.playing and dialog.isVisible() else "Paused"
            if dialog.link.isChecked() and dialog.isVisible():
                c = self.controller
                available = ((c.virtual_flight and c.virtual_flight.reference is dialog.reference)
                             or (c.latest and c.time_aligned))
                state = "Monitor time" if available else "Time unavailable"
            return dialog.scene, dialog.view.frame, state
        return None

    def open_flight_3d(self):
        from .flight_view import Flight3DDialog

        if self.flight_3d_dialog is None:
            self.flight_3d_dialog = Flight3DDialog(self.controller, self)
        self.flight_3d_dialog.show()
        self.flight_3d_dialog.raise_()
        self.flight_3d_dialog.activateWindow()

    def closeEvent(self, event):
        if self.flight_3d_dialog:
            self.flight_3d_dialog.close()
        self.mount.animation.stop()
        self.attitude.shutdown()
        self.controller.shutdown()
        event.accept()
