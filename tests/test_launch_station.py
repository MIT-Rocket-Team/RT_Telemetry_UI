"""Launch routing is selectable without inventing a working station transport."""

import gc

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtWidgets import QTabWidget

from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow
from rocket_gnc_monitor.ui import MissionDialog
from rocket_gnc_monitor.wifi import WifiSelection, WifiSnapshot


@pytest.fixture(autouse=True)
def offline_wifi_status(monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.wifi_panel.read_wifi_status", WifiSnapshot)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


@pytest.mark.parametrize("role", ["telemetry", "video"])
@pytest.mark.parametrize("vehicle", ["balius", "iris"])
def test_launch_profile_has_one_telemetry_board_and_no_local_pointer(qtbot, tmp_path, monkeypatch, role, vehicle):
    w = StationWindow(tmp_path, profile=StationProfile("base", role, vehicle), auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        qtbot.wait(30)
        c = w.controller
        assert w.launch_station and c.board_layout == "launch"
        assert set(w.port_widgets) == set(c.workers) == set(c.states) == {"telemetry"}
        assert c.workers == {"telemetry": None}
        assert "Base station" in w.station_identity.text()
        assert w.wifi_panel is None
        assert w.usb_strip.isVisible() is (role == "telemetry")
        assert w.poll_button.isVisible() is (role == "telemetry")
        assert (w.launch_uplink is not None) is (role == "telemetry")
        assert not any(widget.isVisible() for widget in (
            w.virtual_connect, w.virtual_panel, w.pointer_pose, w.pointer_sent,
            w.pointer_status, w.freeze_gps, *w.pointer_controls,
        ))
        attempted = []
        monkeypatch.setattr(c, "connect", lambda *args: attempted.append(args))
        for key, action in w.legacy_actions.items():
            if key in {"pointer", "up", "down", "left", "right", "zero"} or (role == "video" and key != "log"):
                assert not action.isVisible() and not action.isEnabled()
                action.trigger()
        for serial_role in ("uplink", "pointer"):
            w.connect_role(serial_role)
            w.toggle_connection(serial_role)
        assert attempted == []
        assert not c.command_connected and not c.can_command
        assert c.command_block_reason == "Launch uplink switch protocol not defined"
        assert not any(button.isEnabled() for button in w.rocket_panel.buttons.values())
        assert not w.launch_links.isVisible()
        assert w.launch_links.selector.isVisible() is (role == "telemetry")
        assert not w.companion.isVisible()
    finally:
        w.close()


def test_launch_route_selection_changes_intent_only_and_never_marks_links_connected(qtbot, tmp_path, monkeypatch):
    w = StationWindow(tmp_path, profile=StationProfile("base", "telemetry", "iris"), auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        c, panel = w.controller, w.launch_links
        attempted = []
        monkeypatch.setattr(c, "connect", lambda *args: attempted.append(("connect", args)))
        monkeypatch.setattr(c, "send_rocket", lambda *args: attempted.append(("rocket", args)))
        monkeypatch.setattr(c, "dispatch_pointer", lambda *args: attempted.append(("pointer", args)))
        assert [panel.selector.itemData(i) for i in range(panel.selector.count())] == [
            None, "away1", "away2", "away3", "away4",
        ]
        for station in ("away4", "away1", "away3", "away2", None):
            panel.selector.setCurrentIndex(panel.selector.findData(station))
            w.last_ui = 0
            w.refresh()
            assert c.selected_away_station == station
            assert "transport not configured" in panel.status.text()
            assert not c.command_connected and not c.pointer_connected and not c.ground_connected
            assert c.workers == {"telemetry": None} and c.states == {"telemetry": "Disconnected"}
            assert c.latest is None
            assert not c.can_command
            assert not w.mount.isVisible()
            assert panel.selector.isVisible() and not panel.isVisible()
            assert "unavailable" in w.remote_pointer_caption.text()
            for key, label in panel.path_status.items():
                assert "unavailable" in label.text()
                assert ("Selected route" in label.text()) is (key == station)
        assert not attempted
        assert "Base station only" in panel.uplink.text()
        assert "transmission unavailable" in panel.uplink.text()
    finally:
        w.close()


def test_launch_mission_dialog_does_not_offer_a_local_pointer_location(qtbot, tmp_path):
    w = StationWindow(tmp_path, profile=StationProfile("base", "telemetry", "iris", "urrg"), auto_place=False)
    qtbot.addWidget(w)
    try:
        dialog = MissionDialog(w.controller.mission, w)
        qtbot.addWidget(dialog)
        tabs = dialog.findChild(QTabWidget)
        assert [tabs.tabText(i) for i in range(tabs.count())] == ["Mission"]
        assert "pointer_site_configured" not in dialog.fields
        assert not hasattr(dialog, "pointer_location")
        assert not dialog.mission.pointer_site_configured
        dialog.fields["name"].setText("Iris Launch revised")
        dialog.accept()
        assert dialog.mission.name == "Iris Launch revised"
        assert dialog.mission.launch_site_name == "URRG"
        assert not dialog.mission.pointer_site_configured
        assert w.controller.mission.name == "Iris Launch"
    finally:
        w.close()


@pytest.mark.parametrize("role", ["telemetry", "video"])
def test_away_has_wifi_selection_and_no_uplink_authority(qtbot, tmp_path, role):
    w = StationWindow(tmp_path, profile=StationProfile("away4", role, "iris"), auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        qtbot.wait(30)
        c, wifi = w.controller, w.wifi_panel
        assert not w.launch_station and w.launch_links is None
        assert wifi is not None and wifi.isVisible()
        assert set(c.workers) == {"telemetry", "pointer"}
        assert c.command_role is None and not c.can_command
        assert c.command_block_reason == "Away stations cannot transmit rocket commands"
        wifi.ssid.setEditText("Rocket Away 4")
        wifi.select_network()
        assert WifiSelection.load(wifi.selection_path).ssid == "Rocket Away 4"
        assert "Selected WLAN: Rocket Away 4" in wifi.status.text()
        assert not c.ground_connected and not c.pointer_connected
        assert not any(c.workers.values()) and not c.can_command
        if role == "telemetry":
            assert w.virtual_connect.isVisible() and w.point_button.isVisible()
            assert all(port[0].isVisible() for port in w.port_widgets.values())
            dialog = MissionDialog(c.mission, w)
            qtbot.addWidget(dialog)
            tabs = dialog.findChild(QTabWidget)
            assert [tabs.tabText(i) for i in range(tabs.count())] == ["Mission", "Antenna pointer"]
        else:
            assert not w.usb_strip.isVisible()
    finally:
        w.close()


def test_legacy_station_constructor_preserves_local_board_api(qtbot, tmp_path):
    w = StationWindow(tmp_path, auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        assert not w.profile_locked and not w.launch_station
        assert w.controller.board_layout == "legacy"
        assert set(w.port_widgets) == {"telemetry", "pointer"}
        assert w.launch_links is None and w.wifi_panel is None
        assert w.virtual_connect.isVisible() and w.point_button.isVisible()
    finally:
        w.close()


def test_main_badge_tracks_decoded_state_and_marks_stale_telemetry(qtbot, tmp_path):
    from rocket_gnc_monitor.protocol import TelemetryDecoder
    from test_protocol import frame

    w = StationWindow(tmp_path, profile=StationProfile("launch", "telemetry", "iris"), auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        c, badge = w.controller, w.rocket_state_badge
        c.timer.stop()
        assert badge.isVisible() and badge.window() is w
        assert badge.state_name == "NO TELEMETRY"
        assert badge.background_color == "#000000"
        c.latest = TelemetryDecoder().feed(frame())[0]
        c.mode = "REPLAY"
        w.last_ui = 0
        w.refresh()
        assert badge.state_name == "FLIGHT"
        assert badge.background_color == "#c83232"
        assert badge.status_label.text() == "REPLAY · Telemetry"
        c.mode = "LIVE"
        w.last_ui = 0
        w.refresh()
        assert badge.state_name == "FLIGHT"
        assert badge.status_label.text() == "LIVE · STALE · last telemetry"
        c.latest = None
        w.last_ui = 0
        w.refresh()
        assert badge.state_name == "NO TELEMETRY"
        assert badge.background_color == "#000000"
    finally:
        w.close()


@pytest.mark.parametrize("vehicle", ["balius", "iris"])
def test_launch_serial_shortcuts_feed_main_window_and_uplink_remains_unavailable(qtbot, tmp_path, monkeypatch, vehicle):
    from rocket_gnc_monitor.protocol import TelemetryDecoder
    from test_board_roles import Board
    from test_protocol import frame

    monkeypatch.setattr("rocket_gnc_monitor.controller.SerialWorker", Board)
    w = StationWindow(tmp_path, profile=StationProfile("launch", "telemetry", vehicle, "urrg"), auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        w.resize(1920, 1020)
        c = w.controller
        c.timer.stop()
        w.serial_devices = [{"device": "/test/launch-board"}]
        w.update_port_choices(force=True)
        w.last_ui = 0
        w.refresh()
        assert w.legacy_actions["ground"].isVisible() and w.legacy_actions["ground"].isEnabled()
        w.legacy_actions["ground"].trigger()
        c.tick()
        w.last_ui = 0
        w.refresh()
        assert c.ground_connected and w.poll_button.isEnabled()
        w.legacy_actions["poll"].trigger()
        board = c.workers["telemetry"]
        assert c.polling and board.polling
        sample = TelemetryDecoder().feed(frame())[0]
        c.enqueue(board.generation, "telemetry", "sample", sample)
        c.tick()
        w.last_ui = w.last_table = 0
        w.refresh()
        qtbot.wait(50)
        assert c.latest is sample and c.rocket_link_state()[0] == "RECEIVING"
        assert w.rocket_state_badge.state_name == "FLIGHT"
        assert w.rocket_state_badge.status_label.text() == "LIVE · Telemetry"
        assert "TELEMETRY LIVE" in w.secondary_health.text()
        assert not w.launch_uplink.toggle.isEnabled() and not c.can_command
        assert "UNKNOWN" in w.launch_uplink.status.text()
        assert not board.sent
        for table in (w.canard_panel.table, w.rocket_panel.telemetry, w.rocket_panel.pyros):
            assert table.verticalScrollBar().maximum() == 0
        c.select_away_station("away3")
        w.last_ui = 0
        w.refresh()
        assert not w.launch_uplink.isVisible() and not c.can_command
        assert "UPLINK UNKNOWN" in w.health.text()
        qtbot.wait(50)
        assert w.launch_links.selector.isVisible() and not w.launch_links.isVisible()
        w.launch_uplink.toggle.click()
        assert not c.can_command and not board.sent
        assert "UNKNOWN" in w.launch_uplink.status.text()
        c.switch_mode("REPLAY")
        w.last_ui = 0
        w.refresh()
        assert not w.launch_uplink.toggle.isEnabled()
        assert not c.ground_connected and "UNKNOWN" in w.launch_uplink.status.text()
    finally:
        w.close()


def test_main_base_is_live_only_with_one_window_and_visible_control_surface_rows(qtbot, tmp_path):
    from dataclasses import replace
    from rocket_gnc_monitor.balius_states import BALIUS_STATE_NAMES
    from rocket_gnc_monitor.domain import Sample
    from rocket_gnc_monitor.rocket_protocol import PHASE_ENUM

    w = StationWindow(tmp_path, profile=StationProfile("base", "telemetry", "balius"), auto_place=False)
    qtbot.addWidget(w)
    try:
        w.show()
        w.resize(1920, 1020)
        qtbot.wait(50)
        c = w.controller
        c.timer.stop()
        assert w.main_base and w.base_tabs is None and c.mode == "LIVE"
        assert not w.companion.isVisible()
        assert not any(tab.isVisible() for tab in w.findChildren(QTabWidget))
        assert {key for key, card in w.cards.items() if card.isVisible()} == {
            "attitude", "telemetry", "gps", "servos", "power", "commands",
            "cells", "bms", "recovery", "live_plots", "network", "diagnostics",
        }
        assert w.canard_panel.channels == tuple(f"Canard{i}" for i in range(1, 5)) + tuple(f"Tab{i}" for i in range(1, 5))
        assert w.canard_panel.table.isVisible() and not w.rocket_panel.servos.isVisible()
        assert all(not control.isEnabled() for controls in w.canard_panel.commands.values() for control in controls)
        assert w.rocket_panel.buttons["pd_activate"].text() == "Activate Controls"
        assert w.rocket_panel.buttons["advance_state"].objectName() == "danger"
        badge = w.rocket_state_badge
        assert badge.isVisible() and w.workspace_settings.isVisible()
        assert badge.mapTo(w, QPoint()).x() > w.workspace_settings.mapTo(w, QPoint()).x()
        recovery_position = w.flight_grid.getItemPosition(w.flight_grid.indexOf(w.cards["recovery"]))
        rightmost_column = max(w.flight_grid.getItemPosition(i)[1] for i in range(w.flight_grid.count()))
        assert recovery_position[0] == 0 and recovery_position[1] == rightmost_column
        assert not any(control.isVisible() for control in (w.mode, w.record_button, w.attitude.source))
        assert not any(action.isVisible() or action.isEnabled()
                       for action in (*w.flight_actions.values(), w.legacy_actions["log"]))
        assert not w.flight_3d_dialog.timer.isActive()
        w.open_flight_3d()
        assert not w.cards["flight3d"].isVisible() and not w.flight_3d_dialog.timer.isActive()
        sample = Sample(0, 1, "LIVE")
        for code, state in enumerate(BALIUS_STATE_NAMES):
            c.accept(replace(sample, t=float(code), sequence=code, phase=PHASE_ENUM[code], details={"state_code": code}))
            w.last_ui = w.last_table = 0
            w.refresh()
            assert badge.state_name == state
            assert w.rocket_panel.telemetry.item(0, 1).text() == state
            assert f"LIVE · {state}" in w.banner.text()
        assert c.workers == {"telemetry": None} and not c.can_command
    finally:
        w.close()
