import json
import numpy as np
import pytest
from rocket_gnc_monitor.domain import (
    Mission,
    Sample,
    pointing,
    to_enu,
    target_position,
    wind_from,
    validate_wind,
)
from rocket_gnc_monitor.trajectory import Trajectory


def test_geodetic_cardinal_and_origin():
    assert to_enu(0, 0, 0, (0, 0, 0)) == pytest.approx([0, 0, 0])
    north = pointing((0.01, 0, 100), (0, 0, 0))
    east = pointing((0, 0.01, 100), (0, 0, 0))
    assert north[0] == pytest.approx(0, abs=0.01)
    assert east[0] == pytest.approx(90, abs=0.01)
    assert 4 < east[1] < 6
    with pytest.raises(ValueError):
        pointing((0, 0, 1), (0, 0, 0))
    with pytest.raises(ValueError):
        pointing((0, 0, 1000), (0, 0, 0))


def test_datum_is_required_and_gps_relative_altitude_is_explicit():
    m = Mission(site_configured=True, altitude=200)
    s = Sample(t=0, sequence=0, source="LIVE", latitude=0, longitude=0.01, gps_fix=3, gps_altitude=100)
    with pytest.raises(ValueError):
        target_position(s, m)
    m.legacy_altitude = "gps_agl"
    assert target_position(s, m) == (0, 0.01, 300)
    m.legacy_altitude = "ellipsoid"
    assert target_position(s, m) == (0, 0.01, 100)


def test_mission_calibration_never_survives_load(tmp_path):
    path = tmp_path / "mission.json"
    Mission(pointer_calibrated=True).save(path)
    assert not Mission.load(path).pointer_calibrated
    data = json.loads(path.read_text())
    data["schema_version"] = 99
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        Mission.load(path)


def test_wind_direction_from_and_layer_validation():
    assert wind_from(10, 0) == pytest.approx((0, -10))
    assert wind_from(10, 90) == pytest.approx((-10, 0))
    with pytest.raises(ValueError):
        validate_wind([dict(height=10, east=1, north=0), dict(height=10, east=0, north=1)])


def test_reference_import_roundtrip_and_no_extrapolation(tmp_path):
    ref = Trajectory.demo()
    path = tmp_path / "reference.csv"
    ref.save(path)
    loaded = Trajectory.load(path)
    np.testing.assert_allclose(loaded.points, ref.points)
    assert loaded.at(-1) is None and loaded.at(141) is None
    assert loaded.at(70)[2] == pytest.approx(1500)
    with pytest.raises(ValueError):
        Trajectory([[0, 0, 0, 0], [0, 1, 1, 1]], ref.manifest)
