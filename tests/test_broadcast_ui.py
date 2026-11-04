from rocket_gnc_monitor.station_workspace import StationWindow
from rocket_gnc_monitor.station_profile import StationProfile


def test_base_video_owns_output_panel_close_preserves_program(qtbot,tmp_path):
    window = StationWindow(tmp_path, profile=StationProfile(station='base',role='video'),auto_place=False)
    qtbot.addWidget(window)
    window.open_broadcast()
    controller = window.broadcast
    qtbot.waitUntil(lambda: not controller.last_preview.isNull())
    assert not controller.active and controller.bridge is None
    controller.open_program()
    qtbot.waitUntil(lambda: not controller.last_program.isNull())
    controller.panel.close()
    assert controller.active and controller.program_window.isVisible()
    assert not controller.preview_active
    assert controller.program_window.windowTitle() == 'Rocket Broadcast — Program'
    assert not controller.panel.stream_start.isEnabled()
    assert not controller.panel.record_start.isEnabled()
    controller.stop_program()
    assert not controller.active
    window.close()
    assert not controller.timer.isActive()
    assert controller.session.frames.bytes == 0


def test_no_broadcast_entrypoint_on_away_video(qtbot,tmp_path):
    window = StationWindow(tmp_path,profile=StationProfile(station='away1',role='video'),auto_place=False)
    qtbot.addWidget(window)
    assert not hasattr(window,'broadcast_button')
    window.close()
