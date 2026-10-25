"""URRG startup and editable station locations use offline, explicit coordinates."""

import copy
import gc
import json

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QDialog, QWidget

from rocket_gnc_monitor.domain import Mission
from rocket_gnc_monitor.flight import load_flight, save_flight
from rocket_gnc_monitor.location import decode_mgrs
from rocket_gnc_monitor.site_presets import URRG_LAUNCH_MGRS, URRG_STATION_MGRS, mission_for_station
from rocket_gnc_monitor.station_profile import StationProfile, load_startup_choices, save_startup_choices
from rocket_gnc_monitor.station_workspace import StationWindow
from rocket_gnc_monitor.ui import MissionDialog

# Independently checked by parsing UTM 18N square UN and applying the WGS84
# inverse Transverse Mercator series, rather than deriving expected values
# through the application's MGRS decoder.
STATIONS = [
    ("base", "18TUN2063730181", (42.7031920164, -77.1899920948)),
    ("away1", "18TUN2177730106", (42.7027823090, -77.1760591193)),
    ("away2", "18TUN2291333237", (42.7312211284, -77.1631773978)),
    ("away3", "18TUN2015031101", (42.7113572127, -77.1962260073)),
    ("away4", "18TUN2259229678", (42.6991192588, -77.1659811695)),
]


def pointer_coordinates(mission):
    return mission.pointer_latitude, mission.pointer_longitude, mission.pointer_altitude


@pytest.mark.parametrize("station,code,expected", STATIONS)
@pytest.mark.parametrize("vehicle", ["balius", "iris"])
def test_urrg_startup_mission_uses_selected_station_and_vehicle(station, code, expected, vehicle):
    mission = mission_for_station(StationProfile(station, "telemetry", vehicle, "urrg"))
    assert mission.name == f"{vehicle.title()} Launch"
    assert URRG_STATION_MGRS[station] == code
    if station == "base":
        assert not mission.pointer_site_configured
        assert mission.pointer_location_code == mission.pointer_site_name == ""
        assert pointer_coordinates(mission) == (0, 0, 0)
    else:
        assert mission.pointer_location_code == code
        assert (mission.pointer_latitude, mission.pointer_longitude) == pytest.approx(expected, abs=2e-9)
        assert mission.pointer_site_name == f"URRG:{station}"
        assert mission.pointer_location_format == "mgrs" and mission.pointer_site_configured
    assert mission.launch_site_name == "URRG" and mission.site_configured
    assert URRG_LAUNCH_MGRS == mission.launch_location_code == "18TUN2061530290"
    assert (mission.latitude, mission.longitude) == pytest.approx(
        (42.7041677688, -77.1902950169), abs=2e-9
    )
    assert mission.altitude == mission.altitude_msl == mission.pointer_altitude == 0
    assert not mission.pointer_calibrated


@pytest.mark.parametrize("vehicle", ["balius", "iris"])
def test_custom_startup_has_vehicle_name_without_establishing_locations(vehicle):
    mission = mission_for_station(StationProfile("away4", "video", vehicle, "custom"))
    assert mission.name == f"{vehicle.title()} Launch"
    assert not mission.site_configured and not mission.pointer_site_configured
    assert mission.launch_site_name == mission.pointer_site_name == ""
    assert mission.launch_location_code == mission.pointer_location_code == ""
    assert (mission.latitude, mission.longitude, mission.altitude) == (0, 0, 0)
    assert pointer_coordinates(mission) == (0, 0, 0)


@pytest.mark.parametrize("station,code,expected", STATIONS)
def test_station_preset_selection_preserves_elevations_and_round_trips(qtbot, tmp_path, station, code, expected):
    original = Mission(altitude=350, altitude_msl=310, pointer_altitude=347.25)
    dialog = MissionDialog(original)
    qtbot.addWidget(dialog)
    editor = dialog.pointer_location
    assert not editor.preset.isEnabled()
    assert editor.preset.count() == 6
    assert {editor.preset.itemData(i) for i in range(1, 6)} == {
        f"URRG:{key}" for key, _, _ in STATIONS
    }
    dialog.location.preset.setCurrentIndex(dialog.location.preset.findData("URRG"))
    assert editor.preset.isEnabled() and editor.preset.currentData() == ""
    assert not dialog.fields["pointer_site_configured"].isChecked()
    editor.preset.setCurrentIndex(editor.preset.findData(f"URRG:{station}"))
    assert editor.format.currentData() == "mgrs" and editor.mgrs.text() == code
    assert dialog.fields["pointer_site_configured"].isChecked()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    mission = dialog.mission
    assert (mission.pointer_latitude, mission.pointer_longitude) == pytest.approx(expected, abs=2e-9)
    assert (mission.altitude, mission.altitude_msl, mission.pointer_altitude) == (350, 310, 347.25)
    assert mission.launch_location_code == "18TUN2061530290"
    assert mission.pointer_site_name == f"URRG:{station}"
    assert original.pointer_site_name == "" and pointer_coordinates(original) == (0, 0, 347.25)

    mission.save(tmp_path / "mission.json")
    loaded = Mission.load(tmp_path / "mission.json")
    save_flight(tmp_path / "flight.rktflight", mission)
    restored = load_flight(tmp_path / "flight.rktflight", tmp_path / "cache").mission
    for saved in (loaded, restored):
        assert pointer_coordinates(saved) == pointer_coordinates(mission)
        assert saved.pointer_site_name == mission.pointer_site_name
        assert saved.pointer_location_code == code
    reopened = MissionDialog(restored)
    qtbot.addWidget(reopened)
    assert reopened.pointer_location.preset.currentData() == f"URRG:{station}"
    assert reopened.pointer_location.mgrs.text() == code
    for kind in ("latlon", "mgrs"):
        reopened.pointer_location.format.setCurrentIndex(reopened.pointer_location.format.findData(kind))
    assert reopened.pointer_location.mgrs.text() == code
    reopened.accept()
    assert pointer_coordinates(reopened.mission) == pointer_coordinates(mission)
    assert reopened.mission.pointer_location_code == code

    old = json.loads((tmp_path / "mission.json").read_text())
    old.pop("pointer_site_name")
    (tmp_path / "old.json").write_text(json.dumps(old))
    older = Mission.load(tmp_path / "old.json")
    assert older.pointer_site_name == "" and pointer_coordinates(older) == pointer_coordinates(mission)


def test_display_format_and_altitude_edits_keep_preset_without_coordinate_drift(qtbot):
    mission = mission_for_station(StationProfile("away2", launch_site="urrg"))
    mission.pointer_altitude = 321.75
    dialog = MissionDialog(mission)
    qtbot.addWidget(dialog)
    editor = dialog.pointer_location
    for kind in ("latlon", "mgrs", "latlon", "mgrs"):
        editor.format.setCurrentIndex(editor.format.findData(kind))
        assert editor.preset.currentData() == "URRG:away2"
        if kind == "mgrs":
            assert editor.mgrs.text() == "18TUN2291333237"
    editor.altitude.setValue(345.5)
    dialog.accept()
    assert dialog.mission.pointer_site_name == "URRG:away2"
    assert dialog.mission.pointer_location_code == "18TUN2291333237"
    assert pointer_coordinates(dialog.mission) == (
        mission.pointer_latitude, mission.pointer_longitude, 345.5
    )


@pytest.mark.parametrize("kind", ["latlon", "mgrs"])
def test_manual_pointer_coordinate_edits_clear_preset(qtbot, kind):
    dialog = MissionDialog(mission_for_station(StationProfile("away2", launch_site="urrg")))
    qtbot.addWidget(dialog)
    editor = dialog.pointer_location
    editor.format.setCurrentIndex(editor.format.findData(kind))
    if kind == "latlon":
        editor.latitude.setValue(42.125)
    else:
        editor.mgrs.setText("18TUN2063730181")
    assert editor.preset.currentData() == ""
    dialog.accept()
    assert dialog.mission.pointer_site_name == ""
    if kind == "latlon":
        assert dialog.mission.pointer_latitude == 42.125
    else:
        point = decode_mgrs("18TUN2063730181")
        assert pointer_coordinates(dialog.mission)[:2] == (point.latitude, point.longitude)


def test_relative_to_preset_keeps_current_resolved_altitude(qtbot):
    mission = mission_for_station(StationProfile("base", launch_site="urrg"))
    mission.altitude, mission.pointer_altitude = 350, 345
    dialog = MissionDialog(mission)
    qtbot.addWidget(dialog)
    editor = dialog.pointer_location
    editor.format.setCurrentIndex(editor.format.findData("relative"))
    assert editor.preset.currentData() == ""
    editor.height_difference.setValue(-12.75)
    dialog.fields["altitude"].setValue(390)
    assert editor.value()[1] == 377.25
    editor.preset.setCurrentIndex(editor.preset.findData("URRG:away4"))
    assert editor.altitude.value() == 377.25 and editor.format.currentData() == "mgrs"
    dialog.accept()
    assert dialog.mission.pointer_site_name == "URRG:away4"
    assert dialog.mission.pointer_altitude == 377.25
    assert dialog.mission.pointer_location_code == "18TUN2259229678"


@pytest.mark.parametrize("clear_launch", ["preset", "manual"])
def test_clearing_or_reselecting_launch_never_relocates_absolute_pointer(qtbot, clear_launch):
    mission = mission_for_station(StationProfile("away3", launch_site="urrg"))
    mission.pointer_altitude = 302.5
    dialog = MissionDialog(mission)
    qtbot.addWidget(dialog)
    if clear_launch == "preset":
        dialog.location.preset.setCurrentIndex(0)
    else:
        dialog.location.mgrs.setText("18TUN2177730106")
    assert not dialog.pointer_location.preset.isEnabled()
    assert dialog.pointer_location.preset.currentData() == ""
    dialog.accept()
    assert pointer_coordinates(dialog.mission) == pointer_coordinates(mission)
    assert dialog.mission.pointer_site_name == ""
    dialog.location.preset.setCurrentIndex(dialog.location.preset.findData("URRG"))
    assert dialog.pointer_location.preset.isEnabled()
    assert dialog.pointer_location.preset.currentData() == ""
    dialog.accept()
    assert pointer_coordinates(dialog.mission) == pointer_coordinates(mission)


def test_saved_mission_is_not_relocated_to_current_startup_station(qtbot):
    parent = QWidget()
    parent.profile = StationProfile("away4", launch_site="urrg")
    qtbot.addWidget(parent)
    mission = mission_for_station(StationProfile("away1", launch_site="urrg"))
    mission.pointer_altitude = 301.25
    dialog = MissionDialog(mission, parent)
    editor = dialog.pointer_location
    assert "this station" in editor.preset.itemText(editor.preset.findData("URRG:away4"))
    assert editor.preset.currentData() == "URRG:away1"
    parent.profile = StationProfile("base", launch_site="custom")
    dialog.accept()
    assert dialog.mission.pointer_site_name == "URRG:away1"
    assert pointer_coordinates(dialog.mission) == pointer_coordinates(mission)


@pytest.fixture
def station_window_factory(qtbot, monkeypatch):
    def no_device(*args, **kwargs):
        raise AssertionError("A station preset operation tried to open a physical device")

    monkeypatch.setattr("rocket_gnc_monitor.controller.SerialWorker", no_device)
    monkeypatch.setattr("rocket_gnc_monitor.controller.VideoWorker", no_device)
    monkeypatch.setattr("rocket_gnc_monitor.ui.ports", list)
    monkeypatch.setattr("rocket_gnc_monitor.ui.camera_devices", list)
    monkeypatch.setattr("rocket_gnc_monitor.station_workspace.camera_devices", list)
    def create(data_dir, profile):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()
        window = StationWindow(data_dir, profile=profile, auto_place=False)
        qtbot.addWidget(window)
        return window

    return create


@pytest.mark.parametrize("profile", [
    StationProfile("away4", "telemetry", "iris", "urrg"),
    StationProfile("away2", "video", "balius", "custom"),
])
def test_startup_profile_reaches_controller_and_saved_missions_take_precedence(
    station_window_factory, tmp_path, monkeypatch, profile
):
    window = station_window_factory(tmp_path / "station", profile)
    try:
        controller = window.controller
        expected = mission_for_station(profile)
        assert controller.mission == expected
        assert window.mission_label.text() == expected.name
        assert not any(controller.workers.values())
        assert not any(stream.worker for stream in controller.video_streams.values())
        assert not controller.polling and controller.recorder is None

        saved = mission_for_station(StationProfile("away1", "telemetry", "balius", "urrg"))
        saved.name, saved.pointer_altitude = "Saved field mission", 301.25
        saved.save(tmp_path / "saved.json")
        monkeypatch.setattr(
            "rocket_gnc_monitor.ui.QFileDialog.getOpenFileName",
            lambda *args: (str(tmp_path / "saved.json"), "Mission (*.json)"),
        )
        window.load_mission()
        assert controller.mission == saved
        assert window.profile == profile
        save_flight(tmp_path / "saved.rktflight", saved)
        controller.apply_flight(load_flight(tmp_path / "saved.rktflight", tmp_path / "cache"))
        assert controller.mission == saved
        assert window.profile == profile
    finally:
        window.close()


def test_forget_setup_is_shared_by_file_menus_and_only_removes_startup_opt_in(
    station_window_factory, tmp_path
):
    profile = StationProfile("base", "telemetry", "iris", "urrg")
    data_dir = tmp_path / "station"
    save_startup_choices(profile, data_dir, True)
    profile.save(data_dir)
    (data_dir / "station-layout.json").write_text('{"station": "away"}\n')
    window = station_window_factory(data_dir, profile)
    try:
        controller = window.controller
        active_mission = controller.mission
        active_mission.name = "Field mission already in progress"
        active_mission.pointer_altitude = 327.5
        before_mission = copy.deepcopy(active_mission)
        before_settings = copy.deepcopy(controller.settings)
        active_mission.save(data_dir / "mission.json")
        controller.settings.save(data_dir / "settings.json")
        preserved = {
            name: (data_dir / name).read_bytes()
            for name in ("mission.json", "settings.json", "station-profile.json", "station-layout.json")
        }
        assert load_startup_choices(data_dir) == (profile, True)
        file_menus = [
            next(action.menu() for action in owner.menuBar().actions() if action.text() == "File")
            for owner in (window, window.companion)
        ]
        actions = [
            next(action for action in menu.actions() if action.text() == "Forget setup on next launch")
            for menu in file_menus
        ]
        assert actions[0] is actions[1]
        assert actions[0].isVisible() and actions[0].isEnabled()
        assert actions[0].shortcut().isEmpty()
        actions[1].trigger()

        assert not (data_dir / "startup-choices.json").exists()
        assert load_startup_choices(data_dir) == (StationProfile(), False)
        assert controller.mission is active_mission and controller.mission == before_mission
        assert controller.settings == before_settings and window.profile == profile
        assert {name: (data_dir / name).read_bytes() for name in preserved} == preserved
    finally:
        window.close()
