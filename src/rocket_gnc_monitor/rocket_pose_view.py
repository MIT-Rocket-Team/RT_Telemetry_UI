"""Software-rendered rocket attitude with an upright, azimuth-only camera."""

import math
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QMatrix3x3, QPainter, QPen, QQuaternion
from PySide6.QtWidgets import QWidget

from .flight_scene import quaternion_matrix
from .fonts import FONT_FAMILY
from .rocket_geometry import canopy_mesh, plume_mesh, rocket_mesh
from .widgets import COLORS


class RocketPoseView(QWidget):
    """Display a supplied pose without inferring events or sending commands.

    World +Z always projects vertically. Only the owner's azimuth slider moves
    the camera; mouse dragging, wheel zoom and double-click have no function.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pose = None
        self.canards = 4
        self.azimuth = 40.0
        self.elevation = 14.0
        self._display_quaternion = self._target_quaternion = QQuaternion()
        self._effect_time = 0.0
        self._last_frame = time.monotonic()
        self.animation = QTimer(self)
        self.animation.setTimerType(Qt.TimerType.PreciseTimer)
        self.animation.setInterval(16)
        self.animation.timeout.connect(self.animate)
        self.setMinimumSize(260, 130)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setAccessibleName("3D rocket pose; azimuth slider controls the upright camera")
        self.setToolTip("Use the azimuth slider to view the rocket. Camera vertical remains upright.\n"
                        "Rocket orientation and effects come only from the selected telemetry or reference source.\n"
                        "MOTOR ON means confirmed current burning; ignition history alone never produces a flame.")

    @property
    def display_rotation(self):
        q = self._display_quaternion
        return quaternion_matrix((q.scalar(), q.x(), q.y(), q.z()))

    @property
    def angle_text(self):
        angles = self.pose.angles if self.pose is not None else (None, None, None)
        names = ("∫R", "∫P", "∫Y") if self.pose is not None and self.pose.angle_kind == "gyro_integrals" else ("R", "P", "Y")
        return tuple(f"{name}  {value:+.1f}°" if value is not None and math.isfinite(value)
                     else f"{name}  —" for name, value in zip(names, angles))

    @property
    def orientation_text(self):
        if self.pose is not None and self.pose.attitude_known:
            return "SIMULATION ATTITUDE" if self.pose.source == "SIMULATION" else "TELEMETRY ATTITUDE"
        if self.pose is not None and "Path-aligned" in self.pose.status:
            return "PATH-ALIGNED ILLUSTRATION"
        return "ORIENTATION UNAVAILABLE · neutral model"

    @property
    def effect_text(self):
        def label(name, value):
            return f"{name} " + ("ON" if value is True else "OFF" if value is False else "UNKNOWN")
        motor = label("MOTOR", self.pose.motor if self.pose is not None else None)
        if self.pose is not None and self.pose.ignited is True and self.pose.motor is not True:
            motor = "IGNITED · OFF" if self.pose.motor is False else "IGNITED · BURN ?"
        return (motor,
                label("CHUTE", self.pose.parachute if self.pose is not None else None))

    def set_pose(self, pose):
        previous = self.pose
        self.pose = pose
        rotation = np.asarray(pose.rotation, dtype=float) if pose is not None else np.eye(3)
        known = pose is not None and pose.attitude_known
        if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
            rotation = np.eye(3)
        self._target_quaternion = QQuaternion.fromRotationMatrix(QMatrix3x3(rotation.flatten().tolist())).normalized()
        # Never blend measured and explicitly selected simulation frames. The
        # model supplies neutral telemetry poses and labelled path illustrations.
        if (not self.isVisible() or not known or previous is None or pose.stale
                or previous.source != pose.source):
            self._display_quaternion = QQuaternion(self._target_quaternion)
        self.setAccessibleDescription(self._source_text() + "; " + self.orientation_text
                                      + "; " + "; ".join(self.angle_text + self.effect_text))
        self.update()

    def set_azimuth(self, degrees):
        if isinstance(degrees, bool) or not isinstance(degrees, (float, int)) or not math.isfinite(degrees):
            raise ValueError("Camera azimuth must be a finite number")
        self.azimuth = float(degrees) % 360
        self.update()

    def showEvent(self, event):
        self._last_frame = time.monotonic()
        self._display_quaternion = QQuaternion(self._target_quaternion)
        self.animation.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.animation.stop()
        super().hideEvent(event)

    def animate(self):
        now = time.monotonic()
        dt, self._last_frame = max(0.0, now - self._last_frame), now
        if self.pose is None or self.pose.stale:
            return
        fraction = 1 - math.exp(-dt / 0.06)
        dot = abs(QQuaternion.dotProduct(self._display_quaternion, self._target_quaternion))
        changing = dot < 1 - 1e-7
        if changing:
            self._display_quaternion = QQuaternion.slerp(self._display_quaternion, self._target_quaternion, fraction)
        elif dot < 1:
            self._display_quaternion = QQuaternion(self._target_quaternion)
        if self.pose.motor is True:
            self._effect_time += dt
            changing = True
        if changing:
            self.update()

    def _source_text(self):
        if self.pose is None:
            return "NO TELEMETRY · Neutral illustration"
        return self.pose.source + (" · STALE" if self.pose.stale else "")

    def _angle_source(self):
        if self.pose is not None and self.pose.source == "SIMULATION":
            return "R/P/Y derived" if self.pose.attitude_known else "R/P/Y unavailable"
        if self.pose is not None and self.pose.angle_kind == "gyro_integrals":
            return "Body gyro totals · not attitude"
        return "R/P/Y derived" if self.pose is not None and self.pose.attitude_known else "R/P/Y unavailable"

    def _layout(self):
        footer_top = self.height() - 94
        return (QRectF(8, 4, self.width() - 16, footer_top - 9),
                QRectF(12, footer_top, self.width() - 24, 92))

    def projection(self, viewport=None):
        """Fit visible effects without changing scale as the rocket rotates."""
        viewport = viewport or self._layout()[0]
        az, el = math.radians(self.azimuth), math.radians(self.elevation)
        forward = -np.array([math.cos(az) * math.cos(el), math.sin(az) * math.cos(el), math.sin(el)])
        right = np.array([-math.sin(az), math.cos(az), 0.0])
        up = np.cross(right, forward)
        burning = self.pose is not None and self.pose.motor is True
        recovery = self.pose is not None and self.pose.parachute is True
        radius = 1.08 if burning else 0.60
        low, high = -radius, max(radius, 1.45 if recovery else radius)
        center = np.array([0.0, 0.0, (low + high) / 2])
        scale = max(1.0, min(viewport.width() / (2 * radius), viewport.height() / (high - low)))

        def project(vertices):
            v = np.asarray(vertices, dtype=float) - center
            return np.column_stack((viewport.center().x() + (v @ right) * scale,
                                    viewport.center().y() - (v @ up) * scale))
        return project, forward

    @staticmethod
    def _font(painter, pixels, bold=False):
        font = QFont(FONT_FAMILY)
        font.setPixelSize(pixels)
        font.setBold(bold)
        painter.setFont(font)

    def _draw_text(self, painter, viewport, info):
        x, width = info.x(), info.width()
        centered = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        painter.setPen(QPen(QColor(COLORS["line"]), 0.7))
        painter.drawLine(QPointF(x, info.y() - 2), QPointF(x + width, info.y() - 2))
        painter.setPen(QColor(COLORS["text"]))
        self._font(painter, 12, True)
        painter.drawText(QRectF(x, info.y(), width, 18), centered, self.orientation_text)
        self._font(painter, 13, True)
        for i, text in enumerate(self.angle_text):
            painter.drawText(QRectF(x + i * width / 3, info.y() + 20, width / 3, 20), centered, text)
        self._font(painter, 11, True)
        for i, text in enumerate(self.effect_text):
            value = (self.pose.motor if self.pose else None) if i == 0 else (self.pose.parachute if self.pose else None)
            painter.setPen(QColor(COLORS["gold"] if value is True else COLORS["muted"]))
            painter.drawText(QRectF(x + i * width / 2, info.y() + 41, width / 2, 18), centered, text)
        painter.setPen(QColor(COLORS["muted"]))
        self._font(painter, 10)
        status = self.pose.status.removeprefix(self.pose.source + " · ") if self.pose is not None else "Attitude unavailable · neutral illustration"
        status = self._source_text() + " · " + self._angle_source() + " · " + status
        if self.pose is not None and self.pose.angle_kind == "gyro_integrals":
            status = self._source_text() + " · Body gyro integrals in degrees · not Euler angles"
            if "Invalid orientation metadata" in self.pose.status:
                status += " · Invalid orientation metadata"
        painter.drawText(QRectF(x, info.y() + 61, width, 31),
                         Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap, status)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(COLORS["panel"]))
        viewport, info = self._layout()
        project, forward = self.projection(viewport)
        point = lambda v: QPointF(*project([v])[0])
        rotation = self.display_rotation
        origin = np.zeros(3)
        painter.save()
        painter.setClipRect(viewport)
        painter.setPen(QPen(QColor(COLORS["line"]), 0.7))
        for offset in (-0.5, 0, 0.5):
            painter.drawLine(point([offset, -0.7, -0.65]), point([offset, 0.7, -0.65]))
            painter.drawLine(point([-0.7, offset, -0.65]), point([0.7, offset, -0.65]))
        if self.pose is not None and self.pose.motor is True:
            flicker = 0.97 + 0.03 * math.sin(self._effect_time * 45)
            plume_mesh().draw(painter, project, forward, [rotation @ np.diag([1, 1, flicker])], [origin])
        painter.setOpacity(0.4 if self.pose is None or (not self.pose.attitude_known and self.pose.source != "SIMULATION") else 1.0)
        rocket_mesh(self.canards).draw(painter, project, forward, [rotation], [origin])
        painter.setOpacity(1.0)
        if self.pose is not None and self.pose.parachute is True:
            inflation = min(1.0, max(0.05, self.pose.inflation))
            canopy = np.array([0.0, 0.0, 1.1])
            attachment = rotation @ np.array([0.0, 0.0, 0.24])
            painter.setPen(QPen(QColor(COLORS["muted"]), 0.8))
            for i in range(8):
                angle = i * math.tau / 8
                edge = canopy + np.array([0.42 * inflation * math.cos(angle), 0.42 * inflation * math.sin(angle), 0])
                painter.drawLine(point(attachment), point(edge))
            canopy_mesh().draw(painter, project, forward, [np.eye(3) * inflation], [canopy])
        painter.restore()
        self._draw_text(painter, viewport, info)
