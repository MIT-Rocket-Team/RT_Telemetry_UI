"""Surface controls must not invent channel mapping or wire commands."""

from types import SimpleNamespace

from rocket_gnc_monitor.canard_table import COMMAND_UNAVAILABLE, CanardTable
from rocket_gnc_monitor.domain import Mission, Sample


def make_panel(qtbot, sample=None, block_reason=""):
    errors = []

    def guard(callback):
        try:
            callback()
        except ValueError as error:
            errors.append(str(error))

    controller = SimpleNamespace(mission=Mission(), latest=sample, command_block_reason=block_reason)
    panel = CanardTable(controller, guard)
    qtbot.addWidget(panel)
    return panel, controller, errors


def test_named_surface_rows_and_no_unsupported_transmit(qtbot):
    panel, _, errors = make_panel(qtbot)
    assert panel.channels == ("Canard1", "Canard2", "Canard3", "Canard4", "Tab1", "Tab2", "Tab3", "Tab4")
    assert [panel.table.horizontalHeaderItem(i).text() for i in range(3)] == ["Servo", "Angle°", "Command"]
    for row, name in enumerate(panel.channels):
        assert panel.table.item(row, 1).text() == "—"
        angle, button = panel.commands[name]
        assert not angle.isEnabled() and not button.isEnabled()
        button.clicked.emit()
    assert errors == [COMMAND_UNAVAILABLE] * 8


def test_angles_use_only_explicit_measured_surface_feedback(qtbot):
    sample = Sample(
        t=1,
        sequence=1,
        source="LIVE",
        actuators={
            "Canard 1": {"measured": 4.5, "demand": 9.0},
            "Canard2": {"demand": 12.0, "drive": 1600},
            "Canard3": {"measured": float("nan")},
            "Legacy servo 4": {"drive": 1800},
            "Tab1": {"measured": -2.25},
        },
    )
    panel, controller, _ = make_panel(qtbot, sample)
    assert [panel.table.item(row, 1).text() for row in range(8)] == [
        "4.50",
        "—",
        "—",
        "—",
        "-2.25",
        "—",
        "—",
        "—",
    ]
    controller.latest = None
    panel.refresh()
    assert all(panel.table.item(row, 1).text() == "—" for row in range(8))


def test_shared_command_gate_and_mission_channel_count(qtbot):
    panel, controller, errors = make_panel(qtbot, block_reason="Replay is read-only")
    controller.mission.canard_count = 2
    panel.refresh()
    assert panel.channels == ("Canard1", "Canard2", "Tab1", "Tab2", "Tab3", "Tab4")
    assert panel.table.rowCount() == 6
    assert "Replay is read-only" in panel.commands["Tab4"][1].toolTip()
    panel.send_command("Tab4")
    assert errors == ["Replay is read-only"]
