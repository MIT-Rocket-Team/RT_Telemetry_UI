import asyncio
import threading
import time
import uuid

import pytest

from rocket_gnc_monitor.media import WIDTH, HEIGHT
from rocket_gnc_monitor.network.media import MediaError, MediaService
from rocket_gnc_monitor.network.media_receiver import MediaReceiver
from rocket_gnc_monitor.network.media_srt import SRTSocket, library
from rocket_gnc_monitor.network.runtime import NetworkRuntime
from rocket_gnc_monitor.station_profile import StationProfile


class Capture:
    kind = "camera"
    error = ""

    def __init__(self):
        self.listeners = set()

    def add_frame_listener(self, callback):
        self.listeners.add(callback)

    def remove_frame_listener(self, callback):
        self.listeners.discard(callback)

    def emit(self, data):
        for callback in tuple(self.listeners):
            callback(data, time.monotonic())


def require_native():
    try:
        library()
    except OSError as exc:
        pytest.skip(str(exc))


def test_srt_requires_exact_token_and_only_one_receiver():
    require_native()
    server = SRTSocket(sender=True)
    client = SRTSocket()
    wrong = SRTSocket()
    accepted = []
    server.listen("127.0.0.1", 55920, "rt1:test:secret")
    try:
        with pytest.raises(OSError):
            wrong.connect("127.0.0.1", 55920, "rt1:test:wrong")
        thread = threading.Thread(target=lambda: accepted.append(server.accept()), daemon=True)
        thread.start()
        client.connect("127.0.0.1", 55920, "rt1:test:secret")
        thread.join(3)
        assert len(accepted) == 1
        payload = b"transport verified".ljust(188, b".")
        for _ in range(3):
            accepted[0].send(payload)
            time.sleep(.1)
        assert client.receive() == payload
        with pytest.raises(OSError):
            wrong.connect("127.0.0.1", 55920, "rt1:test:secret")
    finally:
        for item in accepted + [client, wrong, server]:
            item.close()


def test_media_request_lifecycle_and_camera_restart():
    require_native()
    service = MediaService(StationProfile(station="away1", role="video"), port_start=55921, port_end=55925)
    source = Capture()
    service.set_source("digital", source)
    source.emit(bytes(WIDTH * HEIGHT * 3))
    mission, boot = str(uuid.uuid4()), str(uuid.uuid4())
    context = dict(mission_id=mission, boot_id=boot, host="127.0.0.1", source_mode="live")
    stream = service.streams()[0]
    request = dict(request_id=str(uuid.uuid4()), client_node_id=str(uuid.uuid4()), client_boot_id=str(uuid.uuid4()),
                   mission_id=mission, expected_server_boot_id=boot, stream_id=stream["stream_id"],
                   rendition="preview", expected_media_epoch=stream["media_epoch"])
    try:
        response = asyncio.run(service.create_session(request, context))
        assert asyncio.run(service.create_session(request, context)) == response
        with pytest.raises(MediaError) as error:
            asyncio.run(service.create_session(dict(request, rendition="main"), context))
        assert error.value.status == 409
        with pytest.raises(MediaError) as error:
            asyncio.run(service.renew_session(response["session_id"], {"lease_ms": 30000}, context))
        assert error.value.status == 403
        authorized = dict(context, session_token=response["session_token"])
        assert asyncio.run(service.renew_session(response["session_id"], {"lease_ms": 30000}, authorized)) == {"lease_ms": 30000}
        asyncio.run(service.delete_session(response["session_id"], authorized))
        asyncio.run(service.delete_session(response["session_id"], authorized))
        with pytest.raises(MediaError) as error:
            asyncio.run(service.create_session(request, context))
        assert error.value.status == 410
        service.set_source("digital", Capture())
        assert service.streams()[0]["media_epoch"] != stream["media_epoch"]
        assert not source.listeners
    finally:
        service.close()


def test_real_h264_mpegts_srt_http_loopback(tmp_path):
    require_native()
    profile = StationProfile(station="away1", role="video")
    service = MediaService(profile, port_start=55926, port_end=55930)
    runtime = NetworkRuntime(tmp_path, profile, str(uuid.uuid4()),
                             {"network_port": 0, "network_bind_host": "127.0.0.1", "network_discovery": False},
                             media_session_backend=service)
    service.notify = runtime.update_streams
    capture = Capture()
    service.set_source("digital", capture)
    stopped = threading.Event()
    frame = bytes([220, 30, 20]) * (WIDTH * HEIGHT)

    def produce():
        while not stopped.wait(1 / 30):
            capture.emit(frame)

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    runtime.start()
    receivers = []
    try:
        deadline = time.monotonic() + 8
        while (not runtime.local_endpoint or service.channels["digital"].last_frame is None) and time.monotonic() < deadline:
            time.sleep(.03)
        assert runtime.local_endpoint, runtime.error
        identity = runtime.info()
        for rendition in ("preview", "main"):
            receivers.append(MediaReceiver(runtime.local_endpoint, identity, identity, service.streams()[0], rendition))
        deadline = time.monotonic() + 15
        while not all(worker.received_frames >= 3 for worker in receivers) and time.monotonic() < deadline:
            time.sleep(.05)
        assert all(worker.received_frames >= 3 for worker in receivers), [
            (worker.received_frames, worker.error, list(worker.logs)) for worker in receivers]
        assert len(capture.listeners) == 1
        assert len(service.channels["digital"].encoders) == 2
        for rendition in service.streams()[0]["renditions"]:
            timing = rendition["timing"]
            assert timing and int(timing["pts_90k"]) >= 0
            assert int(timing["capture_frame_id"]) > 0
            assert timing["capture_timing_basis"] == "host_frame_delivery_after_decode"
            assert timing["capture_delay_uncertainty_ms"] is None
        for worker in receivers:
            data, _ = worker.frames.get_nowait()
            assert len(data) == WIDTH * HEIGHT * 3
            assert data[0] > 180 and data[1] < 60 and data[2] < 60
            assert worker.frames.qsize() <= 2
    finally:
        for worker in receivers:
            worker.stop()
        stopped.set()
        service.close()
        runtime.stop()
