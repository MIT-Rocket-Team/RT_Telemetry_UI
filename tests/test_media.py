import time
from rocket_gnc_monitor.media import VideoWorker, WIDTH, HEIGHT


def test_video_records_even_when_display_queue_is_not_consumed(tmp_path):
    worker = VideoWorker("demo", record_dir=tmp_path / "video")
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
