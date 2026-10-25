"""Launch, away and video workspaces over one instrument owner.

The v0 widgets remain the single source of control callbacks and telemetry state.
Only their presentation changes: no second Controller, duplicate serial worker,
hidden operational tab, or separate command implementation is introduced here.
"""

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QTableWidget,
    QSizePolicy,
    QHeaderView,
    QFileDialog,
)

from .ui import MainWindow as InstrumentWindow, button, label
from .widgets import ComboBox
from .flight_view import Flight3DDialog
from .media import WIDTH, HEIGHT, camera_devices
from .domain import finite
from .table_cells import set_cell
from .station_profile import StationProfile
from .site_presets import mission_for_station
from .rocket_state_badge import RocketStateBadge


COMPACT_STYLE = """
QWidget { font-size: 11px; }
QLabel#heading { font-size: 20px; }
QLabel#section { font-size: 12px; font-weight: 600; }
QLabel#eyebrow { font-size: 10px; letter-spacing: 1px; }
QLabel#metric { font-size: 24px; }
QPushButton { padding: 4px 7px; border-radius: 4px; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox { padding: 3px 5px; }
QHeaderView::section { padding: 3px 2px; font-size: 10px; }
QTableWidget::item { padding: 1px 3px; }
QFrame#card { border-radius: 7px; }
QCheckBox { spacing: 4px; }
QCheckBox::indicator { width: 12px; height: 12px; }
"""


def row(*widgets, stretch=False):
    layout = QHBoxLayout()
    layout.setSpacing(5)
    for widget in widgets:
        layout.addWidget(widget)
        widget.show()
    if stretch:
        layout.addStretch()
    return layout


class EmbeddedFlightView(Flight3DDialog):
    """Keep the mandatory flight panel visible when Escape bubbles from a child."""

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)


class StationDisplay(QMainWindow):
    """A companion display shares ownership and cannot silently lose its panels."""

    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Window)
        self.owner = owner
        self.controller = owner.controller
        self.setWindowTitle("Rocket GNC Monitor v0a · 2 / Systems")
        self.setWindowIcon(owner.windowIcon())
        self.resize(1920, 1020)
        self.setMinimumSize(1280, 800)

    def closeEvent(self, event):
        if self.owner.closing or self.owner.station_mode != "base":
            event.accept()
        else:
            event.ignore()
            self.statusBar().showMessage(
                "Launch telemetry uses both displays. Relaunch and choose Away telemetry or Video for one window."
                if self.owner.profile_locked else
                "Launch station uses both displays. Choose Away station or Video station for one window.", 8000
            )


class StationWindow(InstrumentWindow):
    def __init__(self, data_dir, station=None, auto_place=True, profile=None):
        self.workspace_ready = False
        self.closing = False
        self.auto_place = auto_place
        self.station_mode = "base"
        self.profile_locked = profile is not None
        self.station_path = Path(data_dir) / "station-layout.json"
        if profile is not None:
            station = profile.layout
        elif station is None:
            try:
                station = json.loads(self.station_path.read_text()).get("station", "base")
            except (OSError, ValueError, AttributeError):
                station = "base"
        self.profile = profile or StationProfile(
            station="away1" if station in ("away", "video") else "base",
            role="video" if station == "video" else "telemetry",
        )
        self.video_wall = None
        self.away_flight_dialog = None
        self.launch_station = self.profile_locked and self.profile.station == "base"
        self.launch_links = None
        self.launch_uplink = None
        self.wifi_panel = None
        super().__init__(data_dir, video_channels=self.profile.local_channels,
                         video_labels={key: self.profile.channel_labels[key] for key in self.profile.local_channels},
                         board_layout=("launch" if self.launch_station
                                       else "away") if self.profile_locked else "legacy",
                         vehicle=self.profile.vehicle,
                         initial_mission=mission_for_station(self.profile) if self.profile_locked else None)
        self.setWindowTitle("Rocket GNC Monitor v0a · 1 / Flight & links" if self.launch_station else "Rocket GNC Monitor v0a · 1 / Flight & antenna")
        self.setMinimumSize(1280, 800)
        self.resize(1920, 1020)
        # Keep old containers alive: their child widgets and signals are reused.
        self.instrument_storage = self.takeCentralWidget()
        self.instrument_storage.setParent(self)
        self.instrument_storage.hide()
        self.cards = {}
        self.companion = StationDisplay(self)
        self.companion.setStyleSheet(self.styleSheet() + COMPACT_STYLE)
        self.build_workspace()
        # One QAction can be installed on two windows without duplicate bindings.
        for action in (*self.legacy_actions.values(), *self.flight_actions.values()):
            self.companion.addAction(action)
        self.companion.addAction(self.settings_action)
        self.workspace_ready = True
        self.set_daylight(self.controller.settings.daylight, persist=False)
        self.refresh_actuators()
        self.set_station_mode(station if station in ("base", "away", "video") else "base", persist=False)
        self.last_ui = 0
        self.refresh()
        QApplication.instance().screenAdded.connect(self.screens_changed)
        QApplication.instance().screenRemoved.connect(self.screens_changed)

    def card(self, key, title):
        panel = QFrame()
        panel.setObjectName("card")
        panel.setProperty("stationCard", key)
        panel.setMinimumSize(0, 0)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(5)
        layout.addWidget(label(title, "section"))
        self.cards[key] = panel
        return layout

    def attach(self, layout, widget, stretch=0):
        widget.setMinimumSize(0, 0)
        widget.setMaximumSize(16777215, 16777215)
        widget.setSizePolicy(
            QSizePolicy.Policy.Ignored if stretch else QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored if stretch else QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(widget, stretch)
        widget.show()

    def build_workspace(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 9, 12, 5)
        outer.setSpacing(6)
        self.station_selector = ComboBox()
        self.station_selector.addItem("Launch station · two displays", "base")
        self.station_selector.addItem("Away station · one display", "away")
        self.station_selector.addItem("Video station · two channels", "video")
        self.station_selector.setAccessibleName("Station workspace")
        self.station_selector.currentIndexChanged.connect(
            lambda: self.set_station_mode(self.station_selector.currentData())
        )
        self.workspace_title = label("ROCKET / v0a", "heading")
        heading = row(self.workspace_title, self.station_selector, self.mission_label)
        self.station_identity = label(
            f"{self.profile.station_label} · {self.profile.role.title()} · {self.profile.vehicle_label}"
            + (f" · {' / '.join(self.profile.telemetry_targets)}"
               if self.profile.role == "telemetry" and self.profile.vehicle == "iris" else ""),
            "eyebrow",
        )
        self.station_identity.setToolTip("Station and role are selected in the startup wizard.")
        heading.insertWidget(1, self.station_identity)
        self.station_identity.setVisible(self.profile_locked)
        self.station_selector.setVisible(not self.profile_locked)
        heading.addStretch()
        heading.addWidget(label("DATA SOURCE", "eyebrow"))
        for widget in (self.mode, self.poll_button, self.record_button):
            heading.addWidget(widget)
            widget.show()
        heading.addWidget(button("Settings…", self.open_settings))
        outer.addLayout(heading)
        if self.launch_station:
            self.launch_network_notice = label("LAUNCH NETWORK · Ethernet / PoE → four LTU-XR links → Away stations 1–4 · data transport pending", "eyebrow")
            self.launch_network_notice.setWordWrap(True)
            outer.addWidget(self.launch_network_notice)
        elif self.profile_locked:
            from .wifi_panel import WifiPanel

            self.wifi_panel = WifiPanel(self.controller.data_dir, self)
            outer.addWidget(self.wifi_panel)
        self.usb_strip = QWidget()
        usb = QHBoxLayout(self.usb_strip)
        usb.setContentsMargins(0, 0, 0, 0)
        for role, title in self.controller.serial_labels.items():
            combo, connect, status = self.port_widgets[role]
            usb.addWidget(label(title, "section"))
            for w in (combo, self.refresh_buttons[role], connect, self.disconnect_buttons[role], status):
                usb.addWidget(w, 1 if w is combo else 0)
                w.show()
        if self.launch_station and self.profile.role == "telemetry":
            from .launch_uplink_panel import LaunchUplinkPanel

            self.launch_uplink = LaunchUplinkPanel(self.controller)
            usb.addWidget(self.launch_uplink)
        outer.addWidget(self.usb_strip)
        self.iris_links = None
        if self.profile_locked and self.profile.vehicle == "iris" and self.profile.role == "telemetry":
            from .iris_link_panel import IrisLinkPanel

            self.iris_links = IrisLinkPanel(self.controller, station=self.profile.station)
            outer.addWidget(self.iris_links)
        self.attach(outer, self.demo_bar)
        self.health = QLabel()
        self.health.setObjectName("eyebrow")
        outer.addWidget(self.health)
        self.metric_strip = QWidget()
        metric_strip = QHBoxLayout(self.metric_strip)
        metric_strip.setContentsMargins(0, 0, 0, 0)
        for key, title in (
            ("altitude", "ALTITUDE · m"),
            ("velocity", "VELOCITY · m/s"),
            ("flight", "FLIGHT TIME · s"),
            ("battery", "BATTERY · V"),
            ("rssi", "RADIO · dBm"),
            ("age", "SAMPLE AGE · s"),
        ):
            box = QFrame()
            box.setObjectName("card")
            line = QHBoxLayout(box)
            line.setContentsMargins(10, 4, 10, 4)
            line.addWidget(label(title, "muted"))
            line.addStretch()
            line.addWidget(self.metrics[key])
            self.metrics[key].show()
            metric_strip.addWidget(box, 1)
        outer.addWidget(self.metric_strip)
        self.flight_board = QWidget()
        self.flight_grid = QGridLayout(self.flight_board)
        self.flight_grid.setContentsMargins(0, 0, 0, 0)
        self.flight_grid.setSpacing(7)
        outer.addWidget(self.flight_board, 1)
        self.video_replay_bar = QWidget()
        self.video_replay_layout = QVBoxLayout(self.video_replay_bar)
        self.video_replay_layout.setContentsMargins(0, 0, 0, 0)
        self.video_replay_layout.setSpacing(4)
        self.video_replay_status = label("Open a recorded flight or session to play its video.", "muted")
        self.video_replay_status.setMinimumWidth(0)
        self.video_replay_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.video_replay_layout.addLayout(
            row(
                button("Open flight…", self.open_flight),
                button("Open session…", self.open_session),
                self.video_replay_status,
            )
        )
        outer.addWidget(self.video_replay_bar)
        self.video_notice = label("", "muted")
        outer.addWidget(self.video_notice)
        self.attach(outer, self.banner)

        root2 = QWidget()
        self.companion.setCentralWidget(root2)
        secondary = QVBoxLayout(root2)
        secondary.setContentsMargins(12, 9, 12, 5)
        secondary.setSpacing(6)
        title = row(label("SYSTEMS / LAUNCH STATION" if self.launch_station else "SYSTEMS / GROUND STATION", "heading"))
        self.secondary_health = label("LIVE · Ground station disconnected", "eyebrow")
        title.addWidget(self.secondary_health, 1)
        title.addWidget(button("Arrange displays", self.arrange_displays))
        title.addWidget(button("Settings…", self.open_settings))
        self.rocket_state_badge = RocketStateBadge()
        title.addWidget(self.rocket_state_badge)
        secondary.addLayout(title)
        self.systems_board = QWidget()
        self.systems_grid = QGridLayout(self.systems_board)
        self.systems_grid.setContentsMargins(0, 0, 0, 0)
        self.systems_grid.setSpacing(7)
        secondary.addWidget(self.systems_board, 1)
        self.secondary_banner = label("", "muted")
        secondary.addWidget(self.secondary_banner)

        self.build_flight_cards()
        self.build_pointer_cards()
        self.build_system_cards()
        self.build_operations_cards()
        self.rocket_panel.setParent(self.companion)
        self.rocket_panel.hide()
        for parent in (self, self.companion):
            for table in parent.findChildren(QTableWidget):
                table.setMinimumSize(0, 0)
                table.verticalHeader().setMinimumSectionSize(17)
                table.verticalHeader().setDefaultSectionSize(20)
                table.horizontalHeader().setMinimumSectionSize(28)
                table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        view_menu = next(a.menu() for a in self.menuBar().actions() if a.text() == "View")
        if self.launch_station:
            for key, action in self.legacy_actions.items():
                if key in {"pointer", "up", "down", "left", "right", "zero"}:
                    action.setVisible(False)
        if self.profile_locked and self.profile.role == "video":
            for action in view_menu.actions():
                if action.text() == "3D flight…":
                    action.setVisible(False)
            serial_menu = next(a.menu() for a in self.menuBar().actions() if a.text() in {"Serial controls", "Session controls"})
            serial_menu.setTitle("Video controls")
            for key, action in self.legacy_actions.items():
                if key != "log":
                    action.setVisible(False)
        view_menu.addAction("Arrange station displays", self.arrange_displays)
        if not self.profile_locked:
            view_menu.addAction("Launch station · two displays", lambda: self.set_station_mode("base"))
            view_menu.addAction("Away station · one display", lambda: self.set_station_mode("away"))
            view_menu.addAction("Video station", lambda: self.set_station_mode("video"))
        # Same menu actions remain usable from the second display on macOS.
        for menu_action in self.menuBar().actions():
            if menu_action.menu():
                self.companion.menuBar().addMenu(menu_action.menu())

    def build_flight_cards(self):
        self.flight_3d_dialog = EmbeddedFlightView(self.controller, self)
        scene = self.flight_3d_dialog
        scene.setWindowFlags(Qt.WindowType.Widget)
        scene.setMinimumSize(0, 0)
        scene.view.setMinimumSize(0, 0)
        # Rearrange its controls into two rows without changing the playback engine.
        old = scene.layout()

        def clear(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().hide()
                if item.layout():
                    clear(item.layout())

        clear(old)
        old.setContentsMargins(0, 0, 0, 0)
        old.setSpacing(3)
        old.addLayout(
            row(
                scene.camera,
                scene.follow,
                scene.projection,
                scene.velocity,
                scene.size,
                button("Fit", scene.view.fit),
            )
        )
        scene.follow.setText("Follow")
        scene.projection.setText("Ground")
        scene.view.setMinimumHeight(130)
        old.addWidget(scene.view, 1)
        old.addLayout(row(scene.play_button, scene.rewind, scene.speed, scene.link, scene.events))
        scene.link.setText("Monitor time")
        old.addWidget(scene.slider)
        old.addWidget(scene.status)
        for w in (scene.view, scene.slider, scene.status):
            w.show()
        scene.title.hide()
        scene.status.setFixedHeight(28)
        layout = self.card("flight3d", "FLIGHT / 3D simulation & events")
        self.attach(layout, scene, 1)
        layout = self.card("trajectory", "GROUND TRACK / reference & actual")
        self.attach(layout, self.trajectory_plot, 1)
        layout.addLayout(row(self.view, button("Import…", self.load_reference)))
        self.attach(layout, self.reference_label)
        self.reference_label.setFixedHeight(26)
        layout = self.card("altitude", "VERTICAL PROFILE / m")
        self.attach(layout, self.altitude_plot, 1)
        layout = self.card("attitude", "ROCKET / orientation & flight status")
        self.attach(layout, self.attitude, 1)
        for key, title, graph in (
            ("gnc_rates", "ANGULAR RATES / °/s", self.rate_plot),
            ("gnc_angles", "INTEGRATED ROTATION / °", self.angle_plot),
        ):
            self.attach(self.card(key, title), graph, 1)
        layout = self.card("actuators", "GNC / canards, tabs & legacy channels")
        self.attach(layout, self.actuators, 1)
        note = label("Zephyrus: four drive values; new actuator feedback unavailable.", "muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.actuator_note = note

    def build_pointer_cards(self):
        layout = self.card("antenna", "AWAY POINTER / illustration" if self.launch_station else "ANTENNA / physical assembly")
        layout.addLayout(
            row(
                *(
                    button(name, lambda checked=False, n=name: self.mount_camera(n))
                    for name in ("Perspective", "Front", "Side", "Top")
                )
            )
        )
        self.attach(layout, self.mount, 1)
        self.mount.display_center = (0, 2.25, 0)
        self.mount.display_padding = 20
        self.mount.show_hints = False
        self.mount.setToolTip("Drag to orbit · Scroll to zoom · Double-click to reset")
        if self.launch_station:
            from .launch_link_panel import LaunchLinkPanel

            self.remote_pointer_caption = label("Select an away pointer · pose unavailable", "muted")
            self.remote_pointer_caption.setWordWrap(True)
            layout.addWidget(self.remote_pointer_caption)
            self.launch_links = LaunchLinkPanel(self.controller, self.profile)
            self.attach(self.card("pointing", "COMMUNICATION / away antenna selection"), self.launch_links, 1)
            return
        self.attach(layout, self.pointer_sent)
        layout = self.card("pointing", "POINTING / control & rehearsal")
        layout.addLayout(row(self.pointer_pose, self.virtual_connect))
        layout.addLayout(
            row(label("Azimuth °"), self.azimuth, label("Elevation °"), self.elevation, self.point_button)
        )
        jogs = [w for w in self.pointer_controls if isinstance(w, QPushButton) and w is not self.point_button]
        layout.addLayout(row(*jogs, self.zero_button))
        layout.addLayout(row(self.track_button, self.hold_button))
        self.attach(layout, self.pointer_status)
        self.attach(layout, self.ground_gps_title)
        self.attach(layout, self.ground_gps)
        layout.addLayout(row(self.freeze_gps, button("Antenna location…", self.edit_pointer_mission)))
        # Preserve visibility switching driven by refresh_virtual_pointer().
        virtual = self.virtual_panel.layout()
        while virtual.count():
            item = virtual.takeAt(0)
            if item.widget():
                item.widget().hide()
        virtual.setContentsMargins(0, 2, 0, 0)
        virtual.setSpacing(3)
        virtual.addLayout(row(self.virtual_follow, self.virtual_reset, self.virtual_speed))
        for w in (self.virtual_slider, self.virtual_clock, self.virtual_pose, self.virtual_location):
            virtual.addWidget(w)
            w.show()
        self.attach(layout, self.virtual_panel)
        layout.addStretch()

    def build_system_cards(self):
        for stream, title in self.controller.video_labels.items():
            v = self.video_widgets[stream]
            layout = self.card(stream, f"{title.upper()} / USB video receiver")
            self.attach(layout, v["image"], 1)
            self.attach(layout, v["status"])
            v["status"].setFixedHeight(18)
            video_row = row(
                v["camera"],
                button("Find", lambda: self.controller.submit("cameras", camera_devices)),
                v["start"],
                v["stop"],
                v["file"],
            )
            video_row.setStretch(0, 1)
            layout.addLayout(video_row)
        if self.profile.station == "base" and self.profile.role == "video":
            from .video_wall import VideoWall

            controls = QWidget()
            local = QVBoxLayout(controls)
            local.setContentsMargins(0, 0, 0, 0)
            v = self.video_widgets["digital"]
            local.addWidget(v["status"])
            local.addLayout(row(
                v["camera"], button("Find", lambda: self.controller.submit("cameras", camera_devices)),
                v["start"], v["stop"], v["file"],
            ))
            self.video_wall = VideoWall(self.profile, local_controls=controls)
            self.attach(self.card("video_wall", "VIDEO / launch site & away stations"), self.video_wall, 1)
        rp = self.rocket_panel
        for key, title, widget in (
            ("telemetry", "ROCKET / full telemetry", rp.telemetry),
            ("gps", "ROCKET / GPS", rp.gps),
            ("servos", "LEGACY SERVOS", rp.servos),
            ("cells", "BATTERY CELLS / V", rp.cells),
            ("power", "POWER RAILS / requested & measured", rp.power),
            ("bms", "BMS / enabled & triggered protections", rp.protections),
        ):
            self.attach(self.card(key, title), widget, 1)
        layout = self.card("commands", "ROCKET COMMANDS / Zephyrus")
        grid = QGridLayout()
        grid.setSpacing(4)
        for i, command in enumerate(
            ("advance_state", "zero_pitchYawRoll", "zero_alt", "zero_velo", "zero_servos", "pd_activate")
        ):
            grid.addWidget(rp.buttons[command], i // 3, i % 3)
        layout.addLayout(grid)
        for title, name in (
            ("Roll °", "set_roll_control_servo_angle"),
            ("Airbrakes °", "set_airbrakes_angle"),
        ):
            layout.addLayout(row(label(title), rp.servo_inputs[name], rp.buttons[name]))
        layout.addLayout(row(label("VTX"), *rp.vtx_buttons))
        layout = self.card("recovery", "RECOVERY / continuity, arm & fire")
        self.attach(layout, rp.pyros, 1)
        layout.addLayout(row(rp.buttons["arm_pyros"], rp.buttons["fire_pyros"]))
        emergency = QGridLayout()
        emergency.setSpacing(4)
        for i, command in enumerate(
            ("EMERG_DEPLOY_PISTON", "EMERG_DEPLOY_BP_WELLS", "EMERG_DEPLOY_TD", "EMERG_DEPLOY_ALL")
        ):
            emergency.addWidget(rp.buttons[command], i // 2, i % 2)
        layout.addLayout(emergency)

    def build_operations_cards(self):
        layout = self.card("mission", "MISSION / launch, wind & OpenRocket")
        layout.addLayout(
            row(
                button("Configure…", self.edit_mission),
                button("Open mission…", self.load_mission),
                button("Save mission…", self.save_mission),
            )
        )
        self.attach(layout, self.mission_summary)
        self.mission_summary.setMaximumHeight(32)
        self.attach(layout, self.wind_table, 1)
        layout.addLayout(
            row(
                button("+ Layer", lambda: self.add_wind_row(1000, 0, 0)),
                button("− Layer", self.remove_wind_row),
                button("Apply", lambda: self.guard(self.apply_wind)),
                button("Import", self.import_wind),
                button("Export", self.export_wind),
            )
        )
        self.attach(layout, self.wind_status)
        self.wind_status.setMaximumHeight(28)
        self.weather_time.setToolTip("Weather valid time, UTC ISO 8601")
        self.weather_msl.setToolTip("Launch elevation above mean sea level, metres")
        layout.addLayout(
            row(
                label("UTC"),
                self.weather_time,
                label("MSL m"),
                self.weather_msl,
                button("Weather", self.fetch_weather),
            )
        )
        layout.addLayout(
            row(self.model_path, button("Model…", self.choose_model), button("Motors…", self.choose_motors))
        )
        self.attach(layout, self.motor_status)
        self.motor_status.setMaximumHeight(18)
        layout.addLayout(
            row(
                button("Run simulation", lambda: self.guard(self.start_simulation), True),
                button(
                    "Cancel",
                    lambda: self.controller.simulation.cancel() if self.controller.simulation else None,
                ),
            )
        )
        self.attach(layout, self.sim_status)
        self.sim_status.setMaximumHeight(32)
        layout = self.card("sessions", "FLIGHT FILE / recording & replay")
        layout.addLayout(
            row(
                button("Open flight…", self.open_flight),
                button("Save flight…", self.save_flight),
                button("Session…", self.open_session),
                button("CSV import", self.import_legacy_csv),
                button("CSV export", self.export_session),
            )
        )
        self.attach(layout, self.flight_file_status)
        self.flight_file_status.setMaximumHeight(27)
        self.attach(layout, self.session_status)
        self.session_status.setMaximumHeight(27)
        self.session_replay_layout = layout
        self.replay_controls = QWidget()
        playback = QVBoxLayout(self.replay_controls)
        playback.setContentsMargins(0, 0, 0, 0)
        playback.setSpacing(4)
        self.attach(playback, self.replay_slider)
        playback.addLayout(
            row(self.play, self.step_button, self.speed, self.replay_clock, self.align_time_button)
        )
        layout.addWidget(self.replay_controls)
        self.align_time_button.setText("Set t = 0")
        self.play.setText("Play / pause")
        layout = self.card("diagnostics", "DIAGNOSTICS / alerts, decoded sample & events")
        layout.addLayout(row(self.alert_status, button("Acknowledge", self.controller.acknowledge_alerts)))
        self.attach(layout, self.link_stats)
        panes = QHBoxLayout()
        for w in (self.details, self.log_view, self.timeline):
            w.setMinimumSize(0, 0)
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            panes.addWidget(w, 1)
            w.show()
        layout.addLayout(panes, 1)
        from .station_panel import StationNetworkPanel

        self.network_panel = StationNetworkPanel(self.controller)
        if self.launch_station:
            self.network_panel.topology.setText(
                "Launch station: Ethernet / PoE to four LTU-XR links.\n"
                "This manual peer link is read-only; operational station transport is pending."
            )
            self.network_panel.topology.setToolTip("The existing manual status link does not enable uplink, remote pointer control or video relay.")
        if self.profile_locked:
            self.network_panel.station_name.setText(
                f"{self.profile.station_label} · {self.profile.role.title()}"
            )
        self.attach(self.card("network", "APRS / manual status link"), self.network_panel, 1)

    def place(self, grid, key, r, c, rs=1, cs=1):
        widget = self.cards[key]
        grid.addWidget(widget, r, c, rs, cs)
        widget.show()

    def set_station_mode(self, value, persist=True):
        if not self.workspace_ready:
            return
        if value not in ("base", "away", "video"):
            raise ValueError("Station mode must be base, away or video")
        if self.profile_locked and value != self.profile.layout:
            return
        self.station_mode = value
        video = value == "video"
        self.setMinimumSize(*(1000, 600) if video else (1280, 800))
        self.workspace_title.setText("VIDEO / v0a" if video else "ROCKET / v0a")
        for widget in (self.mission_label, self.poll_button, self.health, self.metric_strip, self.banner):
            widget.setVisible(not video)
        # Playback controls have one owner and retain their signals and state.
        (self.video_replay_layout if video else self.session_replay_layout).addWidget(self.replay_controls)
        self.replay_controls.show()
        self.align_time_button.setVisible(not video)
        self.mount.zoom = 1.4 if value == "base" else 1.0
        for grid in (self.flight_grid, self.systems_grid):
            while grid.count():
                item = grid.takeAt(0)
                if item.widget():
                    item.widget().hide()
            for i in range(16):
                grid.setRowStretch(i, 0)
                grid.setColumnStretch(i, 0)
        for panel in self.cards.values():
            panel.hide()
        if value == "base":
            self.setWindowTitle("Rocket GNC Monitor v0a · 1 / Flight & links" if self.launch_station else "Rocket GNC Monitor v0a · 1 / Flight & antenna")
            for args in (
                ("flight3d", 0, 0, 2, 2),
                ("trajectory", 2, 0, 1, 1),
                ("altitude", 2, 1, 1, 1),
                ("antenna", 0, 2, 1, 1),
                ("pointing", 1, 2, 2, 1),
                ("attitude", 0, 3, 2, 2),
                ("gnc_rates", 2, 3, 1, 1),
                ("gnc_angles", 2, 4, 1, 1),
                ("actuators", 3, 0, 1, 5),
            ):
                self.place(self.flight_grid, *args)
            for i, weight in enumerate((32, 24, 24, 20)):
                self.flight_grid.setRowStretch(i, weight)
            for i, weight in enumerate((20, 20, 30, 15, 15)):
                self.flight_grid.setColumnStretch(i, weight)
            # Four columns keep every former tab and nested tab in view.
            for args in (
                ("telemetry", 0, 0, 2, 1),
                ("gps", 0, 1, 2, 1),
                ("servos", 2, 0, 1, 1),
                ("cells", 2, 1, 1, 1),
                ("diagnostics", 3, 0, 1, 2),
                ("commands", 0, 2, 1, 1),
                ("power", 1, 2, 1, 1),
                ("bms", 2, 2, 1, 1),
                ("recovery", 3, 2, 1, 1),
                ("mission", 0, 3, 2, 1),
                ("sessions", 2, 3, 1, 1),
                ("network", 3, 3, 1, 1),
            ):
                self.place(self.systems_grid, *args)
            for i, weight in enumerate((23, 31, 15, 31)):
                self.systems_grid.setRowStretch(i, weight)
            for i, weight in enumerate((22, 22, 25, 31)):
                self.systems_grid.setColumnStretch(i, weight)
            if self.isVisible():
                self.companion.show()
        elif value == "away":
            self.setWindowTitle("Rocket GNC Monitor v0a · Away station")
            self.companion.hide()
            for args in (
                ("antenna", 0, 0, 2, 1),
                ("pointing", 2, 0, 2, 1),
                ("attitude", 0, 1, 3, 1),
                ("altitude", 3, 1, 1, 1),
                ("telemetry", 0, 2, 2, 1),
                ("gps", 2, 2, 2, 1),
            ):
                self.place(self.flight_grid, *args)
            for i in range(4):
                self.flight_grid.setRowStretch(i, 1)
            for i, weight in enumerate((42, 30, 28)):
                self.flight_grid.setColumnStretch(i, weight)
        else:
            self.setWindowTitle(
                f"Rocket GNC Monitor v0a · {self.profile.station_label} / {self.profile.vehicle_label} video"
            )
            self.companion.hide()
            if self.video_wall is not None:
                self.place(self.flight_grid, "video_wall", 0, 0)
                self.flight_grid.setColumnStretch(0, 1)
            else:
                for column, stream in enumerate(self.controller.video_streams):
                    self.place(self.flight_grid, stream, 0, column)
                    self.flight_grid.setColumnStretch(column, 1)
            self.flight_grid.setRowStretch(0, 1)
        self.station_selector.blockSignals(True)
        self.station_selector.setCurrentIndex(self.station_selector.findData(value))
        self.station_selector.blockSignals(False)
        if persist:
            try:
                self.station_path.parent.mkdir(parents=True, exist_ok=True)
                self.station_path.write_text(json.dumps({"station": value}, indent=2) + "\n")
            except OSError as exc:
                self.statusBar().showMessage(f"Workspace preference not saved: {exc}", 8000)
        self.last_ui = self.last_table = 0
        self.refresh()
        QTimer.singleShot(0, self.rescale_videos)

    def open_flight_3d(self):
        if not self.workspace_ready:
            return
        if self.profile_locked and self.profile.role == "video":
            return
        if self.profile_locked and self.profile.layout == "away":
            if self.away_flight_dialog is None:
                self.away_flight_dialog = Flight3DDialog(self.controller, self)
            self.away_flight_dialog.show()
            self.away_flight_dialog.raise_()
            self.away_flight_dialog.activateWindow()
            return
        if self.station_mode != "base":
            self.set_station_mode("base")
        self.flight_3d_dialog.load_reference()
        self.flight_3d_dialog.tick()
        self.activateWindow()
        self.cards["flight3d"].setFocus()

    def start_camera(self, stream="digital"):
        # The video computer has USB receivers, but no telemetry PCB (board diagram).
        if self.controller.mode == "REPLAY":
            raise ValueError("Close replay before selecting a USB receiver")
        source = self.video_widgets[stream]["camera"].currentData()
        if source is None or source == "":
            raise ValueError("Find and select a USB camera first")
        self.controller.start_video("camera", source, stream=stream)
        self.update_camera_choices()

    def connect_role(self, role):
        if role not in self.controller.serial_labels or (self.profile_locked and self.profile.role == "video"):
            return
        super().connect_role(role)

    def toggle_connection(self, role):
        if role not in self.controller.serial_labels or (self.profile_locked and self.profile.role == "video"):
            return
        super().toggle_connection(role)

    def video_file(self, stream="digital"):
        if self.controller.mode == "REPLAY":
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"{stream.title()} video source", "", "Video (*.mp4 *.mkv *.mov *.avi *.ts);;All files (*)"
        )
        if path:
            self.controller.start_video("file", path, stream=stream)

    def refresh_actuators(self):
        if not self.workspace_ready:
            return super().refresh_actuators()
        if self.station_mode == "video":
            return
        c = self.controller
        channels = {f"Canard {i + 1}": {} for i in range(c.mission.canard_count)}
        channels.update({f"Tab {i + 1}": {} for i in range(4)})
        if c.latest:
            channels.update(c.latest.actuators)
        self.actuators.setColumnCount(len(channels))
        self.actuators.setRowCount(4)
        self.actuators.setHorizontalHeaderLabels(list(channels))
        self.actuators.setVerticalHeaderLabels(["Requested °", "Measured °", "Drive µs", "Status"])
        self.actuators.verticalHeader().show()
        for column, values in enumerate(channels.values()):
            items = [
                f"{values[k]:.2f}" if finite(values.get(k)) else "—" for k in ("demand", "measured", "drive")
            ]
            items.append("Drive only" if "drive" in values else "Unavailable")
            for r, value in enumerate(items):
                set_cell(self.actuators, r, column, value)

    def arrange_displays(self):
        screens = QApplication.screens()
        primary = self.screen() or screens[0]
        second = next((s for s in screens if s is not primary), primary)
        windows = (
            ((self, primary), (self.companion, second))
            if self.station_mode == "base" else ((self, primary),)
        )
        for window, screen in windows:
            rect = screen.availableGeometry()
            window.setGeometry(rect.adjusted(4, 4, -4, -4))
        if second is primary and self.station_mode == "base":
            self.companion.move(primary.availableGeometry().topLeft() + QPoint(28, 28))
            self.statusBar().showMessage(
                "One display detected. Connect a second display, then choose Arrange displays.", 12000
            )

    def screens_changed(self, *_):
        if self.workspace_ready and self.auto_place:
            QTimer.singleShot(100, self.arrange_displays)

    def showEvent(self, event):
        super().showEvent(event)
        if self.workspace_ready:
            if self.station_mode == "base":
                self.companion.show()
            if self.auto_place:
                QTimer.singleShot(0, self.arrange_displays)

    def rescale_videos(self):
        for stream, pixmap in self.last_pixmaps.items():
            view = self.video_widgets[stream]["image"]
            if pixmap and view.isVisible():
                view.setPixmap(
                    pixmap.scaled(
                        view.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

    def show_frame(self, stream, data):
        # Keep receiving and retaining frames when another workspace is visible;
        # avoid scaling hidden video surfaces or restarting a camera on return.
        image = QImage(data, WIDTH, HEIGHT, WIDTH * 3, QImage.Format.Format_RGB888).copy()
        pixmap = self.last_pixmaps[stream] = QPixmap.fromImage(image)
        if stream == "digital":
            self.last_pixmap = pixmap
            if self.video_wall is not None:
                self.update_local_video_context()
                self.video_wall.set_local_frame(pixmap)
        view = self.video_widgets[stream]["image"]
        if view.isVisible():
            view.setPixmap(
                pixmap.scaled(
                    view.size(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def reset_video_frame(self, stream):
        super().reset_video_frame(stream)
        if stream == "digital" and self.video_wall is not None:
            self.video_wall.reset_local_frame("Local digital camera not started")

    def update_local_video_context(self):
        if self.video_wall is None:
            return
        c = self.controller
        state = c.video_streams["digital"]
        worker = state.worker
        error = state.error or (worker.error if worker else "")
        if c.mode == "REPLAY":
            label = "Replay · paused" if state.replay_paused or not c.replay_playing else "Replay · playing"
            self.video_wall.set_local_source(f"Replay · unavailable: {error}" if error else label)
        elif c.mode == "DEMO":
            label = "Demo · test pattern" if state.config else "Demo · stopped"
            self.video_wall.set_local_source(f"Demo · unavailable: {error}" if error else label)
        elif error:
            self.video_wall.set_local_source(f"Local video · unavailable: {error}")
        elif not state.config:
            self.video_wall.set_local_source("Local video · stopped")
        elif state.config[0] == "file":
            self.video_wall.set_local_source("Local video file")
        elif state.config[0] == "camera":
            active = bool(worker and not worker.stopped.is_set() and worker.thread.is_alive())
            live = bool(active and worker.last_frame)
            label = "Local USB camera" if live else (
                "Local USB camera · awaiting frames" if active else "Local USB camera · starting"
            )
            if worker and not active:
                label = "Local USB camera · stopped"
            self.video_wall.set_local_source(label, live=live)
        else:
            self.video_wall.set_local_source("Local video source")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.workspace_ready and self.station_mode == "video":
            QTimer.singleShot(0, self.rescale_videos)

    def refresh_plots(self, force=False):
        if self.workspace_ready and self.station_mode == "video":
            return
        super().refresh_plots(force=force)

    def refresh(self):
        previous = self.last_ui
        super().refresh()
        if not self.workspace_ready or self.last_ui == previous:
            return
        c = self.controller
        video = self.station_mode == "video"
        self.rocket_state_badge.update_state(
            c.latest, mode=c.mode, fresh=c.mode != "LIVE" or c.rocket_link_state()[0] == "RECEIVING"
        )
        self.update_local_video_context()
        if self.profile_locked and self.profile.role == "video":
            for key, action in self.legacy_actions.items():
                if key != "log":
                    action.setEnabled(False)
        self.usb_strip.setVisible(not video and (self.launch_station or c.mode != "DEMO"))
        if self.launch_uplink is not None:
            self.launch_uplink.refresh()
        self.demo_bar.setVisible(not video and c.mode == "DEMO")
        self.video_replay_bar.setVisible(video and c.mode == "REPLAY")
        if video:
            self.record_button.setText("Stop recording" if c.recorder else "Start recording")
            self.video_replay_status.setText(
                f"Recorded session · {Path(c.reader.path).name}" if c.reader
                else "Open a recorded flight or session to play its video."
            )
            self.video_notice.setText(
                (c.recorder.error or "Recording video to the current session.") if c.recorder else ""
            )
        self.video_notice.setVisible(video and c.recorder is not None)
        status = "   /   ".join(
            self.connection_tiles[k].text().replace("● ", "") for k in ("ground", "rocket", "pointer")
        )
        self.health.setText("USB TELEMETRY · RADIO LINK · ANTENNA   /   " + status)
        if self.launch_station:
            self.launch_links.refresh()
            selected = c.selected_away_station
            self.remote_pointer_caption.setText(
                f"Away station {selected[-1]} · illustration only · remote pose unavailable"
                if selected else "Select an away pointer · pose unavailable"
            )
            self.mount.setVisible(selected is not None)
            local_status = " / ".join(self.connection_tiles[k].text().replace("● ", "") for k in ("ground", "rocket"))
            self.health.setText("LOCAL TELEMETRY · " + local_status + "   /   AWAY NETWORK · four links · transport pending")
            if c.mode == "LIVE":
                self.banner.setText(self.banner.text() + " · UPLINK SWITCH PROTOCOL PENDING")
        self.secondary_health.setText(c.mode + " · " + self.connection_tiles["rocket"].text())
        self.secondary_banner.setText(self.banner.text())
        self.network_panel.refresh()
        if self.iris_links is not None:
            self.iris_links.refresh()
        for stream, widgets in self.video_widgets.items():
            state = c.video_streams[stream]
            available = c.mode != "REPLAY" and not (state.config or state.reserved_cameras)
            widgets["start"].setEnabled(available and bool(widgets["camera"].currentData()))
            widgets["file"].setEnabled(c.mode != "REPLAY")
            widgets["start"].setToolTip("Start this USB video receiver independently of telemetry USB")
            widgets["file"].setToolTip("Open a local video source")
        if c.mode != "REPLAY" and any(s.worker and s.worker.last_frame for s in c.video_streams.values()):
            self.record_button.setEnabled(True)
            self.legacy_actions["log"].setEnabled(True)
        if video:
            return
        for table in (
            self.actuators,
            self.rocket_panel.telemetry,
            self.rocket_panel.gps,
            self.rocket_panel.servos,
            self.rocket_panel.cells,
            self.rocket_panel.power,
            self.rocket_panel.protections,
            self.rocket_panel.pyros,
            self.wind_table,
        ):
            for index in range(table.rowCount()):
                table.setRowHeight(index, 19)
        for table, names in (
            (
                self.rocket_panel.telemetry,
                (
                    "State",
                    "RSSI (dBm)",
                    "RX RSSI (dBm)",
                    "Baro altitude (m)",
                    "Max baro altitude (m)",
                    "Roll gyro total (°)",
                    "Integrated velocity (m/s)",
                    "Legacy tilt proxy (°)",
                    "Temperature (°C)",
                    "Last reception (ms)",
                    "Packet number",
                ),
            ),
            (
                self.rocket_panel.gps,
                (
                    "GPS fix",
                    "Latitude",
                    "Longitude",
                    "Altitude (m)",
                    "Max altitude (m)",
                    "Horizontal precision (m)",
                    "Vertical precision (m)",
                    "Satellites",
                ),
            ),
        ):
            table.setRowCount(len(names))
            for r, name in enumerate(names):
                set_cell(table, r, 0, name)
                if not c.latest:
                    set_cell(table, r, 1, "—")
                table.setRowHeight(r, 19)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(0, round(table.viewport().width() * 0.58))
        set_cell(self.rocket_panel.power, 7, 0, "Temp °C")
        for col in (0, 1):
            self.rocket_panel.pyros.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        # Fixed-size instrument readouts must not hide their final channel/field.
        # Logs, imported wind layers and raw JSON remain bounded scrolling panes.
        for table in (
            self.actuators,
            self.rocket_panel.telemetry,
            self.rocket_panel.gps,
            self.rocket_panel.servos,
            self.rocket_panel.cells,
            self.rocket_panel.power,
            self.rocket_panel.protections,
            self.rocket_panel.pyros,
        ):
            table.setMinimumHeight(
                sum(table.rowHeight(r) for r in range(table.rowCount()))
                + table.horizontalHeader().height()
                + 4
            )
        self.cards["actuators"].setMinimumHeight(
            self.actuators.minimumHeight() + self.actuator_note.sizeHint().height() + 46
        )

    def set_daylight(self, enabled, *, persist=True):
        super().set_daylight(enabled, persist=persist)
        if self.workspace_ready:
            style = self.styleSheet() + COMPACT_STYLE
            self.setStyleSheet(style)
            self.companion.setStyleSheet(style)

    def closeEvent(self, event):
        self.closing = True
        if self.wifi_panel is not None:
            self.wifi_panel.cleanup()
        if self.away_flight_dialog is not None:
            self.away_flight_dialog.close()
        if self.workspace_ready:
            self.network_panel.shutdown()
            if self.video_wall is not None:
                self.video_wall.close()
            self.companion.close()
        super().closeEvent(event)
