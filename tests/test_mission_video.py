import hashlib
import time
import uuid

from PySide6.QtGui import QColor, QImage

from rocket_gnc_monitor.mission_application import MissionController
from rocket_gnc_monitor.mission_video import MissionCapture, MissionProfile, MissionVideoPublisher
from rocket_gnc_monitor.station_workspace import StationWindow
from rocket_gnc_monitor.video_wall import VideoWall


def slate(tmp_path, identity='digital-2', kind='digital'):
    path = tmp_path / (identity + '.png')
    image = QImage(960, 540, QImage.Format.Format_RGB888)
    image.fill(QColor(220, 35, 25))
    assert image.save(str(path))
    return dict(id=identity, kind=kind, ordinal=2, vehicleId='balius', receivers=['gs1'],
                assetPath=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def test_zero_and_arbitrary_registry_and_pause_carriers(qtbot, tmp_path):
    for streams in ((), (slate(tmp_path), slate(tmp_path, 'analog-3', 'analog'))):
        wall = VideoWall(MissionProfile(station='base', role='video', mission_streams=streams))
        qtbot.addWidget(wall)
        assert tuple(wall.primary_views) == tuple(s['id'] for s in streams)
    source = MissionCapture(slate(tmp_path))
    source.state = dict(timeUs='0', frame='0')
    source.time_us = 0
    assert source.publish(time.monotonic())['pauseCarrier'] is False
    assert source.publish(time.monotonic()) is None  # Static pixels do not create logical frames.
    source.paused = True
    assert source.publish(time.monotonic())['pauseCarrier'] is True
    source.paused = False
    source.time_us = 6_000_000
    assert source.publish(time.monotonic()) is None


def test_mission_capture_reaches_real_base_wall_over_srt_and_recovers(qtbot, tmp_path):
    streams = (slate(tmp_path),)
    mission_id = str(uuid.uuid4())
    windows = []
    publishers = []
    try:
        for site, station in [('gs1', 'away1'), ('launch', 'base')]:
            profile = MissionProfile(station=station, role='video', mission_streams=streams)
            manifest = dict(station=dict(id=site, origin=[42, -77, 250], owner='observer', pointerEnabled=False),
                            videoStreams=streams, role='video')
            window = StationWindow(tmp_path / site, profile=profile, auto_place=False,
                network_config=dict(network_bind_host='127.0.0.1', network_port=0, network_discovery=False,
                                    network_accept_source_modes=['simulation'], mission_simulation=True,
                                    mission_id=mission_id, peers=[]),
                controller_factory=lambda *a, m=manifest, **kw: MissionController(*a, manifest=m, **kw))
            qtbot.addWidget(window)
            window.show()
            windows.append(window)
            publishers.append(MissionVideoPublisher(window, streams, tmp_path / site))
        away, base = windows
        qtbot.waitUntil(lambda: bool(away.station_network.runtime.local_endpoint), timeout=5000)
        base.station_network.runtime.add_endpoint(away.station_network.runtime.local_endpoint)
        frame = 0

        def produce(duration):
            nonlocal frame
            until = time.monotonic() + duration
            while time.monotonic() < until:
                frame += 1
                publishers[0].update(frame * 33333, [dict(streamId='digital-2', timeUs=str(frame * 33333), frame=str(frame))])
                qtbot.wait(34)

        produce(5)
        viewers = base.station_network.viewer.workers
        assert viewers and any(v.received_frames > 5 for v in viewers.values()), {k: v.error for k, v in viewers.items()}
        tile = base.video_wall.primary_views['digital-2']
        assert tile.pixmap() and not tile.pixmap().isNull()
        pixel = tile.pixmap().toImage().pixelColor(10, 10)
        assert pixel.red() > 180 and pixel.green() < 80
        base.grab().save('/tmp/mission-video-wall.png')
        # No new logical frame means no carrier: the real five-second watchdog fires.
        qtbot.wait(6500)
        assert any(v.error for v in viewers.values())
        produce(6)
        assert any(v.last_frame and time.monotonic() - v.last_frame < 2 for v in base.station_network.viewer.workers.values())
        assert (tmp_path / 'launch' / 'wall-timing.jsonl').stat().st_size > 0
    finally:
        for publisher in publishers:
            publisher.close()
        for window in windows:
            window.close()
