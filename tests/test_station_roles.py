"""Station identity and vehicle channel profiles shape the visible role workspace."""

import gc

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QPixmap

from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow
from rocket_gnc_monitor.wifi import WifiSnapshot


@pytest.fixture(autouse=True)
def offline_wifi_status(monkeypatch):
    monkeypatch.setattr('rocket_gnc_monitor.wifi_panel.read_wifi_status', WifiSnapshot)


@pytest.mark.parametrize('site,vehicle,channels', [
    ('away1', 'balius', {'digital', 'analog'}),
    ('away2', 'iris', {'digital', 'analog'}),
    ('away4', 'iris', {'digital', 'analog2'}),
    ('base', 'balius', {'digital'}),
    ('base', 'iris', {'digital'}),
])
def test_video_role_uses_only_its_local_receivers_and_remote_wall(qtbot, tmp_path, monkeypatch, site, vehicle, channels):
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    profile = StationProfile(station=site, role='video', vehicle=vehicle)
    w = StationWindow(tmp_path, profile=profile, auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        qtbot.wait(50)
        assert w.station_mode == 'video' and not w.companion.isVisible()
        assert set(w.controller.video_streams) == channels
        assert set(w.video_widgets) == channels
        assert w.station_identity.isVisible() and not w.station_selector.isVisible()
        assert profile.station_label in w.station_identity.text()
        assert not any(widget.isVisible() for widget in
                       (w.usb_strip, w.poll_button, w.health, w.banner, *w.metrics.values()))
        assert not any(w.controller.workers.values())
        assert not any(state.worker for state in w.controller.video_streams.values())
        assert w.controller.mode == 'LIVE'
        attempted = []
        monkeypatch.setattr(w.controller, 'connect', lambda *args: attempted.append(args))
        for role, key in [('telemetry', 'ground'), ('pointer', 'pointer')]:
            if role in w.port_widgets:
                w.port_widgets[role][0].addItem('Test board', '/test/serial')
                w.port_widgets[role][0].setCurrentIndex(w.port_widgets[role][0].count() - 1)
            w.last_ui = 0
            w.refresh()
            assert not w.legacy_actions[key].isEnabled()
            assert not w.legacy_actions[key].isVisible()
            w.legacy_actions[key].trigger()
            w.connect_role(role)
            w.toggle_connection(role)
        assert not attempted
        assert w.legacy_actions['log'].isVisible()
        visible = {key for key, card in w.cards.items() if card.isVisible()}
        if site == 'base':
            assert set(w.port_widgets) == {'telemetry'} and w.controller.board_layout == 'launch'
            assert visible == {'video_wall'}
            assert set(w.video_wall.primary_views) == set(profile.channels)
            assert set(w.video_wall.thumbnails) == {(station, channel) for station, channels in profile.away_channels.items() for channel in channels}
            assert w.video_widgets['digital']['camera'].isVisible()
            assert w.video_wall.selected_sources['digital'] == ('local', 'digital')
        else:
            assert visible == channels and w.video_wall is None
            assert all(v['camera'].isVisible() for v in w.video_widgets.values())
        # A different role needs a new startup choice; display actions cannot
        # silently change the station identity or its local receiver contract.
        w.set_station_mode('base')
        assert w.station_mode == 'video' and w.profile == profile
    finally:
        w.close()


def test_away_telemetry_retains_selected_station_number(qtbot, tmp_path):
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    profile = StationProfile(station='away3', role='telemetry', vehicle='balius')
    w = StationWindow(tmp_path, profile=profile, auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        qtbot.wait(50)
        assert w.station_mode == 'away' and not w.companion.isVisible()
        assert 'Away station 3' in w.station_identity.text()
        assert 'Away station 3' in w.network_panel.station_name.text()
        assert {key for key, card in w.cards.items() if card.isVisible()} == {
            'antenna', 'pointing', 'altitude', 'attitude', 'telemetry', 'gps'}
        assert not any(w.cards[key].isVisible() for key in profile.channels)
        w.open_flight_3d()
        assert w.away_flight_dialog.isVisible()
        assert w.away_flight_dialog.controller is w.controller
        assert w.station_mode == 'away' and not w.companion.isVisible()
    finally:
        w.close()


def test_launch_wall_local_source_labels_follow_demo_file_and_paused_replay(qtbot, tmp_path):
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    w = StationWindow(tmp_path, profile=StationProfile('base', 'video', 'iris'), auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        c = w.controller
        frame = QPixmap(32, 18)
        frame.fill()
        for mode, config, playing, expected in [
            ('DEMO', None, False, 'Demo'),
            ('LIVE', ('file', 'example.mp4'), False, 'Local video file'),
            ('REPLAY', None, False, 'Replay · paused'),
            ('REPLAY', None, True, 'Replay · playing'),
        ]:
            c.mode = mode
            c.replay_playing = playing
            c.video_streams['digital'].config = config
            w.update_local_video_context()
            w.video_wall.set_local_frame(frame)
            text = w.video_wall.primary_status['digital'].text()
            assert expected in text and 'LIVE' not in text
    finally:
        w.controller.video_streams['digital'].config = None
        w.close()


@pytest.mark.parametrize('site,vehicle,serial_roles,switches', [
    ('base', 'balius', {'telemetry'}, set()),
    ('base', 'iris', {'telemetry'}, {'telemetry'}),
    ('away4', 'iris', {'telemetry', 'pointer'}, {'downlink'}),
])
def test_telemetry_station_board_inventory_and_iris_placeholders(qtbot, tmp_path, site, vehicle, serial_roles, switches):
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    w = StationWindow(tmp_path, profile=StationProfile(site, 'telemetry', vehicle), auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        qtbot.wait(30)
        assert set(w.controller.workers) == serial_roles == set(w.port_widgets)
        assert all(port[0].isVisible() for port in w.port_widgets.values())
        assert not any(w.controller.workers.values())
        if not switches:
            assert w.iris_links is None
        else:
            assert w.iris_links.isVisible()
            assert set(w.iris_links.selectors) == switches
            assert not any(button.isEnabled() for button in w.iris_links.buttons.values())
            assert all('Placeholder' in label.text() or 'PLACEHOLDER' in label.text()
                       for label in w.iris_links.status.values())
    finally:
        w.close()


@pytest.mark.parametrize('kind', ['file', 'camera'])
def test_launch_wall_stopped_input_keeps_frame_without_live_status(qtbot, tmp_path, kind):
    w = StationWindow(tmp_path, profile=StationProfile('base', 'video', 'iris'), auto_place=False)
    qtbot.addWidget(w)
    try:
        c = w.controller
        c.timer.stop()
        c.video_streams['digital'].config = (kind, 'test-input')
        frame = QPixmap(32, 18)
        frame.fill()
        w.video_wall.set_local_frame(frame)
        before = w.video_wall.feeds[('local', 'digital')].frame.cacheKey()
        c.stop_video('digital')
        w.update_local_video_context()
        assert 'stopped' in w.video_wall.primary_status['digital'].text()
        assert not w.video_wall.local_source_live
        assert w.video_wall.feeds[('local', 'digital')].frame.cacheKey() == before
    finally:
        w.close()


@pytest.mark.parametrize('worker_present,last_frame,error,active,expected,live', [
    (False, 0, '', False, 'starting', False),
    (True, 0, '', True, 'awaiting frames', False),
    (True, 1, 'Camera disconnected', True, 'unavailable', False),
    (True, 1, '', False, 'stopped', False),
    (True, 1, '', True, 'LIVE', True),
])
def test_launch_wall_camera_live_requires_healthy_worker_and_new_frame(
        qtbot, tmp_path, worker_present, last_frame, error, active, expected, live):
    from types import SimpleNamespace

    w = StationWindow(tmp_path, profile=StationProfile('base', 'video', 'iris'), auto_place=False)
    qtbot.addWidget(w)
    state = w.controller.video_streams['digital']
    try:
        w.controller.timer.stop()
        state.config = ('camera', 'test-input')
        state.worker = SimpleNamespace(
            last_frame=last_frame, error=error,
            stopped=SimpleNamespace(is_set=lambda: not active),
            thread=SimpleNamespace(is_alive=lambda: active),
        ) if worker_present else None
        frame = QPixmap(32, 18)
        frame.fill()
        w.video_wall.set_local_frame(frame)
        w.update_local_video_context()
        assert expected in w.video_wall.primary_status['digital'].text()
        assert w.video_wall.local_source_live is live
    finally:
        state.worker = None
        state.config = None
        w.close()


def test_launch_wall_per_channel_replay_pause_overrides_global_playback(qtbot, tmp_path):
    w = StationWindow(tmp_path, profile=StationProfile('base', 'video', 'iris'), auto_place=False)
    qtbot.addWidget(w)
    try:
        c = w.controller
        c.timer.stop()
        c.mode = 'REPLAY'
        c.replay_playing = True
        c.video_streams['digital'].config = ('file', 'recording.mp4')
        frame = QPixmap(32, 18)
        frame.fill()
        w.video_wall.set_local_frame(frame)
        c.stop_video('digital', pause_replay=True)
        w.update_local_video_context()
        assert c.replay_playing
        assert w.video_wall.primary_status['digital'].text() == 'Replay · paused'
        assert not w.video_wall.local_source_live
    finally:
        w.close()
