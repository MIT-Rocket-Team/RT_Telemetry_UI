"""Pure 1920x1080 Camera + Arc HUD composition from a qualified snapshot."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
import numpy as np
from .display_units import DisplayUnits
from .fonts import FONT_FAMILY
from .broadcast_model import FrameView
from .rocket_geometry import rocket_mesh, plume_mesh

WIDTH, HEIGHT = 1920, 1080
WHITE, MUTED, RED = "#f7f7f7", "#b5bcc4", "#af1f39"


def draw_pose(painter, pose, rect):
    painter.fillRect(rect, QColor("#101820"))
    if not pose.attitude_known or pose.stale:
        text(painter, rect, "ORIENTATION UNAVAILABLE", 21)
        return
    meshes = [rocket_mesh()]
    if pose.motor is True:
        meshes.append(plume_mesh())
    faces = []
    for mesh in meshes:
        for start, count, material in zip(mesh.starts, mesh.counts, mesh.materials):
            points = mesh.vertices[start:start + count]
            rotated = np.asarray(points) @ pose.rotation.T
            projected = [(rect.center().x() + 145 * v[0], rect.center().y() - 145 * v[2]) for v in rotated]
            faces.append((float(rotated[:, 1].mean()), projected, mesh.colors[material][24][0]))
    for _, projected, color in sorted(faces, key=lambda f: f[0]):
        painter.setPen(QPen(QColor(color), 1))
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in projected]))
    caption = "SIMULATION ATTITUDE" if pose.simulated_display else "TELEMETRY ATTITUDE"
    text(painter, QRectF(rect.x(), rect.bottom() - 31, rect.width(), 28), caption, 18)


def text(painter, rect, value, pixels, *, color=WHITE, align=Qt.AlignmentFlag.AlignCenter, bold=False):
    font = QFont(FONT_FAMILY)
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
        font = QFont(FONT_FAMILY)
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


def compose(camera: QImage | None, view: FrameView, units: DisplayUnits, logo: QImage,
            *, inset: QImage | None = None, pose=None) -> QImage:
    """Pure composition on the GUI thread. Inputs must already share scope and time T."""
    frame = QImage(WIDTH, HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
    frame.fill(QColor("#030609"))
    painter = QPainter(frame)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                           | QPainter.RenderHint.SmoothPixmapTransform)
    if camera is not None and not camera.isNull() and view.scene not in {"Standby", "End card"}:
        if any(view.camera_crop):
            left, top, right, bottom = view.camera_crop
            camera = camera.copy(round(camera.width()*left), round(camera.height()*top),
                                 round(camera.width()*(1-left-right)), round(camera.height()*(1-top-bottom)))
        letterbox(painter, camera, QRectF(0, 0, WIDTH, HEIGHT))
    else:
        camera_slate(painter, view.mode)
        if view.scene in {"Standby", "End card"}:
            painter.fillRect(QRectF(280, 390, 1360, 220), QColor("#101820"))
            text(painter, QRectF(300, 410, 1320, 90), view.mission, 56, bold=True)
            text(painter, QRectF(300, 510, 1320, 55), view.caption or ("STANDING BY" if view.scene == "Standby" else "THANK YOU FOR WATCHING"), 30)

    # Scrims preserve legibility over a bright sky as well as over the blank slate.
    for start, end, colors in ((0, 200, (190, 0)), (680, 1080, (0, 235))):
        gradient = QLinearGradient(0, start, 0, end)
        gradient.setColorAt(0, QColor(0, 0, 0, colors[0]))
        gradient.setColorAt(1, QColor(0, 0, 0, colors[1]))
        painter.fillRect(QRectF(0, start, WIDTH, end - start), gradient)
    if not logo.isNull():
        painter.drawImage(QRectF(48, 32, 240, 240 * logo.height() / logo.width()), logo)
    text(painter, QRectF(1480, 36, 392, 36), (view.mode + " · PAUSED") if view.paused else view.mode, 24,
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
    source = f"{view.telemetry_source} · {view.mission} TELEMETRY"
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
    for fraction, event in zip((.09, .26, .43, .60, .77, .94), view.event_labels):
        point = curve.pointAtPercent(fraction)
        done = event in view.completed_events
        painter.setPen(QPen(QColor(WHITE if done else MUTED), 2))
        painter.setBrush(QColor(WHITE if done else "#111820"))
        painter.drawEllipse(point, 7, 7)
        text(painter, QRectF(point.x() - 70, point.y() - 41, 140, 27), event, 18,
             color="#efbd63" if event in view.uncertain_events else WHITE if done else MUTED, bold=done)
    text(painter, QRectF(820, 941, 800, 85), mission_clock(view.mission_elapsed_s, view.clock_state),
         48 if view.clock_state in {"countdown", "hold", "awaiting_launch"} else 62, bold=True)
    state = view.flight_state if available else "TELEMETRY UNAVAILABLE"
    if view.telemetry_status == "stale":
        state = f"LAST KNOWN: {state}"
    text(painter, QRectF(820, 1026, 800, 30), f"{view.mission} · {state}", 23, color=MUTED)
    if view.telemetry_notice:
        text(painter, QRectF(510, 105, 900, 36), view.telemetry_notice, 22, color="#efbd63", bold=True)
    text(painter, QRectF(510, 64, 900, 32), view.timing_label, 19, color=MUTED)
    if view.caption and view.scene not in {"Standby", "End card"}:
        text(painter, QRectF(480, 730, 960, 44), view.caption, 26)
    if inset is not None:
        letterbox(painter, inset, QRectF(1424, 148, 448, 252))
        painter.setPen(QPen(QColor(WHITE), 2))
        painter.drawRect(QRectF(1424, 148, 448, 252))
    if pose is not None:
        draw_pose(painter, pose, QRectF(1424, 148, 448, 252))
    painter.end()
    return frame

