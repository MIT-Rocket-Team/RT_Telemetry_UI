from dataclasses import replace
import json
import zipfile
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from rocket_gnc_monitor.controller import Controller
from rocket_gnc_monitor.settings import AppSettings, validate_engine_jar
from rocket_gnc_monitor.ui import MainWindow


@pytest.fixture
def engine_file(tmp_path):
    path = tmp_path / "Engine with spaces.jar"
    with zipfile.ZipFile(path, "w") as archive:
        for name in (
            "info/openrocket/core/document/OpenRocketDocument.class",
            "info/openrocket/core/startup/Application.class",
            "com/google/inject/Guice.class",
        ):
            archive.writestr(name, b"fixture")
    return path


def test_settings_roundtrip_and_atomic_failure(tmp_path, engine_file, monkeypatch):
    path = tmp_path / "settings.json"
    assert AppSettings.load(path) == AppSettings()
    settings = AppSettings(
        openrocket_jar=str(engine_file),
        sessions_directory=str(tmp_path),
        daylight=True,
        simulation_timeout=300,
    ).validate(paths=True)
    settings.save(path)
    assert AppSettings.load(path) == settings
    original = path.read_bytes()

    def failed_replace(*args):
        raise OSError("disk error")

    monkeypatch.setattr("rocket_gnc_monitor.settings.os.replace", failed_replace)
    with pytest.raises(OSError):
        AppSettings().save(path)
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Engine with spaces.jar", "settings.json"]


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"simulation_timeout": 0},
        {"daylight": "yes"},
        {"openrocket_jar": "relative.jar"},
        {"schema_version": 2},
    ],
)
def test_invalid_preferences_are_reported(tmp_path, data):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Could not load settings"):
        AppSettings.load(path)


def test_missing_external_engine_is_preserved_and_invalid_jar_rejected(tmp_path, engine_file):
    settings = AppSettings(openrocket_jar=str(engine_file))
    settings.save(tmp_path / "settings.json")
    engine_file.unlink()
    loaded = AppSettings.load(tmp_path / "settings.json")
    assert loaded.engine_path == engine_file
    with pytest.raises(ValueError, match="existing"):
        loaded.validate(paths=True)
    engine_file.write_text("not a jar")
    with pytest.raises(ValueError, match="Java archive"):
        validate_engine_jar(engine_file)


def test_settings_shortcut_cancel_validation_save_and_restart(qtbot, tmp_path, engine_file, monkeypatch):
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    qtbot.wait(80)
    assert window.settings_action.menuRole() == QAction.MenuRole.PreferencesRole
    mission_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "Mission")
    assert all(action.menuRole() == QAction.MenuRole.NoRole for action in mission_menu.actions())
    assert window.settings_action.shortcut() == QKeySequence("Ctrl+,")
    assert len(window.legacy_actions) == 10
    qtbot.keyClick(window, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: window.settings_dialog is not None)
    dialog = window.settings_dialog
    assert dialog.isVisible() and window.controller.mode == "LIVE"
    window.open_settings()
    assert window.settings_dialog is dialog
    dialog.engine.setText(str(engine_file))
    dialog.reject()
    assert window.settings_dialog is None and window.controller.settings == AppSettings()
    assert not (tmp_path / "settings.json").exists()
    window.open_settings()
    dialog = window.settings_dialog
    dialog.engine.setText(str(tmp_path / "missing.jar"))
    dialog.accept()
    assert dialog.isVisible() and dialog.error.isVisible()
    assert not (tmp_path / "settings.json").exists()
    dialog.engine.setText(str(engine_file))
    dialog.sessions.setText(str(tmp_path))
    dialog.theme.setCurrentIndex(1)
    dialog.timeout.setValue(240)
    dialog.accept()
    assert window.settings_dialog is None
    assert window.daylight_action.isChecked()
    saved = window.controller.settings
    assert saved.engine_path == engine_file and saved.simulation_timeout == 240
    calls = []
    monkeypatch.setattr(window.controller, "start_recording", lambda directory: calls.append(directory))
    window.record()
    assert calls == [tmp_path]
    window.close()
    restored = MainWindow(tmp_path)
    qtbot.addWidget(restored)
    assert restored.controller.settings == saved
    assert restored.daylight_action.isChecked()
    assert restored.controller.mode == "LIVE" and not restored.controller.ground_connected
    restored.open_settings()
    restored.settings_dialog.populate(AppSettings())
    restored.settings_dialog.accept()
    assert restored.controller.settings == AppSettings() and not restored.daylight_action.isChecked()
    restored.close()


def test_corrupt_settings_do_not_prevent_launch_or_get_overwritten(qtbot, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{broken")
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    assert window.controller.settings_error and window.controller.settings == AppSettings()
    assert "defaults are active" in window.statusBar().currentMessage()
    assert path.read_text() == "{broken"
    window.close()


def test_simulation_captures_settings_at_submission(qtbot, tmp_path, engine_file, monkeypatch):
    c = Controller(tmp_path)
    c.timer.stop()
    c.mission.site_configured = True
    settings = AppSettings(openrocket_jar=str(engine_file), simulation_timeout=240)
    c.save_settings(settings)
    monkeypatch.setattr(c, "submit", lambda *args: None)
    c.run_simulation()
    job = c.simulation
    c.save_settings(replace(settings, simulation_timeout=360))
    assert job.settings == settings and c.settings.simulation_timeout == 360
    c.shutdown()
