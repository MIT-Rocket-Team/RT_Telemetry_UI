import queue
import time
import uuid

from rocket_gnc_monitor.media import HEIGHT, WIDTH
from rocket_gnc_monitor.network.media import MediaService
from rocket_gnc_monitor.network.media_receiver import VideoWallNetwork
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.video_wall import VideoWall


class Receiver:
    def __init__(self, endpoint, identity, peer, stream, rendition):
        self.frames = queue.Queue(maxsize=2)
        self.last_frame = None
        self.error = "Connecting"
        self.stopped = False

    def deliver(self):
        self.last_frame = time.monotonic()
        self.error = ""
        self.frames.put_nowait((bytes(WIDTH * HEIGHT * 3), self.last_frame))

    def stop(self):
        self.stopped = True


def test_all_eight_previews_and_transactional_main_promotion(qtbot, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.network.media_receiver.MediaReceiver", Receiver)
    mission = str(uuid.uuid4())
    peers = []
    for index in range(1, 5):
        service = MediaService(StationProfile(station=f"away{index}", role="video", vehicle="iris"))
        streams = service.streams()
        service.close()
        peers.append(dict(endpoint=f"http://192.0.2.{index}:8766", state="ONLINE", streams=streams,
                          info=dict(station=f"gs{index}", role="video", mission_id=mission, source_mode="live",
                                    node_id=str(uuid.uuid4()), boot_id=str(uuid.uuid4()))))

    class Runtime:
        mission_id = mission

        def info(self):
            return dict(node_id=str(uuid.uuid4()), boot_id=str(uuid.uuid4()), mission_id=mission)

        def peers_snapshot(self):
            return peers

    wall = VideoWall(StationProfile(station="base", role="video", vehicle="iris"))
    qtbot.addWidget(wall)
    manager = VideoWallNetwork(Runtime(), wall)
    manager.timer.stop()
    try:
        manager.refresh()
        assert sum(key[2] == "preview" for key in manager.workers) == 8
        assert len(wall.thumbnails) == 8
        old = wall.selected_sources["digital"]
        wall.select_remote("away2", "digital")
        assert wall.selected_sources["digital"] == old
        manager.refresh()
        main = manager.workers[("away2", "digital", "main")]
        main.deliver()
        manager.refresh()
        assert wall.selected_sources["digital"] == ("away2", "digital")
        assert ("away2", "digital", "preview") not in manager.workers
        assert "Main" in wall.primary_status["digital"].text()
        wall.select_remote("away3", "digital")
        manager.refresh()
        failed = manager.workers[("away3", "digital", "main")]
        failed.error = "Capacity unavailable"
        manager.refresh()
        assert wall.selected_sources["digital"] == ("away2", "digital")
        assert "previous source retained" in wall.primary_status["digital"].text()
        wall.select_local_digital()
        assert wall.selected_sources["digital"] == ("local", "digital")
    finally:
        manager.close()
