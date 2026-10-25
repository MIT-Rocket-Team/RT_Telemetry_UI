from dataclasses import replace
import math
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt
from rocket_gnc_monitor.controller import Controller
from rocket_gnc_monitor.domain import Mission, pointing
from rocket_gnc_monitor.flight import save_flight, load_flight
from rocket_gnc_monitor.protocol import pointer_packet
from rocket_gnc_monitor.trajectory import Trajectory
from rocket_gnc_monitor.ui import MainWindow, MissionDialog
from rocket_gnc_monitor.virtual_pointer import VirtualPointer, VirtualFlight, VIRTUAL_POINTER_DEVICE


def reference(origin=(42.7, -77.2, 200)):
    return Trajectory(
        [[0, 0, 0, 0], [5, 100, 200, 500], [10, 300, 400, 1000]],
        dict(
            schema_version=1,
            frame="ENU",
            units="m,s",
            altitude_datum="launch_relative",
            origin=list(origin),
            name="Test reference",
        ),
    )


def mission():
    return Mission(
        pointer_site_configured=True, pointer_latitude=42.699, pointer_longitude=-77.201, pointer_altitude=195
    )


def test_virtual_wire_commands_slew_hold_and_disconnect():
    messages = []
    raw = []
    worker = VirtualPointer(1, lambda *args: messages.append(args), lambda *args: raw.append(args))
    worker.send(pointer_packet(350, 30), "absolute")
    worker.advance(0.1)
    assert worker.pose == pytest.approx((351, 6))
    assert worker.target == (350, 30)
    worker.advance(1)
    assert worker.pose == worker.target
    for opcode, expected in [(1, (350, 35)), (2, (350, 30)), (3, (345, 30)), (4, (350, 30)), (5, (0, 0))]:
        worker.send(pointer_packet(opcode=opcode), str(opcode))
        worker.advance(1)
        assert worker.pose == expected
    assert all(item[0] == "virtual_pointer_tx" for item in raw)
    worker.send(pointer_packet(180, 60), "held")
    worker.hold()
    worker.advance(10)
    assert worker.pose == (0, 0) and worker.commands.empty()
    worker.stop()
    with pytest.raises(ValueError, match="disconnected"):
        worker.send(pointer_packet(), "closed")
    assert any(message[2:] == ("sent", "absolute") for message in messages)


def test_virtual_frame_conversion_uses_pointer_location_and_ellipsoid_height():
    ref = reference()
    m = mission()
    flight = VirtualFlight(ref, m)
    assert flight.angles == pytest.approx(pointing(ref.manifest["origin"], flight.origin), abs=1e-8)
    flight.seek(5)
    assert flight.distance > 500 and 0 < flight.angles[1] < 90
    moved = VirtualFlight(ref, replace(m, pointer_longitude=-77.199))
    assert moved.angles[0] > 270 and flight.angles[0] < 90
    assert math.dist(flight.mount_position, (0, 0, 0)) > 100


def test_virtual_cardinals_interpolation_overhead_and_playback_end():
    m = Mission(pointer_site_configured=True)
    ref = reference((0, 0, 0))
    flight = VirtualFlight(ref, m)
    assert flight.angles is None  # Coincident origin has no pointing direction.
    for xyz, angles in [
        ((100, 0, 100), (90, 45)),
        ((0, 100, 100), (0, 45)),
        ((-100, 0, 100), (270, 45)),
        ((0, -100, 100), (180, 45)),
    ]:
        r = Trajectory([[0, *xyz], [10, *xyz]], dict(ref.manifest))
        assert VirtualFlight(r, m).angles == pytest.approx(angles)
    flight.speed = 2
    flight.playing = True
    flight.advance(2.5)
    assert flight.time == 5 and flight.position == [100, 200, 500]
    flight.advance(10)
    assert flight.time == 10 and not flight.playing
    flight.seek(-100)
    assert flight.time == 0
    with pytest.raises(ValueError):
        flight.seek(float("nan"))
    above = Trajectory([[0, 0, 0, 10], [1, 0, 0, 100]], dict(ref.manifest))
    assert VirtualFlight(above, m).angles == (0, 90)


def test_missing_configuration_and_real_transport_cannot_follow(qtbot, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="antenna pointer location"):
        VirtualFlight(reference(), Mission())
    with pytest.raises(ValueError, match="OpenRocket"):
        VirtualFlight(None, mission())
    c = Controller(tmp_path)
    c.timer.stop()
    c.reference = reference()
    c.mission = mission()
    c.workers["pointer"] = SimpleNamespace(device="physical")
    c.states["pointer"] = "Connected"
    try:
        with pytest.raises(ValueError, match="Virtual antenna"):
            c.start_virtual_trajectory()
        with pytest.raises(ValueError, match="virtual pointer"):
            c.send_virtual_target()
    finally:
        c.workers["pointer"] = None
        c.shutdown()


def test_virtual_controller_manual_follow_pause_seek_change_and_mode_isolation(qtbot, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Physical serial opened")

    monkeypatch.setattr("rocket_gnc_monitor.controller.SerialWorker", forbidden)
    c = Controller(tmp_path)
    c.timer.stop()
    c.mission = mission()
    c.reference = reference()
    try:
        with pytest.raises(ValueError, match="not a ground station"):
            c.connect("telemetry", VIRTUAL_POINTER_DEVICE)
        c.connect("pointer", VIRTUAL_POINTER_DEVICE)
        c.tick()
        assert c.pointer_connected and not c.ground_connected
        assert c.workers["telemetry"] is None
        c.manual_point(90, 30)
        c.tick()
        c.virtual_pointer.advance(1)
        assert c.virtual_pointer.pose == (90, 30)
        c.start_virtual_trajectory()
        c.advance_virtual_flight(2.5, 100)
        assert c.virtual_flight.time == 2.5
        c.tick()
        c.hold()
        clock = c.virtual_flight.time
        pose = c.virtual_pointer.pose
        c.advance_virtual_flight(2, 102)
        c.virtual_pointer.advance(2)
        assert c.virtual_flight.time == clock and c.virtual_pointer.pose == pose
        c.seek_virtual_trajectory(5)
        c.tick()
        assert c.virtual_flight.time == 5 and not c.virtual_flight.playing
        c.start_virtual_trajectory()
        c.manual_point(0, 0)
        assert not c.virtual_flight.playing
        c.tick()
        c.start_virtual_trajectory()
        c.mission.pointer_altitude += 10
        c.advance_virtual_flight(1, 104)
        assert c.virtual_flight is None
        assert c.latest is None and not c.history and not c.track
        c.switch_mode("REPLAY")
        assert not c.virtual_pointer and c.virtual_flight is None
    finally:
        c.shutdown()


def test_pointer_location_profile_and_portable_flight_roundtrip(qtbot, tmp_path):
    dialog = MissionDialog(Mission())
    qtbot.addWidget(dialog)
    dialog.pointer_location.latitude.setValue(42.7)
    dialog.pointer_location.longitude.setValue(-77.2)
    dialog.pointer_location.altitude.setValue(123.5)
    dialog.fields["pointer_site_configured"].setChecked(True)
    dialog.accept()
    m = dialog.mission
    assert m.pointer_site_configured and m.pointer_altitude == 123.5
    m.save(tmp_path / "mission.json")
    assert Mission.load(tmp_path / "mission.json").pointer_latitude == 42.7
    save_flight(tmp_path / "test.rktflight", m, reference=reference())
    loaded = load_flight(tmp_path / "test.rktflight", tmp_path / "cache")
    assert loaded.mission.pointer_site_configured and loaded.mission.pointer_altitude == 123.5


def test_virtual_ui_connect_shortcuts_and_trajectory_controls(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.ui.ports", lambda: [])
    w = MainWindow(tmp_path)
    qtbot.addWidget(w)
    w.show()
    w.controller.timer.stop()
    c = w.controller
    c.mission = mission()
    c.reference = reference()

    def refresh():
        w.last_ui = 0
        w.refresh()

    try:
        pointer = w.port_widgets["pointer"][0]
        ground = w.port_widgets["telemetry"][0]
        assert pointer.findData(VIRTUAL_POINTER_DEVICE) >= 0 and ground.findData(VIRTUAL_POINTER_DEVICE) < 0
        w.pages.setCurrentIndex(2)
        qtbot.mouseClick(w.virtual_connect, Qt.MouseButton.LeftButton)
        c.tick()
        refresh()
        assert w.virtual_panel.isVisible() and w.point_button.isEnabled()
        assert not w.record_button.isEnabled() and not w.track_button.isVisible()
        assert "VIRTUAL" in w.connection_tiles["pointer"].text()
        qtbot.mouseClick(w.virtual_follow, Qt.MouseButton.LeftButton)
        assert c.virtual_flight.playing
        w.virtual_speed.setCurrentIndex(4)
        assert c.virtual_flight.speed == 4
        c.advance_virtual_flight(1, 100)
        c.tick()
        refresh()
        assert c.virtual_flight.time >= 4 and w.reference_marker.getData()[0].size == 1
        qtbot.mouseClick(w.virtual_follow, Qt.MouseButton.LeftButton)
        assert not c.virtual_flight.playing
        w.virtual_slider.setValue(5000)
        assert c.virtual_flight.time == 5
        w.legacy_actions["up"].trigger()
        c.tick()
        assert c.pointer_last_command == "UP" and not c.virtual_flight.playing
        refresh()
        assert (w.mount.azimuth, w.mount.elevation) == c.virtual_pointer.pose
        c.disconnect("pointer")
        refresh()
        assert not w.virtual_panel.isVisible() and not w.point_button.isEnabled()
    finally:
        w.close()
