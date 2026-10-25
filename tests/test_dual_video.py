"""Two USB source reservations, independent lifecycles and portable recordings."""

import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytestqt.exceptions import TimeoutError as QtTimeoutError
from rocket_gnc_monitor.controller import Controller
from rocket_gnc_monitor.domain import demo_sample
from rocket_gnc_monitor.recording import SessionReader, SessionRecorder
from rocket_gnc_monitor.ui import MainWindow


class FakeVideo:
    def __init__(self, kind, source="", record_dir=None, seek=0, speed=1, single_frame=False):
        self.kind, self.source = kind, source
        self.record_dir, self.seek = record_dir, seek
        self.speed, self.single_frame = speed, single_frame
        self.frames = queue.Queue()
        self.error, self.last_frame = "", 0
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_camera_reservation_pending_close_and_independent_frames(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.controller.VideoWorker", FakeVideo)
    c = Controller(tmp_path)
    received = []
    c.video_frame.connect(lambda channel, data: received.append((channel, data)))
    try:
        first = c.start_video("camera", "0", stream="digital")
        with pytest.raises(ValueError, match="Digital"):
            c.start_video("camera", "0", stream="analog")
        c.start_video("camera", "1", stream="analog")
        qtbot.waitUntil(lambda: all(s.worker is not None for s in c.video_streams.values()))
        digital, analog = (c.video_streams[n].worker for n in ("digital", "analog"))
        digital.frames.put(b"digital")
        analog.frames.put(b"analog")
        c.tick()
        assert received == [("digital", b"digital"), ("analog", b"analog")]
        # Queue a close behind a deliberately slow task to exercise the reservation
        # while the USB handle still belongs to Digital.
        gate = threading.Event()
        c.video_streams["digital"].executor.submit(lambda: gate.wait(2))
        c.stop_video("digital")
        try:
            with pytest.raises(ValueError, match="Digital"):
                c.start_video("camera", "0", stream="analog")
            assert c.video_streams["analog"].worker is analog and not analog.stopped
        finally:
            gate.set()
        qtbot.waitUntil(lambda: not c.video_streams["digital"].reserved_cameras)
        assert digital.stopped and first.done()
        c.start_video("camera", "0", stream="analog")
        qtbot.waitUntil(lambda: c.video_streams["analog"].worker is not None)
        assert c.video_streams["analog"].worker.source == "0"
        assert analog.stopped
        c.switch_mode("REPLAY")
        qtbot.waitUntil(lambda: all(not s.reserved_cameras for s in c.video_streams.values()))
        assert all(s.worker is None and s.config is None for s in c.video_streams.values())
    finally:
        c.shutdown()


def test_choices_exclude_other_camera_and_connected_serial_aliases(qtbot, tmp_path, monkeypatch):
    devices = [dict(device=p) for p in ("/dev/cu.A", "/dev/tty.A", "/dev/cu.B")]
    monkeypatch.setattr("rocket_gnc_monitor.ui.ports", lambda: devices)
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    c = window.controller

    def values(combo):
        return [combo.itemData(i) for i in range(combo.count())]

    def update():
        window.last_ui = 0
        window.refresh()

    try:
        ground, pointer = (window.port_widgets[n][0] for n in ("telemetry", "pointer"))
        c.workers["telemetry"] = SimpleNamespace(device="/dev/cu.A")
        c.states["telemetry"] = "Connecting"
        update()
        assert values(pointer) == ["/dev/cu.B", "virtual://antenna-pointer"]
        assert ground.currentData() == "/dev/cu.A"
        c.states["telemetry"] = "Connected"
        c.workers["pointer"] = SimpleNamespace(device="/dev/cu.B")
        c.states["pointer"] = "Connected"
        update()
        assert "/dev/cu.B" not in values(ground)
        window.refresh_ports()
        assert "/dev/tty.A" not in values(pointer)
        c.workers["telemetry"] = None
        c.states["telemetry"] = "Disconnected"
        update()
        assert "/dev/cu.B" not in values(ground)
        c.workers["pointer"] = None
        c.states["pointer"] = "Disconnected"
        update()
        assert set(values(ground)) == {d["device"] for d in devices}
        assert set(values(pointer)) == {d["device"] for d in devices} | {"virtual://antenna-pointer"}

        window.task_done("cameras", [("USB receiver", "0"), ("USB receiver", "1")])
        digital, analog = (window.video_widgets[n]["camera"] for n in ("digital", "analog"))
        digital.setCurrentIndex(digital.findData("0"))
        assert "0" not in values(analog)
        analog.setCurrentIndex(analog.findData("1"))
        assert "1" not in values(digital)
        window.task_done("cameras", [("USB receiver", "0"), ("USB receiver", "1")])
        assert digital.currentData() == "0" and analog.currentData() == "1"
        assert "0" not in values(analog) and "1" not in values(digital)
        digital.setCurrentIndex(0)
        assert "0" in values(analog)
    finally:
        c.workers = {"telemetry": None, "pointer": None}
        window.close()


def test_legacy_video_is_digital_and_stream_gaps_remain_separate(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.controller.VideoWorker", FakeVideo)
    c = Controller(tmp_path / "app")
    r = SessionRecorder(tmp_path / "recordings", c.mission, "LIVE")
    for i in range(10):
        sample = demo_sample(i, i)
        sample.received = r.start + i
        r.sample(sample)
    for name, start, stream in [
        ("video", 0, None),
        ("video_analog_1", 3, "analog"),
        ("video_analog_2", 7, "analog"),
    ]:
        folder = r.path / name
        folder.mkdir()
        (folder / "segment.mkv").write_bytes(b"fake video")
        (folder / "segments.csv").write_text("segment.mkv,0,2\n")
        data = dict(directory=name, kind="camera")
        if stream:
            data["stream"] = stream
        r.put("event", dict(name="Video recording started", data=data), r.start + start)
    r.close()
    try:
        c.open_replay(r.path)
        c.seek(1)
        qtbot.waitUntil(lambda: c.video is not None)
        assert Path(c.video.source) == r.path / "video" / "segment.mkv"
        assert c.video.single_frame and c.video.seek == 1
        assert c.video_streams["analog"].worker is None
        c.seek(3.5)
        qtbot.waitUntil(lambda: c.video_streams["analog"].worker is not None)
        assert c.video is None
        assert c.video_streams["analog"].worker.seek == 0.5
        c.seek(6)
        assert all(s.worker is None for s in c.video_streams.values())
        c.replay_playing = True
        c.replay_time = 7.2
        c.replay_video(force=False)
        qtbot.waitUntil(lambda: c.video_streams["analog"].worker is not None)
        worker = c.video_streams["analog"].worker
        assert "video_analog_2" in worker.source and not worker.single_frame
        c.replay_video(force=False)
        assert c.video_streams["analog"].worker is worker
        c.stop_video("analog", pause_replay=True)
        c.replay_video(force=False)
        assert c.video_streams["analog"].config is None
        c.replay_video()
        qtbot.waitUntil(lambda: c.video_streams["analog"].worker is not None)
    finally:
        c.shutdown()


def test_both_streams_record_and_round_trip_in_a_flight(qtbot, tmp_path):
    c = Controller(tmp_path / "app")

    def diagnostics():
        states = {}
        for channel, state in c.video_streams.items():
            worker = state.worker or state.backend
            future = state.lifecycle_future
            states[channel] = dict(
                published_worker=state.worker is not None,
                received_frames=worker.received_frames if worker else 0,
                lifecycle_error=state.error,
                worker_error=worker.error if worker else "",
                lifecycle_done=future.done() if future else None,
                lifecycle_running=future.running() if future else None,
                ffmpeg_logs=list(worker.logs) if worker else [],
            )
        return json.dumps(states, indent=2)

    def both_frames():
        for channel, state in c.video_streams.items():
            if state.error or (state.worker and state.worker.error):
                pytest.fail(f"{channel} failed while waiting for frames:\n{diagnostics()}")
        return all(s.worker is not None and s.worker.received_frames > 3 for s in c.video_streams.values())

    def wait_for_frames(timeout):
        try:
            qtbot.waitUntil(both_frames, timeout=timeout)
        except QtTimeoutError:
            pytest.fail(f"Video frames not ready within {timeout} ms:\n{diagnostics()}", pytrace=False)

    try:
        c.switch_mode("DEMO")
        wait_for_frames(6000)
        c.start_recording(tmp_path / "recordings")
        # Recording restarts each input. Graceful stop/terminate/kill can use
        # 2 + 2 + 1 seconds before FFmpeg starts and delivers new frames.
        wait_for_frames(20000)
        qtbot.wait(800)
        # Stopping an individual feed immediately before logging must still wait
        # for that feed's final segment when saving the flight.
        c.stop_video("analog")
        digital = c.video_streams["digital"].worker
        assert digital is not None and not digital.stopped.is_set()
        c.stop_recording()
        archive = tmp_path / "dual.rktflight"
        c.save_flight(archive)
        qtbot.waitUntil(lambda: not c.flight_busy, timeout=10000)
        assert archive.exists()
        c.open_flight(archive)
        qtbot.waitUntil(lambda: not c.flight_busy, timeout=10000)
        assert c.reader.manifest["complete"]
        assert len(list(c.reader.path.glob("video*/segments.csv"))) == 2
        starts = [
            (t, e) for t, e in c.reader.events(c.reader.duration) if e["name"] == "Video recording started"
        ]
        assert {e["data"]["stream"] for _, e in starts} == {"digital", "analog"}
        c.seek(max(t for t, _ in starts) + 0.25)
        qtbot.waitUntil(
            lambda: all(
                s.worker is not None and s.worker.received_frames > 0 for s in c.video_streams.values()
            ),
            timeout=6000,
        )
        assert all(s.worker.single_frame for s in c.video_streams.values())
        assert not any(c.workers.values())
        metadata = json.loads((c.reader.path / "manifest.json").read_text())
        assert metadata["flight_video_segments"] == 2
    finally:
        c.shutdown()


def test_failed_second_open_leaves_first_stream_running(qtbot, tmp_path, monkeypatch):
    def open_video(kind, source, *args):
        if source == "broken":
            raise OSError("USB camera unavailable")
        return FakeVideo(kind, source, *args)

    monkeypatch.setattr("rocket_gnc_monitor.controller.VideoWorker", open_video)
    c = Controller(tmp_path)
    try:
        c.start_video("camera", "0")
        qtbot.waitUntil(lambda: c.video is not None)
        first = c.video
        c.start_video("camera", "broken", stream="analog")
        qtbot.waitUntil(lambda: bool(c.video_streams["analog"].error))
        assert c.video is first and not first.stopped
        assert c.video_streams["analog"].config is None
        assert not c.video_streams["analog"].reserved_cameras
        monkeypatch.setattr(c, "require_ground_station", lambda: None)
        session = c.start_recording(tmp_path / "sessions")
        c.stop_recording()
        qtbot.waitUntil(lambda: c.recording_close_future.done())
        c.recording_close_future.result()
        reader = SessionReader(session)
        try:
            assert reader.manifest["complete"]
            assert any(e["name"] == "Video lifecycle error" for _, e in reader.events(60))
        finally:
            reader.close()
    finally:
        c.shutdown()


def test_dual_panels_do_not_overlap_at_minimum_window_size(qtbot, tmp_path):
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    try:
        window.resize(1120, 800)
        window.pages.setCurrentIndex(1)
        window.show()
        for mode in ("LIVE", "DEMO"):
            window.controller.switch_mode(mode)
            window.last_ui = 0
            window.refresh()
            qtbot.wait(250)
            assert window.size().toTuple() == (1120, 800)
            area = window.pages.currentWidget()
            assert area.widget().height() <= max(760, area.viewport().height())
            for widgets in window.video_widgets.values():
                view, status, camera = (widgets[n] for n in ("image", "status", "camera"))
                assert view.geometry().bottom() < status.geometry().top()
                assert status.geometry().bottom() < camera.geometry().top()
    finally:
        window.close()
