"""The replacement rocket instrument keeps telemetry and reference playback distinct."""

import gc

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from rocket_gnc_monitor.protocol import ZephyrusDecoder
from rocket_gnc_monitor.rocket_pose_panel import RocketPosePanel
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow
from rocket_gnc_monitor.ui import MainWindow
from rocket_gnc_monitor.wifi import WifiSnapshot
from test_flight_3d import reference
from test_protocol import frame


@pytest.fixture(params=["launch", "away", "legacy"])
def pose_window(request, qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.wifi_panel.read_wifi_status", WifiSnapshot)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    if request.param == "legacy":
        w = MainWindow(tmp_path)
    else:
        station = "launch" if request.param == "launch" else "away3"
        w = StationWindow(tmp_path, profile=StationProfile(station, "telemetry", "iris", "urrg"), auto_place=False)
    qtbot.addWidget(w)
    w.show()
    if request.param == "legacy":
        w.pages.setCurrentIndex(1)
    w.resize(1920, 1020)
    w.controller.timer.stop()
    yield w
    w.close()


def test_all_telemetry_workspaces_replace_horizon_with_slider_controlled_rocket(pose_window, qtbot):
    w, c = pose_window, pose_window.controller
    panel = w.attitude
    assert isinstance(panel, RocketPosePanel) and panel.isVisible()
    assert panel.source.currentData() == "telemetry"
    assert not hasattr(w, "attitude_label")
    c.mode = "REPLAY"
    sample = ZephyrusDecoder().feed(frame())[0]
    sample.attitude = [25., 15., 40.]
    c.latest = sample
    panel.refresh()
    pose = panel.view.pose
    assert pose.angles == (25., 15., 40.)
    assert pose.angle_kind == "gyro_integrals" and not pose.attitude_known
    np.testing.assert_array_equal(pose.rotation, np.eye(3))
    assert "ORIENTATION UNAVAILABLE" in panel.view.orientation_text
    assert pose.motor is None and pose.parachute is None
    assert "REPLAY" in pose.source
    for azimuth in (0, 90, 180, 270, 360):
        panel.azimuth.setValue(azimuth)
        assert panel.azimuth_value.text() == f"{azimuth}°"
        assert panel.view.azimuth == azimuth % 360
        assert np.array_equal(panel.view.pose.rotation, pose.rotation)
        assert c.latest is sample and not c.dispatched_commands and not c.rocket_commands
    assert panel.timer.isActive()
    panel.hide()
    qtbot.wait(20)
    assert not panel.timer.isActive() and not panel.view.animation.isActive()


def test_reference_events_follow_existing_player_without_becoming_telemetry(pose_window):
    w, c = pose_window, pose_window.controller
    panel = w.attitude
    c.reference = reference()
    c.latest = ZephyrusDecoder().feed(frame())[0]
    c.mode = "REPLAY"
    panel.source.setCurrentIndex(panel.source.findData("openrocket"))
    dialog = (w.away_flight_dialog if getattr(w, "profile_locked", False)
              and w.profile.layout == "away" else w.flight_3d_dialog)
    assert dialog is not None
    dialog.timer.stop()
    for stamp, burning, deployed in ((1, True, False), (3, False, False), (5, False, True)):
        dialog.playback.seek(stamp)
        dialog.tick()
        panel.refresh()
        pose = panel.view.pose
        assert pose.source == "SIMULATION"
        assert pose.motor is burning and pose.parachute is deployed
        assert pose.time == stamp
        assert np.array_equal(pose.rotation, dialog.view.frame.rotation)
        assert panel.timeline.text() == f"{stamp:.2f} s · Paused"
    panel.source.setCurrentIndex(panel.source.findData("telemetry"))
    assert panel.view.pose.motor is None and panel.view.pose.parachute is None
    assert panel.view.pose.source == "REPLAY"
    panel.source.setCurrentIndex(panel.source.findData("openrocket"))
    c.reference = None
    panel.refresh()
    assert panel.source.currentData() == "openrocket"
    assert panel.view.pose.motor is None and panel.view.pose.parachute is None
    assert panel.view.pose.source == "SIMULATION"
    assert panel.timeline.text() == "No reference"
    assert "Load a trajectory" in panel.view.pose.status
    assert not c.dispatched_commands and not c.rocket_commands
