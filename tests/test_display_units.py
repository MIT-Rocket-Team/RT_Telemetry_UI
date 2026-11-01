from collections import deque
from dataclasses import asdict, replace
import time

import pytest

from rocket_gnc_monitor.display_units import DisplayUnits
from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.settings import AppSettings
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow


@pytest.mark.parametrize("kind,value,expected,label", [
    ("distance", .3048, 1, "ft"), ("velocity", .3048, 1, "ft/s"),
    ("acceleration", .3048, 1, "ft/s²"), ("temperature", 100, 212, "°F"),
    ("wind", .44704, 1, "mph"), ("mass", .0283495231, 1, "oz"),
    ("length", .0254, 1, "in"), ("pressure", 100, 1, "mbar"),
    ("force", 1, 1, "N"), ("impulse", 1, 1, "Ns"), ("rate", 360, 1, "r/s"),
])
def test_openrocket_imperial_defaults(kind, value, expected, label):
    units = DisplayUnits()
    assert units.convert(value, kind) == pytest.approx(expected)
    assert units.unit(kind) == label
    assert units.convert(None, kind) is None


def test_display_switch_updates_values_labels_and_plots_without_mutating_data(qtbot, tmp_path):
    window = StationWindow(tmp_path, profile=StationProfile(), auto_place=False)
    qtbot.addWidget(window)
    c = window.controller
    sample = Sample(t=1, sequence=1, source="LIVE", received=time.monotonic(), altitude=30.48, velocity=3.048,
                    gps_altitude=60.96, rates=[360, 180, 90], details={"state_code": 2, "bms_temperature": 20})
    c.latest = sample
    c.history = deque([sample])
    before = sample.to_dict()
    mission_before = asdict(c.mission)
    try:
        assert c.settings.display_units == "imperial"
        for system, altitude, velocity, height_unit, temp, rates in (
            ("imperial", "100.0", "10.0", "ft", "68.000", [1]),
            ("metric", "30.5", "3.0", "m", "20.000", [1]),
            ("imperial", "100.0", "10.0", "ft", "68.000", [1]),
        ):
            window.apply_settings(replace(c.settings, display_units=system))
            assert window.metrics["altitude"].text() == altitude
            assert window.metrics["velocity"].text() == velocity
            assert window.metric_unit_labels["altitude"][0].text() == "ALTITUDE · " + height_unit
            assert window.altitude_curve.yData.tolist() == pytest.approx([100 if system == "imperial" else 30.48])
            assert window.rate_curves[0].yData.tolist() == rates
            assert window.rocket_panel.telemetry.item(3, 0).text() == f"Baro altitude ({height_unit})"
            assert window.rocket_panel.gps.item(3, 1).text() == ("200.000" if system == "imperial" else "60.960")
            assert float(window.rocket_panel.power.item(7, 3).text()) == float(temp)
            assert sample.to_dict() == before and c.history[0] is sample
            assert asdict(c.mission) == mission_before
        window.open_settings()
        dialog = window.settings_dialog
        dialog.units.setCurrentIndex(dialog.units.findData("metric"))
        dialog.accept()
        assert AppSettings.load(tmp_path / "settings.json").display_units == "metric"
    finally:
        window.close()
