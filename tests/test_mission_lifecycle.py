"""Catch native wrapper reuse when a connected application opens another mission."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_older_desktop_runtime_is_rejected_before_connection(monkeypatch):
    from rocket_gnc_monitor import mission_runtime

    monkeypatch.setattr(mission_runtime.PySide6, '__version__', '6.11.3')
    with pytest.raises(RuntimeError, match='make setup'):
        mission_runtime.capabilities()


def test_station_workspaces_can_be_destroyed_and_rebuilt():
    # Isolate native crashes so pytest reports a useful child-process failure.
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), '--probe'],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, capture_output=True,
        # Sixty complete instrument windows are substantially slower on hosted
        # Intel Macs. Keep every cycle while bounding the entire child process.
        text=True, timeout=300, check=False,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-8000:]
    assert '30 station rebuild cycles passed' in result.stdout


def probe():
    import gc
    import tempfile

    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QTimer
    from PySide6.QtWidgets import QApplication, QMenuBar

    from rocket_gnc_monitor.mission_application import MissionController
    from rocket_gnc_monitor.mission_video import MissionProfile
    from rocket_gnc_monitor.station_workspace import StationWindow

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    for iteration in range(30):
        print(f'Rebuilding stations: {iteration + 1}/30', flush=True)
        with tempfile.TemporaryDirectory() as directory:
            for station, role in [('base', 'video'), ('away1', 'telemetry')]:
                manifest = {'station': {'id': 'gs1', 'origin': [42, -77, 250],
                                       'owner': 'observer', 'pointerEnabled': False}, 'role': role}
                window = StationWindow(
                    Path(directory) / station,
                    profile=MissionProfile(station=station, role=role, mission_streams=()),
                    auto_place=False,
                    controller_factory=lambda *a, m=manifest, **kw: MissionController(*a, manifest=m, **kw),
                )
                try:
                    assert isinstance(window.companion.menuBar(), QMenuBar)
                    window.show()
                    for _ in range(50):
                        loop = QEventLoop()
                        QTimer.singleShot(1, loop.quit)
                        loop.exec()
                finally:
                    window.close()
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                del window
                gc.collect()
                # Reuse native allocations after the layouts have been destroyed.
                for _ in range(500):
                    objects = (QObject(), QTimer(), QEventLoop())
                    assert all(isinstance(obj, QObject) for obj in objects)
                app.processEvents()
    print('30 station rebuild cycles passed', flush=True)


if __name__ == '__main__':
    probe()
