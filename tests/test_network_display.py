"""Remote observations cannot establish local hardware state or command targets."""

import threading
import time
from types import SimpleNamespace

import pytest

from rocket_gnc_monitor.controller import Controller
from rocket_gnc_monitor.network.observation import observation_to_sample
from rocket_gnc_monitor.rocket_pose import telemetry_pose
from rocket_gnc_monitor.ui import MainWindow
from test_network_observation import observation
from test_protocol import frame
from rocket_gnc_monitor.protocol import TelemetryDecoder


def remote_sample(altitude=900):
    payload = observation()
    payload["values"]["baro_altitude"]["value_m"] = altitude
    return observation_to_sample(payload, received_monotonic=time.monotonic(), source_age_ms=60,
                                 source_label="GS3")


@pytest.fixture
def controller(qapp, tmp_path):
    controller = Controller(tmp_path)
    controller.timer.stop()
    yield controller
    controller.shutdown()


def test_remote_selection_never_changes_local_input_or_command_state(controller):
    c = controller
    local = TelemetryDecoder().feed(frame(1, altitude=123))[0]
    c.accept(local)
    c.time_aligned, c.flight_zero = True, 1
    c.workers["telemetry"] = SimpleNamespace(stop=lambda: None, send=lambda *_: None)
    c.states["telemetry"] = "Connected"
    c.polling = True
    c.last_live_received = local.received
    before = (c.latest, tuple(c.history), tuple(c.track), c.rocket_link_state()[0], c.last_live_received)
    assert c.can_command
    c.remote_display_key = "remote-node"
    c.remote_display_sample = remote_sample()
    c.remote_display_history.append(c.remote_display_sample)
    assert c.display_latest.altitude == 900 and c.display_fresh
    assert c.display_history is c.remote_display_history
    assert c.display_flight_zero == 0 and not c.display_time_aligned
    assert not c.can_command and "read-only" in c.command_block_reason
    with pytest.raises(ValueError, match="read-only"):
        c.send_rocket("advance_state")
    assert (c.latest, tuple(c.history), tuple(c.track), c.rocket_link_state()[0], c.last_live_received) == before
    c.remote_display_sample.received -= 100
    assert not c.display_fresh
    assert c.rocket_link_state()[0] == "RECEIVING"
    c.remote_display_key = None
    assert c.display_latest is local and c.display_history is c.history and c.can_command
    assert c.display_flight_zero == 1 and c.display_time_aligned


def test_selected_missing_remote_does_not_fall_back_to_local(controller):
    c = controller
    c.accept(TelemetryDecoder().feed(frame())[0])
    c.remote_display_key = "not-connected"
    assert c.display_latest is None and not c.display_fresh
    assert len(c.display_history) == 0 and not c.can_command


def test_network_pose_labels_live_remote_and_keeps_gyro_totals_unqualified():
    sample = remote_sample()
    pose = telemetry_pose(sample, mode="LIVE", fresh=False)
    assert pose.source == "NETWORK" and pose.stale
    assert "GS3" in pose.status and "READ ONLY" in pose.status
    assert "REPLAY" not in pose.status
    assert not pose.attitude_known and pose.angle_kind == "gyro_integrals"
    assert pose.motor is None and pose.parachute is None


def test_serial_callback_uses_current_sink_and_rejects_superseded_connection(controller, monkeypatch):
    c = controller
    seen = []

    class Board:
        def __init__(self, role, device, generation, emit, raw, **kwargs):
            self.device, self.generation = device, generation
            self.stop_event = threading.Event()
            self.observation = kwargs.get("on_observation")

        def stop(self):
            self.stop_event.set()

    monkeypatch.setattr("rocket_gnc_monitor.controller.SerialWorker", Board)
    c.network_observation_sink = lambda *args: seen.append(("first", args))
    c.connect("telemetry", "test-port")
    first = c.workers["telemetry"]
    c.polling = True
    sample = TelemetryDecoder().feed(frame())[0]
    first.observation(sample, frame(), {"connection_id": "first"})
    assert seen[-1][0] == "first"
    c.network_observation_sink = lambda *args: seen.append(("updated", args))
    first.observation(sample, frame(), {})
    assert seen[-1][0] == "updated"
    c.connect("telemetry", "test-port")
    c.polling = True
    first.observation(sample, frame(), {})
    assert len(seen) == 2
    c.workers["telemetry"].observation(sample, frame(), {})
    assert len(seen) == 3
    c.polling = False
    c.workers["telemetry"].observation(sample, frame(), {})
    assert len(seen) == 3


def test_visible_tables_plots_pose_and_banner_read_remote_without_local_connection(qtbot, tmp_path):
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    c = window.controller
    c.timer.stop()
    local = TelemetryDecoder().feed(frame(8, altitude=10))[0]
    c.accept(local)
    remote = remote_sample(900)
    c.remote_display_key = "remote-node"
    c.remote_display_sample = remote
    c.remote_display_history.append(remote)
    window.last_ui = window.last_table = 0
    window.refresh()
    assert window.metrics["altitude"].text() == "900.0"
    assert "NETWORK" in window.banner.text() and "READ ONLY" in window.banner.text()
    assert "GS3" in window.banner.text()
    assert window.alert_status.text().startswith("Local board")
    assert window.link_stats.text().startswith("Local board")
    assert window.attitude.view.pose.source == "NETWORK"
    assert window.altitude_curve.yData.tolist() == [900]
    assert float(window.rocket_panel.telemetry.item(3, 1).text()) == 900
    assert not window.rocket_panel.buttons["advance_state"].isEnabled()
    assert c.latest is local and c.history[-1] is local
    remote.received -= 100
    window.last_ui = 0
    window.refresh()
    assert "TELEMETRY STALE" in window.banner.text()
    window.close()
