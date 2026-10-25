from rocket_gnc_monitor.ui import MainWindow
from rocket_gnc_monitor.widgets import DARK_COLORS, COLORS
from PySide6.QtWidgets import QScrollArea
from PySide6.QtCore import Qt, QPoint
from rocket_gnc_monitor.widgets import MountView
from rocket_gnc_monitor.widgets import MOUNT_COMPASS, mount_rotations
import numpy as np
import os
import time
import pytest


def test_mount_compass_chirality_and_command_headings():
    north, east, up = np.array(MOUNT_COMPASS["N"]), np.array(MOUNT_COMPASS["E"]), np.array([0, 1, 0])
    assert np.array_equal(np.cross(east, north), up)
    for azimuth, cardinal in [(0, "N"), (90, "E"), (180, "S"), (270, "W"), (360, "N")]:
        for elevation in (0, 35, 80):
            rotation, tilt = mount_rotations(azimuth, elevation)
            boresight = rotation @ tilt @ north
            horizontal = boresight - np.dot(boresight, up) * up
            assert horizontal / np.linalg.norm(horizontal) == pytest.approx(
                MOUNT_COMPASS[cardinal], abs=1e-12
            )
            assert np.dot(boresight, up) == pytest.approx(np.sin(np.radians(elevation)))
            assert np.linalg.det(rotation @ tilt) == pytest.approx(1)  # No reflection in the model.


def test_recorded_demo_controls_and_legacy_readouts(qtbot, tmp_path):
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.resize(1120, 800)
    window.show()
    c = window.controller
    c.timer.stop()

    def refresh():
        window.last_ui = window.last_table = 0
        window.refresh()

    window.mode.setCurrentText("DEMO")
    refresh()
    assert window.demo_bar.isVisible() and not window.serial_bar.isVisible()
    assert window.demo_station.currentText() == "GS2"
    assert "RECORDED TELEMETRY" in window.connection_tiles["rocket"].text()
    qtbot.mouseClick(window.demo_play, Qt.MouseButton.LeftButton)
    assert not c.demo_playing
    qtbot.mouseClick(window.demo_start, Qt.MouseButton.LeftButton)
    assert c.demo_playing and c.demo_time == 0
    assert c.latest.sequence == 9814
    window.demo_speed.setCurrentIndex(4)
    assert c.demo_speed == 4
    window.demo_station.setCurrentText("GS3")
    refresh()
    assert c.latest.details["demo_station"] == "GS3"
    assert not window.track_button.isEnabled()
    panel = window.rocket_panel
    assert panel.power.item(1, 2).text() == "True"
    assert panel.pyros.item(0, 2).text() == "CONNECTED"
    window.pages.setCurrentIndex(1)
    qtbot.wait(30)
    assert window.size().toTuple() == (1120, 800)
    assert window.pages.currentWidget().height() > 500
    assert window.demo_slider.width() > 150
    window.mode.setCurrentText("LIVE")
    refresh()
    assert not window.demo_bar.isVisible() and window.serial_bar.isVisible()
    assert not window.record_button.isEnabled() and not window.point_button.isEnabled()
    window.close()


def test_workspaces_render_at_minimum_size_and_daylight(qtbot, tmp_path):
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.resize(1120, 800)
    window.show()
    qtbot.wait(300)
    for index in range(8):
        window.pages.setCurrentIndex(index)
        qtbot.wait(40)
        assert not window.grab().isNull()
        assert window.pages.currentWidget().height() > 400
    for area in window.pages.widget(2).findChildren(QScrollArea):
        assert area.widget().width() <= area.viewport().width()
    window.pages.setCurrentIndex(4)
    window.task_done(
        "simulation",
        ValueError(
            "OpenRocket: Missing motor thrust curve: MITRT N8406. In Mission & wind, use Select custom motor files "
            "to add the matching .eng or .rse file."
        ),
    )
    qtbot.wait(80)
    area = window.simulation_scroll
    position = window.sim_status.mapTo(area.viewport(), QPoint(0, 0))
    assert position.y() >= 0
    assert position.y() + window.sim_status.height() <= area.viewport().height()
    assert area.widget().width() <= area.viewport().width()
    window.controller.switch_mode("REPLAY")
    window.controller.pointer_sent = (123, 45)
    window.last_ui = 0
    window.refresh()
    assert (window.mount.azimuth, window.mount.elevation) == (123, 45)
    assert not window.azimuth.isEnabled()
    window.set_daylight(True)
    assert COLORS["panel"] == "#ffffff"
    window.set_daylight(False)
    assert COLORS == DARK_COLORS
    window.close()


def test_orbit_changes_camera_without_changing_antenna_pose(qtbot):
    mount = MountView()
    qtbot.addWidget(mount)
    mount.resize(800, 600)
    mount.show()
    initial_pose = (mount.azimuth, mount.elevation)
    initial_view = (mount.camera_yaw, mount.camera_pitch)
    qtbot.mousePress(mount, Qt.MouseButton.LeftButton, pos=QPoint(220, 200))
    qtbot.mouseMove(mount, QPoint(420, 340))
    qtbot.mouseRelease(mount, Qt.MouseButton.LeftButton, pos=QPoint(420, 340))
    assert (mount.camera_yaw, mount.camera_pitch) != initial_view
    assert (mount.azimuth, mount.elevation) == initial_pose
    assert mount.drag_position is None
    for pitch in (0, 90, 180, 270, 359):
        mount.camera_pitch = pitch
        assert not mount.grab().isNull()
    qtbot.mouseDClick(mount, Qt.MouseButton.LeftButton, pos=QPoint(200, 200))
    assert (mount.camera_yaw, mount.camera_pitch) == initial_view
    assert (mount.azimuth, mount.elevation) == initial_pose


@pytest.mark.skipif(os.name == "nt", reason="POSIX pseudo-terminal connection test")
def test_live_panel_tracks_valid_packets_and_relocks_on_disconnect(qtbot, tmp_path):
    import pty
    from test_protocol import frame

    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    c = window.controller
    pairs = [pty.openpty(), pty.openpty()]
    window.show()

    def refresh():
        window.last_ui = 0
        window.refresh()

    try:
        assert c.mode == "LIVE" and window.pages.currentIndex() == 0
        assert not window.record_button.isEnabled() and not window.point_button.isEnabled()
        assert all(not control.isEnabled() for control in window.live_controls)
        assert "disconnected" in window.connection_headline.text()
        assert c.video is None
        c.connect("telemetry", os.ttyname(pairs[0][1]))
        qtbot.waitUntil(lambda: c.ground_connected)
        refresh()
        assert window.record_button.isEnabled() and not window.point_button.isEnabled()
        assert c.rocket_link_state() == ("PAUSED", None)
        c.set_polling(True)
        refresh()
        assert window.connection_headline.text() == "Waiting for the rocket"
        bad = bytearray(frame())
        bad[12] ^= 1
        os.write(pairs[0][0], bad)
        qtbot.waitUntil(lambda: c.stats["rejected"] > 0)
        assert c.rocket_link_state()[0] == "WAITING"
        os.write(pairs[0][0], frame(2))
        qtbot.waitUntil(lambda: c.rocket_link_state()[0] == "RECEIVING")
        refresh()
        assert window.connection_headline.text() == "Rocket telemetry live"
        c.connect("pointer", os.ttyname(pairs[1][1]))
        qtbot.waitUntil(lambda: c.states["pointer"] == "Connected")
        refresh()
        assert window.point_button.isEnabled() and window.zero_button.isEnabled()
        c.last_live_received = c.latest.received = time.monotonic() - 10
        c.tracking = True
        c.tick()
        refresh()
        assert c.rocket_link_state()[0] == "STALE" and not c.tracking
        assert "lost / stale" in window.connection_headline.text()
        c.disconnect("telemetry")
        refresh()
        assert not window.record_button.isEnabled() and window.point_button.isEnabled()
        assert window.zero_button.isEnabled() and not window.track_button.isEnabled()
        c.point(90, 30)
        qtbot.waitUntil(lambda: c.pointer_pending is None)
        c.connect("telemetry", os.ttyname(pairs[0][1]))
        qtbot.waitUntil(lambda: c.ground_connected)
        assert c.rocket_link_state() == ("PAUSED", None)
        c.set_polling(True)
        assert c.rocket_link_state() == ("WAITING", None)
        refresh()
        assert window.connection_headline.text() == "Waiting for the rocket"
    finally:
        window.close()
        for pair in pairs:
            for fd in pair:
                os.close(fd)
