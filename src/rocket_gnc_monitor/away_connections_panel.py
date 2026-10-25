"""Read-only station health display, ready for the future connection adapter.

Call observe_link/observe_rocket on the GUI thread with measured values, then
refresh periodically so rocket ages and link expiry advance between reports.
This widget does not open connections, issue pings or infer remote health from
the Base station's own telemetry board.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .away_connection_health import AwayConnectionHealth
from .widgets import COLORS


def health_color(status):
    light = QColor(COLORS["panel"]).lightness() > 128
    return {
        "offline": "#737982" if light else "#939aa4",
        "good": "#187a43" if light else "#55d38b",
        "fair": "#a65a00" if light else "#f2aa4c",
        "bad": "#ba3339" if light else "#f16d73",
    }[status]


def milliseconds(value):
    if value is None:
        return "—"
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}"


def inverse(value):
    if value is None:
        return "—"
    return "∞" if value == 0 else f"{1 / value:.3g}"


class StationConnectionTile(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.title = title
        self.reading = None
        self.role_status = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def set_reading(self, reading):
        self.reading = reading
        state = self.state_label()
        self.setAccessibleName(f"{self.title}: {state}")
        self.setAccessibleDescription(
            f"LTU-XR ping {milliseconds(reading.ping_ms)} milliseconds; "
            f"rocket sample age {milliseconds(reading.sample_age_ms)} milliseconds"
        )
        self.update()

    def state_label(self):
        if self.reading.status == "bad" and self.reading.sample_age_ms is None:
            return "No samples"
        return {"offline": "Offline", "good": "Good", "fair": "Fair", "bad": "Stale"}[self.reading.status]

    def paintEvent(self, event):
        reading = self.reading
        if reading is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(COLORS["line"]), 1))
        p.setBrush(QColor(COLORS["panel"]))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 5, 5)

        def font(size, bold=False):
            f = QFont(self.font())
            f.setPixelSize(size)
            f.setBold(bold)
            p.setFont(f)

        font(16, True)
        p.setPen(QColor(health_color(reading.status)))
        p.drawText(QPointF(9, 24), self.title)
        font(10)
        p.drawText(QRectF(49, 8, self.width() - 58, 20),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.state_label())

        if self.role_status:
            font(8)
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(QRectF(7, 26, self.width() - 14, 14), Qt.AlignmentFlag.AlignCenter, self.role_status)
        top, bottom = 44, max(46, self.height() - 62)
        chart_height = bottom - top
        p.setPen(QPen(QColor(COLORS["line"]), .6))
        for fraction in (0, .5, 1):
            y = bottom - chart_height * fraction
            p.drawLine(QPointF(10, y), QPointF(self.width() - 10, y))
        half = (self.width() - 20) / 2
        for index, (title, value, quality, shade, unit) in enumerate((
            ("LTU-XR", reading.ping_ms, reading.ping_quality,
             "good" if reading.online else "offline", "RTT"),
            ("Rocket", reading.sample_age_ms, reading.rocket_quality, reading.status, "age"),
        )):
            center = 10 + half * (index + .5)
            width = min(33, half * .6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(COLORS["raised"]))
            p.drawRect(QRectF(center - width / 2, top, width, chart_height))
            if quality > 0:
                p.setBrush(QColor(health_color(shade)))
                p.drawRect(QRectF(center - width / 2, bottom - chart_height * quality,
                                 width, chart_height * quality))
            centered = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            p.setPen(QColor(COLORS["text"]))
            font(10, True)
            p.drawText(QRectF(10 + half * index, bottom + 4, half, 16), centered, title)
            font(9)
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(QRectF(10 + half * index, bottom + 20, half, 15), centered,
                       f"{milliseconds(value)} ms {unit}")
            p.drawText(QRectF(10 + half * index, bottom + 36, half, 15), centered,
                       f"{inverse(value)} ms⁻¹")


class AwayConnectionsPanel(QWidget):
    def __init__(self, health=None, parent=None):
        super().__init__(parent)
        self.setObjectName("transparent")
        self.health = health if health is not None else AwayConnectionHealth()
        self.tiles = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(5)
        grid = QGridLayout()
        grid.setSpacing(6)
        for index in range(4):
            station = f"away{index + 1}"
            tile = self.tiles[station] = StationConnectionTile(f"GS{index + 1}")
            tile.setToolTip(
                "Left: inverse LTU-XR ping. Right: inverse rocket sample age.\n"
                "Bar height is a relative score, not packet delivery or RF signal strength.\n"
                f"Full scale: ping ≤ {self.health.ping_full_scale_ms:g} ms; "
                f"sample age ≤ {self.health.rocket_full_scale_ms:g} ms.\n"
                "GS title: gray when offline; green below 250 ms; orange below 1 s; "
                "red at 1 s or older, or without a rocket sample.\n"
                "Zero milliseconds has an unbounded inverse; bar height is capped at full scale."
            )
            grid.addWidget(tile, index // 2, index % 2)
        for i in range(2):
            grid.setRowStretch(i, 1)
            grid.setColumnStretch(i, 1)
        outer.addLayout(grid, 1)
        self.status = QLabel("Awaiting station data")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setToolTip("The inter-station protocol is not connected yet. No background pings are sent.")
        outer.addWidget(self.status)
        self.refresh()

    def observe_link(self, station, *, online, ping_ms=None):
        self.health.observe_link(station, online=online, ping_ms=ping_ms)
        self.refresh()

    def observe_rocket(self, station, *, sample_age_ms):
        self.health.observe_rocket(station, sample_age_ms=sample_age_ms)
        self.refresh()

    def refresh(self):
        online = 0
        for station, tile in self.tiles.items():
            reading = self.health.view(station)
            tile.set_reading(reading)
            online += reading.online
        self.status.setText(f"{online} / 4 stations online" if online else "Awaiting station data")
