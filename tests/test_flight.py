"""Portable flight round trips, running-capture snapshots and rejected corrupt files."""

import csv
from pathlib import Path
import zipfile

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QFileDialog, QMessageBox
from rocket_gnc_monitor.controller import Controller
from rocket_gnc_monitor.demo import DemoFlight
from rocket_gnc_monitor.domain import Mission, demo_sample
from rocket_gnc_monitor.flight import save_flight, load_flight
from rocket_gnc_monitor.location import decode_mgrs
from rocket_gnc_monitor.recording import SessionRecorder, SessionReader, read_raw
from rocket_gnc_monitor.trajectory import Trajectory
from rocket_gnc_monitor.ui import MainWindow


def test_planned_flight_portable_assets_and_urrg(tmp_path):
    point = decode_mgrs("18TUN2061530290")
    model = tmp_path / "rocket.ork"
    model.write_bytes((Path(__file__).parent / "fixtures" / "zephy_testlaunch.ork").read_bytes())
    motor = tmp_path / "custom.eng"
    motor.write_bytes(b"motor curve fixture")
    mission = Mission(
        name="URRG test",
        launch_site_name="URRG",
        launch_location_format="mgrs",
        launch_location_code=point.code,
        latitude=point.latitude,
        longitude=point.longitude,
        site_configured=True,
        model=str(model),
        motor_files=[str(motor)],
        pointer_calibrated=True,
    )
    reference = Trajectory(
        [[0, 0, 0, 0], [1, 2, 3, 100]],
        dict(
            schema_version=1,
            frame="ENU",
            units="m,s",
            origin=[point.latitude, point.longitude, 0],
            name="Test reference",
        ),
    )
    archive = tmp_path / "flight.rktflight"
    save_flight(archive, mission, reference)
    model.unlink()
    motor.unlink()
    loaded = load_flight(archive, tmp_path / "another-machine")
    assert loaded.session is None
    assert loaded.mission.launch_site_name == "URRG"
    assert loaded.mission.launch_location_code == "18TUN2061530290"
    assert loaded.mission.latitude == mission.latitude and loaded.mission.longitude == mission.longitude
    assert not loaded.mission.pointer_calibrated and mission.pointer_calibrated
    assert Path(loaded.mission.model).read_bytes()[:2] == b"PK"
    assert Path(loaded.mission.motor_files[0]).read_bytes() == b"motor curve fixture"
    assert loaded.reference.points.tolist() == reference.points.tolist()
    # Re-saving an extracted flight must embed assets again, without dependence on its cache.
    second = tmp_path / "second.rktflight"
    save_flight(second, loaded.mission, loaded.reference)
    assert load_flight(second, tmp_path / "third-machine").mission.launch_site_name == "URRG"


def test_live_snapshot_preserves_matching_database_raw_and_csv_without_stopping(tmp_path):
    recorder = SessionRecorder(tmp_path, Mission(), "LIVE")
    try:
        for index in range(200):
            sample = demo_sample(index * 0.01, index)
            sample.received = recorder.start + index * 0.001
            recorder.sample(sample)
            recorder.raw("pointer_rx", bytes([index % 256]) * 7)
        archive = tmp_path / "active.rktflight"
        save_flight(archive, Mission(), session=recorder.path, active=True, scope="Recording snapshot")
        assert recorder.thread.is_alive() and not recorder.closed
        for index in range(200, 400):
            recorder.sample(demo_sample(index * 0.01, index))
        loaded = load_flight(archive, tmp_path / "opened")
        reader = SessionReader(loaded.session)
        try:
            assert reader.count == 200 and reader.manifest["snapshot"]
            assert not reader.manifest["complete"]
            with (loaded.session / "telemetry.csv").open() as handle:
                assert len(list(csv.DictReader(handle))) == reader.count
            raw_rows = reader.db.execute("SELECT COUNT(*) FROM raw_index").fetchone()[0]
            assert len(list(read_raw(loaded.session / "raw.bin"))) == raw_rows == 200
        finally:
            reader.close()
        recorder.close()
        final = SessionReader(recorder.path)
        assert final.count == 400 and final.manifest["complete"]
        final.close()
    finally:
        recorder.close()


def test_archive_rejects_corruption_and_paths_and_leaves_no_extraction(tmp_path):
    original = tmp_path / "original.rktflight"
    save_flight(original, Mission())
    with zipfile.ZipFile(original) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    for index, extra in enumerate(("../escaped", "/absolute", "assets\\escape", "C:/escape", "MISSION.JSON")):
        bad = tmp_path / f"bad{index}.rktflight"
        with zipfile.ZipFile(bad, "w") as archive:
            for name, content in contents.items():
                archive.writestr(name, content)
            archive.writestr(extra, b"test")
        with pytest.raises(ValueError):
            load_flight(bad, tmp_path / "cache")
    bad = tmp_path / "damaged.rktflight"
    with zipfile.ZipFile(bad, "w") as archive:
        for name, content in contents.items():
            archive.writestr(
                name, content.replace(b"Untitled", b"Modified") if name == "mission.json" else content
            )
    with pytest.raises(ValueError, match="checksum"):
        load_flight(bad, tmp_path / "cache")
    assert not list((tmp_path / "cache").iterdir())
    assert not (tmp_path / "escaped").exists()


def test_failed_save_preserves_previous_file(tmp_path):
    archive = tmp_path / "flight.rktflight"
    save_flight(archive, Mission(name="Original"))
    original = archive.read_bytes()
    with pytest.raises(ValueError, match="missing"):
        save_flight(archive, Mission(model=str(tmp_path / "missing.ork")))
    assert archive.read_bytes() == original
    assert not list(tmp_path.glob(".flight-*"))


def test_full_demo_export_and_replay_position(qtbot, tmp_path):
    c = Controller(tmp_path / "app")
    try:
        c.switch_mode("DEMO")
        c.play_demo(False)
        c.seek_demo(c.demo.launch_time + 3)
        expected_sample = c.latest.to_dict()
        archive = tmp_path / "zephyrus.rktflight"
        c.save_flight(archive)
        qtbot.waitUntil(lambda: not c.flight_busy, timeout=60000)
        assert archive.exists()
        with zipfile.ZipFile(archive) as saved:
            assert not any(name.endswith(("-wal", "-shm")) for name in saved.namelist())
        c.switch_mode("LIVE")
        c.open_flight(archive)
        qtbot.waitUntil(lambda: not c.flight_busy, timeout=10000)
        assert c.mode == "REPLAY" and not c.replay_playing and not any(c.workers.values())
        assert c.reader.count == len(DemoFlight("GS2").rows)
        assert c.latest.sequence == expected_sample["sequence"]
        assert c.latest.enu == expected_sample["enu"]
        assert c.latest.details == expected_sample["details"]
        assert not list(c.reader.path.glob("video*"))
        c.switch_mode("LIVE")
        assert c.reader is None and c.last_session_path is None
    finally:
        c.shutdown()


def test_buffered_telemetry_save_and_invalid_open_keeps_current_flight(qtbot, tmp_path):
    c = Controller(tmp_path)
    results = []
    c.task_done.connect(lambda name, result: results.append((name, result)))
    try:
        for index in range(10):
            sample = demo_sample(index * 0.1, index)
            sample.received = 100 + index * 0.1
            c.history.append(sample)
        archive = tmp_path / "buffer.rktflight"
        c.save_flight(archive)
        qtbot.waitUntil(lambda: not c.flight_busy)
        c.open_flight(archive)
        qtbot.waitUntil(lambda: not c.flight_busy)
        assert c.reader.count == 10 and c.latest.sequence == 9
        assert "buffer only" in c.reader.manifest["export_scope"]
        reader = c.reader
        invalid = tmp_path / "bad.rktflight"
        invalid.write_bytes(b"not an archive")
        c.open_flight(invalid)
        qtbot.waitUntil(lambda: not c.flight_busy)
        assert c.reader is reader and c.latest.sequence == 9
        assert isinstance(results[-1][1], ValueError)
    finally:
        c.shutdown()


def test_save_completed_capture_includes_final_video_segment(qtbot, tmp_path):
    c = Controller(tmp_path / "app")
    try:
        c.switch_mode("DEMO")
        qtbot.waitUntil(lambda: c.video is not None and c.video.received_frames > 3, timeout=5000)
        c.start_recording(tmp_path / "recordings")
        qtbot.wait(1000)
        c.stop_recording()
        archive = tmp_path / "capture.rktflight"
        c.save_flight(archive)  # Must wait for recorder and video finalization in the background.
        qtbot.waitUntil(lambda: not c.flight_busy, timeout=10000)
        c.open_flight(archive)
        qtbot.waitUntil(lambda: not c.flight_busy, timeout=10000)
        assert c.reader.manifest["complete"]
        assert list(c.reader.path.glob("video*/segment_*.mkv"))
        c.seek(c.reader.duration * 0.5)
        qtbot.waitUntil(lambda: c.video is not None and c.video.received_frames > 0, timeout=6000)
        assert c.video.single_frame
    finally:
        c.shutdown()


def test_file_menu_dialogs_and_shortcuts(qtbot, tmp_path, monkeypatch):
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    archive = tmp_path / "planning.rktflight"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(archive.with_suffix("")), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(archive), ""))
    try:
        window.controller.mission.name = "Saved planning flight"
        assert window.flight_actions["save"].shortcut() == QKeySequence(QKeySequence.StandardKey.Save)
        assert window.flight_actions["open"].shortcut() == QKeySequence(QKeySequence.StandardKey.Open)
        window.flight_actions["save"].trigger()
        qtbot.waitUntil(lambda: not window.controller.flight_busy)
        assert archive.exists() and "Saved planning" not in errors
        window.controller.mission.name = "Changed"
        window.flight_actions["open"].trigger()
        qtbot.waitUntil(lambda: not window.controller.flight_busy)
        assert window.controller.mission.name == "Saved planning flight"
        assert window.controller.mode == "LIVE" and not window.controller.ground_connected
        assert window.pages.currentIndex() == 4
        assert "Opened planning.rktflight" in window.flight_file_status.text()
        assert not errors
        assert len(window.legacy_actions) == 10
    finally:
        window.close()
