"""Main Base controls confirm a specific live state transition before dispatch."""

import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from rocket_gnc_monitor.balius_states import BALIUS_STATE_NAMES, balius_state_name
from rocket_gnc_monitor.controller import Controller
from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.rocket_panel import RocketPanel
from rocket_gnc_monitor.rocket_protocol import rocket_packet


@pytest.fixture
def controls(qtbot, tmp_path):
    controller = Controller(tmp_path)
    controller.timer.stop()
    packets, errors = [], []
    controller.workers["telemetry"] = SimpleNamespace(send=lambda data, identifier: packets.append(data))
    controller.states["telemetry"] = "Connected"
    controller.polling = True
    controller.accept(Sample(0, 1, "LIVE", details={"state_code": 1, "boot_index": 0}))

    def guard(callback):
        try:
            return callback()
        except ValueError as exc:
            errors.append(str(exc))

    panel = RocketPanel(controller, guard)
    qtbot.addWidget(panel)
    panel.configure_base_controls()
    yield controller, panel, packets, errors
    controller.workers["telemetry"] = None
    controller.shutdown()


@pytest.mark.parametrize("keyboard", [False, True])
def test_advance_names_exact_transition_and_cancel_sends_nothing(controls, monkeypatch, qtbot, keyboard):
    _controller, panel, packets, errors = controls
    prompts = []
    answer = QMessageBox.StandardButton.No

    def confirm(*args):
        prompts.append(args)
        return answer

    monkeypatch.setattr(QMessageBox, "question", confirm)
    button = panel.buttons["advance_state"]
    assert button.objectName() == "danger"
    assert panel.buttons["pd_activate"].text() == "Activate Controls"

    def activate():
        if keyboard:
            qtbot.keyClick(button, Qt.Key.Key_Space)
        else:
            button.click()

    activate()
    assert not packets and not errors
    assert "From: PREFLIGHT (1)" in prompts[-1][2]
    assert "To: FLIGHT (2)" in prompts[-1][2]
    assert prompts[-1][-1] == QMessageBox.StandardButton.No
    answer = QMessageBox.StandardButton.Yes
    activate()
    assert packets == [rocket_packet("advance_state", 2)]


@pytest.mark.parametrize("change", ["state", "reboot", "stale", "disconnect"])
def test_advance_rechecks_after_modal_dialog(controls, monkeypatch, change):
    controller, panel, packets, errors = controls

    def confirm(*args):
        if change == "state":
            controller.latest.details["state_code"] = 2
        elif change == "reboot":
            controller.latest.details["boot_index"] = 1
        elif change == "stale":
            controller.latest.received = time.monotonic() - 100
        else:
            controller.states["telemetry"] = "Disconnected"
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    panel.buttons["advance_state"].click()
    assert not packets and errors


@pytest.mark.parametrize("state", [None, -1, 4, 5, 6, True, "1"])
def test_unknown_or_final_state_cannot_be_advanced(controls, state):
    controller, panel, packets, errors = controls
    controller.latest.details["state_code"] = state
    panel.set_controls_enabled()
    assert not panel.buttons["advance_state"].isEnabled()
    panel.request("advance_state")
    assert not packets and errors


@pytest.mark.parametrize("state", range(4))
def test_balius_follows_provisional_five_state_sequence(controls, monkeypatch, state):
    controller, panel, packets, errors = controls
    controller.latest.details["state_code"] = state
    prompts = []

    def confirm(*args):
        prompts.append(args[2])
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    panel.set_controls_enabled()
    panel.buttons["advance_state"].click()
    assert not errors
    assert packets == [rocket_packet("advance_state", state + 1)]
    assert f"From: {BALIUS_STATE_NAMES[state]} ({state})" in prompts[0]
    assert f"To: {BALIUS_STATE_NAMES[state + 1]} ({state + 1})" in prompts[0]
    assert balius_state_name(controller.latest) == BALIUS_STATE_NAMES[state]


def test_balius_low_battery_is_final_and_timers_are_not_states(controls):
    controller, panel, _packets, _errors = controls
    controller.latest.details["state_code"] = 4
    assert balius_state_name(controller.latest) == "LOW_BATTERY"
    panel.set_controls_enabled()
    assert "LOW_BATTERY is the final" in panel.buttons["advance_state"].toolTip()
    for phase in ("Lockout", "Override timer", "Main", "End"):
        assert balius_state_name(Sample(0, 1, "LIVE", phase=phase)) == "UNKNOWN"


def test_launch_switch_guard_and_secondary_panel_are_unchanged(controls, qtbot):
    controller, panel, packets, errors = controls
    controller.board_layout = "launch"
    panel.set_controls_enabled()
    assert not panel.buttons["advance_state"].isEnabled()
    assert not panel.buttons["pd_activate"].isEnabled()
    panel.request("advance_state")
    assert not packets and "protocol not defined" in errors[-1]
    secondary = RocketPanel(controller, lambda callback: callback())
    qtbot.addWidget(secondary)
    assert not secondary.base_controls
    assert secondary.buttons["pd_activate"].text() == "PD Activate"
    assert secondary.buttons["advance_state"].objectName() != "danger"
