"""DirectShow receiver identities remain independent of their friendly names."""

import queue
from types import SimpleNamespace

import pytest

from rocket_gnc_monitor.controller import Controller
from rocket_gnc_monitor.media import VideoWorker, _directshow_cameras, camera_devices


FIRST = r"@device_pnp_\\?\usb#vid_534d&pid_2109#receiver_a#{capture}"
SECOND = r"@device_pnp_\\?\usb#vid_534d&pid_2109#receiver_b#{capture}"
LISTING = f'''[dshow @ 000001] "USB Video" (video)
[dshow @ 000001]   Alternative name "{FIRST}"
[dshow @ 000001] "USB Video" (video)
[dshow @ 000001]   Alternative name "{SECOND}"
[dshow @ 000001] "USB Audio" (audio)
[dshow @ 000001]   Alternative name "@device_cm_audio_receiver"
Error opening input file dummy.
'''


def test_duplicate_friendly_names_keep_distinct_directshow_identities(monkeypatch):
    calls = []

    def enumerate_devices(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stderr=LISTING.encode(), returncode=1)

    monkeypatch.setattr("rocket_gnc_monitor.media.platform.system", lambda: "Windows")
    monkeypatch.setattr("rocket_gnc_monitor.media.subprocess.run", enumerate_devices)
    assert camera_devices() == [("USB Video", FIRST), ("USB Video", SECOND)]
    assert calls[0][0][-6:] == ["-f", "dshow", "-list_devices", "true", "-i", "dummy"]


def test_directshow_fallback_orphan_audio_and_duplicate_identity_handling():
    lines = [
        '[dshow] Alternative name "orphan"',
        '[dshow] "Fallback camera" (video)',
        '[dshow] "Microphone" (audio)',
        '[dshow] Alternative name "microphone-id"',
        '[dshow] "Primary camera" (video)',
        '[dshow] Alternative name "camera-id"',
        '[dshow] "Alias camera" (video)',
        '[dshow] Alternative name "camera-id"',
        '[dshow] "Fallback camera" (video)',
        'Unrelated diagnostic line',
        '[dshow] Alternative name "not-a-camera-id"',
    ]
    assert _directshow_cameras(lines) == [("Fallback camera", "Fallback camera"), ("Primary camera", "camera-id")]


def test_audio_only_and_empty_directshow_lists_have_no_cameras():
    assert _directshow_cameras([]) == []
    assert _directshow_cameras([
        '[dshow] "Microphone" (audio)', '[dshow] Alternative name "audio-id"',
    ]) == []


def test_mac_camera_indexes_and_screen_filter_remain_unchanged(monkeypatch):
    listing = b'''[AVFoundation] AVFoundation video devices:
[AVFoundation] [0] USB Video
[AVFoundation] [1] USB Video
[AVFoundation] [2] Capture screen 0
[AVFoundation] AVFoundation audio devices:
[AVFoundation] [0] Microphone
'''
    monkeypatch.setattr("rocket_gnc_monitor.media.platform.system", lambda: "Darwin")
    monkeypatch.setattr("rocket_gnc_monitor.media.subprocess.run", lambda *a, **k: SimpleNamespace(stderr=listing))
    assert camera_devices() == [("USB Video", "0"), ("USB Video", "1")]


def test_distinct_directshow_ids_can_open_together_but_one_id_cannot_be_reused(qtbot, tmp_path, monkeypatch):
    def open_video(kind, source, *_):
        return SimpleNamespace(kind=kind, source=source, frames=queue.Queue(), error="", last_frame=0, stop=lambda: None)

    monkeypatch.setattr("rocket_gnc_monitor.controller.VideoWorker", open_video)
    cameras = _directshow_cameras(LISTING.splitlines())
    c = Controller(tmp_path)
    try:
        c.start_video("camera", cameras[0][1], stream="digital")
        c.start_video("camera", cameras[1][1], stream="analog")
        qtbot.waitUntil(lambda: all(state.worker for state in c.video_streams.values()))
        assert c.video.source == FIRST and c.video_streams["analog"].worker.source == SECOND
        with pytest.raises(ValueError, match="Digital"):
            c.start_video("camera", FIRST, stream="analog")
        with pytest.raises(ValueError, match="Analog"):
            c.start_video("camera", SECOND, stream="digital")
    finally:
        c.shutdown()


def test_directshow_alternative_identifier_reaches_ffmpeg_unchanged(monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.media.platform.system", lambda: "Windows")
    worker = object.__new__(VideoWorker)
    worker.kind, worker.source = "camera", FIRST
    worker.single_frame, worker.record_dir = False, None
    arguments = worker.arguments()
    assert arguments[arguments.index("-i") + 1] == "video=" + FIRST
