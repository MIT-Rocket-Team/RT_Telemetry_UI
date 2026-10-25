"""Native, software-rendered instruments; no browser or OpenGL dependency."""

from __future__ import annotations
import math
import time
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget, QComboBox, QStyle
from .fonts import FONT_FAMILY
from .mount_geometry import mount_mesh

COLORS = dict(
    bg="#0b111a",
    panel="#121d2a",
    raised="#1a293a",
    line="#2a3b4f",
    text="#e8eff7",
    muted="#98abc0",
    accent="#79d4c8",
    gold="#e5b76d",
    violet="#ba9aef",
    red="#ef9294",
)
DARK_COLORS = dict(COLORS)
LIGHT_COLORS = dict(
    bg="#edf2f5",
    panel="#ffffff",
    raised="#e1eaf0",
    line="#b9c9d4",
    text="#152b3c",
    muted="#486174",
    accent="#157d70",
    gold="#986019",
    violet="#7650a5",
    red="#b43840",
)

STYLE = """
QWidget { background: #0b111a; color: #e8eff7; font-family: 'Lucida Grande'; font-size: 13px; }
QMainWindow { background: #0b111a; }
QFrame#card, QGroupBox { background: #121d2a; border: 1px solid #2a3b4f; border-radius: 9px; }
QFrame#card QLabel, QFrame#card QWidget#transparent { background: transparent; }
QLabel#eyebrow { color: #79d4c8; font-size: 11px; letter-spacing: 2px; }
QLabel#heading { font-size: 25px; font-weight: 600; }
QLabel#muted { color: #98abc0; }
QLabel#metric { font-size: 28px; font-weight: 500; }
QLabel#section { font-size: 16px; font-weight: 600; }
QPushButton { background: #1a293a; border: 1px solid #34485e; border-radius: 5px; padding: 7px 12px; }
QPushButton:hover { background: #23374b; border-color: #79d4c8; }
QPushButton:pressed { background: #34485e; }
QPushButton:checked { background: #24464a; border-color: #79d4c8; color: #79d4c8; }
QPushButton:disabled { color: #617287; border-color: #263344; }
QPushButton#primary { background: #79d4c8; color: #091715; border-color: #79d4c8; font-weight: 600; }
QPushButton#primary:disabled { background: #1a293a; color: #617287; border-color: #263344; }
QPushButton#danger { color: #ef9294; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit { background: #172333; border: 1px solid #34485e; border-radius: 5px; padding: 6px; selection-background-color: #2f716e; }
/* Use the styled list: macOS menu popups mismeasure the checkmark gutter with this font and stylesheet. */
QComboBox { combobox-popup: 0; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: #617287; border-color: #263344; }
QComboBox QAbstractItemView { background: #172333; selection-background-color: #2f716e; }
QListWidget#navigation { background: #0e1722; border: none; padding: 8px; outline: none; }
QListWidget#navigation::item { padding: 13px 9px; margin-bottom: 5px; border-radius: 6px; }
QListWidget#navigation::item:selected { background: #213c42; color: #9ceddf; }
QListWidget#navigation::item:hover { background: #1a293a; }
QTableWidget, QPlainTextEdit, QListWidget { background: #121d2a; border: 1px solid #2a3b4f; gridline-color: #243348; selection-background-color: #264b55; }
QHeaderView::section { background: #1a293a; color: #aabfd2; padding: 7px; border: none; }
QTableWidget::item { padding: 5px; }
QSlider::groove:horizontal { height: 5px; background: #34485e; border-radius: 2px; }
QSlider::handle:horizontal { background: #79d4c8; width: 14px; margin: -5px 0; border-radius: 7px; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #617287; border-radius: 3px; }
QCheckBox::indicator:checked { background: #79d4c8; border-color: #79d4c8; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #0b111a; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #34485e; min-height: 30px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: #203447; color: #e8eff7; border: 1px solid #52657a; padding: 7px; }
QStatusBar { background: #121d2a; color: #98abc0; }
QSplitter::handle { background: #0b111a; }
"""


class ComboBox(QComboBox):
    """Measure the polished popup rows, including padding and a scrollbar gutter."""

    def showPopup(self):
        view = self.view()
        view.ensurePolished()
        width = max(
            self.width(),
            view.sizeHintForColumn(self.modelColumn())
            + 16
            + 2 * view.frameWidth()
            + view.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, view),
        )
        screen = self.screen().availableGeometry()
        view.setMinimumWidth(min(width, max(1, screen.width() - 24)))
        super().showPopup()


# Renderer coordinates: +Y is up and +Z is north, so east must be -X.
# E × N = U; positive azimuth turns north toward east when viewed from above.
MOUNT_COMPASS = {"N": (0, 0, 1), "E": (-1, 0, 0), "S": (0, 0, -1), "W": (1, 0, 0)}


def mount_rotations(azimuth, elevation):
    az, el = math.radians(azimuth), math.radians(elevation)
    rotation = np.array([[math.cos(az), 0, -math.sin(az)], [0, 1, 0], [math.sin(az), 0, math.cos(az)]])
    tilt = np.array([[1, 0, 0], [0, math.cos(el), math.sin(el)], [0, -math.sin(el), math.cos(el)]])
    return rotation, tilt


class MountView(QWidget):
    """The photo-informed model shares real attachment transforms across camera views."""

    def __init__(self):
        super().__init__()
        self.azimuth, self.elevation = 0.0, 0.0
        self.mesh = mount_mesh()
        self._display_pose = (0.0, 0.0)
        self.pose_provider = None
        self.animation = QTimer(self)
        self.animation.setTimerType(Qt.TimerType.PreciseTimer)
        self.animation.setInterval(16)
        self.animation.timeout.connect(self.animate)
        self._last_frame = time.monotonic()
        self.camera = "Perspective"
        self.camera_yaw, self.camera_pitch = 72.0, 20.0
        self.zoom = 1.0
        self.drag_position = None
        self.setMinimumSize(350, 360)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            "Drag to orbit the camera. Scroll to zoom. Double-click to reset the view. Viewing does not move the antenna."
        )
        self.setAccessibleName("Antenna assembly; drag to orbit")

    def showEvent(self, event):
        self._last_frame = time.monotonic()
        self._display_pose = (self.azimuth, self.elevation)
        self.animation.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.animation.stop()
        super().hideEvent(event)

    def animate(self):
        now = time.monotonic()
        dt, self._last_frame = max(0, now - self._last_frame), now
        if self.pose_provider:
            pose = self.pose_provider()
            if pose is not None:
                self.azimuth, self.elevation = pose
        az, el = self._display_pose
        da, de = (self.azimuth - az + 180) % 360 - 180, self.elevation - el
        if abs(da) + abs(de) < 1e-4:
            return
        fraction = 1 - math.exp(-dt / 0.045)
        self._display_pose = ((az + da * fraction) % 360, el + de * fraction)
        self.update()

    def set_camera(self, name):
        self.camera_yaw, self.camera_pitch = {
            "Perspective": (72.0, 20.0),
            "Front": (0.0, 0.0),
            "Side": (90.0, 0.0),
            "Top": (0.0, 90.0),
        }[name]
        self.camera, self.zoom = name, 1.0
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_position is not None:
            delta = event.position() - self.drag_position
            self.drag_position = event.position()
            self.camera_yaw = (self.camera_yaw - delta.x() * 0.45) % 360
            self.camera_pitch = (self.camera_pitch + delta.y() * 0.45) % 360
            self.camera = "Orbit"
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.set_camera("Perspective")
            event.accept()

    def wheelEvent(self, event):
        self.zoom = min(2.5, max(0.65, self.zoom * 1.12 ** (event.angleDelta().y() / 120)))
        self.update()
        event.accept()

    def set_pose(self, azimuth, elevation):
        if (self.azimuth, self.elevation) != (azimuth, elevation):
            self.azimuth, self.elevation = azimuth, elevation
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(COLORS["panel"]))
        rotation, tilt = mount_rotations(*self._display_pose)

        def identity(v):
            return np.asarray(v, dtype=float)

        def turn(v):
            return rotation @ np.asarray(v)

        def carrier(v):
            return rotation @ tilt @ np.asarray(v) + np.array([0, 3.28, 0])

        def avenger(v):
            return carrier(np.asarray(v) + [-1.03, 0, 0.16])

        center = np.array(getattr(self, "display_center", (0, 3.28, 0)))
        yaw, pitch = math.radians(self.camera_yaw), math.radians(self.camera_pitch)
        eye = center + 12 * np.array(
            [math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch)]
        )
        unit = lambda a: a / max(np.linalg.norm(a), 1e-10)
        forward = unit(center - eye)
        # Analytic camera basis stays defined at top/bottom views and through a full orbit.
        right = np.array([math.cos(yaw), 0, -math.sin(yaw)])
        up = np.cross(right, forward)
        # A fixed envelope prevents zoom/centering jumps as the mount or camera turns.
        scale = self.zoom * min(self.width() - 40, self.height() - getattr(self, "display_padding", 70)) / 7.8

        def project_many(vertices):
            v = np.asarray(vertices) - center
            return np.column_stack(
                (
                    self.width() / 2 + (v @ right) * scale,
                    self.height() / 2 - (v @ up) * scale,
                )
            )

        def project(v):
            return QPointF(*project_many([v])[0])

        feed_tip = [0, 0, 1.04]
        p.setPen(QPen(QColor(COLORS["line"]), 0.6))
        for t in np.arange(-2.5, 2.6, 0.5):
            p.drawLine(project([t, 0, -2.5]), project([t, 0, 2.5]))
            p.drawLine(project([-2.5, 0, t]), project([2.5, 0, t]))
        self.mesh.draw(
            p,
            project_many,
            forward,
            [np.eye(3), rotation, rotation @ tilt],
            [np.zeros(3), np.zeros(3), np.array([0, 3.28, 0])],
        )
        p.setPen(QPen(QColor(COLORS["accent"]), 1.4, Qt.PenStyle.DashLine))
        p.drawLine(project(carrier(feed_tip)), project(carrier([0, 0, 2.68])))
        p.setPen(QColor(COLORS["muted"]))
        p.setFont(QFont(FONT_FAMILY, 10))
        for name, direction in MOUNT_COMPASS.items():
            point = project(np.asarray(direction) * 2.27)
            p.drawText(point, name)
        if getattr(self, "show_hints", True):
            p.drawText(
                QRectF(8, self.height() - 24, self.width() - 16, 20),
                Qt.AlignmentFlag.AlignCenter,
                "DRAG TO ORBIT · SCROLL TO ZOOM · DOUBLE-CLICK TO RESET",
            )
