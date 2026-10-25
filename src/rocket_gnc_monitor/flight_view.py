"""Orbitable 3D flight view and a read-only simulation playback window."""

import math
import time
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QWidget,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QSlider,
    QCheckBox,
    QDoubleSpinBox,
)
from .widgets import COLORS, ComboBox
from .fonts import FONT_FAMILY
from PySide6.QtGui import QFont
from .flight_scene import FlightScene, Playback
from .rocket_geometry import rocket_mesh, canopy_mesh, plume_mesh
from .domain import to_enu


EVENT_NAMES = {
    "IGNITION": "Ignition",
    "LIFTOFF": "Liftoff",
    "BURNOUT": "Burnout",
    "APOGEE": "Apogee",
    "RECOVERY_DEVICE_DEPLOYMENT": "Parachute",
    "GROUND_HIT": "Touchdown",
    "STAGE_SEPARATION": "Separation",
    "TUMBLE": "Tumble",
    "SIM_ABORT": "Simulation stopped",
}


class FlightView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = self.frame = None
        self.yaw, self.pitch, self.zoom = -55.0, 24.0, 1.0
        self.pan = np.zeros(2)
        self.follow = False
        self.show_projection = True
        self.show_velocity = True
        self.rocket_size = 1.0
        self.canards = 4
        self.actual = np.empty((0, 3))
        self.antenna = None
        self.drag = None
        self.setMinimumSize(480, 360)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAccessibleName("3D rocket trajectory; drag to orbit, shift-drag to pan, scroll to zoom")
        self.frames = 0
        self.draw_ms = 0.0

    def set_scene(self, scene):
        self.scene = scene
        self.path = scene.points[
            np.linspace(0, len(scene.points) - 1, min(6000, len(scene.points))).astype(int), 1:
        ]
        self.frame = scene.frame(scene.start)
        self.fit()

    def fit(self):
        self.zoom = 1.0
        self.pan[:] = 0
        self.update()

    def camera_view(self, name):
        self.yaw, self.pitch = {"Perspective": (-55, 24), "Top": (-90, 90), "Side": (-90, 0)}[name]
        self.fit()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self.drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag is not None:
            delta = event.position() - self.drag
            self.drag = event.position()
            if (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                or event.buttons() & Qt.MouseButton.RightButton
            ):
                self.pan += [delta.x(), delta.y()]
            else:
                self.yaw -= delta.x() * 0.4
                self.pitch = max(-90, min(90, self.pitch + delta.y() * 0.4))
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.camera_view("Perspective")
        event.accept()

    def wheelEvent(self, event):
        self.zoom = max(0.1, min(40, self.zoom * 1.12 ** (event.angleDelta().y() / 120)))
        self.update()
        event.accept()

    def paintEvent(self, event):
        start = time.perf_counter()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(COLORS["panel"]))
        if self.scene is None or self.frame is None:
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Run OpenRocket or import a trajectory to view a flight in 3D",
            )
            return
        scene, frame = self.scene, self.frame
        yaw, pitch = map(math.radians, (self.yaw, self.pitch))
        forward = -np.array(
            [math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch), math.sin(pitch)]
        )
        right = np.array([-math.sin(yaw), math.cos(yaw), 0])
        up = np.cross(right, forward)
        center = frame.position if self.follow else scene.center
        scale = min(self.width() - 110, self.height() - 130) / scene.span * self.zoom * 0.8

        def project(vertices):
            v = np.asarray(vertices) - center
            return np.column_stack(
                (
                    self.width() / 2 + self.pan[0] + v @ right * scale,
                    self.height() / 2 + self.pan[1] - v @ up * scale,
                )
            )

        def point(v):
            return QPointF(*project([v])[0])

        def line(a, b, color, width=1):
            p.setPen(QPen(QColor(color), width))
            p.drawLine(point(a), point(b))

        def path(vertices, color, width=1, style=Qt.PenStyle.SolidLine):
            if len(vertices) < 2:
                return
            xy = project(vertices)
            shape = QPainterPath(QPointF(*xy[0]))
            for x, y in xy[1:]:
                shape.lineTo(x, y)
            p.setPen(QPen(QColor(color), width, style))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(shape)

        step = 10 ** math.floor(math.log10(scene.span / 5))
        if scene.span / step > 15:
            step *= 5
        ground_low = np.floor((scene.low[:2] - scene.span * 0.1) / step) * step
        ground_high = np.ceil((scene.high[:2] + scene.span * 0.1) / step) * step
        for x in np.arange(ground_low[0], ground_high[0] + step / 2, step):
            line([x, ground_low[1], 0], [x, ground_high[1], 0], COLORS["line"], 0.6)
        for y in np.arange(ground_low[1], ground_high[1] + step / 2, step):
            line([ground_low[0], y, 0], [ground_high[0], y, 0], COLORS["line"], 0.6)
        if self.show_projection:
            ground = self.path.copy()
            ground[:, 2] = 0
            path(ground, COLORS["muted"], 1, Qt.PenStyle.DashLine)
            line([*frame.position[:2], 0], frame.position, COLORS["muted"], 0.8)
        path(self.path, COLORS["gold"], 1.6)
        path(self.actual, COLORS["accent"], 2.1)
        p.setFont(QFont(FONT_FAMILY, 10))
        axes = [
            ([scene.span * 0.2, 0, 0], "E", "#e88469"),
            ([0, scene.span * 0.2, 0], "N", "#66bda7"),
            ([0, 0, scene.span * 0.2], "UP", "#67a7e3"),
        ]
        for end, label, color in axes:
            line([0, 0, 0], end, color, 1.4)
            p.drawText(point(end) + QPointF(5, -4), label)
        p.setPen(QColor(COLORS["text"]))
        p.drawText(point([0, 0, 0]) + QPointF(6, 14), "Launch")
        # Limit text collisions while retaining event dots and the full event selector.
        used = []
        for ev in scene.events:
            if ev["type"] not in EVENT_NAMES or not scene.start <= ev["time"] <= scene.end:
                continue
            pos = scene.trajectory.at(ev["time"])
            if pos is None:
                continue
            xy = point(pos)
            p.setPen(QColor(COLORS["gold"]))
            p.setBrush(QColor(COLORS["gold"]))
            p.drawEllipse(xy, 3, 3)
            text = EVENT_NAMES[ev["type"]]
            rect = QRectF(xy.x() + 6, xy.y() - 18, 120, 17)
            if self.rect().contains(rect.toRect()) and not any(rect.intersects(r) for r in used):
                p.drawText(rect, text)
                used.append(rect)
        if self.antenna is not None:
            xy = point(self.antenna)
            p.setBrush(QColor(COLORS["violet"]))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([xy + QPointF(0, -7), xy + QPointF(-6, 5), xy + QPointF(6, 5)]))
            p.setPen(QColor(COLORS["violet"]))
            p.drawText(xy + QPointF(8, 5), "Antenna")
        size = scene.span * 0.065 * self.rocket_size
        rotation = frame.rotation * size
        position = frame.position
        if frame.powered:
            flicker = 0.96 + 0.04 * math.sin(frame.time * 45)
            plume_mesh().draw(p, project, forward, [rotation @ np.diag([1, 1, flicker])], [position])
        rocket_mesh(self.canards).draw(p, project, forward, [rotation], [position])
        if frame.recovery:
            canopy = position + np.array([0, 0, size * 1.1])
            for i in range(8):
                angle = i * math.tau / 8
                edge = canopy + size * np.array(
                    [0.42 * frame.inflation * math.cos(angle), 0.42 * frame.inflation * math.sin(angle), 0]
                )
                line(position + frame.rotation[:, 2] * size * 0.2, edge, COLORS["muted"], 0.9)
            canopy_mesh().draw(p, project, forward, [np.eye(3) * size * frame.inflation], [canopy])
        if self.show_velocity and np.linalg.norm(frame.velocity) > 1e-6:
            direction = frame.velocity / np.linalg.norm(frame.velocity)
            tip = position + direction * size * 1.6
            line(position, tip, COLORS["accent"], 2)
            side = np.cross(direction, forward)
            if np.linalg.norm(side) > 1e-8:
                side /= np.linalg.norm(side)
                for sign in (-1, 1):
                    line(tip, tip - direction * size * 0.25 + side * sign * size * 0.12, COLORS["accent"], 2)
        p.setPen(QColor(COLORS["text"]))
        p.setFont(QFont(FONT_FAMILY, 12, QFont.Weight.Bold))
        p.drawText(QRectF(18, 15, self.width() - 36, 26), frame.state)
        p.setFont(QFont(FONT_FAMILY, 10))
        p.setPen(QColor(COLORS["muted"]))
        p.drawText(
            QRectF(18, 43, self.width() - 36, 24),
            f"{frame.time:.2f} s  ·  Altitude {position[2]:,.1f} m  ·  Speed {np.linalg.norm(frame.velocity):,.1f} m/s",
        )
        p.drawText(
            QRectF(18, self.height() - 54, self.width() - 36, 22),
            frame.attitude + " · schematic rocket enlarged for visibility",
        )
        p.drawText(
            QRectF(18, self.height() - 28, self.width() - 36, 22),
            "DRAG TO ORBIT · SHIFT-DRAG TO PAN · SCROLL TO ZOOM · DOUBLE-CLICK TO FIT",
        )
        p.end()
        self.frames += 1
        self.draw_ms += (time.perf_counter() - start) * 1000


class Flight3DDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("3D flight · Rocket GNC Monitor")
        self.resize(1120, 820)
        self.setMinimumSize(820, 620)
        root = QVBoxLayout(self)
        self.title = QLabel("OpenRocket reference · simulation playback")
        root.addWidget(self.title)
        row = QHBoxLayout()
        root.addLayout(row)
        self.camera = ComboBox()
        self.camera.addItems(["Perspective", "Top", "Side"])
        row.addWidget(self.camera)
        self.follow = QCheckBox("Follow rocket")
        row.addWidget(self.follow)
        self.projection = QCheckBox("Ground projection")
        self.projection.setChecked(True)
        row.addWidget(self.projection)
        self.velocity = QCheckBox("Velocity")
        self.velocity.setChecked(True)
        row.addWidget(self.velocity)
        row.addWidget(QLabel("Rocket size"))
        self.size = QDoubleSpinBox()
        self.size.setRange(0.2, 5)
        self.size.setSingleStep(0.2)
        self.size.setValue(1)
        row.addWidget(self.size)
        fit = QPushButton("Fit")
        row.addWidget(fit)
        self.view = FlightView()
        root.addWidget(self.view, 1)
        self.camera.currentTextChanged.connect(self.view.camera_view)
        fit.clicked.connect(self.view.fit)
        for field, name in [
            (self.follow, "follow"),
            (self.projection, "show_projection"),
            (self.velocity, "show_velocity"),
        ]:
            field.toggled.connect(lambda value, n=name: (setattr(self.view, n, value), self.view.update()))
        self.size.valueChanged.connect(lambda v: (setattr(self.view, "rocket_size", v), self.view.update()))
        row = QHBoxLayout()
        root.addLayout(row)
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_play)
        row.addWidget(self.play_button)
        self.rewind = QPushButton("Rewind")
        self.rewind.clicked.connect(self.reset)
        row.addWidget(self.rewind)
        self.speed = ComboBox()
        for speed in (0.1, 0.25, 0.5, 1, 2, 4, 10):
            self.speed.addItem(f"{speed:g}×", speed)
        self.speed.setCurrentIndex(3)
        self.speed.currentIndexChanged.connect(self.change_speed)
        row.addWidget(self.speed)
        self.link = QCheckBox("Follow monitor time")
        row.addWidget(self.link)
        self.events = ComboBox()
        self.events.setAccessibleName("Jump to flight event")
        self.events.currentIndexChanged.connect(self.jump_event)
        row.addWidget(self.events, 1)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName("3D flight playback time")
        root.addWidget(self.slider)
        self.slider.sliderPressed.connect(self.pause)
        self.slider.valueChanged.connect(self.seek)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.reference = None
        self.scene = None
        self.playback = None
        self.last_overlay = 0.0
        self.last_labels = 0.0
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)
        self.load_reference()
        self.link.setChecked(controller.virtual_flight is not None)
        self.link.toggled.connect(self.link_changed)
        self.timer.start()

    def load_reference(self):
        ref = self.controller.reference
        if ref is self.reference:
            return
        self.reference = ref
        if ref is None:
            self.scene = self.playback = None
            self.view.scene = self.view.frame = None
            self.view.update()
            return
        self.scene = FlightScene(ref)
        self.playback = Playback(self.scene.start, self.scene.end)
        self.playback.set_speed(self.speed.currentData())
        self.view.set_scene(self.scene)
        self.view.canards = self.controller.mission.canard_count
        self.title.setText(ref.manifest.get("name", "Reference trajectory") + " · simulation reference")
        self.slider.blockSignals(True)
        self.slider.setRange(0, 100000)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.events.blockSignals(True)
        self.events.clear()
        self.events.addItem("Jump to event…", None)
        for ev in self.scene.events:
            if ev["type"] in EVENT_NAMES and self.scene.start <= ev["time"] <= self.scene.end:
                self.events.addItem(
                    f"{ev['time']:.2f} s · {EVENT_NAMES[ev['type']]} · {ev.get('source', '')}", ev["time"]
                )
        self.events.blockSignals(False)

    def link_changed(self):
        if self.playback:
            self.playback.play(False)
        self.tick()

    def toggle_play(self):
        if self.playback:
            if self.playback.time() >= self.playback.end:
                self.playback.seek(self.playback.start)
            self.playback.play(not self.playback.playing)

    def pause(self):
        if self.playback:
            self.playback.play(False)

    def reset(self):
        if self.playback:
            self.playback.play(False)
            self.playback.seek(self.playback.start)
            self.tick()

    def change_speed(self):
        if self.playback:
            self.playback.set_speed(self.speed.currentData())

    def seek(self):
        if self.playback and not self.link.isChecked():
            self.playback.play(False)
            self.playback.seek(
                self.scene.start + self.slider.value() / 100000 * (self.scene.end - self.scene.start)
            )
            self.tick()

    def jump_event(self):
        value = self.events.currentData()
        if value is not None and self.playback and not self.link.isChecked():
            self.playback.play(False)
            self.playback.seek(value)
            self.tick()

    def tick(self):
        self.load_reference()
        if self.playback is None:
            for w in (self.play_button, self.rewind, self.slider, self.speed, self.events):
                w.setEnabled(False)
            self.status.setText("Run OpenRocket or import a trajectory in Mission & wind.")
            return
        c = self.controller
        now = time.monotonic()
        linked = self.link.isChecked()
        stamp = self.playback.time()
        source = "Independent playback"
        if linked:
            if c.virtual_flight and c.virtual_flight.reference is self.reference:
                flight = c.virtual_flight
                stamp = flight.time + (
                    max(0, min(0.06, now - c.last_tick)) * flight.speed if flight.playing else 0
                )
                source = "Following virtual-pointer rehearsal"
            elif c.latest and c.time_aligned:
                stamp = c.latest.t - c.flight_zero
                source = "Simulation aligned to monitor flight time"
            else:
                source = "Monitor time unavailable · align flight time or start virtual rehearsal"
        for w in (self.play_button, self.rewind, self.slider, self.speed, self.events):
            w.setEnabled(not linked)
        frame = self.scene.frame(stamp)
        if self.view.frame is None or frame.time != self.view.frame.time:
            self.view.frame = frame
            self.view.update()
        if now - self.last_overlay > 0.2:
            self.last_overlay = now
            origin = self.reference.manifest.get("origin")
            self.view.actual = np.empty((0, 3))
            self.view.antenna = None
            if origin:
                m = c.mission
                if m.site_configured and np.allclose(
                    origin, (m.latitude, m.longitude, m.altitude), rtol=0, atol=1e-7
                ):
                    track = list(c.track)
                    if track:
                        self.view.actual = np.asarray(track)[:: max(1, len(track) // 2000), :3]
                if m.pointer_site_configured:
                    self.view.antenna = np.array(
                        to_enu(m.pointer_latitude, m.pointer_longitude, m.pointer_altitude, origin)
                    )
            self.view.update()
        if now - self.last_labels > 0.1:
            self.last_labels = now
            self.play_button.setText("Pause" if self.playback.playing else "Play")
            if not self.slider.isSliderDown():
                self.slider.blockSignals(True)
                self.slider.setValue(
                    round(
                        (frame.time - self.scene.start)
                        / max(1e-9, self.scene.end - self.scene.start)
                        * 100000
                    )
                )
                self.slider.blockSignals(False)
            self.status.setText(
                f"{source} · {frame.time:.2f} / {self.scene.end:.2f} s · Gold: reference · Teal: actual track (matching origin) · Purple: antenna"
            )

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        if self.playback:
            self.playback.play(False)
        super().hideEvent(event)
