from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from rocket_gnc_monitor.launch_uplink_panel import LaunchUplinkPanel


@pytest.mark.parametrize("mode", ["LIVE", "REPLAY"])
def test_unknown_hardware_switch_is_disabled_and_never_reported_off(qtbot, mode):
    def forbidden(*args, **kwargs):
        raise AssertionError("The placeholder must not call a hardware operation")

    controller = SimpleNamespace(mode=mode, latest=object(), connect=forbidden,
                                 send=forbidden, rocket_command=forbidden)
    panel = LaunchUplinkPanel(controller)
    qtbot.addWidget(panel)
    panel.show()
    sample = controller.latest
    assert not panel.toggle.isEnabled()
    assert not panel.toggle.isCheckable()
    assert panel.toggle.text() == "Unavailable"
    assert panel.status.text() == "Hardware uplink state: UNKNOWN"
    assert panel.placeholder.text() == "Placeholder · firmware command not defined"
    assert "OFF" not in panel.status.text()
    qtbot.mouseClick(panel.toggle, Qt.MouseButton.LeftButton)
    panel.toggle.clicked.emit()
    panel.refresh()
    assert controller.latest is sample
    assert panel.status.text() == "Hardware uplink state: UNKNOWN"
