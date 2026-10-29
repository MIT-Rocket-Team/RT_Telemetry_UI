"""Ordinary Base-mode clients receiving OpenRocket-owned Away services."""
import base64
import hashlib
import socket
import time
import uuid

import pytest
from PySide6.QtGui import QColor, QImage

from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.mission_network import MissionNetwork
from rocket_gnc_monitor.network import NetworkRuntime
from rocket_gnc_monitor.network_bridge import validate_simulation_streams
from rocket_gnc_monitor.network_ui import StationNetworkDialog
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def mission(streams=()):
    return {'identity': {'missionId': str(uuid.uuid4()), 'title': 'OpenRocket network test'},
            'sites': [{'id': 'gs1', 'enabled': True}], 'video': {'streams': list(streams)},
            'network': {'bindHost': '127.0.0.1', 'video': bool(streams), 'vehicleMode': 'balius'}}


def base(qtbot, tmp_path, role):
    window = StationWindow(tmp_path, profile=StationProfile(role=role), auto_place=False,
        network_config={'network_bind_host': '127.0.0.1', 'network_port': port(),
                        'network_discovery': False, 'mission_id': None, 'peers': []})
    qtbot.addWidget(window)
    window.show()
    assert window.controller.mode == 'LIVE'
    return window


def join(qtbot, window, service):
    bridge = window.station_network
    bridge.runtime.add_endpoint(service.local_endpoint)
    qtbot.waitUntil(lambda: any(p.get('info', {}).get('node_id') == service.node_id for p in bridge.peers), timeout=6000)
    dialog = StationNetworkDialog(bridge, window)
    qtbot.addWidget(dialog)
    dialog.refresh()
    row = next(i for i, p in enumerate(dialog._rows) if p.get('info', {}).get('node_id') == service.node_id)
    dialog.table.selectRow(row)
    assert 'SIMULATION' in dialog.table.item(row, 0).text()
    dialog.join_selected()
    qtbot.waitUntil(lambda: any(p.get('synchronized') for p in bridge.peers), timeout=7000)
    dialog.close()
    assert bridge.runtime.accept_source_modes == ['simulation']


def test_normal_base_joins_simulation_receives_exact_bytes_and_pauses(qtbot, tmp_path):
    publisher = MissionNetwork(tmp_path / 'publisher')
    window = None
    try:
        configured = mission()
        publisher.configure(configured, discovery=False)
        service = publisher.services['gs1', 'telemetry'][0]
        window = base(qtbot, tmp_path / 'base', 'telemetry')
        join(qtbot, window, service)
        sample = Sample(t=1, sequence=1, source='SIMULATION', received=1, altitude=120,
                        details={'codec': 'custom.test', 'packetId': 'tx-1'})
        raw = b'precise received frame'
        publisher.begin([])
        publisher.advance('1000000', [{'id': 'gs1', 'deliveries': [{'packetId': 'tx-1', 'data': base64.b64encode(raw).decode()}],
                           'videoState': [], 'pointerPose': {'azimuthDeg': 42}}],
                          [{'id': 'gs1', 'samples': [sample.to_dict()], 'tracking': True, 'rejected': 0}])
        qtbot.waitUntil(lambda: bool(window.station_network._history.get(service.node_id)), timeout=5000)
        displayed = window.controller.display_latest
        assert displayed.altitude == 120
        assert 'SIMULATION' in displayed.details['display_source']
        assert window.controller.latest is None  # No route to local hardware control.
        assert not window.controller.can_command
        peers = window.station_network.runtime.peers_snapshot()
        received = next(p for p in peers if p.get('info', {}).get('node_id') == service.node_id)['latest'][0]
        assert received['packet']['raw_b64'] == base64.b64encode(raw).decode()
        assert received['values']['native']['details']['missionTimeUs'] == '1000000'
        publisher.phase('paused')
        qtbot.wait(1300)
        assert len(window.station_network._history[service.node_id]) == 1
        assert service.boot_id == received['observation_id'].split(':')[1]
        assert any(p['state'] == 'ONLINE' for p in window.station_network.peers)
        publisher.phase('running')
    finally:
        if window:
            window.close()
        publisher.close()


def test_normal_base_video_receives_custom_slate_via_leased_srt(qtbot, tmp_path):
    image = QImage(960, 540, QImage.Format.Format_RGB888)
    image.fill(QColor(220, 35, 25))
    path = tmp_path / 'digital-2.png'
    assert image.save(str(path))
    stream = {'id': 'digital-2', 'kind': 'digital', 'vehicleId': 'balius', 'receivers': ['gs1'],
              'assetPath': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    publisher = MissionNetwork(tmp_path / 'publisher')
    window = None
    try:
        configuration = mission([stream])
        configuration['outputs'] = {'recordEncodedVideo': True}
        publisher.configure(configuration, discovery=False)
        publisher.begin([stream])
        assert publisher.services['gs1', 'video'][1].streams()[0]['vehicle'] == 'balius'
        assert 'assetPath' not in publisher.services['gs1', 'video'][0].info()['simulation']['streams'][0]
        window = base(qtbot, tmp_path / 'base', 'video')
        join(qtbot, window, publisher.services['gs1', 'video'][0])
        for count in range(170):
            publisher.advance(str(count * 33333), [{'id': 'gs1', 'deliveries': [], 'videoState': [
                {'streamId': 'digital-2', 'frame': str(count), 'timeUs': str(count * 33333)}]}],
                [{'id': 'gs1', 'samples': [], 'tracking': False}])
            qtbot.wait(34)
        workers = window.station_network.viewer.workers
        assert workers and any(w.received_frames > 5 for w in workers.values()), {k: (w.error, list(w.logs), w.received_frames) for k, w in workers.items()}
        pixels = window.video_wall.primary_views['digital-2'].pixmap().toImage()
        assert not pixels.isNull() and pixels.pixelColor(10, 10).red() > 180
        boot = publisher.services['gs1', 'video'][0].boot_id
        epoch = publisher.services['gs1', 'video'][1].streams()[0]['media_epoch']
        publisher.phase('paused')
        qtbot.wait(1400)
        assert any(w.last_frame and time.monotonic() - w.last_frame < 1 for w in workers.values())
        publisher.phase('running')
        assert publisher.services['gs1', 'video'][0].boot_id == boot
        assert publisher.services['gs1', 'video'][1].streams()[0]['media_epoch'] == epoch
        saved = publisher.finish()['files']
        assert any(f['name'].endswith('.mkv') and __import__('pathlib').Path(f['path']).stat().st_size > 1000 for f in saved)
        # Leaving returns the original ordinary video wall and its local input controls.
        window.station_network.apply({**window.station_network.config, 'receive_source_mode': 'live', 'mission_id': None})
        assert 'digital' in window.video_wall.primary_views
    finally:
        if window:
            window.close()
        publisher.close()


def test_stream_metadata_is_bounded_and_checked():
    with pytest.raises(ValueError):
        validate_simulation_streams([{}] * 65)
    with pytest.raises(ValueError):
        validate_simulation_streams([{'id': '../file', 'kind': 'digital', 'vehicleId': 'balius', 'receivers': ['gs1']}])


def test_scoped_slot_conflicts_do_not_mix_live_and_simulated_missions(tmp_path):
    runtime = NetworkRuntime(tmp_path, StationProfile(), str(uuid.uuid4()), {'network_discovery': False})
    template = dict(station='gs1', role='telem', vehicle_mode='balius', source_mode='live',
                    node_id=str(uuid.uuid4()), boot_id=str(uuid.uuid4()), mission_id=str(uuid.uuid4()))
    runtime._peers = {str(i): {'info': {**template, **change}, 'state': 'CONNECTING',
                    '_last_seen': time.monotonic(), 'endpoint': str(i)} for i, change in enumerate([
        {}, {'node_id': str(uuid.uuid4()), 'source_mode': 'simulation'},
        {'node_id': str(uuid.uuid4()), 'mission_id': str(uuid.uuid4())}])}
    runtime._refresh_public()
    assert not any(p.get('conflict') for p in runtime._peers.values())
    runtime._peers['3'] = {'info': {**template, 'node_id': str(uuid.uuid4())},
                           'state': 'CONNECTING', '_last_seen': time.monotonic(), 'endpoint': '3'}
    runtime._refresh_public()
    assert runtime._peers['0']['conflict'] and runtime._peers['3']['conflict']


@pytest.mark.skipif(__import__('os').environ.get('RUN_MISSION_DISCOVERY') != '1', reason='Explicit local-LAN multicast qualification')
def test_mdns_discovers_openrocket_without_any_manual_endpoint(qtbot, tmp_path):
    publisher = MissionNetwork(tmp_path / 'publisher')
    receiver = None
    try:
        config = mission()
        config['network']['bindHost'] = '0.0.0.0'
        publisher.configure(config, discovery=True)
        source = publisher.services['gs1', 'telemetry'][0]
        receiver = NetworkRuntime(tmp_path / 'base', StationProfile(), config['identity']['missionId'],
            {'network_bind_host': '0.0.0.0', 'network_port': 0, 'network_discovery': True,
             'network_accept_source_modes': ['simulation']})
        receiver.start()
        qtbot.waitUntil(lambda: any(p.get('info', {}).get('node_id') == source.node_id and p.get('synchronized')
                                   for p in receiver.peers_snapshot()), timeout=20000)
        assert not receiver._endpoints
    finally:
        if receiver:
            receiver.stop()
        publisher.close()
