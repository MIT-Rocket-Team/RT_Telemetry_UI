"""Launch-station video selection over local USB and explicitly supplied remote frames.

This widget does not open cameras or establish station links. Remote reception
timestamps, when supplied, are from the receiving computer's monotonic clock.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .widgets import COLORS


AWAY_STATIONS = ("away1", "away2", "away3", "away4")
LOCAL_DIGITAL = ("local", "digital")
STALE_SECONDS = 5.0


class VideoImage(QWidget):
    """Paint a retained frame with letterboxing instead of stretching its pixels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self.placeholder = "Awaiting station link"
        self.setMinimumSize(40, 30)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def pixmap(self):
        return self._pixmap

    def set_frame(self, pixmap):
        self._pixmap = QPixmap(pixmap)
        self.update()

    def reset_frame(self, text):
        self._pixmap = QPixmap()
        self.placeholder = text
        self.update()

    def frame_rect(self):
        if self._pixmap.isNull():
            return QRectF()
        size = self._pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                      size.width(), size.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._pixmap.isNull():
            painter.setPen(Qt.GlobalColor.lightGray)
            painter.drawText(self.rect().adjusted(8, 6, -8, -6),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.placeholder)
        else:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.frame_rect(), self._pixmap, QRectF(self._pixmap.rect()))

    def resizeEvent(self, event):
        self.update()
        super().resizeEvent(event)


class VideoTile(QFrame):
    activated = Signal(str, str)

    def __init__(self, station, channel, title, *, thumbnail=False, parent=None):
        super().__init__(parent)
        self.station, self.channel, self.thumbnail = station, channel, thumbnail
        self.selected = False
        self.setObjectName("card")
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.layout_column = QVBoxLayout(self)
        self.layout_column.setContentsMargins(7, 5, 7, 5)
        self.layout_column.setSpacing(4)
        self.header = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setObjectName("section")
        self.source_label = QLabel()
        self.source_label.setObjectName("muted")
        self.header.addWidget(self.title)
        self.header.addStretch()
        self.header.addWidget(self.source_label)
        self.layout_column.addLayout(self.header)
        self.image = VideoImage()
        self.layout_column.addWidget(self.image, 1)
        self.status_label = QLabel()
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        self.layout_column.addWidget(self.status_label)
        if thumbnail:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.setAccessibleName(f"Away station {station[-1]} {title}; double-click to show in main view")
            for child in (self.title, self.source_label, self.image, self.status_label):
                child.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (self.thumbnail and event.type() == QEvent.Type.MouseButtonDblClick
                and event.button() == Qt.MouseButton.LeftButton):
            self.activated.emit(self.station, self.channel)
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def mouseDoubleClickEvent(self, event):
        if self.thumbnail and event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.station, self.channel)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if self.thumbnail and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit(self.station, self.channel)
            event.accept()
        else:
            super().keyPressEvent(event)

    def set_selected(self, selected):
        self.selected = bool(selected)
        border = COLORS["accent"] if self.selected else COLORS["line"]
        self.setStyleSheet(f"VideoTile {{ border: {2 if selected else 1}px solid {border}; border-radius: 7px; }}")


@dataclass
class Feed:
    frame: QPixmap | None = None
    received: float | None = None
    quality: str = ""
    available: bool = False
    reason: str = "Awaiting station link"


class VideoWall(QWidget):
    """Main views plus permanent thumbnails for all four away stations.

    ``selected_sources[channel]`` is ``(station, channel)``; local USB digital
    uses ``('local', 'digital')``. Selecting a remote stream only changes its
    matching main channel and never removes the source from the thumbnail grid.
    """

    def __init__(self, profile, local_controls=None, parent=None):
        super().__init__(parent)
        if profile.station != "base":
            raise ValueError("The launch video wall requires a launch-station profile")
        expected = ("digital", "analog", "analog2") if profile.vehicle == "iris" else ("digital", "analog")
        if profile.vehicle not in ("iris", "balius") or tuple(profile.channels) != expected:
            raise ValueError("Unsupported vehicle or video channels")
        self.profile = profile
        self.channels = tuple(profile.channels)
        self.channel_labels = dict(profile.channel_labels)
        if any(not isinstance(self.channel_labels.get(channel), str) for channel in self.channels):
            raise ValueError("Every video channel needs a display label")
        self.away_channels = {station: tuple(channels) for station, channels in profile.away_channels.items()}
        if set(self.away_channels) != set(AWAY_STATIONS) or any(
            not channels or len(set(channels)) != len(channels)
            or any(channel not in self.channels for channel in channels)
            for channels in self.away_channels.values()
        ):
            raise ValueError("Away-station channels must match the vehicle's receiver layout")
        self.selected_sources = {"digital": LOCAL_DIGITAL}
        for channel in self.channels[1:]:
            station = next((station for station in AWAY_STATIONS if channel in self.away_channels[station]), None)
            if station is None:
                raise ValueError(f"No away station supplies {channel!r}")
            self.selected_sources[channel] = station, channel
        self.feeds = {(station, channel): Feed() for station in AWAY_STATIONS
                      for channel in self.away_channels[station]}
        self.feeds[LOCAL_DIGITAL] = Feed(reason="Awaiting local USB camera")
        self.local_source_label = "Local video"
        self.local_source_live = False
        self.primary_tiles, self.primary_views, self.primary_sources, self.primary_status = {}, {}, {}, {}
        self.thumbnails, self.station_groups = {}, {}
        self.local_controls = local_controls
        self.setMinimumSize(0, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        primary_row = QHBoxLayout()
        primary_row.setSpacing(8)
        for channel in self.channels:
            tile = VideoTile("base", channel, self.channel_labels[channel])
            self.primary_tiles[channel] = tile
            self.primary_views[channel] = tile.image
            self.primary_sources[channel] = tile.source_label
            self.primary_status[channel] = tile.status_label
            primary_row.addWidget(tile, 1)
            if channel == "digital":
                self.return_local_button = QPushButton("Local digital")
                self.return_local_button.setToolTip("Show this launch station's digital video source")
                self.return_local_button.clicked.connect(self.select_local_digital)
                tile.header.addWidget(self.return_local_button)
                if local_controls is not None:
                    tile.layout_column.insertWidget(1, local_controls)
        layout.addLayout(primary_row, 3)
        hint = QLabel("AWAY STATIONS  ·  Double-click a thumbnail to replace its matching main view")
        hint.setObjectName("eyebrow")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        remote_row = QHBoxLayout()
        remote_row.setSpacing(8)
        for station in AWAY_STATIONS:
            group = QWidget()
            group.setMinimumSize(0, 0)
            column = QVBoxLayout(group)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(5)
            heading = QLabel(f"Away station {station[-1]}")
            heading.setObjectName("section")
            column.addWidget(heading)
            for channel in self.away_channels[station]:
                tile = VideoTile(station, channel, self.channel_labels[channel], thumbnail=True)
                tile.source_label.setText(f"Away {station[-1]}")
                tile.activated.connect(self.select_remote)
                self.thumbnails[(station, channel)] = tile
                column.addWidget(tile, 1)
            self.station_groups[station] = group
            remote_row.addWidget(group, 1)
        layout.addLayout(remote_row, 2)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh_status)
        self._refresh_selection()

    def _remote_key(self, station, channel):
        if station not in AWAY_STATIONS:
            raise ValueError(f"Unknown away station: {station!r}")
        if channel not in self.away_channels[station]:
            raise ValueError(f"Channel {channel!r} is unavailable at {station} for {self.profile.vehicle}")
        return station, channel

    @staticmethod
    def _frame(frame):
        if not isinstance(frame, QPixmap) or frame.isNull():
            raise ValueError("A video frame must be a nonempty QPixmap")
        return QPixmap(frame)

    def set_local_frame(self, pixmap):
        self.feeds[LOCAL_DIGITAL] = Feed(self._frame(pixmap), time.monotonic(), available=True, reason="")
        self._refresh_feed(LOCAL_DIGITAL)

    def set_local_source(self, label, live=False):
        """Describe local camera/demo/file/replay truthfully; only cameras age.

        Call this when the source or playback state changes, even if no new frame
        arrives (for example when pausing replay). ``live=True`` is reserved for
        an actual connected camera, never a demo or a decoded recording.
        """
        if not isinstance(label, str) or not label.strip() or type(live) is not bool:
            raise ValueError("Local video source needs a label and a boolean live flag")
        self.local_source_label, self.local_source_live = label.strip(), live
        if self.selected_sources["digital"] == LOCAL_DIGITAL:
            self.primary_sources["digital"].setText("Launch station · " + self.local_source_label)
            self.primary_status["digital"].setText(self._status(LOCAL_DIGITAL))

    def reset_local_frame(self, text="Awaiting local USB camera"):
        self.feeds[LOCAL_DIGITAL] = Feed(reason=str(text) or "Awaiting local USB camera")
        self._refresh_feed(LOCAL_DIGITAL)

    def update_remote_frame(self, station, channel, pixmap, quality="", received=None):
        key = self._remote_key(station, channel)
        now = time.monotonic()
        stamp = now if received is None else received
        if (isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                or not math.isfinite(stamp) or stamp > now + 1):
            raise ValueError("Frame reception time must use the local monotonic clock")
        if not isinstance(quality, str):
            raise ValueError("Link quality must be text")
        self.feeds[key] = Feed(self._frame(pixmap), float(stamp), quality.strip(), True, "")
        self._refresh_feed(key)

    def mark_remote_unavailable(self, station, channel, reason="Station link unavailable"):
        key = self._remote_key(station, channel)
        feed = self.feeds[key]
        feed.available = False
        feed.reason = str(reason) or "Station link unavailable"
        self._refresh_feed(key)

    def select_remote(self, station, channel):
        key = self._remote_key(station, channel)
        self.selected_sources[channel] = key
        self._refresh_selection(channel)

    def select_local_digital(self):
        self.selected_sources["digital"] = LOCAL_DIGITAL
        self._refresh_selection("digital")

    def _status(self, key):
        feed = self.feeds[key]
        quality = f"Quality: {feed.quality}" if feed.quality else "Quality unavailable"
        if not feed.available:
            retained = " · Last frame retained" if feed.frame is not None else ""
            return feed.reason + retained + (f" · {quality}" if key != LOCAL_DIGITAL else "")
        if key == LOCAL_DIGITAL and not self.local_source_live:
            return self.local_source_label
        age = max(0.0, time.monotonic() - feed.received)
        if age > STALE_SECONDS:
            state = f"STALE · {age:.1f}s since last frame"
        else:
            state = "LIVE" if key != LOCAL_DIGITAL else self.local_source_label + " · LIVE"
        return state + (f" · {quality}" if key != LOCAL_DIGITAL else "")

    def _display(self, tile, key):
        feed = self.feeds[key]
        if feed.frame is None:
            tile.image.reset_frame(feed.reason)
        else:
            tile.image.set_frame(feed.frame)
        tile.status_label.setText(self._status(key))
        tile.status_label.setToolTip(tile.status_label.text())

    def _refresh_feed(self, key):
        if key in self.thumbnails:
            self._display(self.thumbnails[key], key)
        channel = key[1]
        if self.selected_sources[channel] == key:
            self._display(self.primary_tiles[channel], key)

    def _refresh_selection(self, channel=None):
        for name in (self.channels if channel is None else (channel,)):
            key = self.selected_sources[name]
            tile = self.primary_tiles[name]
            tile.source_label.setText("Launch station · " + self.local_source_label if key == LOCAL_DIGITAL
                                      else f"Away station {key[0][-1]}")
            self._display(tile, key)
        for key, tile in self.thumbnails.items():
            tile.set_selected(self.selected_sources[key[1]] == key)
            tile.status_label.setText(self._status(key))
        self.return_local_button.setEnabled(self.selected_sources["digital"] != LOCAL_DIGITAL)

    def refresh_status(self):
        for key, tile in self.thumbnails.items():
            tile.status_label.setText(self._status(key))
            tile.status_label.setToolTip(tile.status_label.text())
        for channel, key in self.selected_sources.items():
            self.primary_status[channel].setText(self._status(key))
            self.primary_status[channel].setToolTip(self.primary_status[channel].text())

    def showEvent(self, event):
        self.refresh_status()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
