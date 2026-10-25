from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from rocket_gnc_monitor.launch_uplink_panel import LaunchUplinkPanel


def make_panel(qtbot, mode="LIVE"):
    switches = []

    def forbidden(*args, **kwargs):
        raise AssertionError("The placeholder must not call a hardware operation")

    controller = SimpleNamespace(
        mode=mode, simulated_uplink_enabled=False, latest=object(),
        connect=forbidden, send=forbidden, rocket_command=forbidden,
    )

    def simulate(enabled):
        assert controller.mode == "DEMO"
        assert type(enabled) is bool
        switches.append(enabled)
        controller.simulated_uplink_enabled = enabled

    controller.set_simulated_uplink = simulate
    panel = LaunchUplinkPanel(controller)
    qtbot.addWidget(panel)
    panel.show()
    return panel, controller, switches


@pytest.mark.parametrize("mode", ["LIVE", "REPLAY"])
def test_unknown_hardware_switch_is_disabled_and_never_reported_off(qtbot, mode):
    panel, controller, switches = make_panel(qtbot, mode)
    controller.simulated_uplink_enabled = True
    panel.refresh()
    assert not panel.toggle.isEnabled()
    assert not panel.toggle.isChecked()
    assert panel.toggle.text() == "Unavailable"
    assert panel.status.text() == "Hardware uplink state: UNKNOWN"
    assert panel.placeholder.text() == "Placeholder · firmware command not defined"
    assert "OFF" not in panel.status.text()
    qtbot.mouseClick(panel.toggle, Qt.MouseButton.LeftButton)
    panel._toggle(True)  # A delayed/programmatic signal has the same mode guard.
    assert switches == []


def test_demo_switch_starts_off_then_simulates_both_directions(qtbot):
    panel, controller, switches = make_panel(qtbot, "DEMO")
    sample = controller.latest
    assert panel.toggle.isEnabled() and not panel.toggle.isChecked()
    assert panel.toggle.text() == "Uplink OFF"
    assert panel.status.text() == "DEMO · Simulated uplink OFF"
    qtbot.mouseClick(panel.toggle, Qt.MouseButton.LeftButton)
    assert switches == [True] and controller.simulated_uplink_enabled
    assert panel.toggle.isChecked() and panel.toggle.text() == "Uplink ON"
    assert panel.status.text() == "DEMO · Simulated uplink ON"
    qtbot.mouseClick(panel.toggle, Qt.MouseButton.LeftButton)
    assert switches == [True, False] and not controller.simulated_uplink_enabled
    assert panel.status.text() == "DEMO · Simulated uplink OFF"
    assert controller.latest is sample


@pytest.mark.parametrize("destination", ["LIVE", "REPLAY"])
def test_mode_change_between_refresh_and_click_cannot_simulate(qtbot, destination):
    panel, controller, switches = make_panel(qtbot, "DEMO")
    controller.mode = destination
    panel._toggle(True)
    assert switches == []
    assert not panel.toggle.isEnabled()
    assert panel.status.text() == "Hardware uplink state: UNKNOWN"


def test_periodic_refresh_never_changes_controller_state(qtbot):
    panel, controller, switches = make_panel(qtbot, "DEMO")
    controller.simulated_uplink_enabled = True
    panel.refresh()
    panel.refresh()
    assert switches == []
    assert panel.toggle.isChecked()
    assert controller.simulated_uplink_enabled
