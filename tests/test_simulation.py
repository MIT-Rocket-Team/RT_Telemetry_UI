import hashlib
import json
import shutil
from pathlib import Path
import sys
import zipfile
import xml.etree.ElementTree as ET
import numpy as np
import pytest
from rocket_gnc_monitor.domain import Mission
from rocket_gnc_monitor.trajectory import SimulationJob, bundled_motor_files, runtime_root

FIXTURE = Path(__file__).parent / "fixtures" / "zephy_testlaunch.ork"
MOTOR_DIGEST = "057a820844b93fe3307401623a92d4c6"
RUNTIME = runtime_root()
ENGINE_AVAILABLE = all(
    path.exists()
    for path in (
        RUNTIME / "java" / "bin" / ("java.exe" if sys.platform == "win32" else "java"),
        RUNTIME / "openrocket.jar",
        RUNTIME / "rocket-bridge.jar",
    )
)
engine = pytest.mark.skipif(not ENGINE_AVAILABLE, reason="Build the bundled Java runtime and bridge first")


def test_bundled_curve_has_original_bytes_and_matches_the_model():
    (curve,) = bundled_motor_files()
    assert (
        hashlib.sha256(curve.read_bytes()).hexdigest()
        == "c18d886508d26ad86eff21c1a7db51b7d25d389027a09e404c5fce3105168a0e"
    )
    with zipfile.ZipFile(FIXTURE) as archive:
        root = ET.fromstring(archive.read("rocket.ork"))
    assert root.findtext(".//motormount/motor/digest") == MOTOR_DIGEST
    assert root.findtext(".//motormount/motor/designation") == "N8406"


@engine
def test_zephyrus_simulates_with_bundled_motor_without_user_configuration(tmp_path):
    mission = Mission(model=str(FIXTURE), site_configured=True, latitude=42.7042296, longitude=-77.1919152)
    assert mission.motor_files == []
    result = SimulationJob(mission, tmp_path).run()
    assert result.manifest["motors"] == [dict(designation="N8406", digest=MOTOR_DIGEST, manufacturer="MITRT")]
    assert len(result.points) > 1000
    assert 4000 < result.points[:, 3].max() < 6000
    assert result.points[-1, 0] > 100 and np.isfinite(result.points).all()
    assert (np.diff(result.points[:, 0]) > 0).all()
    assert result.manifest["extensions"] == "disabled" and not result.manifest["inertia_override"]
    assert result.manifest["request"]["bundled_motor_count"] == 1
    assert (
        result.manifest["motor_sha256"]["motor_0.eng"]
        == hashlib.sha256(bundled_motor_files()[0].read_bytes()).hexdigest()
    )


@engine
def test_missing_custom_curve_has_an_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr("rocket_gnc_monitor.trajectory.bundled_motor_files", lambda: [])
    with pytest.raises(ValueError, match="Missing motor thrust curve: MITRT N8406") as error:
        SimulationJob(Mission(model=str(FIXTURE)), tmp_path).run()
    assert ".eng or .rse" in str(error.value) and "Select custom motor files" in str(error.value)
    assert "Traceback" not in str(error.value) and "RocketBridge.java" not in str(error.value)
    assert json.loads((tmp_path / "error.json").read_text())["schema_version"] == 1
    assert not (tmp_path / "trajectory.csv").exists()


@engine
def test_unpowered_configuration_is_reported_before_simulation(tmp_path):
    with zipfile.ZipFile(FIXTURE) as archive:
        root = ET.fromstring(archive.read("rocket.ork"))
    for mount in root.findall(".//motormount"):
        for motor in mount.findall("motor"):
            mount.remove(motor)
    model = tmp_path / "unpowered.ork"
    with zipfile.ZipFile(model, "w") as archive:
        archive.writestr("rocket.ork", ET.tostring(root))
    with pytest.raises(ValueError, match="has no active motor"):
        SimulationJob(Mission(model=str(model)), tmp_path / "job").run()
    assert not (tmp_path / "job" / "trajectory.csv").exists()


@engine
def test_custom_jar_and_time_limit_are_used_and_recorded(tmp_path):
    from rocket_gnc_monitor.settings import AppSettings

    custom = tmp_path / "Custom OpenRocket.jar"
    shutil.copyfile(RUNTIME / "openrocket.jar", custom)
    settings = AppSettings(openrocket_jar=str(custom), simulation_timeout=180)
    result = SimulationJob(Mission(model=str(FIXTURE)), tmp_path / "job", settings).run()
    assert len(result.points) > 1000
    assert result.manifest["engine_path"] == str(custom)
    assert result.manifest["engine_sha256"] == hashlib.sha256(custom.read_bytes()).hexdigest()
    assert result.manifest["simulation_timeout"] == 180
