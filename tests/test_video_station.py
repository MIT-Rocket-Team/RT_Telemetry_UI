"""The diagram's video computer works without a telemetry USB board."""

import csv
import json
import queue

import pytest
from PySide6.QtCore import Qt

from rocket_gnc_monitor.controller import Controller
from rocket_gnc_monitor.media import VideoWorker
from rocket_gnc_monitor.recording import SessionReader
from rocket_gnc_monitor.station_workspace import StationWindow


class CameraStub:
    def __init__(self, kind, source="", record_dir=None, seek=0, speed=1, single_frame=False):
        self.kind, self.source, self.record_dir = kind, source, record_dir
        self.seek, self.speed, self.single_frame = seek, speed, single_frame
        self.frames = queue.Queue(maxsize=2)
        self.error, self.last_frame, self.received_frames = "", 0, 0
        self.stopped = False

    def stop(self):
        self.stopped = True


def refresh(window):
    window.last_ui = 0
    window.refresh()


def choices(combo):
    return {combo.itemData(index) for index in range(combo.count())}


def test_video_computer_opens_distinct_cameras_without_unlocking_rocket(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.controller.VideoWorker", CameraStub)
    window = StationWindow(tmp_path, station="video", auto_place=False)
    qtbot.addWidget(window)
    window.show()
    c = window.controller
    try:
        window.resize(1280, 800)
        qtbot.wait(50)
        assert window.station_mode == "video" and not window.companion.isVisible()
        assert {key for key, card in window.cards.items() if card.isVisible()} == {"digital", "analog"}
        assert not window.usb_strip.isVisible() and not window.poll_button.isVisible()
        assert not any(widget.isVisible() for widget in window.metrics.values())
        assert c.mode == "LIVE" and not c.ground_connected
        window.task_done("cameras", [("Digital USB receiver", "0"), ("Analog USB receiver", "1")])
        digital, analog = (window.video_widgets[stream] for stream in ("digital", "analog"))
        for widgets in (digital, analog):
            assert all(widgets[key].isVisible() for key in ("camera", "start", "stop", "file", "status"))
            assert widgets["image"].isVisible() and widgets["image"].width() > 350
        digital["camera"].setCurrentIndex(digital["camera"].findData("0"))
        analog["camera"].setCurrentIndex(analog["camera"].findData("1"))
        refresh(window)
        assert digital["start"].isEnabled() and analog["start"].isEnabled()
        assert "0" not in choices(analog["camera"]) and "1" not in choices(digital["camera"])
        qtbot.mouseClick(digital["start"], Qt.MouseButton.LeftButton)
        qtbot.mouseClick(analog["start"], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: all(state.worker for state in c.video_streams.values()))
        assert c.video_streams["digital"].worker.source == "0"
        assert c.video_streams["analog"].worker.source == "1"
        with pytest.raises(ValueError, match="Digital"):
            c.start_video("camera", "0", stream="analog")
        for state in c.video_streams.values():
            state.worker.last_frame = 1.0
        refresh(window)
        assert window.record_button.isEnabled() and window.legacy_actions["log"].isEnabled()
        assert window.record_button.isVisible()
        assert not window.rocket_panel.buttons["advance_state"].isEnabled()
        assert not window.poll_button.isEnabled()
        with pytest.raises(ValueError, match="disconnected"):
            c.send_rocket("advance_state")
        assert c.workers == {"telemetry": None, "pointer": None}

        workers = {stream: state.worker for stream, state in c.video_streams.items()}
        for mode in ("base", "away", "video"):
            window.set_station_mode(mode)
            qtbot.wait(40)
            refresh(window)
            assert all(state.worker is workers[stream] and not state.worker.stopped
                       for stream, state in c.video_streams.items())
            assert all(window.cards[stream].isVisible() == (mode == "video") for stream in workers)
        assert json.loads(window.station_path.read_text())["station"] == "video"
        assert digital["camera"].currentData() == "0" and analog["camera"].currentData() == "1"
        assert "0" not in choices(analog["camera"]) and "1" not in choices(digital["camera"])

        c.switch_mode("REPLAY")
        refresh(window)
        assert window.video_replay_bar.isVisible() and window.replay_controls.isVisible()
        assert window.play.isVisible() and window.replay_slider.isVisible() and window.speed.isVisible()
        assert not window.align_time_button.isVisible()
        assert not digital["start"].isEnabled() and not analog["start"].isEnabled()
        assert not digital["file"].isEnabled() and not window.record_button.isEnabled()
        with pytest.raises(ValueError, match="replay"):
            window.start_camera("digital")
        window.set_station_mode("base")
        refresh(window)
        assert window.replay_controls.isVisible() and window.align_time_button.isVisible()
        assert not window.video_replay_bar.isVisible()
        window.set_station_mode("video")
    finally:
        window.close()

    restored = StationWindow(tmp_path, auto_place=False)
    qtbot.addWidget(restored)
    try:
        restored.show()
        qtbot.wait(50)
        assert restored.station_mode == "video" and not restored.companion.isVisible()
        assert restored.controller.mode == "LIVE"
        assert not any(state.worker for state in restored.controller.video_streams.values())
    finally:
        restored.close()


def test_recording_requires_received_video_and_replay_stays_read_only(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.controller.VideoWorker", CameraStub)
    c = Controller(tmp_path / "app")
    try:
        with pytest.raises(ValueError, match="disconnected"):
            c.start_recording(tmp_path / "recordings")
        c.start_video("camera", "0")
        qtbot.waitUntil(lambda: c.video is not None)
        assert c.video.last_frame == 0
        with pytest.raises(ValueError, match="disconnected"):
            c.start_recording(tmp_path / "recordings")
        assert c.recorder is None and not (tmp_path / "recordings").exists()
        c.switch_mode("REPLAY")
        c.start_video("file", "replay.mkv")
        qtbot.waitUntil(lambda: c.video is not None)
        c.video.last_frame = 1.0
        with pytest.raises(ValueError, match="read-only"):
            c.start_recording(tmp_path / "recordings")
        assert c.recorder is None and not any(c.workers.values())
    finally:
        c.shutdown()


def test_video_only_session_finalizes_and_replays_without_telemetry(qtbot, tmp_path, monkeypatch):
    # Replace only the physical USB input with FFmpeg's generated frames. Codec,
    # worker lifecycle, session writer, finalized segment and replay remain real.
    def synthetic_receiver(kind, source="", record_dir=None, seek=0, speed=1, single_frame=False):
        return VideoWorker("demo" if kind == "camera" else kind, source,
                           record_dir, seek, speed, single_frame)

    monkeypatch.setattr("rocket_gnc_monitor.controller.VideoWorker", synthetic_receiver)
    c = Controller(tmp_path / "app")
    try:
        c.start_video("camera", "0", stream="digital")
        qtbot.waitUntil(lambda: c.video is not None and c.video.received_frames >= 3, timeout=6000)
        assert c.mode == "LIVE" and not c.ground_connected and c.latest is None
        session = c.start_recording(tmp_path / "recordings")
        qtbot.waitUntil(lambda: c.video is not None and c.video.record_dir is not None
                       and c.video.received_frames >= 15, timeout=6000)
        c.stop_recording()
        qtbot.waitUntil(lambda: c.recording_close_future.done(), timeout=6000)
        c.recording_close_future.result()
        reader = SessionReader(session)
        try:
            assert reader.manifest["complete"] and reader.integrity == "ok" and reader.count == 0
            assert reader.duration > 0.4 and reader.at(reader.duration) is None
            starts = [(t, event) for t, event in reader.events(reader.duration)
                      if event["name"] == "Video recording started"]
            assert len(starts) == 1 and starts[0][1]["data"]["stream"] == "digital"
            folder = session / starts[0][1]["data"]["directory"]
            with (folder / "segments.csv").open(newline="") as handle:
                segments = list(csv.reader(handle))
            assert len(segments) == 1
            name, begin, end = segments[0]
            assert (folder / name).stat().st_size > 0 and float(end) > float(begin)
            seek = starts[0][0] + (float(begin) + float(end)) / 2
            assert seek < reader.duration
        finally:
            reader.close()
        c.open_replay(session)
        c.seek(seek)
        qtbot.waitUntil(lambda: c.video is not None and c.video.kind == "file"
                       and c.video.received_frames > 0, timeout=6000)
        assert c.mode == "REPLAY" and c.reader.count == 0 and c.latest is None
        assert c.video.single_frame and not any(c.workers.values())
        with pytest.raises(ValueError, match="read-only"):
            c.start_recording(tmp_path / "forbidden")
    finally:
        c.shutdown()
