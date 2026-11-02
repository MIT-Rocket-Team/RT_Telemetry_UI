"""Executable composition sketch, not a live source or YouTube publisher.

Inputs: one already-selected camera QImage and a presentation snapshot at T.
Output: one 1920x1080 QImage, optionally a PNG for inspecting the chosen layout.
The production adapter owns timing/identity/freshness; OBS owns audio and RTMPS.
Run with the repository's environment; see YOUTUBE_BROADCAST_PLAN.md section 23.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, fields
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication

from rocket_gnc_monitor.display_units import DisplayUnits

WIDTH, HEIGHT = 1920, 1080
WHITE, MUTED, RED = "#f7f7f7", "#b5bcc4", "#af1f39"
EVENTS = ("LIFTOFF", "BURNOUT", "APOGEE", "DROGUE", "MAIN", "LANDING")
LOGO = Path(__file__).resolve().parents[1] / "assets/mit-rocket-team-logo-web.png"


@dataclass(frozen=True)
class FrameView:
    """Normalized, prequalified presentation input; this is NOT rt-gs wire JSON."""

    mission: str = "MISSION"
    mode: str = "LIVE SOURCE"
    camera_label: str = "ONBOARD"
    telemetry_source: str = "UNAVAILABLE"
    altitude_m: float | None = None
    altitude_datum: str = "unknown"
    vertical_speed_m_s: float | None = None
    vertical_speed_valid: bool = False
    flight_state: str = "UNAVAILABLE"
    mission_elapsed_s: float | None = None
    clock_state: str = "unavailable"
    telemetry_status: str = "unavailable"
    telemetry_age_s: float | None = None
    completed_events: tuple[str, ...] = ()
    camera_notice: str = ""

    @classmethod
    def read(cls, path: Path):
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
            raise ValueError("Expected a normalized FrameView object, not a raw telemetry packet")
        if "completed_events" in data:
            if not isinstance(data["completed_events"], list):
                raise ValueError("completed_events must be a list")
            data["completed_events"] = tuple(data["completed_events"])
        view = cls(**data)
        if view.mode not in {"LIVE SOURCE", "SIMULATION", "REPLAY"}:
            raise ValueError("Unrecognized source mode")
        if view.telemetry_status not in {"fresh", "stale", "unavailable"}:
            raise ValueError("Unrecognized telemetry status")
        if view.clock_state not in {"elapsed", "countdown", "hold", "awaiting_launch", "unavailable"}:
            raise ValueError("Unrecognized clock state")
        for name in ("altitude_m", "vertical_speed_m_s", "mission_elapsed_s", "telemetry_age_s"):
            value = getattr(view, name)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise ValueError(f"{name} must be a finite number or null")
        if view.telemetry_age_s is not None and view.telemetry_age_s < 0:
            raise ValueError("telemetry_age_s cannot be negative")
        if type(view.vertical_speed_valid) is not bool:
            raise ValueError("vertical_speed_valid must be a boolean")
        for name in ("mission", "camera_label", "telemetry_source", "altitude_datum",
                     "flight_state", "camera_notice"):
            if not isinstance(getattr(view, name), str):
                raise TypeError(f"{name} must be text")
        if any(event not in EVENTS for event in view.completed_events):
            raise ValueError("Event names must match this layout's configured events")
        return view


def text(painter, rect, value, pixels, *, color=WHITE, align=Qt.AlignmentFlag.AlignCenter, bold=False):
    font = QFont("Helvetica Neue")
    font.setPixelSize(pixels)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    painter.setFont(font)
    painter.setPen(QColor(color))
    value = QFontMetricsF(font).elidedText(str(value), Qt.TextElideMode.ElideRight, rect.width())
    painter.drawText(rect, align, value)


def letterbox(painter, image, rect):
    painter.fillRect(rect, QColor("#030609"))
    factor = min(rect.width() / image.width(), rect.height() / image.height())
    w, h = image.width() * factor, image.height() * factor
    target = QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)
    painter.drawImage(target, image)


def camera_slate(painter, mode):
    painter.fillRect(QRectF(0, 0, WIDTH, HEIGHT), QColor("#101820"))
    painter.setPen(QPen(QColor("#182631"), 1))
    for x in range(0, WIDTH, 120):
        painter.drawLine(x, 0, x, HEIGHT)
    for y in range(0, HEIGHT, 120):
        painter.drawLine(0, y, WIDTH, y)
    title = "OPENROCKET" if mode == "SIMULATION" else "CAMERA UNAVAILABLE"
    subtitle = "TRANSMISSION PLACEHOLDER" if mode == "SIMULATION" else "WAITING FOR VIDEO"
    text(painter, QRectF(320, 420, 1280, 90), title, 56, color="#8995a2", bold=True)
    text(painter, QRectF(320, 510, 1280, 50), subtitle, 24, color="#8995a2")


def gauge(painter, x, label, value, unit, detail):
    # Fixed reference arcs: no fill fraction, max value, or red warning threshold is implied.
    arc = QRectF(x, 820, 190, 190)
    painter.setPen(QPen(QColor(WHITE), 4))
    painter.drawArc(arc, 225 * 16, -247 * 16)
    painter.setPen(QPen(QColor(RED), 4))
    painter.drawArc(arc, -22 * 16, -23 * 16)
    text(painter, QRectF(x + 8, 854, 174, 30), label, 19, bold=True)
    # Fit numbers rather than truncating a magnitude into a misleading partial number.
    for pixels in range(48, 23, -2):
        font = QFont("Helvetica Neue")
        font.setPixelSize(pixels)
        font.setWeight(QFont.Weight.DemiBold)
        if QFontMetricsF(font).horizontalAdvance(value) <= 174:
            break
    if QFontMetricsF(font).horizontalAdvance(value) > 174:
        value = "—"
    text(painter, QRectF(x + 8, 890, 174, 65), value, pixels, bold=True)
    text(painter, QRectF(x + 8, 950, 174, 27), unit, 21, color=MUTED)
    text(painter, QRectF(x, 979, 190, 27), detail, 16, color=MUTED)


def mission_clock(seconds, state="elapsed"):
    if state == "hold":
        return "HOLD" if seconds is None else f"HOLD {mission_clock(seconds)}"
    if state == "awaiting_launch" or (state == "countdown" and seconds is not None and seconds >= 0):
        return "AWAITING LAUNCH"
    if seconds is None or state == "unavailable":
        return "T  —:—:—"
    # Quantize before splitting so rounding never produces a seconds field of 60.0.
    ticks = round(abs(seconds) * 10)
    hours, remainder = divmod(ticks, 36000)
    minutes, remainder = divmod(remainder, 600)
    whole, tenth = divmod(remainder, 10)
    sign = "+" if seconds >= 0 else "−"
    return f"T{sign}{hours:02}:{minutes:02}:{whole:02}.{tenth}"


def compose(camera: QImage | None, view: FrameView, units: DisplayUnits, logo: QImage) -> QImage:
    """Pure composition on the GUI thread. Inputs must already share scope and time T."""
    frame = QImage(WIDTH, HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
    frame.fill(QColor("#030609"))
    painter = QPainter(frame)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                           | QPainter.RenderHint.SmoothPixmapTransform)
    if camera is not None and not camera.isNull():
        letterbox(painter, camera, QRectF(0, 0, WIDTH, HEIGHT))
    else:
        camera_slate(painter, view.mode)

    # Scrims preserve legibility over a bright sky as well as over the blank slate.
    for start, end, colors in ((0, 200, (190, 0)), (680, 1080, (0, 235))):
        gradient = QLinearGradient(0, start, 0, end)
        gradient.setColorAt(0, QColor(0, 0, 0, colors[0]))
        gradient.setColorAt(1, QColor(0, 0, 0, colors[1]))
        painter.fillRect(QRectF(0, start, WIDTH, end - start), gradient)
    painter.drawImage(QRectF(48, 32, 240, 240 * logo.height() / logo.width()), logo)
    text(painter, QRectF(1480, 36, 392, 36), view.mode, 24,
         color="#efbd63" if view.mode != "LIVE SOURCE" else WHITE,
         align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, bold=True)
    text(painter, QRectF(1340, 78, 532, 32), view.camera_label, 21, color=MUTED,
         align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if view.camera_notice:
        painter.fillRect(QRectF(570, 170, 780, 54), QColor(0, 0, 0, 200))
        text(painter, QRectF(580, 177, 760, 40), view.camera_notice, 24)

    available = view.telemetry_status != "unavailable"
    altitude_labels = {"above_launch": "ABOVE LAUNCH", "msl": "MSL", "ellipsoid": "ELLIPSOID"}
    altitude = units.convert(view.altitude_m, "distance") if available else None
    if view.altitude_datum not in altitude_labels:
        altitude = None
    speed = units.convert(view.vertical_speed_m_s, "velocity") if available and view.vertical_speed_valid else None
    gauge(painter, 48, "VERT SPEED", "—" if speed is None else f"{speed:+,.1f}",
          units.unit("velocity"), "SIGNED · VERTICAL")
    gauge(painter, 264, "ALTITUDE", "—" if altitude is None else f"{altitude:,.0f}",
          units.unit("distance"), altitude_labels.get(view.altitude_datum, "DATUM UNKNOWN"))
    source = f"{view.mission} TELEMETRY · {view.telemetry_source}"
    if view.telemetry_status == "stale":
        age = "" if view.telemetry_age_s is None else f" · {view.telemetry_age_s:.1f}s"
        source = f"STALE · {view.telemetry_source}{age}"
    elif not available:
        source = "TELEMETRY UNAVAILABLE"
    text(painter, QRectF(48, 1014, 420, 32), source, 21, bold=True)

    curve = QPainterPath(QPointF(540, 1060))
    curve.cubicTo(980, 820, 1432, 820, 1872, 1060)
    ribbon = QPainterPath(curve)
    ribbon.lineTo(1872, 1080)
    ribbon.lineTo(540, 1080)
    ribbon.closeSubpath()
    painter.fillPath(ribbon, QColor(18, 22, 28, 100))
    painter.setPen(QPen(QColor("#b8bec5"), 2))
    painter.drawPath(curve)
    # Categorical event order; distance along the arc is not a time prediction.
    for fraction, event in zip((.09, .26, .43, .60, .77, .94), EVENTS):
        point = curve.pointAtPercent(fraction)
        done = event in view.completed_events
        painter.setPen(QPen(QColor(WHITE if done else MUTED), 2))
        painter.setBrush(QColor(WHITE if done else "#111820"))
        painter.drawEllipse(point, 7, 7)
        text(painter, QRectF(point.x() - 70, point.y() - 41, 140, 27), event, 18,
             color=WHITE if done else MUTED, bold=done)
    text(painter, QRectF(820, 941, 800, 85), mission_clock(view.mission_elapsed_s, view.clock_state),
         48 if view.clock_state in {"countdown", "hold", "awaiting_launch"} else 62, bold=True)
    state = view.flight_state if available else "TELEMETRY UNAVAILABLE"
    if view.telemetry_status == "stale":
        state = f"LAST KNOWN: {state}"
    text(painter, QRectF(820, 1026, 800, 30), f"{view.mission} · {state}", 23, color=MUTED)
    painter.end()
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True, help="Normalized FrameView JSON")
    parser.add_argument("--camera", type=Path, help="One selected camera still; omit for a placeholder")
    parser.add_argument("--units", choices=("imperial", "metric"), default="imperial")
    parser.add_argument("--out", type=Path, required=True, help="PNG composition to write")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    view = FrameView.read(args.snapshot)
    camera = QImage(str(args.camera)) if args.camera else None
    if camera is not None and camera.isNull():
        parser.error("Could not read --camera image")
    logo = QImage(str(LOGO))
    if logo.isNull():
        parser.error(f"Missing or invalid team logo: {LOGO}")
    frame = compose(camera, view, DisplayUnits(args.units), logo)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if not frame.save(str(args.out), "PNG"):
        parser.error("Could not write output PNG")
    print(f"Rendered {frame.width()}x{frame.height()}: {args.out}")
    # Keep the QApplication alive until every GUI resource has been released.
    del frame, logo, camera, app


if __name__ == "__main__":
    main()
