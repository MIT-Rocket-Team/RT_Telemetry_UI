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
        assert not panel.selectors[board].isEnabled()
        panel.selectors[board].setCurrentIndex(1)
        assert not panel.buttons[board].isEnabled()
        qtbot.mouseClick(panel.buttons[board], Qt.MouseButton.LeftButton)
        assert "Hardware target: unknown" in panel.status[board].text()
        assert "firmware command not defined" in panel.status[board].text()
    assert events == [] and controller.latest is sample


def test_launch_has_one_common_telemetry_board_target(qtbot):
    panel, controller, events, sample = make_panel(qtbot)
    assert set(panel.selectors) == set(panel.buttons) == set(panel.status) == {"telemetry"}
    assert panel.selectors["telemetry"].currentData() == "sustainer"
    assert not panel.selectors["telemetry"].isEnabled()
    qtbot.mouseClick(panel.buttons["telemetry"], Qt.MouseButton.LeftButton)
    assert not events and controller.latest is sample


@pytest.mark.parametrize("station,default", [("away1", "sustainer"), ("away2", "sustainer"), ("away3", "sustainer"), ("away4", "booster")])
def test_away_station_has_only_receiver_with_correct_default(qtbot, station, default):
    panel, _, events, _ = make_panel(qtbot, station=station)
    assert set(panel.selectors) == set(panel.buttons) == set(panel.status) == {"downlink"}
    assert panel.selectors["downlink"].currentData() == default
    assert not panel.selectors["downlink"].isEnabled()
    qtbot.mouseClick(panel.buttons["downlink"], Qt.MouseButton.LeftButton)
    assert not events


def test_away_live_receiver_retains_hardware_placeholder(qtbot):
    panel, _, events, _ = make_panel(qtbot, station="away4")
    assert "Hardware target: unknown" in panel.status["downlink"].text()
    assert "firmware command not defined" in panel.status["downlink"].text()
    assert not panel.buttons["downlink"].isEnabled() and not events
