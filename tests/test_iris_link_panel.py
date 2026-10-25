from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from rocket_gnc_monitor.iris_link_panel import IrisLinkPanel


def make_panel(qtbot, station="base", mode="LIVE"):
    events = []

    def no_hardware(*args, **kwargs):
        raise AssertionError("A placeholder must never invoke a hardware operation")

    sample = object()
    controller = SimpleNamespace(
        mode=mode, latest=sample, log=lambda name, data: events.append((name, data)),
        send=no_hardware, rocket_command=no_hardware, connect=no_hardware,
        workers={"downlink": object(), "pointer": object()},
    )
    panel = IrisLinkPanel(controller, station=station)
    qtbot.addWidget(panel)
    panel.resize(1500, 80)
    panel.show()
    return panel, controller, events, sample


@pytest.mark.parametrize("mode", ["LIVE", "REPLAY"])
def test_placeholders_do_not_actuate_or_claim_hardware_state(qtbot, mode):
    panel, controller, events, sample = make_panel(qtbot, mode=mode)
    for board in ("telemetry",):
        assert panel.selectors[board].isEnabled()
        panel.selectors[board].setCurrentIndex(1)
        assert not panel.buttons[board].isEnabled()
        panel.simulate(board)
        assert panel.simulated_targets[board] is None
        assert "Hardware target: unknown" in panel.status[board].text()
        assert "firmware command not defined" in panel.status[board].text()
    assert events == [] and controller.latest is sample


def test_launch_demo_has_one_common_telemetry_board_target(qtbot):
    panel, controller, events, sample = make_panel(qtbot, mode="DEMO")
    assert set(panel.selectors) == set(panel.buttons) == set(panel.status) == {"telemetry"}
    assert panel.selectors["telemetry"].currentData() == "sustainer"
    assert panel.selectors["telemetry"].accessibleName() == "Telemetry Board Target desired target"
    panel.selectors["telemetry"].setCurrentIndex(1)
    qtbot.mouseClick(panel.buttons["telemetry"], Qt.MouseButton.LeftButton)
    assert panel.simulated_targets == {"telemetry": "booster"}
    assert events[-1] == (
        "Iris telemetry board switch simulated",
        dict(board="telemetry", target="booster", source="DEMO", placeholder=True),
    )
    assert len(events) == 1 and controller.latest is sample
    panel.selectors["telemetry"].setCurrentIndex(0)
    assert panel.simulated_targets["telemetry"] == "booster"
    assert "Simulated target: Booster" in panel.status["telemetry"].text()
    assert "Flight data unchanged" in panel.status["telemetry"].text()
    qtbot.mouseClick(panel.buttons["telemetry"], Qt.MouseButton.LeftButton)
    assert panel.simulated_targets == {"telemetry": "sustainer"}
    assert len(events) == 2 and controller.latest is sample
    for removed_board in ("downlink", "uplink"):
        with pytest.raises(ValueError, match=removed_board):
            panel.simulate(removed_board)


@pytest.mark.parametrize("station,default", [("away1", "sustainer"), ("away2", "sustainer"), ("away3", "sustainer"), ("away4", "booster")])
def test_away_station_has_only_receiver_with_correct_default(qtbot, station, default):
    panel, _, events, _ = make_panel(qtbot, station=station, mode="DEMO")
    assert set(panel.selectors) == set(panel.buttons) == set(panel.status) == {"downlink"}
    assert panel.selectors["downlink"].currentData() == default
    panel.simulate("downlink")
    assert panel.simulated_targets == {"downlink": default}
    assert events[0][1]["target"] == default
    with pytest.raises(ValueError, match="uplink"):
        panel.simulate("uplink")


def test_away_live_receiver_retains_hardware_placeholder(qtbot):
    panel, _, events, _ = make_panel(qtbot, station="away4")
    assert "Hardware target: unknown" in panel.status["downlink"].text()
    assert "firmware command not defined" in panel.status["downlink"].text()
    assert not panel.buttons["downlink"].isEnabled() and not events


@pytest.mark.parametrize("destination", ["LIVE", "REPLAY"])
def test_leaving_demo_clears_simulation_and_rechecks_action_mode(qtbot, destination):
    panel, controller, events, _ = make_panel(qtbot, mode="DEMO")
    panel.simulate("telemetry")
    controller.mode = destination
    # The callback also guards the mode before the next periodic UI refresh.
    panel.simulate("telemetry")
    assert len(events) == 1
    assert panel.simulated_targets == {"telemetry": None}
    assert not any(action.isEnabled() for action in panel.buttons.values())
    controller.mode = "DEMO"
    panel.refresh()
    assert all(action.isEnabled() for action in panel.buttons.values())
    assert panel.simulated_targets == {"telemetry": None}
