from types import SimpleNamespace
import time
import pytest
from rocket_gnc_monitor.controller import Controller
from rocket_gnc_monitor.domain import demo_sample


@pytest.fixture
def controller(qapp, tmp_path):
    c = Controller(tmp_path)
    c.timer.stop()
    yield c
    c.shutdown()


def test_startup_is_live_without_data_and_requires_ground_connection(controller, tmp_path):
    c = controller
    c.tick()
    assert c.mode == "LIVE" and c.latest is None and c.reference is None
    assert c.video is None and not c.time_aligned and not any(c.workers.values())
    assert c.rocket_link_state() == ("DISCONNECTED", None)
    for action in [
        lambda: c.point(90, 30),
        c.reference_zero,
        c.start_tracking,
        lambda: c.start_recording(tmp_path),
    ]:
        with pytest.raises(ValueError, match="(?i)(ground station|antenna pointer)"):
            action()
    assert c.pointer_pending is None and c.recorder is None


def test_ground_loss_discards_pending_tracking_command(controller):
    import queue

    c = controller
    commands = queue.Queue()
    commands.put((time.monotonic() + 0.5, b"command", "pending"))
    c.workers["pointer"] = SimpleNamespace(commands=commands)
    c.pointer_pending = ("pending", (100, 20))
    c.dispatched_commands["pending"] = (100, 20)
    c.tracking = True
    try:
        c.disconnect("telemetry")
        assert commands.empty() and not c.dispatched_commands and c.pointer_pending is None
        assert "Tracking off" in c.pointer_status
    finally:
        c.workers["pointer"] = None


def test_demo_and_replay_have_no_physical_motion_path(controller, monkeypatch):
    c = controller
    c.switch_mode("DEMO")

    def forbidden(*args, **kwargs):
        raise AssertionError("Physical worker constructed")

    monkeypatch.setattr("rocket_gnc_monitor.controller.SerialWorker", forbidden)
    c.point(90, 45)
    assert c.pointer_sent == (90, 45)
    with pytest.raises(ValueError):
        c.connect("pointer", "COM1")
    c.switch_mode("REPLAY")
    with pytest.raises(ValueError):
        c.point(90, 45)
    with pytest.raises(ValueError):
        c.start_tracking()
    assert not any(c.workers.values())


def test_stale_target_stops_tracking_and_requires_explicit_resume(controller):
    c = controller
    c.switch_mode("LIVE")
    c.latest = demo_sample(10, 1)
    c.latest.received = time.monotonic() - 30
    c.tracking = True
    c.tick()
    assert not c.tracking and "stale" in c.pointer_status
    c.latest = demo_sample(10, 2)
    c.tick()
    assert not c.tracking


def test_queued_old_connection_sample_cannot_repopulate(controller):
    c = controller
    c.switch_mode("LIVE")
    c.messages.put((123, "telemetry", "sample", demo_sample(10, 1)))
    c.tick()
    assert c.latest is None


def test_duplicate_device_rejected_before_connect(controller):
    c = controller
    c.switch_mode("LIVE")
    c.workers["telemetry"] = SimpleNamespace(device="/dev/cu.test")
    try:
        with pytest.raises(ValueError, match="both"):
            c.connect("pointer", "/dev/tty.test")
    finally:
        c.workers["telemetry"] = None


def test_replay_seek_clears_future_track(controller, tmp_path):
    from rocket_gnc_monitor.recording import SessionRecorder

    c = controller
    r = SessionRecorder(tmp_path, c.mission, "DEMO")
    for i in range(30):
        s = demo_sample(i, i)
        s.received = r.start + i
        r.sample(s)
    r.close()
    c.open_replay(r.path)
    c.seek(25)
    assert c.latest.sequence == 25
    c.seek(4)
    assert c.latest.sequence == 4 and max(s.sequence for s in c.history) <= 4
    assert not any(c.workers.values())


def test_joining_midflight_does_not_invent_launch_time(controller):
    c = controller
    c.switch_mode("LIVE")
    s = demo_sample(30, 1)
    s.phase = "Flight"
    c.accept(s)
    assert not c.time_aligned
    s = demo_sample(31, 2)
    s.phase = "Preflight"
    c.accept(s)
    s = demo_sample(32, 3)
    s.phase = "Flight"
    c.accept(s)
    assert c.time_aligned and c.flight_zero == 32


def test_hold_retains_a_command_already_dispatched(controller):
    import queue

    c = controller
    c.switch_mode("LIVE")
    c.workers["pointer"] = SimpleNamespace(generation=4, commands=queue.Queue())
    c.dispatched_commands["id"] = (100, 30)
    c.pointer_pending = ("id", (100, 30))
    c.hold()
    c.messages.put((4, "pointer", "sent", "id"))
    try:
        c.tick()
        assert c.pointer_sent == (100, 30) and not c.tracking
        assert "Tracking off" in c.pointer_status
    finally:
        c.workers["pointer"] = None


def test_battery_alert_has_dwell_hysteresis_and_acknowledgment(controller):
    c = controller
    c.switch_mode("LIVE")
    c.latest = demo_sample(10, 1)
    c.latest.battery = 8
    now = time.monotonic()
    c.update_alerts(now)
    assert "battery" not in c.alerts
    c.update_alerts(now + 1.1)
    assert "battery" in c.alerts
    c.acknowledge_alerts()
    assert c.alerts["battery"]["acknowledged"]
    c.latest.battery = 9.2
    c.update_alerts(now + 1.2)
    assert "battery" in c.alerts
    c.latest.battery = 9.4
    c.update_alerts(now + 1.3)
    assert "battery" not in c.alerts


def test_replay_applies_pointer_events_when_playing(controller, tmp_path):
    from rocket_gnc_monitor.recording import SessionRecorder

    c = controller
    r = SessionRecorder(tmp_path, c.mission, "LIVE", time_aligned=False)
    for i in range(20):
        s = demo_sample(i, i)
        s.received = r.start + i
        r.sample(s)
    r.put("event", {"name": "Pointer command sent", "data": {"angles": [25, 30]}}, r.start + 2)
    r.put("event", {"name": "Aligned", "data": {"flight_zero": 15}}, r.start + 2)
    r.close()
    c.open_replay(r.path)
    c.seek(1.9)
    assert c.pointer_sent is None and not c.time_aligned
    c.replay_playing = True
    c.last_tick = time.monotonic() - 0.2
    c.tick()
    assert c.pointer_sent == (25, 30) and c.flight_zero == 15 and c.time_aligned


def test_async_result_from_previous_mode_is_rejected(controller, qtbot):
    c = controller
    c.switch_mode("DEMO")
    results = []
    c.task_done.connect(lambda name, result: results.append((name, result)))
    c.submit("weather", lambda: {"layers": []})
    c.switch_mode("LIVE")
    qtbot.waitUntil(lambda: c.futures[0][1].done(), timeout=2000)
    c.tick()
    assert isinstance(next(result for name, result in results if name == "weather"), ValueError)
