"""Single-display launch workspaces retain the same controller and hardware."""

import gc

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow


@pytest.mark.parametrize("vehicle", ["balius", "iris"])
def test_base_station_keeps_one_window_and_shared_flight_state(qtbot, tmp_path, monkeypatch, vehicle):
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    profile = StationProfile(vehicle=vehicle, display_mode="launch_v2", launch_site="urrg")
    w = StationWindow(tmp_path, profile=profile, auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        w.resize(1280, 800)
        qtbot.wait(100)
        c = w.controller
        assert c.board_layout == "launch" and c.workers == {"telemetry": None}
        assert w.wifi_panel is None and not c.pointer_connected and not c.can_command
        assert w.station_mode == "launch_v2" and w.workspace_title.text() == "BASE STATION"
        assert c.mission.name == f"{vehicle.title()} Launch" and c.mission.launch_site_name == "URRG"
        assert not w.companion.isVisible() and not w.cards["antenna"].isVisible()
        assert {k for k, card in w.cards.items() if card.isVisible()} == {
            "attitude", "altitude", "trajectory", "telemetry", "gps",
        }
        assert [w.base_tabs.tabText(i).replace("&&", "&") for i in range(5)] == [
            "Flight", "Systems", "Mission & replay", "Network", "3D simulation",
        ]
        for table in (w.rocket_panel.telemetry, w.rocket_panel.gps):
            assert table.y() + table.height() <= table.parentWidget().height()
            assert table.verticalScrollBar().maximum() == 0
        sample = Sample(t=1, sequence=1, source="LIVE", altitude=123, phase="PREFLIGHT")
        c.accept(sample)
        c.timer.stop()
        w.base_route.setCurrentIndex(w.base_route.findData("away3"))
        assert c.selected_away_station == "away3"
        history = list(c.history)
        for index in range(5):
            w.base_tabs.setCurrentIndex(index)
            w.last_ui = 0
            w.refresh()
            qtbot.wait(20)
            assert c.latest is sample and list(c.history) == history
            assert w.rocket_state_badge.isVisible() and w.rocket_state_badge.state_name == "PREFLIGHT"
            assert w.base_route.isVisible() and w.metric_strip.isVisible()
            assert not w.companion.isVisible()
            assert w.flight_3d_dialog.timer.isActive() is (index == 4)
            if index:
                assert w.base_tabs.widget(index).horizontalScrollBar().maximum() == 0
        w.launch_links.selector.setCurrentIndex(w.launch_links.selector.findData("away1"))
        w.last_ui = 0
        w.refresh()
        assert w.base_route.currentData() == c.selected_away_station == "away1"
        assert c.workers == {"telemetry": None} and not c.can_command
        w.base_tabs.setCurrentIndex(0)
        monkeypatch.setattr(w.flight_3d_dialog, "load_reference", lambda: None)
        w.open_flight_3d()
        assert w.base_tabs.currentIndex() == 4 and w.away_flight_dialog is None
        w.arrange_displays()
        assert not w.companion.isVisible() and w.station_mode == "launch_v2"
    finally:
        w.close()
