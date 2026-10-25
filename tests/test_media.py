import time
import json
from rocket_gnc_monitor.media import VideoWorker, WIDTH, HEIGHT
from recorded_flight import video_fixture


def test_video_records_even_when_display_queue_is_not_consumed(tmp_path):
    worker = VideoWorker("file", video_fixture(tmp_path), record_dir=tmp_path / "video")
    deadline = time.monotonic() + 6
    try:
        while worker.received_frames < 35 and not worker.error and time.monotonic() < deadline:
            time.sleep(0.03)
        assert not worker.error
        assert worker.received_frames >= 35
        assert worker.frames.qsize() <= 2
        assert len(worker.frames.get_nowait()) == WIDTH * HEIGHT * 3
    finally:
        worker.stop()
    assert list((tmp_path / "video").glob("*.mkv"))
    assert (tmp_path / "video" / "segments.csv").read_text().strip()
    metadata = [json.loads(line) for line in (tmp_path / "video" / "capture-timing.jsonl").read_text().splitlines()]
    assert metadata[0]["source_mode"] == "replay"
    assert any(item["kind"] == "capture.frame" for item in metadata)


def test_recording_disk_error_does_not_stop_capture(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("recording destination is unavailable")
    worker = VideoWorker("file", video_fixture(tmp_path), record_dir=blocked)
    try:
        deadline = time.monotonic() + 6
        while worker.received_frames < 35 and not worker.error and time.monotonic() < deadline:
            time.sleep(.03)
        assert worker.received_frames >= 35
        assert not worker.error
        assert worker.recording_error
    finally:
        worker.stop()
