import math
import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from rocket_gnc_monitor.domain import Mission
from rocket_gnc_monitor.flight import save_flight, load_flight
from rocket_gnc_monitor.flight_scene import FlightScene, Playback, slerp, quaternion_matrix
from rocket_gnc_monitor.trajectory import Trajectory
from rocket_gnc_monitor.ui import MainWindow
from rocket_gnc_monitor.widgets import MountView
from rocket_gnc_monitor.mount_geometry import mount_mesh
from rocket_gnc_monitor.virtual_pointer import VIRTUAL_POINTER_DEVICE


def reference():
    events = [
        dict(time=t, type=kind, source="Motor" if kind in {"IGNITION", "BURNOUT"} else "Main")
        for t, kind in [
            (0, "IGNITION"),
            (2, "BURNOUT"),
            (3, "APOGEE"),
            (4, "RECOVERY_DEVICE_DEPLOYMENT"),
            (6, "GROUND_HIT"),
        ]
    ]
    return Trajectory(
        [[0, 0, 0, 0], [2, 0, 0, 100], [4, 10, 20, 80], [6, 20, 30, 0]],
        dict(
            schema_version=1,
            frame="ENU",
            units="m,s",
            origin=[42, -77, 300],
            altitude_datum="launch_relative",
            visuals_schema_version=1,
            attitude_frame="body_to_ENU",
            body_axis="+Z",
            flight_events=events,
            name="Test flight",
        ),
        [
            [1, 0, 0, 0, 0, 0, 50, 0, 0, 0],
            [-1, 0, 0, 0, 0, 0, 25, 0, 0, 0],
            [1, 0, 0, 0, 5, 5, -20, 0, 0, 0],
            [1, 0, 0, 0, 0, 0, -20, 0, 0, 0],
        ],
    )


def test_slerp_chirality_normalization_and_missing_attitude():
    q = slerp([1, 0, 0, 0], [0, 0, 1, 0], 0.5)
    assert quaternion_matrix(q) @ np.array([0, 0, 1]) == pytest.approx([1, 0, 0])
    assert slerp([1, 0, 0, 0], [-1, 0, 0, 0], 0.5) == pytest.approx([1, 0, 0, 0])
    assert slerp([0, 0, 0, 0], [1, 0, 0, 0], 0.5) is None
    assert slerp([math.nan, 0, 0, 0], [1, 0, 0, 0], 0.5) is None


def test_event_boundaries_motor_sources_recovery_and_old_references():
    r = reference()
    s = FlightScene(r)
    assert s.frame(1).powered and not s.frame(2).powered
    assert not s.frame(3.99).recovery and s.frame(4).recovery
    assert s.frame(4.7).inflation == pytest.approx(1)
    assert "held" in s.frame(4.7).attitude
    assert s.frame(100).time == 6 and s.frame(100).state == "LANDED"
    r.manifest["flight_events"] += [
        dict(time=1, type="IGNITION", source="Second"),
        dict(time=3, type="BURNOUT", source="Second"),
    ]
    assert FlightScene(r).frame(2.5).powered
    old = Trajectory(r.points, dict(schema_version=1, frame="ENU", units="m,s", origin=[42, -77, 300]))
    frame = FlightScene(old).frame(5)
    assert not frame.powered and not frame.recovery and "unavailable" in frame.attitude
    assert frame.state == "FLIGHT EVENTS UNAVAILABLE"
    r.motion[:, 7] = 4
    assert "undersampled" in FlightScene(r).frame(1).attitude
    assert "undersampled" not in FlightScene(r).frame(2).attitude
    with pytest.raises(ValueError):
        s.frame(math.nan)


def test_motion_csv_and_portable_flight_roundtrip(tmp_path):
    r = reference()
    r.motion[1, 8] = math.nan
    r.save(tmp_path / "flight.csv")
    loaded = Trajectory.load(tmp_path / "flight.csv")
    np.testing.assert_allclose(loaded.motion, r.motion, equal_nan=True)
    assert loaded.manifest["flight_events"] == r.manifest["flight_events"]
    save_flight(tmp_path / "flight.rktflight", Mission(), r)
    archive = load_flight(tmp_path / "flight.rktflight", tmp_path / "cache")
    np.testing.assert_allclose(archive.reference.motion, r.motion, equal_nan=True)
    assert FlightScene(archive.reference).frame(5).recovery
    text = (tmp_path / "flight.csv").read_text().replace(",qx,", ",missing,")
    (tmp_path / "flight.csv").write_text(text)
    with pytest.raises(ValueError, match="complete"):
        Trajectory.load(tmp_path / "flight.csv")
    manifest = dict(r.manifest)
    manifest["flight_events"] = [{"time": 1}]
    with pytest.raises(ValueError, match="events"):
        Trajectory(r.points, manifest)


def test_playback_uses_elapsed_clock_and_preserves_seek_speed():
    now = [10.0]
    p = Playback(0, 10, lambda: now[0])
    p.play(True)
    now[0] += 1
    assert p.time() == 1
    p.set_speed(2)
    now[0] += 2
    assert p.time() == 5
    p.play(False)
    now[0] += 5
    assert p.time() == 5
    p.seek(9)
    p.play(True)
    now[0] += 1
    assert p.time() == 10 and not p.playing
    p.seek(-1)
    assert p.time() == 0
    with pytest.raises(ValueError):
        p.set_speed(0)


def test_mount_animation_short_route_cache_and_visibility(qtbot, monkeypatch):
    mount = MountView()
    qtbot.addWidget(mount)
    mount.show()
    assert mount.mesh is mount_mesh() and mount.animation.isActive()
    mount._display_pose = (359, 0)
    mount.set_pose(1, 30)
    now = [mount._last_frame + 0.016]
    monkeypatch.setattr("rocket_gnc_monitor.widgets.time.monotonic", lambda: now[0])
    mount.animate()
    az, el = mount._display_pose
    assert az > 359 or az < 1
    assert 0 < el < 30 and (mount.azimuth, mount.elevation) == (1, 30)
    now[0] += 1
    mount.animate()
    assert mount._display_pose == pytest.approx((1, 30), abs=1e-6)
    mount.hide()
    assert not mount.animation.isActive()


def test_viewer_playback_events_linking_and_no_commands(qtbot, tmp_path, monkeypatch):
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    c = window.controller
    c.timer.stop()
    c.reference = reference()
    c.mission = Mission(
        latitude=42,
        longitude=-77,
        altitude=300,
        site_configured=True,
        pointer_site_configured=True,
        pointer_latitude=41.999,
        pointer_longitude=-77.001,
        pointer_altitude=300,
    )
    window.open_flight_3d()
    dialog = window.flight_3d_dialog
    dialog.timer.stop()
    try:
        assert dialog.scene is not None

        def forbidden(*args, **kwargs):
            raise AssertionError("Viewer sent pointer command")

        monkeypatch.setattr(c, "dispatch_pointer", forbidden)
        dialog.playback.seek(1)
        dialog.tick()
        assert dialog.view.frame.powered
        dialog.playback.seek(5)
        dialog.tick()
        assert dialog.view.frame.recovery
        assert not dialog.view.grab().isNull()
        pose = dialog.view.frame.rotation.copy()
        yaw = dialog.view.yaw
        qtbot.mousePress(dialog.view, Qt.MouseButton.LeftButton, pos=QPoint(200, 200))
        qtbot.mouseMove(dialog.view, QPoint(250, 220))
        qtbot.mouseRelease(dialog.view, Qt.MouseButton.LeftButton)
        assert dialog.view.yaw != yaw
        np.testing.assert_array_equal(dialog.view.frame.rotation, pose)
        c.connect("pointer", VIRTUAL_POINTER_DEVICE)
        c.tick()
        c.prepare_virtual_flight()
        c.virtual_flight.seek(3)
        dialog.link.setChecked(True)
        dialog.tick()
        assert dialog.view.frame.time == 3 and not dialog.play_button.isEnabled()
        assert c.latest is None and not c.ground_connected
        c.reference = None
        dialog.tick()
        assert dialog.view.scene is None and not dialog.slider.isEnabled()
        dialog.close()
        assert not dialog.timer.isActive()
    finally:
        window.close()
