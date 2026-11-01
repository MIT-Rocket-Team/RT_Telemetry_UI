"""A saved dead endpoint must not blank the four-station video wall."""
import hashlib
import socket
import uuid

from PySide6.QtGui import QColor, QImage

from rocket_gnc_monitor.mission_network import MissionNetwork
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow


def test_all_eight_real_video_feeds_survive_unreachable_saved_peer(qtbot, tmp_path):
    image = QImage(960, 540, QImage.Format.Format_RGB888)
    image.fill(QColor(35, 210, 25))
    path = tmp_path / "slate.png"
    assert image.save(str(path))
    stations = [f"gs{i}" for i in range(1, 5)]
    streams = [dict(id=f"balius.{kind}", kind=kind, vehicleId="balius", receivers=stations,
                    assetPath=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
               for kind in ("digital", "analog")]
    publisher = MissionNetwork(tmp_path / "publisher")
    window = None
    # Bound but not listening: keep the dead port unavailable to other tests.
    with socket.socket() as dead:
        dead.bind(("127.0.0.1", 0))
        try:
            publisher.configure(dict(identity=dict(missionId=str(uuid.uuid4()), title="Video regression"),
                sites=[dict(id=s, enabled=True) for s in stations], video=dict(streams=streams),
                network=dict(bindHost="127.0.0.1", video=True, vehicleMode="balius")), discovery=False)
            publisher.begin(streams)
            publisher.advance("0", [dict(id=s, deliveries=[], videoState=[
                dict(streamId=stream["id"], frame="0", timeUs="0") for stream in streams]) for s in stations],
                [dict(id=s, samples=[], tracking=False) for s in stations])
            publisher.phase("paused")
            services = [publisher.services[s, "video"][0] for s in stations]
            window = StationWindow(tmp_path / "base", profile=StationProfile(role="video"), auto_place=False,
                # Let the service reserve its own port atomically; the eight
                # publisher services can reuse a port selected then released here.
                network_config=dict(network_bind_host="127.0.0.1", network_port=0, mission_simulation=True,
                    network_discovery=False, mission_id=None,
                    peers=[f"http://127.0.0.1:{dead.getsockname()[1]}", *[s.local_endpoint for s in services]]))
            qtbot.addWidget(window)
            window.show()
            bridge = window.station_network
            bridge.join_peer(dict(endpoint=services[0].local_endpoint, info=services[0].info()))
            def all_decoded():
                return all(feed.available for key, feed in window.video_wall.feeds.items() if key[0] != "local")
            qtbot.waitUntil(all_decoded, timeout=25000)
            assert any(p.get("info") is None for p in bridge.runtime.peers_snapshot())
            assert len(window.video_wall.thumbnails) == 8
            for tile in window.video_wall.thumbnails.values():
                assert tile.image.pixmap().toImage().pixelColor(10, 10).green() > 180
            for view in window.video_wall.primary_views.values():
                assert view.pixmap().toImage().pixelColor(10, 10).green() > 180
        finally:
            if window:
                window.close()
            publisher.close()
