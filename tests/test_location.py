"""Known external reference points and launch-dialog persistence, with no network."""

import json
import math
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox
from rocket_gnc_monitor.domain import Mission, to_enu
from rocket_gnc_monitor.flight import save_flight, load_flight
from rocket_gnc_monitor.location import (
    coordinates,
    decode_mgrs,
    decode_plus_code,
    encode_mgrs,
    encode_plus_code,
    pointer_from_launch,
)
from rocket_gnc_monitor.ui import MissionDialog


@pytest.mark.parametrize(
    "code,expected",
    [
        ("15TWG0000049776", (41.999997975128, -93.0)),  # Published mgrs package example.
        ("31U DQ 48251 11932", (48.8581938379, 2.2944892452)),  # Eiffel Tower.
        ("zab0000044542", (84.9999945967, 0.0)),  # UPS polar grid.
    ],
)
def test_mgrs_reference_points(code, expected):
    point = decode_mgrs(code)
    assert (point.latitude, point.longitude) == pytest.approx(expected, abs=0.0001)
    assert "1 m grid" in point.note


def test_mgrs_precision_is_explicit_and_invalid_grids_rejected():
    assert "100,000 m" in decode_mgrs("31UDQ").note
    assert "100 m" in decode_mgrs("31UDQ482119").note
    for invalid in [
        "",
        "61UDQ4825111932",
        "0UDQ4825111932",
        "19ICG1234512345",
        "19TCG123451234",
        "19TCG123456123456",
        "19TCG12\x0034",
    ]:
        with pytest.raises(ValueError):
            decode_mgrs(invalid)


def test_plus_code_reference_short_resolution_and_validation():
    point = decode_plus_code(" 8fvc9g8f+6x ")
    assert (point.latitude, point.longitude) == pytest.approx((47.3655625, 8.5249375))
    full = decode_plus_code("87JC9W64+4C")
    short = decode_plus_code("9w64+4c", (42.36, -71.09))
    assert short == full
    with pytest.raises(ValueError, match="nearby"):
        decode_plus_code("9W64+4C")
    for invalid in ["not a code", "ZZZZZZZZ+ZZ", "87JC9W64+4", "87JC9W64+4C Cambridge, MA"]:
        with pytest.raises(ValueError):
            decode_plus_code(invalid)
    with pytest.raises(ValueError):
        decode_plus_code("9W64+4C", (100, -71))
    assert decode_plus_code("849VCWC8+R9").longitude == pytest.approx(-122.0840625)


def test_coordinate_validation_and_display_encodings():
    for lat, lon in [(42.36037, -71.09355), (-33.8566, 151.2153), (85, 0), (-85, 0), (0, 179.9999)]:
        m = decode_mgrs(encode_mgrs(lat, lon))
        p = decode_plus_code(encode_plus_code(lat, lon))
        assert (m.latitude, m.longitude) == pytest.approx((lat, lon), abs=0.0001)
        assert (p.latitude, p.longitude) == pytest.approx((lat, lon), abs=0.0001)
    for lat, lon in [(91, 0), (0, 181), (float("nan"), 0), (0, float("inf"))]:
        with pytest.raises(ValueError):
            coordinates(lat, lon)


def test_mission_location_formats_save_and_reopen_without_coordinate_drift(qtbot, tmp_path):
    for kind, code in [("mgrs", "15TWG0000049776"), ("pluscode", "8FVC9G8F+6X")]:
        original = Mission(altitude=123, altitude_msl=110, site_configured=True)
        dialog = MissionDialog(original)
        qtbot.addWidget(dialog)
        dialog.location.format.setCurrentIndex(dialog.location.format.findData(kind))
        field = dialog.location.mgrs if kind == "mgrs" else dialog.location.plus_code
        field.setText(code)
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        mission = dialog.mission
        assert mission.altitude == 123 and mission.altitude_msl == 110
        assert original.latitude == 0 and original.longitude == 0
        assert mission.launch_location_format == kind and mission.launch_location_code == code
        filename = tmp_path / (kind + ".json")
        mission.save(filename)
        loaded = Mission.load(filename)
        reopened = MissionDialog(loaded)
        qtbot.addWidget(reopened)
        assert reopened.location.format.currentData() == kind
        reopened.accept()
        assert (reopened.mission.latitude, reopened.mission.longitude) == (
            mission.latitude,
            mission.longitude,
        )
        # Changing the display format alone must not quantize or move the launch site.
        for index in (0, 1, 2, 0):
            reopened.location.format.setCurrentIndex(index)
        reopened.accept()
        assert (reopened.mission.latitude, reopened.mission.longitude) == (
            mission.latitude,
            mission.longitude,
        )
    path = tmp_path / "old-mission.json"
    original.save(path)
    data = json.loads(path.read_text())
    data.pop("launch_location_format")
    data.pop("launch_location_code")
    path.write_text(json.dumps(data))
    assert Mission.load(path).launch_location_format == "latlon"


def test_dialog_invalid_code_and_short_code_reference(qtbot, monkeypatch):
    dialog = MissionDialog(Mission())
    qtbot.addWidget(dialog)
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: messages.append(text))
    dialog.location.format.setCurrentIndex(2)
    dialog.location.plus_code.setText("9W64+4C")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and "nearby" in messages[-1]
    dialog.location.reference_lat.setText("42.36")
    dialog.location.reference_lon.setText("-71.09")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.mission.launch_location_code == "87JC9W64+4C"
    assert dialog.mission.latitude == pytest.approx(42.3603125)
    invalid = MissionDialog(Mission())
    qtbot.addWidget(invalid)
    invalid.location.format.setCurrentIndex(1)
    invalid.location.mgrs.setText("invalid")
    invalid.accept()
    assert invalid.result() != QDialog.DialogCode.Accepted and "MGRS" in messages[-1]


def test_urrg_preset_exact_coordinates_persist_and_manual_edits_clear_name(qtbot, tmp_path):
    original = Mission(altitude=350, altitude_msl=310)
    dialog = MissionDialog(original)
    qtbot.addWidget(dialog)
    dialog.location.preset.setCurrentIndex(dialog.location.preset.findData("URRG"))
    assert dialog.location.format.currentData() == "mgrs"
    assert dialog.location.mgrs.text() == "18TUN2061530290"
    assert dialog.fields["site_configured"].isChecked()
    dialog.accept()
    mission = dialog.mission
    point = decode_mgrs("18TUN2061530290")
    assert (mission.latitude, mission.longitude) == (point.latitude, point.longitude)
    assert mission.launch_site_name == "URRG" and mission.altitude == 350 and mission.altitude_msl == 310
    assert original.launch_site_name == "" and not original.site_configured
    path = tmp_path / "urrg.json"
    mission.save(path)
    reopened = MissionDialog(Mission.load(path))
    qtbot.addWidget(reopened)
    assert reopened.location.preset.currentData() == "URRG"
    reopened.accept()
    assert reopened.mission.launch_location_code == "18TUN2061530290"
    reopened.location.format.setCurrentIndex(0)
    assert reopened.location.preset.currentData() == "URRG"
    reopened.location.latitude.setValue(42)
    assert reopened.location.preset.currentData() == ""


def test_pointer_mgrs_entry_roundtrip_independent_from_launch(qtbot, tmp_path, monkeypatch):
    original = Mission(latitude=42, longitude=-77, altitude=300)
    dialog = MissionDialog(original)
    qtbot.addWidget(dialog)
    editor = dialog.pointer_location
    editor.format.setCurrentIndex(editor.format.findData('mgrs'))
    messages = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda parent, title, text: messages.append(text))
    editor.mgrs.setText('invalid')
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and 'MGRS' in messages[-1]
    editor.mgrs.setText('18t un 20615 30290')
    editor.altitude.setValue(315)
    dialog.fields['pointer_site_configured'].setChecked(True)
    dialog.accept()
    m = dialog.mission
    point = decode_mgrs('18TUN2061530290')
    assert (m.pointer_latitude, m.pointer_longitude) == (point.latitude, point.longitude)
    assert m.pointer_location_code == '18TUN2061530290' and m.pointer_location_format == 'mgrs'
    assert (m.latitude, m.longitude, m.altitude) == (42, -77, 300)
    assert m.pointer_altitude == 315 and original.pointer_latitude == 0
    m.save(tmp_path / 'mission.json')
    reopened = MissionDialog(Mission.load(tmp_path / 'mission.json'))
    qtbot.addWidget(reopened)
    assert reopened.pointer_location.format.currentData() == 'mgrs'
    for kind in ('latlon', 'mgrs', 'latlon', 'mgrs'):
        reopened.pointer_location.format.setCurrentIndex(reopened.pointer_location.format.findData(kind))
    reopened.accept()
    assert (reopened.mission.pointer_latitude, reopened.mission.pointer_longitude) == (point.latitude, point.longitude)
    save_flight(tmp_path / 'mgrs.rktflight', m)
    restored = load_flight(tmp_path / 'mgrs.rktflight', tmp_path / 'cache').mission
    assert restored.pointer_location_code == m.pointer_location_code
    old = json.loads((tmp_path / 'mission.json').read_text())
    for key in ('pointer_location_format', 'pointer_location_code', 'pointer_launch_heading', 'pointer_launch_distance', 'pointer_height_difference'):
        old.pop(key)
    (tmp_path / 'old.json').write_text(json.dumps(old))
    assert Mission.load(tmp_path / 'old.json').pointer_location_format == 'latlon'


@pytest.mark.parametrize('launch', [(42.7, -77.2, 350), (-33, 151, 0), (85, 0, 200), (0, 179.999, 0)])
def test_relative_pointer_heading_distance_and_altitude(launch):
    for heading in (0, 90, 180, 270, 43.5):
        for distance in (0, 1000, 100_000):
            point, height = pointer_from_launch(launch, heading, distance, 15)
            east, north, _ = to_enu(*launch, (point.latitude, point.longitude, height))
            angle = math.radians(heading)
            assert (east, north) == pytest.approx((distance * math.sin(angle), distance * math.cos(angle)), abs=1e-4)
            assert height == launch[2] + 15
    for heading, distance in [(361, 100), (90, -1), (90, 100001), (float('nan'), 100)]:
        with pytest.raises(ValueError):
            pointer_from_launch(launch, heading, distance, 0)


def test_pointer_relative_profile_recalculates_with_launch_and_persists(qtbot, tmp_path, monkeypatch):
    dialog = MissionDialog(Mission(latitude=42.7, longitude=-77.2, altitude=300))
    qtbot.addWidget(dialog)
    editor = dialog.pointer_location
    editor.format.setCurrentIndex(editor.format.findData('relative'))
    editor.heading.setValue(90)
    editor.distance.setValue(500)
    editor.height_difference.setValue(-10)
    messages = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda parent, title, text: messages.append(text))
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and 'launch origin' in messages[-1]
    dialog.fields['site_configured'].setChecked(True)
    dialog.fields['pointer_site_configured'].setChecked(True)
    dialog.location.latitude.setValue(43)
    dialog.fields['altitude'].setValue(400)
    dialog.accept()
    m = dialog.mission
    assert m.pointer_location_format == 'relative' and m.pointer_altitude == 390
    assert m.pointer_longitude < m.longitude
    east, north, _ = to_enu(m.latitude, m.longitude, m.altitude, (m.pointer_latitude, m.pointer_longitude, m.pointer_altitude))
    assert (east, north) == pytest.approx((500, 0), abs=1e-4)
    m.save(tmp_path / 'relative.json')
    loaded = Mission.load(tmp_path / 'relative.json')
    assert loaded.pointer_launch_heading == 90 and loaded.pointer_launch_distance == 500
    reopened = MissionDialog(loaded)
    qtbot.addWidget(reopened)
    assert reopened.pointer_location.format.currentData() == 'relative'
    assert reopened.pointer_location.height_difference.value() == -10
    reopened.accept()
    assert reopened.mission.pointer_latitude == pytest.approx(m.pointer_latitude, abs=1e-10)
    loaded.latitude += 0.1
    loaded.altitude += 20
    loaded.validate()
    assert loaded.pointer_latitude > m.pointer_latitude and loaded.pointer_altitude == 410
    save_flight(tmp_path / 'relative.rktflight', m)
    restored = load_flight(tmp_path / 'relative.rktflight', tmp_path / 'cache').mission
    assert restored.pointer_location_format == 'relative' and restored.pointer_height_difference == -10
