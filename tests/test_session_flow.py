from rocket_gnc_monitor.controller import Controller
from recorded_flight import video_fixture, sample_fixture


def test_record_stop_replay_and_paused_video_seek(qtbot, tmp_path):
    c = Controller(tmp_path)
    try:
        c.start_video("file", video_fixture(tmp_path))
        qtbot.waitUntil(lambda: c.video is not None and c.video.received_frames > 3, timeout=5000)
        path = c.start_recording(tmp_path)
        for index in range(20):
            c.accept(sample_fixture(index * 0.05, index), record=True)
            qtbot.wait(80)
        c.stop_recording()
        qtbot.waitUntil(lambda: not any(name == "recording_closed" for name, _ in c.futures), timeout=6000)
        c.open_replay(path)
        assert c.reader.count > 10 and c.reader.manifest["complete"]
        c.seek(c.reader.duration * 0.5)
        qtbot.waitUntil(lambda: c.video is not None and c.video.received_frames > 0, timeout=6000)
        assert c.video.single_frame and not c.replay_playing
        assert not any(c.workers.values())
        assert list(path.glob("video*/segments.csv"))
        assert c.latest.source == "LIVE"
    finally:
        c.shutdown()
