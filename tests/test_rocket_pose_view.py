from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, QSizeF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.rocket_pose import RocketPose, rotation_from_rpy, simulation_pose, telemetry_pose
from rocket_gnc_monitor.rocket_pose_view import RocketPoseView
from rocket_gnc_monitor.rocket_geometry import rocket_mesh, plume_mesh, canopy_mesh


def make_view(qtbot, pose=None, size=(550, 180)):
    view = RocketPoseView()
    qtbot.addWidget(view)
    view.resize(*size)
    view.set_pose(pose)
    view.show()
    return view


def pose(angles=(0, 0, 0), **kwargs):
    return RocketPose(rotation=rotation_from_rpy(*angles), angles=angles, attitude_known=True,
                      source="LIVE", status="LIVE · Illustrative R/P/Y · axes uncalibrated", **kwargs)


def test_reported_gyro_totals_never_drive_a_false_orientation(qtbot):
    item = Sample(t=1, sequence=1, source="LIVE", phase="Flight", attitude=[45, 20, -10])
    view = make_view(qtbot, telemetry_pose(item))
    assert view.angle_text == ("∫R  +45.0°", "∫P  +20.0°", "∫Y  -10.0°")
    assert view.effect_text == ("MOTOR UNKNOWN", "CHUTE UNKNOWN")
    np.testing.assert_allclose(view.display_rotation, np.eye(3), atol=1e-6)
    assert view._angle_source() == "Body gyro totals · not attitude"
    assert view.orientation_text == "ORIENTATION UNAVAILABLE · neutral model"
    assert not view.pose.attitude_known


@pytest.mark.parametrize("burning,caption", [(None, "IGNITED · BURN ?"), (False, "IGNITED · OFF"), (True, "MOTOR ON")])
def test_ignition_history_is_visible_without_inventing_current_burning(qtbot, monkeypatch, burning, caption):
    flames = []
    monkeypatch.setattr("rocket_gnc_monitor.rocket_pose_view.plume_mesh",
                        lambda: SimpleNamespace(draw=lambda *args: flames.append(True)))
    view = make_view(qtbot, pose(motor=burning, ignited=True))
    assert view.effect_text[0] == caption
    flames.clear()
    view.grab()
    assert bool(flames) == (burning is True)


@pytest.mark.parametrize("angles", [(35, 0, 0), (0, 35, 0), (0, 0, 35)])
def test_all_three_angles_change_visible_rocket_and_roll_stripes(qtbot, angles):
    view = make_view(qtbot, pose(), size=(550, 450))
    view.animation.stop()
    original = view.grab().toImage()
    view.hide()
    view.set_pose(pose(angles))
    view.show()
    view.animation.stop()
    changed = view.grab().toImage()
    # Compare just the model area, excluding raw-angle text labels.
    viewport = view._layout()[0].toRect()
    assert original.copy(viewport) != changed.copy(viewport)
    np.testing.assert_allclose(view.display_rotation, rotation_from_rpy(*angles), atol=1e-6)


def test_partial_unknown_telemetry_shows_neutral_model_and_retains_raw_fields(qtbot):
    item = Sample(t=1, sequence=1, source="LIVE", attitude=[45, None, 30])
    view = make_view(qtbot, telemetry_pose(item))
    np.testing.assert_allclose(view.display_rotation, np.eye(3))
    assert view.angle_text == ("∫R  +45.0°", "∫P  —", "∫Y  +30.0°")
    assert "Orientation unavailable" in view.pose.status and not view.pose.attitude_known
    view.set_pose(None)
    assert view.angle_text == ("R  —", "P  —", "Y  —")
    assert view._source_text() == "NO TELEMETRY · Neutral illustration"


def test_explicit_path_aligned_simulation_preserves_matrix_without_claimed_angles(qtbot):
    rotation = rotation_from_rpy(12, 50, 23)
    frame = SimpleNamespace(rotation=rotation, attitude="Path-aligned illustration · attitude unavailable",
                            powered=False, recovery=False, inflation=0, time=5, state="COAST / PAD")
    view = make_view(qtbot, simulation_pose(frame))
    assert not view.pose.attitude_known
    np.testing.assert_allclose(view.display_rotation, rotation, atol=1e-6)
    assert view.angle_text == ("R  —", "P  —", "Y  —")
    assert view._source_text() == "SIMULATION"


def test_larger_framing_fits_visible_effects_and_stays_fixed_during_rotation(qtbot):
    view = make_view(qtbot, pose(), size=(550, 320))
    viewport, labels = view._layout()
    assert labels.top() > viewport.bottom()
    assert viewport.width() > 0.9 * view.width()
    project, _ = view.projection()
    body_height = np.ptp(project([[0, 0, -.43], [0, 0, .55]])[:, 1])
    assert body_height > 0.75 * viewport.height()
    probes = np.array([[0, 0, 0], [1, 1, 1]])
    for burning, recovery in ((False, False), (True, False), (False, True), (True, True)):
        view.set_pose(pose(motor=burning, parachute=recovery, inflation=1))
        for azimuth in range(0, 360, 45):
            view.set_azimuth(azimuth)
            project, _ = view.projection()
            framing = project(probes)
            for angles in ((0, 0, 0), (20, 75, 40), (90, -90, 180), (0, 180, 0)):
                current = pose(angles, motor=burning, parachute=recovery, inflation=1)
                view.set_pose(current)
                np.testing.assert_array_equal(view.projection()[0](probes), framing)
                vertices = [rocket_mesh().vertices @ current.rotation.T]
                if burning:
                    vertices.append(plume_mesh().vertices @ current.rotation.T)
                if recovery:
                    vertices.append(canopy_mesh().vertices + [0, 0, 1.1])
                xy = project(np.vstack(vertices))
                assert np.all(xy[:, 0] >= viewport.left()) and np.all(xy[:, 0] <= viewport.right())
                assert np.all(xy[:, 1] >= viewport.top()) and np.all(xy[:, 1] <= viewport.bottom())


@pytest.mark.parametrize("motor,chute,expected", [(None, None, []), (False, False, []),
                                                  (True, False, ["motor"]),
                                                  (False, True, ["chute"]),
                                                  (True, True, ["motor", "chute"])])
def test_only_confirmed_active_effects_are_drawn(qtbot, monkeypatch, motor, chute, expected):
    effects = []
    monkeypatch.setattr("rocket_gnc_monitor.rocket_pose_view.plume_mesh",
                        lambda: SimpleNamespace(draw=lambda *args: effects.append("motor")))
    monkeypatch.setattr("rocket_gnc_monitor.rocket_pose_view.canopy_mesh",
                        lambda: SimpleNamespace(draw=lambda *args: effects.append("chute")))
    view = make_view(qtbot, pose(motor=motor, parachute=chute, inflation=1))
    effects.clear()
    assert not view.grab().isNull()
    assert effects == expected


def test_animation_uses_independent_timer_and_freezes_stale_effects(qtbot, monkeypatch):
    view = make_view(qtbot, pose(motor=True))
    assert view.animation.isActive() and view.animation.interval() == 16
    now = [view._last_frame + 0.016]
    monkeypatch.setattr("rocket_gnc_monitor.rocket_pose_view.time.monotonic", lambda: now[0])
    view.set_pose(pose((90, 30, 0), motor=True))
    view.animate()
    rotation = view.display_rotation.copy()
    assert not np.allclose(rotation, np.eye(3))
    assert not np.allclose(rotation, view.pose.rotation)
    assert view._effect_time > 0
    now[0] += 1
    view.animate()
    np.testing.assert_allclose(view.display_rotation, view.pose.rotation, atol=1e-5)
    view.set_pose(replace(view.pose, stale=True))
    effect_time = view._effect_time
    now[0] += 1
    view.animate()
    assert view._effect_time == effect_time
    assert view._source_text() == "LIVE · STALE"
    view.hide()
    assert not view.animation.isActive()


def test_azimuth_slider_keeps_world_vertical_and_does_not_change_pose(qtbot):
    view = make_view(qtbot, pose((12, 23, 34)))
    target = view.pose.rotation.copy()
    for angle in (0, 45, 90, 180, 270, 360):
        view.set_azimuth(angle)
        points = view.projection()[0]([[0, 0, 0], [0, 0, 1]])
        assert points[0, 0] == pytest.approx(points[1, 0])
        assert points[1, 1] < points[0, 1]
        np.testing.assert_array_equal(view.pose.rotation, target)
    assert view.azimuth == 0
    with pytest.raises(ValueError):
        view.set_azimuth(float("nan"))


def test_no_drag_double_click_or_wheel_camera_controls(qtbot):
    view = make_view(qtbot, pose())
    view.set_azimuth(123)
    qtbot.mousePress(view, Qt.MouseButton.LeftButton, pos=QPoint(60, 60))
    qtbot.mouseMove(view, QPoint(160, 100))
    qtbot.mouseRelease(view, Qt.MouseButton.LeftButton)
    qtbot.mouseDClick(view, Qt.MouseButton.LeftButton, pos=QPoint(70, 70))
    wheel = QWheelEvent(QPointF(60, 60), QPointF(view.mapToGlobal(QPoint(60, 60))), QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(view, wheel)
    assert view.azimuth == 123 and view.elevation == 14
    assert view.cursor().shape() == Qt.CursorShape.ArrowCursor


@pytest.mark.parametrize("size", [(550, 150), (550, 180), (550, 450)])
def test_compact_and_tall_rendering(qtbot, size):
    view = make_view(qtbot, pose((35, 20, -15), motor=True, parachute=True, inflation=1), size=size)
    image = view.grab()
    assert (view.width(), view.height()) == size
    assert image.devicePixelRatio() == view.devicePixelRatioF()
    # Grabs contain physical pixels; Qt rounds fractional-DPI dimensions.
    assert image.size() == (QSizeF(*size) * image.devicePixelRatio()).toSize()
    assert not image.isNull()
