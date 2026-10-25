"""Compact rocket pose with explicit telemetry/reference source selection."""

from dataclasses import replace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider

from .rocket_pose import telemetry_pose, simulation_pose
from .rocket_pose_view import RocketPoseView
from .widgets import ComboBox


class RocketPosePanel(QWidget):
    def __init__(self, controller, simulation_provider, open_simulation, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.simulation_provider = simulation_provider
        self.open_simulation = open_simulation
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.source = ComboBox()
        self.source.setAccessibleName("Rocket orientation data source")
        self.source.addItem("Telemetry", "telemetry")
        self.source.addItem("OpenRocket reference", "openrocket")
        controls.addWidget(self.source)
        self.playback = QPushButton("Playback…")
        self.playback.setToolTip("Open the existing OpenRocket reference playback controls")
        self.playback.clicked.connect(lambda: self.open_simulation())
        controls.addWidget(self.playback)
        controls.addStretch()
        self.timeline = QLabel()
        self.timeline.setAccessibleName("OpenRocket reference playback time")
        controls.addWidget(self.timeline)
        layout.addLayout(controls)
        self.view = RocketPoseView()
        layout.addWidget(self.view, 1)
        camera = QHBoxLayout()
        camera.setSpacing(6)
        camera.addWidget(QLabel("View azimuth"))
        self.azimuth = QSlider(Qt.Orientation.Horizontal)
        self.azimuth.setRange(0, 360)
        self.azimuth.setValue(305)
        self.azimuth.setAccessibleName("Rocket viewing azimuth in degrees")
        self.azimuth.setToolTip("Rotate the camera around the vertical axis; vertical remains upright")
        camera.addWidget(self.azimuth, 1)
        self.azimuth_value = QLabel("305°")
        self.azimuth_value.setMinimumWidth(35)
        camera.addWidget(self.azimuth_value)
        layout.addLayout(camera)
        self.azimuth.valueChanged.connect(self.set_azimuth)
        self.source.currentIndexChanged.connect(self.source_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.refresh)
        self.set_azimuth(self.azimuth.value())
        self.refresh()

    def set_azimuth(self, value):
        self.view.set_azimuth(value)
        self.azimuth_value.setText(f"{value}°")

    def source_changed(self, *_):
        if self.source.currentData() == "openrocket":
            self.open_simulation()
        self.refresh()

    def refresh(self):
        c = self.controller
        self.view.canards = c.mission.canard_count
        reference = self.source.currentData() == "openrocket"
        self.playback.setVisible(reference)
        self.timeline.setVisible(reference)
        if reference:
            result = self.simulation_provider()
            if result is None:
                pose = replace(simulation_pose(None), status="Load a trajectory and use Playback")
                self.timeline.setText("No reference")
            else:
                scene, frame, playback_state = result
                event_types = {event["type"] for event in scene.events}
                pose = simulation_pose(
                    frame, events_available=bool(scene.events),
                    motor_events_available=bool(event_types & {"IGNITION", "BURNOUT"}),
                    parachute_events_available="RECOVERY_DEVICE_DEPLOYMENT" in event_types,
                )
                self.timeline.setText(f"{frame.time:.2f} s · {playback_state}")
        else:
            pose = telemetry_pose(c.latest, mode=c.mode,
                                  fresh=c.mode != "LIVE" or c.rocket_link_state()[0] == "RECEIVING")
        self.view.set_pose(pose)

    def showEvent(self, event):
        self.refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def shutdown(self):
        self.timer.stop()
        self.view.hide()
