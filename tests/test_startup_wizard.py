import sys
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QWizard

from rocket_gnc_monitor.station_profile import StationProfile, load_startup_choices, save_startup_choices
from rocket_gnc_monitor.startup_wizard import StartupWizard


def test_wizard_preserves_defaults_and_presents_four_required_pages(qtbot):
    profile = StationProfile("away4", "video", "iris", "urrg")
    wizard = StartupWizard(profile)
    qtbot.addWidget(wizard)
    wizard.show()
    assert wizard.profile == profile
    assert not wizard.remember_choices
    assert len(wizard.pageIds()) == 4
    assert len(wizard.choices["station"]) == 5
    assert wizard.currentId() == 0
    assert wizard.button(QWizard.WizardButton.CancelButton).isVisible()
    wizard.next()
    assert wizard.currentId() == 1
    wizard.next()
    assert wizard.currentId() == 2
    wizard.next()
    assert wizard.currentId() == 3
    assert wizard.button(QWizard.WizardButton.FinishButton).text() == "Open station"
    assert wizard.button(QWizard.WizardButton.CancelButton).isVisible()
    assert "Away station 4 · Video · Iris" in wizard.summary.text()
    assert "Local USB inputs: Sustainer Digital, Booster Analog." in wizard.summary.text()
    assert "Sustainer Analog" not in wizard.summary.text()
    assert "Mission: Iris Launch." in wizard.summary.text()
    assert "URRG launch pad: 18TUN2061530290" in wizard.summary.text()
    assert "Away station 4: 18TUN2259229678" in wizard.summary.text()
    assert "elevations separately" in wizard.summary.text()


def test_wizard_selection_and_back_navigation_keep_station_identity(qtbot):
    wizard = StartupWizard()
    qtbot.addWidget(wizard)
    wizard.show()
    wizard.choices["station"]["away2"].setChecked(True)
    wizard.next()
    wizard.choices["role"]["video"].setChecked(True)
    wizard.next()
    wizard.choices["vehicle"]["iris"].setChecked(True)
    assert wizard.profile == StationProfile("away2", "video", "iris")
    wizard.next()
    wizard.choices["launch_site"]["urrg"].setChecked(True)
    assert wizard.profile == StationProfile("away2", "video", "iris", "urrg")
    wizard.back()
    wizard.back()
    wizard.back()
    assert wizard.choices["station"]["away2"].isChecked()
    wizard.choices["station"]["base"].setChecked(True)
    assert "Local USB inputs: Sustainer Digital." in wizard.summary.text()
    assert "connection pending" in wizard.summary.text()
    assert "Booster Analog" not in wizard.summary.text()
    assert "Launch station reference: 18TUN2063730181" in wizard.summary.text()
    wizard.choices["launch_site"]["custom"].setChecked(True)
    assert "URRG launch pad" not in wizard.summary.text()
    assert "Set the launch location in Mission profile; choose an away antenna in the workspace." in wizard.summary.text()


@pytest.mark.parametrize("station,code", [
    ("base", "18TUN2063730181"), ("away1", "18TUN2177730106"),
    ("away2", "18TUN2291333237"), ("away3", "18TUN2015031101"),
    ("away4", "18TUN2259229678"),
])
def test_wizard_summary_tracks_selected_station_and_vehicle(qtbot, station, code):
    wizard = StartupWizard(StationProfile(launch_site="urrg"))
    qtbot.addWidget(wizard)
    wizard.choices["station"][station].setChecked(True)
    site_label = "Launch station reference" if station == "base" else wizard.profile.station_label
    assert f"{site_label}: {code}" in wizard.summary.text()
    assert "Mission: Balius Launch." in wizard.summary.text()
    wizard.choices["vehicle"]["iris"].setChecked(True)
    assert "Mission: Iris Launch." in wizard.summary.text()


def test_iris_summary_distinguishes_sustainer_and_booster_telemetry(qtbot):
    wizard = StartupWizard(StationProfile("away1", "telemetry", "iris"))
    qtbot.addWidget(wizard)
    assert "Telemetry assignment: Sustainer." in wizard.summary.text()
    wizard.choices["station"]["away4"].setChecked(True)
    assert "Telemetry assignment: Booster." in wizard.summary.text()
    wizard.choices["station"]["base"].setChecked(True)
    assert "Telemetry assignment: Sustainer, Booster." in wizard.summary.text()
    wizard.choices["station"]["away3"].setChecked(True)
    wizard.choices["role"]["video"].setChecked(True)
    assert "Local USB inputs: Sustainer Digital, Sustainer Analog." in wizard.summary.text()


@pytest.mark.parametrize("remembered", [False, True])
def test_cancel_startup_does_not_create_controller_or_save_profile(qapp, tmp_path, monkeypatch, remembered):
    from PySide6 import QtWidgets
    from rocket_gnc_monitor import __main__ as entry
    from rocket_gnc_monitor.controller import Controller

    def forbidden(*args, **kwargs):
        raise AssertionError("Cancelling setup must not construct a controller")

    if remembered:
        save_startup_choices(StationProfile("away2", "video", "iris", "urrg"), tmp_path, True)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    monkeypatch.setattr(Controller, "__init__", forbidden)

    def cancel(wizard):
        assert wizard.remember_choices == remembered
        wizard.remember_checkbox.setChecked(not remembered)
        wizard.choices["station"]["away4"].setChecked(True)
        return wizard.DialogCode.Rejected

    monkeypatch.setattr(StartupWizard, "exec", cancel)
    monkeypatch.setattr(sys, "argv", ["rocket-gnc-monitor", "--data-dir", str(tmp_path)])
    with monkeypatch.context() as patch:
        patch.setattr(QtWidgets, "QApplication", lambda *_: qapp)
        assert entry.main() == 0
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("remember", [False, True])
def test_accepted_startup_only_saves_choices_when_operator_opts_in(qapp, tmp_path, monkeypatch, remember):
    from PySide6 import QtWidgets
    from rocket_gnc_monitor import __main__ as entry, station_workspace

    selected = StationProfile("away2", "video", "iris", "urrg")
    opened = []
    StationProfile("away4", "video", "iris", "urrg").save(tmp_path)
    legacy_path = tmp_path / "station-profile.json"
    legacy_contents = legacy_path.read_bytes()

    def choose(wizard):
        assert wizard.profile == StationProfile()
        assert not wizard.remember_choices
        for key, value in selected.to_dict().items():
            wizard.choices[key][value].setChecked(True)
        wizard.remember_checkbox.setChecked(remember)
        return wizard.DialogCode.Accepted

    class Window:
        def __init__(self, data, *, profile, auto_place):
            assert load_startup_choices(data) == ((selected, True) if remember else (StationProfile(), False))
            opened.append(profile)
            self.controller = SimpleNamespace(video_streams=dict.fromkeys(profile.local_channels))

        def show(self):
            pass

    monkeypatch.setattr(station_workspace, "StationWindow", Window)
    monkeypatch.setattr(StartupWizard, "exec", choose)
    monkeypatch.setattr(sys, "argv", ["rocket-gnc-monitor", "--data-dir", str(tmp_path)])
    monkeypatch.setattr(qapp, "exec", lambda: 0)
    with monkeypatch.context() as patch:
        patch.setattr(QtWidgets, "QApplication", lambda *_: qapp)
        assert entry.main() == 0
    assert opened == [selected]
    assert legacy_path.read_bytes() == legacy_contents


@pytest.mark.parametrize("skip_setup", [False, True])
@pytest.mark.parametrize("remembered", [False, True])
@pytest.mark.parametrize("remember_flag", [None, "--remember-setup", "--no-remember-setup"])
def test_cli_preselects_all_wizard_choices_and_can_open_directly(
        qapp, tmp_path, monkeypatch, skip_setup, remembered, remember_flag):
    from PySide6 import QtWidgets
    from rocket_gnc_monitor import __main__ as entry, station_workspace

    selected = StationProfile("away3", "video", "iris", "urrg")
    original = StationProfile("away1", "telemetry", "balius", "custom")
    if remembered:
        save_startup_choices(original, tmp_path, True)
    remember = remembered if remember_flag is None else remember_flag == "--remember-setup"
    visited = []
    opened = []

    def choose(wizard):
        assert not skip_setup, "Explicit --skip-setup must bypass the wizard"
        assert wizard.remember_choices == remember
        visited.append(wizard.profile)
        return wizard.DialogCode.Accepted

    class Window:
        def __init__(self, data, *, profile, auto_place):
            opened.append(profile)
            self.controller = SimpleNamespace(video_streams=dict.fromkeys(profile.local_channels))

        def show(self):
            pass

    monkeypatch.setattr(station_workspace, "StationWindow", Window)
    monkeypatch.setattr(StartupWizard, "exec", choose)
    arguments = ["rocket-gnc-monitor", "--data-dir", str(tmp_path), "--site", "away3",
                 "--role", "video", "--vehicle", "iris", "--launch-site", "urrg"]
    if skip_setup:
        arguments.append("--skip-setup")
    if remember_flag is not None:
        arguments.append(remember_flag)
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(qapp, "exec", lambda: 0)
    with monkeypatch.context() as patch:
        patch.setattr(QtWidgets, "QApplication", lambda *_: qapp)
        assert entry.main() == 0
    assert opened == [selected]
    assert visited == ([] if skip_setup else [selected])
    expected = (original, True) if remembered else (StationProfile(), False)
    if not skip_setup or remember_flag is not None:
        expected = (selected, True) if remember else (StationProfile(), False)
    assert load_startup_choices(tmp_path) == expected


def test_launch_wizard_describes_one_local_board_and_remote_pointer(qtbot):
    wizard = StartupWizard(StationProfile("launch", "telemetry", "iris", "urrg"))
    qtbot.addWidget(wizard)
    choice = wizard.choices["station"]["base"]
    assert choice.accessibleName() == "Launch station"
    assert "four LTU-XR links" in choice.text()
    assert "One telemetry board" in choice.text()
    summary = wizard.summary.text()
    assert "Ethernet / PoE to four LTU-XR links" in summary
    assert "Choose an away antenna pointer" in summary
    assert "One telemetry board: downlink reception and polling." in summary
    assert "Uplink enable: firmware pending; DEMO simulation only." in summary
    assert "One shared board target." in summary
    assert "no local antenna pointer" in summary
    assert "Base" not in summary
    assert "uplink boards" not in summary


def test_launch_wizard_limits_board_description_to_telemetry_role(qtbot):
    wizard = StartupWizard(StationProfile("launch", "telemetry", "balius"))
    qtbot.addWidget(wizard)
    assert "One telemetry board" in wizard.summary.text()
    assert "One shared board target" not in wizard.summary.text()
    wizard.choices["role"]["video"].setChecked(True)
    assert "Local USB inputs: Digital." in wizard.summary.text()
    assert "One telemetry board" not in wizard.summary.text()
    assert "Uplink enable" not in wizard.summary.text()
