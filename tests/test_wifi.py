from concurrent.futures import Future
import json
import subprocess

import pytest
from PySide6.QtCore import Qt

from rocket_gnc_monitor.wifi import WifiSelection, WifiSnapshot, read_wifi_status, wifi_settings_url
from rocket_gnc_monitor.wifi_panel import WifiPanel


def test_selection_roundtrip_atomic_failure_and_invalid_file(tmp_path, monkeypatch):
    path = tmp_path / "station-wifi.json"
    assert WifiSelection.load(path) == WifiSelection()
    selected = WifiSelection("URRG Away 3")
    selected.save(path)
    assert WifiSelection.load(path) == selected
    original = path.read_bytes()

    def failed_replace(*_):
        raise OSError("Disk unavailable")

    monkeypatch.setattr("rocket_gnc_monitor.wifi.os.replace", failed_replace)
    with pytest.raises(OSError, match="Disk unavailable"):
        WifiSelection("Other Wi-Fi").save(path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
    path.write_text(json.dumps({"schema_version": 2, "ssid": "Wrong schema"}))
    with pytest.raises(ValueError, match="Unsupported Wi-Fi selection version"):
        WifiSelection.load(path)


@pytest.mark.parametrize("ssid", ["a" * 33, "é" * 17, "network\nname", "nul\x00name", 12])
def test_invalid_ssid_is_rejected(ssid):
    with pytest.raises(ValueError):
        WifiSelection(ssid).validate()


def test_mac_read_only_discovery_reports_saved_names_not_scan(monkeypatch):
    calls = []
    responses = [
        "Hardware Port: Ethernet\nDevice: en0\nEthernet Address: 00:00:00:00:00:01\n\n"
        "Hardware Port: Wi-Fi\nDevice: en7\nEthernet Address: 00:00:00:00:00:02\n",
        "Current Wi-Fi Network: URRG Away 3\n",
        "Preferred networks on en7:\n\tURRG Away 3\n\tTeam Wi-Fi\n\tTeam Wi-Fi\n",
    ]

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0, stdout=responses.pop(0))

    monkeypatch.setattr("rocket_gnc_monitor.wifi.subprocess.run", run)
    snapshot = read_wifi_status("Darwin")
    assert snapshot.current_ssids == ("URRG Away 3",)
    assert snapshot.saved_ssids == ("URRG Away 3", "Team Wi-Fi")
    assert [call[0] for call in calls] == [
        ["/usr/sbin/networksetup", "-listallhardwareports"],
        ["/usr/sbin/networksetup", "-getairportnetwork", "en7"],
        ["/usr/sbin/networksetup", "-listpreferredwirelessnetworks", "en7"],
    ]
    assert all(call[1]["timeout"] == 4 and not call[1].get("shell") for call in calls)


def test_mac_redacted_current_name_is_unknown_and_saved_selection_still_available(monkeypatch):
    responses = iter([
        "Hardware Port: AirPort\nDevice: en1\n",
        "You are not associated with an AirPort network.\n",
        "Preferred networks on en1:\n\tURRG Away 4\n",
    ])
    monkeypatch.setattr("rocket_gnc_monitor.wifi._run", lambda _: next(responses))
    snapshot = read_wifi_status("Darwin")
    assert not snapshot.current_ssids
    assert snapshot.saved_ssids == ("URRG Away 4",)
    assert "did not report" in snapshot.detail and "permissions" in snapshot.detail


def test_command_failure_is_graceful(monkeypatch):
    def timeout(arguments):
        raise subprocess.TimeoutExpired(arguments, 4)

    monkeypatch.setattr("rocket_gnc_monitor.wifi._run", timeout)
    assert "unavailable" in read_wifi_status("Darwin").detail
    assert "unavailable" in read_wifi_status("Windows").detail
    assert "manually" in read_wifi_status("Linux").detail


def test_mac_explicit_redaction_does_not_become_a_connected_network(monkeypatch):
    responses = iter([
        "Hardware Port: Wi-Fi\nDevice: en1\n",
        "Current Wi-Fi Network: <redacted>\n",
        "Preferred networks on en1:\n\tAway 1\n",
    ])
    monkeypatch.setattr("rocket_gnc_monitor.wifi._run", lambda _: next(responses))
    snapshot = read_wifi_status("Darwin")
    assert snapshot.current_ssids == ()
    assert snapshot.saved_ssids == ("Away 1",)
    assert "redacted" in snapshot.detail


def test_windows_current_ssids_exclude_bssids_and_support_multiple_adapters(monkeypatch):
    calls = []

    def run(arguments):
        calls.append(arguments)
        return "    SSID                   : Away 1\n    BSSID : aa:bb:cc:dd:ee:ff\n    SSID : Backup\n"

    monkeypatch.setattr("rocket_gnc_monitor.wifi._run", run)
    snapshot = read_wifi_status("Windows")
    assert snapshot.current_ssids == ("Away 1", "Backup")
    assert snapshot.saved_ssids == ()
    assert calls == [["netsh", "wlan", "show", "interfaces"]]
    monkeypatch.setattr("rocket_gnc_monitor.wifi._run", lambda _: "Zugriff verweigert.\n")
    assert not read_wifi_status("Windows").current_ssids


@pytest.fixture
def panel_factory(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.wifi_panel.read_wifi_status", lambda: WifiSnapshot(
        ("Office Wi-Fi",), ("Office Wi-Fi", "URRG Away 2"), ""
    ))
    panels = []

    def create():
        panel = WifiPanel(tmp_path)
        panels.append(panel)
        qtbot.addWidget(panel)
        qtbot.waitUntil(lambda: panel._future is None)
        return panel

    yield create
    for panel in panels:
        panel.cleanup()


def test_selecting_network_only_saves_intent_and_does_not_claim_connection(panel_factory, tmp_path):
    panel = panel_factory()
    assert not (tmp_path / "station-wifi.json").exists()
    panel.ssid.setEditText("URRG Away 2")
    panel.select_button.click()
    assert WifiSelection.load(tmp_path / "station-wifi.json").ssid == "URRG Away 2"
    assert "Selected WLAN: URRG Away 2" in panel.status.text()
    assert "OS Wi-Fi: Office Wi-Fi" in panel.status.text()
    assert "Matches selection" not in panel.status.text()
    reopened = panel_factory()
    assert reopened.ssid.currentText() == "URRG Away 2"
    assert "OS Wi-Fi: Office Wi-Fi" in reopened.status.text()
    reopened.ssid.setEditText("")
    reopened.select_button.click()
    assert WifiSelection.load(tmp_path / "station-wifi.json").ssid == ""


def test_status_refresh_retains_unsaved_manual_draft(panel_factory, qtbot):
    panel = panel_factory()
    panel.ssid.setEditText("new manual WLAN")
    panel.refresh_button.click()
    qtbot.waitUntil(lambda: panel._future is None)
    assert panel.ssid.currentText() == "new manual WLAN"
    assert panel.selection.ssid == ""
    assert "Selected WLAN: None" in panel.status.text()


def test_ssid_markup_is_displayed_as_literal_text(panel_factory):
    panel = panel_factory()
    ssid = "<b>Away & 3</b>"
    panel.ssid.setEditText(ssid)
    panel.select_network()
    panel.snapshot = WifiSnapshot((ssid,))
    panel._render_status()
    assert panel.status.textFormat() == Qt.TextFormat.PlainText
    assert f"Selected WLAN: {ssid} · OS Wi-Fi: {ssid}" in panel.status.text()
    assert panel.selection.ssid == ssid
    assert "Matches selection" in panel.status.text()


def test_wifi_status_does_not_block_gui_and_cleanup_is_idempotent(qtbot, tmp_path, monkeypatch):
    future = Future()

    class PendingExecutor:
        def __init__(self, **_):
            self.closed = False

        def submit(self, function):
            return future

        def shutdown(self, *, wait, cancel_futures):
            assert not wait and cancel_futures
            self.closed = True

    monkeypatch.setattr("rocket_gnc_monitor.wifi_panel.ThreadPoolExecutor", PendingExecutor)
    panel = WifiPanel(tmp_path)
    qtbot.addWidget(panel)
    assert panel._future is future and not panel.refresh_button.isEnabled()
    panel.ssid.setEditText("Away network")
    panel.select_button.click()
    assert panel.selection.ssid == "Away network"
    panel.cleanup()
    panel.cleanup()
    assert panel._executor.closed and not panel._poll.isActive()
    future.set_result(WifiSnapshot(("Away network",)))
    panel._finish_refresh()
    assert not panel.snapshot.current_ssids


def test_open_native_settings_is_explicit_and_no_credentials_are_captured(panel_factory, monkeypatch):
    calls = []
    monkeypatch.setattr("rocket_gnc_monitor.wifi_panel.wifi_settings_url", lambda: wifi_settings_url("Darwin"))
    monkeypatch.setattr("rocket_gnc_monitor.wifi_panel.QDesktopServices.openUrl", lambda url: calls.append(url) or True)
    panel = panel_factory()
    assert calls == []
    panel.settings_button.click()
    assert [url.toString() for url in calls] == ["x-apple.systempreferences:com.apple.wifi-settings-extension"]
    assert wifi_settings_url("Windows") == "ms-settings:network-wifi"
    assert wifi_settings_url("Linux") == ""


def test_corrupt_selection_surfaces_error_without_overwriting(panel_factory, tmp_path):
    path = tmp_path / "station-wifi.json"
    path.write_text("{broken")
    panel = panel_factory()
    assert "Could not load Wi-Fi selection" in panel.status.text()
    assert path.read_text() == "{broken"
