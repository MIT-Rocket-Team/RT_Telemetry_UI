"""A launch video wall preserves every away thumbnail and promotes matching feeds."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QPushButton, QWidget

from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.video_wall import AWAY_STATIONS, LOCAL_DIGITAL, VideoWall


def frame(color, width=160, height=90):
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(color))
    return pixmap


def make_wall(qtbot, vehicle="iris", controls=None):
    wall = VideoWall(StationProfile(station="base", role="video", vehicle=vehicle), controls)
    qtbot.addWidget(wall)
    wall.resize(1600, 960)
    wall.show()
    return wall


@pytest.mark.parametrize("vehicle,channels", [
    ("iris", ("digital", "analog", "analog2")), ("balius", ("digital", "analog")),
])
def test_initial_wall_has_all_remote_slots_and_no_fabricated_frames(qtbot, vehicle, channels):
    wall = make_wall(qtbot, vehicle)
    assert tuple(wall.primary_views) == channels
    assert set(wall.station_groups) == set(AWAY_STATIONS)
    assert set(wall.thumbnails) == {
        (station, channel) for station, channels in wall.profile.away_channels.items() for channel in channels
    }
    assert len(wall.thumbnails) == 8
    assert wall.selected_sources["digital"] == LOCAL_DIGITAL
    for channel in channels[1:]:
        assert wall.selected_sources[channel] == ("away4" if channel == "analog2" else "away1", channel)
    for (station, channel), tile in wall.thumbnails.items():
        assert tile.isVisible() and tile.image.isVisible()
        assert tile.image.pixmap().isNull()
        assert tile.title.text() == wall.profile.channel_labels[channel]
        assert station[-1] in tile.source_label.text()
        assert "Awaiting station link" in tile.status_label.text()
        assert "Quality unavailable" in tile.status_label.text()
        assert "LIVE" not in tile.status_label.text()
    assert all(image.pixmap().isNull() for image in wall.primary_views.values())
    assert not wall.return_local_button.isEnabled()
    assert wall.timer.isActive()
    wall.hide()
    assert not wall.timer.isActive()


def test_double_click_promotes_only_matching_channel_and_preserves_thumbnail(qtbot):
    wall = make_wall(qtbot)
    wall.set_local_frame(frame("red"))
    wall.update_remote_frame("away1", "analog", frame("green"), quality="Strong")
    wall.update_remote_frame("away4", "analog2", frame("blue"), quality="Strong")
    before = dict(wall.selected_sources)
    unrelated = {channel: wall.primary_views[channel].pixmap().cacheKey() for channel in ("digital", "analog2")}
    wall.update_remote_frame("away3", "analog", frame("yellow"), quality="Weak · -98 dBm")
    tile = wall.thumbnails[("away3", "analog")]
    with qtbot.waitSignal(tile.activated):
        qtbot.mouseDClick(tile.image, Qt.MouseButton.LeftButton)
    assert wall.selected_sources == dict(before, analog=("away3", "analog"))
    assert wall.primary_views["analog"].pixmap().cacheKey() == tile.image.pixmap().cacheKey()
    assert "Away station 3" == wall.primary_sources["analog"].text()
    assert "Weak · -98 dBm" in wall.primary_status["analog"].text()
    assert len(wall.thumbnails) == 8 and all(t.isVisible() for t in wall.thumbnails.values())
    assert tile.selected and tile.isVisible()
    assert not wall.thumbnails[("away1", "analog")].selected
    assert {channel: wall.primary_views[channel].pixmap().cacheKey() for channel in unrelated} == unrelated


def test_updates_reach_main_only_for_selected_source_and_local_usb_can_return(qtbot):
    controls = QWidget()
    control = QPushButton("Camera settings", controls)
    wall = make_wall(qtbot, controls=controls)
    wall.set_local_frame(frame("red"))
    local_key = wall.primary_views["digital"].pixmap().cacheKey()
    wall.update_remote_frame("away2", "digital", frame("blue"))
    assert wall.primary_views["digital"].pixmap().cacheKey() == local_key
    wall.select_remote("away2", "digital")
    remote_key = wall.primary_views["digital"].pixmap().cacheKey()
    assert remote_key == wall.thumbnails[("away2", "digital")].image.pixmap().cacheKey()
    wall.set_local_frame(frame("yellow"))
    wall.update_remote_frame("away3", "digital", frame("green"))
    assert wall.primary_views["digital"].pixmap().cacheKey() == remote_key
    wall.update_remote_frame("away2", "digital", frame("cyan"), quality="72%")
    assert wall.primary_views["digital"].pixmap().toImage().pixelColor(0, 0) == QColor("cyan")
    assert "72%" in wall.primary_status["digital"].text()
    assert controls.isVisible() and control.isVisible()
    qtbot.mouseClick(wall.return_local_button, Qt.MouseButton.LeftButton)
    assert wall.selected_sources["digital"] == LOCAL_DIGITAL
    assert wall.primary_views["digital"].pixmap().toImage().pixelColor(0, 0) == QColor("yellow")
    assert not wall.return_local_button.isEnabled()
    wall.reset_local_frame("Digital camera disconnected")
    assert wall.primary_views["digital"].pixmap().isNull()
    assert "Digital camera disconnected" == wall.primary_status["digital"].text()
    assert "LIVE" not in wall.primary_status["digital"].text()


def test_remote_staleness_and_disconnection_never_label_retained_frames_live(qtbot, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("rocket_gnc_monitor.video_wall.time", SimpleNamespace(monotonic=lambda: clock[0]))
    wall = make_wall(qtbot)
    wall.update_remote_frame("away1", "analog", frame("green"), quality="-70 dBm", received=99.0)
    key = wall.primary_views["analog"].pixmap().cacheKey()
    assert "LIVE" in wall.primary_status["analog"].text()
    clock[0] = 105.0
    wall.refresh_status()
    for label in (wall.primary_status["analog"], wall.thumbnails[("away1", "analog")].status_label):
        assert "STALE" in label.text() and "LIVE" not in label.text()
        assert "-70 dBm" in label.text()
    assert wall.primary_views["analog"].pixmap().cacheKey() == key
    wall.mark_remote_unavailable("away1", "analog", "Station disconnected")
    assert "Station disconnected" in wall.primary_status["analog"].text()
    assert "Last frame retained" in wall.primary_status["analog"].text()
    assert "LIVE" not in wall.primary_status["analog"].text()
    wall.update_remote_frame("away1", "analog", frame("red"))
    assert "LIVE" in wall.primary_status["analog"].text()
    assert "Quality unavailable" in wall.primary_status["analog"].text()


@pytest.mark.parametrize("station,channel", [
    ("base", "digital"), ("away0", "digital"), ("away5", "analog"),
    ("away1", "analog2"), ("away1", "unknown"),
])
def test_invalid_station_or_vehicle_channel_is_rejected(qtbot, station, channel):
    wall = make_wall(qtbot, "balius")
    selected = dict(wall.selected_sources)
    for operation in (
        lambda: wall.select_remote(station, channel),
        lambda: wall.update_remote_frame(station, channel, frame("blue")),
        lambda: wall.mark_remote_unavailable(station, channel),
    ):
        with pytest.raises(ValueError):
            operation()
    assert wall.selected_sources == selected
    assert len(wall.thumbnails) == 8


def test_invalid_frames_and_timestamps_do_not_create_live_feeds(qtbot):
    wall = make_wall(qtbot)
    for value in (QPixmap(), None):
        with pytest.raises(ValueError, match="QPixmap"):
            wall.update_remote_frame("away1", "analog", value)
    for received in (float("nan"), float("inf"), True, "yesterday", 1e20):
        with pytest.raises(ValueError, match="monotonic"):
            wall.update_remote_frame("away1", "analog", frame("blue"), received=received)
    assert wall.primary_views["analog"].pixmap().isNull()
    assert "LIVE" not in wall.primary_status["analog"].text()


@pytest.mark.parametrize("station,channel", [("away1", "analog2"), ("away2", "analog2"),
                                             ("away3", "analog2"), ("away4", "analog")])
def test_iris_rejects_receivers_not_present_at_the_selected_away_station(qtbot, station, channel):
    wall = make_wall(qtbot)
    selected = dict(wall.selected_sources)
    for operation in (
        lambda: wall.select_remote(station, channel),
        lambda: wall.update_remote_frame(station, channel, frame("blue")),
        lambda: wall.mark_remote_unavailable(station, channel),
    ):
        with pytest.raises(ValueError, match="unavailable"):
            operation()
    assert wall.selected_sources == selected
    wall.mark_remote_unavailable("away4", "analog2", "Away station 4 disconnected")
    assert ("away4", "analog2") in wall.thumbnails
    assert wall.thumbnails[("away4", "analog2")].isVisible()
    assert "disconnected" in wall.primary_status["analog2"].text()


def test_local_source_context_distinguishes_live_camera_demo_and_paused_replay(qtbot, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("rocket_gnc_monitor.video_wall.time", SimpleNamespace(monotonic=lambda: clock[0]))
    wall = make_wall(qtbot)
    wall.set_local_frame(frame("blue"))
    assert "LIVE" not in wall.primary_status["digital"].text()
    for label in ("Demo · test pattern", "Local file", "Replay · paused"):
        wall.set_local_source(label, live=False)
        clock[0] += 10
        wall.refresh_status()
        assert wall.primary_status["digital"].text() == label
        assert "LIVE" not in wall.primary_status["digital"].text()
        assert "STALE" not in wall.primary_status["digital"].text()
        assert wall.primary_sources["digital"].text() == "Launch station · " + label
    wall.set_local_source("Local USB", live=True)
    wall.set_local_frame(frame("red"))
    assert "LIVE" in wall.primary_status["digital"].text()
    clock[0] += 6
    wall.refresh_status()
    assert "STALE" in wall.primary_status["digital"].text()
    assert "LIVE" not in wall.primary_status["digital"].text()
    wall.select_remote("away2", "digital")
    wall.set_local_source("Replay · paused", live=False)
    assert wall.primary_sources["digital"].text() == "Away station 2"
    wall.select_local_digital()
    assert wall.primary_status["digital"].text() == "Replay · paused"


def test_video_keeps_aspect_ratio_when_primary_and_thumbnails_resize(qtbot):
    wall = make_wall(qtbot)
    wall.update_remote_frame("away1", "analog", frame("blue", 160, 90))
    for image in (wall.primary_views["analog"], wall.thumbnails[("away1", "analog")].image):
        for width, height in ((500, 200), (100, 200), (320, 180)):
            image.resize(width, height)
            target = image.frame_rect()
            assert target.width() / target.height() == pytest.approx(16 / 9, abs=0.04)
            assert 0 <= target.left() <= image.width()
            assert 0 <= target.top() <= image.height()
            assert target.right() <= image.width() and target.bottom() <= image.height()
