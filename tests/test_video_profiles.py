"""Station video inputs stay distinct through capture, recording and portable replay."""

import gc
import json
import queue
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from rocket_gnc_monitor.controller import Controller
from rocket_gnc_monitor.ui import MainWindow


IRIS = ("digital", "analog", "analog2")
BOOSTER = ("digital", "analog2")


class CameraStub:
    def __init__(self, kind, source="", record_dir=None, seek=0, speed=1, single_frame=False):
        self.kind, self.source, self.record_dir = kind, source, record_dir
        self.seek, self.speed, self.single_frame = seek, speed, single_frame
        self.frames = queue.Queue()
        self.error, self.last_frame = "", 0
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_default_video_profile_and_immutable_channel_membership(qtbot, tmp_path):
    channels = list(IRIS)
    default = Controller(tmp_path / "default")
    iris = Controller(tmp_path / "iris", video_channels=channels)
    try:
        assert default.video_channels == ("digital", "analog")
        assert default.video_labels == {"digital": "Digital", "analog": "Analog"}
        assert iris.video_channels == IRIS
        assert iris.video_labels == {"digital": "Digital", "analog": "Analog 1", "analog2": "Analog 2"}
        channels.pop()
        assert iris.video_channels == IRIS
        with pytest.raises(AttributeError):
            iris.video_channels = ("digital",)
        with pytest.raises(TypeError):
            iris.video_streams["extra"] = None
        with pytest.raises(TypeError):
            iris.video_labels["analog"] = "Changed"
    finally:
        default.shutdown()
        iris.shutdown()


@pytest.mark.parametrize("channels", [(), ("digital", "digital"), ("unknown",), ("analog",)])
def test_invalid_channel_profiles_rejected_before_creating_data(qtbot, tmp_path, channels):
    directory = tmp_path / "invalid-profile"
    with pytest.raises(ValueError, match="Video channels"):
        Controller(directory, video_channels=channels)
    assert not directory.exists()


def test_station_video_labels_are_copied_and_immutable(qtbot, tmp_path):
    labels = {"digital": "Digital", "analog2": "Booster Analog"}
    c = Controller(tmp_path, video_channels=BOOSTER, video_labels=labels)
    try:
        labels["analog2"] = "Changed"
        assert c.video_labels == {"digital": "Digital", "analog2": "Booster Analog"}
        with pytest.raises(TypeError):
            c.video_labels["analog2"] = "Changed"
    finally:
        c.shutdown()


@pytest.mark.parametrize("labels", [
    {}, {"digital": "Digital"}, {"digital": "Digital", "analog2": "Booster", "analog": "Sustainer"},
    {"digital": "Digital", "analog2": " "}, {"digital": "Digital", "analog2": 2}, ["Digital", "Booster"],
])
def test_invalid_video_labels_rejected_before_creating_data(qtbot, tmp_path, labels):
    directory = tmp_path / "invalid-labels"
    with pytest.raises(ValueError, match="Video labels"):
        Controller(directory, video_channels=BOOSTER, video_labels=labels)
    assert not directory.exists()


def test_booster_receiver_excludes_digital_camera_and_uses_station_label(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.controller.VideoWorker", CameraStub)
    c = Controller(tmp_path, video_channels=BOOSTER,
                   video_labels={"digital": "Digital", "analog2": "Booster Analog"})
    try:
        c.start_video("camera", "0", stream="digital")
        with pytest.raises(ValueError, match="Digital"):
            c.start_video("camera", "0", stream="analog2")
        c.start_video("camera", "1", stream="analog2")
        with pytest.raises(ValueError, match="Booster Analog"):
            c.start_video("camera", "1", stream="digital")
        with pytest.raises(ValueError, match="not active"):
            c.start_video("camera", "2", stream="analog")
        qtbot.waitUntil(lambda: all(state.worker for state in c.video_streams.values()))
        assert tuple(c.video_streams) == BOOSTER
    finally:
        c.shutdown()


def test_third_camera_reservation_survives_pending_close(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.controller.VideoWorker", CameraStub)
    c = Controller(tmp_path, video_channels=IRIS)
    frames, legacy = [], []
    c.video_frame.connect(lambda channel, data: frames.append((channel, data)))
    c.frame.connect(legacy.append)
    gate = threading.Event()
    try:
        for channel, camera in zip(IRIS, ("0", "1", "2")):
            c.start_video("camera", camera, stream=channel)
        with pytest.raises(ValueError, match="Analog 2"):
            c.start_video("camera", "2", stream="analog")
        with pytest.raises(ValueError, match="Analog 1"):
            c.start_video("camera", "1", stream="analog2")
        qtbot.waitUntil(lambda: all(state.worker for state in c.video_streams.values()))
        workers = {channel: state.worker for channel, state in c.video_streams.items()}
        for channel, worker in workers.items():
            worker.frames.put(channel.encode())
        c.tick()
        assert frames == [(channel, channel.encode()) for channel in IRIS]
        assert legacy == [b"digital"]

        c.video_streams["analog2"].executor.submit(lambda: gate.wait(3))
        c.stop_video("analog2")
        with pytest.raises(ValueError, match="Analog 2"):
            c.start_video("camera", "2", stream="digital")
        assert all(c.video_streams[channel].worker is workers[channel] for channel in ("digital", "analog"))
        assert not any(workers[channel].stopped for channel in ("digital", "analog"))
        gate.set()
        qtbot.waitUntil(lambda: not c.video_streams["analog2"].reserved_cameras)
        c.start_video("camera", "2", stream="digital")
        qtbot.waitUntil(lambda: c.video is not None)
        assert c.video.source == "2" and workers["digital"].stopped
        assert workers["analog2"].stopped
    finally:
        gate.set()
        c.shutdown()


def test_digital_only_profile_rejects_inactive_inputs_and_demo_starts_only_digital(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.controller.VideoWorker", CameraStub)
    c = Controller(tmp_path, video_channels=("digital",))
    resets = []
    c.video_reset.connect(resets.append)
    try:
        for channel in ("analog", "analog2", "unknown", ""):
            for operation in (
                lambda: c.start_video("camera", "0", stream=channel),
                lambda: c.stop_video(channel),
                lambda: c.replay_video(channel),
            ):
                with pytest.raises(ValueError, match="not active"):
                    operation()
        with pytest.raises(ValueError, match="active video channel"):
            c.start_video("camera", "0", stream=None)
        assert not c.futures
        c.switch_mode("DEMO")
        qtbot.waitUntil(lambda: c.video is not None)
        assert c.video_channels == ("digital",) and tuple(c.video_streams) == ("digital",)
        assert c.video.kind == "demo" and c.video.source == "digital"
        assert set(resets) == {"digital"}
        assert c.video_config == ("demo", "digital")
        c.switch_mode("LIVE")
        assert c.video_config is None
    finally:
        c.shutdown()


@pytest.mark.parametrize("channels", [IRIS, BOOSTER, ("digital",)])
def test_video_widgets_follow_profile_and_exclude_every_selected_camera(qtbot, tmp_path, channels):
    # Dispose previous native window actions before Python's cyclic collection.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    labels = {"digital": "Digital", "analog2": "Booster Analog"} if channels == BOOSTER else None
    window = MainWindow(tmp_path, video_channels=channels, video_labels=labels)
    qtbot.addWidget(window)
    try:
        assert tuple(window.video_widgets) == channels
        assert tuple(window.last_pixmaps) == channels
        window.task_done("cameras", [("USB receiver", str(index)) for index in range(3)])
        for index, channel in enumerate(channels):
            combo = window.video_widgets[channel]["camera"]
            combo.setCurrentIndex(combo.findData(str(index)))
            assert combo.currentData() == str(index)
        for index, channel in enumerate(channels):
            widgets = window.video_widgets[channel]
            combo = widgets["camera"]
            assert combo.accessibleName() == f"{window.controller.video_labels[channel]} USB camera"
            choices = {combo.itemData(i) for i in range(combo.count())}
            assert all(str(other) not in choices for other in range(len(channels)) if other != index)
            window.reset_video_frame(channel)
            assert widgets["image"].text() == f"{window.controller.video_labels[channel].upper()} · NO VIDEO"
        window.update_camera_choices()
        assert window.camera is window.video_widgets["digital"]["camera"]
    finally:
        window.close()


@pytest.mark.parametrize("channels", [IRIS, BOOSTER])
def test_video_profile_recording_round_trip_and_single_channel_replay(qtbot, tmp_path, channels):
    labels = {"digital": "Digital", "analog2": "Booster Analog"} if channels == BOOSTER else None
    c = Controller(tmp_path / "iris", video_channels=channels, video_labels=labels)
    base = Controller(tmp_path / "base", video_channels=("digital",))
    first_frames = {}
    c.video_frame.connect(lambda channel, data: first_frames.setdefault(channel, data))

    def all_frames():
        return all(state.worker is not None and state.worker.received_frames > 3
                   for state in c.video_streams.values())

    try:
        # Real bundled FFmpeg generators exercise the selected encoders without USB hardware.
        for channel in channels:
            c.start_video("demo", channel, stream=channel)
        qtbot.waitUntil(all_frames, timeout=10000)
        qtbot.waitUntil(lambda: set(first_frames) == set(channels), timeout=10000)
        assert len(set(first_frames.values())) == len(channels)
        c.start_recording(tmp_path / "sessions")
        qtbot.waitUntil(all_frames, timeout=10000)
        qtbot.wait(800)
        c.stop_video("analog2")
        assert all(c.video_streams[channel].worker for channel in channels if channel != "analog2")
        c.stop_recording()
        archive = tmp_path / "iris-video-profile.rktflight"
        c.save_flight(archive)
        qtbot.waitUntil(lambda: not c.flight_busy, timeout=10000)
        assert archive.exists()
        c.open_flight(archive)
        qtbot.waitUntil(lambda: not c.flight_busy, timeout=10000)
        assert c.reader is not None and c.reader.manifest["complete"]
        starts = [(t, event) for t, event in c.reader.events(c.reader.duration)
                  if event["name"] == "Video recording started"]
        assert {event["data"]["stream"] for _, event in starts} == set(channels)
        assert len(list(c.reader.path.glob("video*/segments.csv"))) == len(channels)
        metadata = json.loads((c.reader.path / "manifest.json").read_text())
        assert metadata["flight_video_segments"] == len(channels)
        cursor = max(t for t, _ in starts) + 0.2
        c.seek(cursor)
        qtbot.waitUntil(lambda: all(state.worker is not None and state.worker.received_frames > 0
                                   for state in c.video_streams.values()), timeout=10000)
        other_channel = "analog" if "analog" in channels else "digital"
        other_worker = c.video_streams[other_channel].worker
        assert all(state.worker.single_frame for state in c.video_streams.values())
        c.stop_video("analog2", pause_replay=True)
        c.replay_video(force=False)
        assert c.video_streams["analog2"].config is None
        assert c.video_streams[other_channel].worker is other_worker
        c.replay_video("analog2")
        qtbot.waitUntil(lambda: c.video_streams["analog2"].worker is not None)

        base.open_flight(archive)
        qtbot.waitUntil(lambda: not base.flight_busy, timeout=10000)
        base.seek(cursor)
        qtbot.waitUntil(lambda: base.video is not None and base.video.received_frames > 0, timeout=10000)
        assert tuple(base.video_streams) == ("digital",)
        assert len(list(base.reader.path.glob("video*/segments.csv"))) == len(channels)
        assert base.video.single_frame and "video_digital_" in base.video.source
        assert not any(base.workers.values())
    finally:
        c.shutdown()
        base.shutdown()
