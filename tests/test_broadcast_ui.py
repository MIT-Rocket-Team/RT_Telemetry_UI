from rocket_gnc_monitor.station_workspace import StationWindow
from rocket_gnc_monitor.station_profile import StationProfile


def test_base_video_owns_output_panel_close_preserves_program(qtbot, tmp_path):
    window = StationWindow(tmp_path, profile=StationProfile(station="base", role="video"), auto_place=False)
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
    assert controller.program_window.windowTitle() == "Rocket Broadcast — Program"
    assert not controller.panel.stream_start.isEnabled()
    assert not controller.panel.record_start.isEnabled()
    controller.stop_program()
    assert not controller.active
    window.close()
    assert not controller.timer.isActive()
    assert controller.session.frames.bytes == 0


def test_no_broadcast_entrypoint_on_away_video(qtbot, tmp_path):
    window = StationWindow(tmp_path, profile=StationProfile(station="away1", role="video"), auto_place=False)
    qtbot.addWidget(window)
    assert not hasattr(window, "broadcast_button")
    window.close()


def test_renderer_fault_slates_and_keeps_scheduler_alive(qtbot, tmp_path, monkeypatch):
    window = StationWindow(tmp_path, profile=StationProfile(station="base", role="video"), auto_place=False)
    qtbot.addWidget(window)
    window.open_broadcast()
    c = window.broadcast
    c.open_program()
    monkeypatch.setattr(c, "render", lambda snapshot: (_ for _ in ()).throw(ValueError("fixture")))
    c.tick()
    assert c.error and c.timer.isActive() and not c.last_program.isNull()
    assert "PROGRAM ERROR" in c.panel.overview.text()
    window.close()


def test_unprepared_edits_cannot_take_the_previous_preview(qtbot, tmp_path):
    window = StationWindow(tmp_path, profile=StationProfile(station="base", role="video"), auto_place=False)
    qtbot.addWidget(window)
    window.open_broadcast()
    panel = window.broadcast.panel
    panel.refresh()
    assert panel.take_button.isEnabled()
    panel.caption.setText("New caption")
    panel.refresh()
    assert not panel.take_button.isEnabled()
    panel.prepare()
    panel.refresh()
    assert panel.take_button.isEnabled()
    window.close()


def test_small_replay_seek_invalidates_presentation_epoch(qtbot, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from rocket_gnc_monitor.broadcast_model import BroadcastSelection

    window = StationWindow(tmp_path, profile=StationProfile(station="base", role="video"), auto_place=False)
    qtbot.addWidget(window)
    window.open_broadcast()
    c = window.controller
    broadcast = window.broadcast
    c.reader = SimpleNamespace(
        duration=10, manifest={}, events=lambda *a: [], between=lambda *a: [], close=lambda: None
    )
    monkeypatch.setattr(c, "replay_video", lambda: None)
    epoch = broadcast.epoch
    broadcast.session.program = BroadcastSelection("Flight", "old-camera")
    c.seek(0.1)
    assert broadcast.epoch != epoch and broadcast.session.program.scene == "Standby"
    assert not broadcast.session.frames.frames
    window.close()


def test_new_camera_epoch_invalidates_measured_timing(qtbot, tmp_path):
    from dataclasses import replace
    from rocket_gnc_monitor.broadcast_model import CameraSource, CameraKey, BroadcastSelection
    from rocket_gnc_monitor.broadcast_timing import Calibration, TimingConfig

    window = StationWindow(tmp_path, profile=StationProfile(station="base", role="video"), auto_place=False)
    qtbot.addWidget(window)
    window.open_broadcast()
    c = window.broadcast
    key = CameraKey("mission", "balius", "live", "node", "boot", "away1", "digital", "epoch")
    c.session.sources["camera:a"] = CameraSource("camera:a", "A", key, "away1", "digital")
    c.set_timing(TimingConfig(0.5, True, {"camera:a": Calibration(0.1, 0.02, "flash", "2026-11-03", 10)}))
    c.session.program = BroadcastSelection("Flight", "camera:a")
    c.check_timing_bindings()
    assert c.session.timing.calibrated
    c.session.sources["camera:a"] = replace(c.session.sources["camera:a"], key=replace(key, epoch="new"))
    c.check_timing_bindings()
    assert not c.session.timing.calibrated and c.session.program.scene == "Standby"
    window.close()


def test_custom_timeline_does_not_remove_actual_liftoff_control(qtbot, tmp_path):
    window = StationWindow(tmp_path, profile=StationProfile(station="base", role="video"), auto_place=False)
    qtbot.addWidget(window)
    window.open_broadcast()
    c = window.broadcast
    c.panel.events.setText("ASCENT, DESCENT")
    c.panel.save_events()
    c.panel.refresh()
    assert c.settings.events == ("ASCENT", "DESCENT")
    assert c.panel.event.findText("LIFTOFF") >= 0
    c.clock_action("on_program", event="LIFTOFF")
    assert c.session.clock.revisions[-1].state == "elapsed"
    window.close()
