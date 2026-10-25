"""Standard close/quit actions work from each station display and its dialogs."""

import gc

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from rocket_gnc_monitor.station_workspace import StationWindow


@pytest.fixture
def window(qtbot, tmp_path):
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    window = StationWindow(tmp_path, station="base", auto_place=False)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.companion.isVisible)
    yield window
    window.close()


def focus(qtbot, target):
    target.show()
    target.raise_()
    target.activateWindow()
    QApplication.setActiveWindow(target)
    qtbot.waitUntil(lambda: QApplication.activeWindow() is target)
    qtbot.wait(50)


def test_close_shortcut_closes_systems_without_stopping_the_session(window, qtbot):
    focus(qtbot, window.companion)
    QTest.keySequence(window.companion, QKeySequence(QKeySequence.StandardKey.Close))
    qtbot.waitUntil(lambda: not window.companion.isVisible())
    assert window.isVisible() and not getattr(window.controller, "_shutdown", False)
    window.refresh()
    assert not window.companion.isVisible()
    window.arrange_displays()
    qtbot.waitUntil(window.companion.isVisible)


@pytest.mark.parametrize("modal", [False, True])
def test_close_shortcut_closes_only_the_active_dialog(window, qtbot, modal):
    if modal:
        dialog = QDialog(window)
        dialog.setModal(True)
        qtbot.addWidget(dialog)
    else:
        window.open_settings()
        dialog = window.settings_dialog
    focus(qtbot, dialog)
    QTest.keySequence(dialog, QKeySequence(QKeySequence.StandardKey.Close))
    qtbot.waitUntil(lambda: not dialog.isVisible())
    assert window.isVisible() and window.companion.isVisible()
    assert not getattr(window.controller, "_shutdown", False)


@pytest.mark.parametrize("active", ["primary", "systems", "settings", "modal"])
def test_quit_shortcut_closes_every_window_and_shuts_down_once(window, qtbot, monkeypatch, active):
    from rocket_gnc_monitor.flight_view import Flight3DDialog

    flight = Flight3DDialog(window.controller, window)
    qtbot.addWidget(flight)
    flight.show()
    window.open_settings()
    settings = window.settings_dialog
    if active == "modal":
        target = QDialog(window)
        target.setModal(True)
        qtbot.addWidget(target)
    else:
        target = {"primary": window, "systems": window.companion, "settings": settings}[active]
    quit_calls = []
    monkeypatch.setattr(QApplication.instance(), "quit", lambda: quit_calls.append(True))
    focus(qtbot, target)
    QTest.keySequence(target, window.quit_action.shortcut())
    qtbot.waitUntil(lambda: quit_calls == [True])
    assert not any(w.isVisible() for w in (window, window.companion, settings, flight, target))
    assert window.controller._shutdown
    assert not any(timer.isActive() for timer in (
        window.controller.timer, window.mount.animation, window.network_panel.timer,
        window.flight_3d_dialog.timer, flight.timer,
    ))


def test_close_primary_stops_owned_windows_and_controller(window, qtbot):
    window.open_settings()
    settings = window.settings_dialog
    focus(qtbot, window)
    QTest.keySequence(window, QKeySequence(QKeySequence.StandardKey.Close))
    qtbot.waitUntil(lambda: not window.isVisible())
    assert not window.companion.isVisible() and not settings.isVisible()
    assert window.controller._shutdown
