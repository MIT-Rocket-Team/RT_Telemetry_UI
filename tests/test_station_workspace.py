"""Station layouts share live state, retain shortcuts and expose every instrument."""

import json
import gc

import pytest
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListWidget, QStackedWidget, QTabWidget

from rocket_gnc_monitor.protocol import ZephyrusDecoder
from rocket_gnc_monitor.flight_view import Flight3DDialog
from rocket_gnc_monitor.station_workspace import StationWindow
from rocket_gnc_monitor.virtual_pointer import VIRTUAL_POINTER_DEVICE
from test_protocol import frame


BASE_CARDS = {
    "flight3d", "trajectory", "altitude", "attitude", "antenna", "pointing",
    "gnc_rates", "gnc_angles", "actuators", "telemetry",
    "gps", "servos", "cells", "commands", "power", "bms", "recovery",
    "mission", "sessions", "diagnostics", "network",
}
AWAY_CARDS = {"antenna", "pointing", "altitude", "attitude", "telemetry", "gps"}
VIDEO_CARDS = {"digital", "analog"}


@pytest.fixture
def station(qtbot, tmp_path, monkeypatch):
    def no_physical_serial(*args, **kwargs):
        raise AssertionError("A workspace operation attempted to open physical serial")

    monkeypatch.setattr("rocket_gnc_monitor.controller.SerialWorker", no_physical_serial)
    # Complete the preceding fixture's deferred Qt destruction before creating
    # another set of native plot menus in the shared test QApplication.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    window = StationWindow(tmp_path, station="base", auto_place=False)
    qtbot.addWidget(window)
    window.resize(1920, 1020)
    window.companion.resize(1920, 1020)
    window.show()
    qtbot.waitUntil(lambda: window.companion.isVisible())
    # Windows may constrain a window's initial geometry to the VM's display.
    # Size the realized windows explicitly for the target 1080p layout check.
    window.resize(1920, 1020)
    window.companion.resize(1920, 1020)
    qtbot.wait(120)
    yield window
    window.close()


def refresh(window):
    window.last_ui = window.last_table = 0
    window.refresh()


def test_base_exposes_all_categories_and_complete_instrument_tables(station, qtbot):
    w, c = station, station.controller
    assert w.companion.controller is c
    assert w.flight_3d_dialog.controller is c
    assert w.station_mode == "base" and c.mode == "LIVE"
    assert all(worker is None for worker in c.workers.values())
    assert all(stream.worker is None for stream in c.video_streams.values())
    assert not c.polling and c.recorder is None
    assert set(w.cards) == BASE_CARDS | VIDEO_CARDS
    assert {key for key, card in w.cards.items() if card.isVisible()} == BASE_CARDS
    for key, card in w.cards.items():
        if key in VIDEO_CARDS:
            assert not card.isVisible(), key
            continue
        assert card.window() in (w, w.companion), key
        assert card.width() > 100 and card.height() > 50, key
    for owner in (w, w.companion):
        assert not any(widget.isVisible() for widget in owner.findChildren(QTabWidget))
        assert not any(widget.isVisible() for widget in owner.findChildren(QStackedWidget))
        assert not any(
            widget.isVisible() for widget in owner.findChildren(QListWidget)
            if widget.objectName() == "navigation"
        )

    c.switch_mode("DEMO")
    c.play_demo(False)
    refresh(w)
    qtbot.wait(150)
    refresh(w)
    qtbot.wait(80)
    assert (w.width(), w.height()) == (1920, 1020)
    assert (w.companion.width(), w.companion.height()) == (1920, 1020)
    p = w.rocket_panel
    for name, table in {
        "telemetry": p.telemetry, "gps": p.gps, "servos": p.servos,
        "cells": p.cells, "rails": p.power, "bms": p.protections,
        "pyros": p.pyros, "actuators": w.actuators,
    }.items():
        assert table.isVisible(), name
        assert table.rowCount() > 0, name
        assert table.verticalScrollBar().maximum() == 0, name
        last_row = table.rowCount() - 1
        assert table.rowViewportPosition(last_row) + table.rowHeight(last_row) <= table.viewport().height(), name


def test_station_switch_keeps_live_pointer_and_flight_state(station, qtbot):
    w, c = station, station.controller
    c.connect("pointer", VIRTUAL_POINTER_DEVICE)
    qtbot.waitUntil(lambda: c.pointer_connected)
    pointer = c.virtual_pointer
    c.manual_point(90, 30)
    c.tick()
    pointer.advance(2)
    sample = ZephyrusDecoder().feed(frame())[0]
    c.accept(sample)
    history = list(c.history)
    mission = c.mission
    c.timer.stop()
    refresh(w)

    w.set_station_mode("away")
    qtbot.wait(60)
    assert w.station_mode == "away" and not w.companion.isVisible()
    assert {key for key, card in w.cards.items() if card.isVisible()} == AWAY_CARDS
    assert c.mode == "LIVE" and c.virtual_pointer is pointer and c.pointer_connected
    assert c.latest is sample and list(c.history) == history and c.mission is mission
    assert pointer.pose == pytest.approx((90, 30))
    assert json.loads(w.station_path.read_text())["station"] == "away"
    assert not w.flight_3d_dialog.timer.isActive()

    w.set_station_mode("base")
    qtbot.wait(60)
    assert w.companion.isVisible()
    assert {key for key, card in w.cards.items() if card.isVisible()} == BASE_CARDS
    assert c.virtual_pointer is pointer and c.latest is sample and list(c.history) == history
    assert w.flight_3d_dialog.timer.isActive()
    assert json.loads(w.station_path.read_text())["station"] == "base"

    w.set_station_mode("video")
    qtbot.wait(60)
    refresh(w)
    assert not w.companion.isVisible()
    assert {key for key, card in w.cards.items() if card.isVisible()} == VIDEO_CARDS
    assert c.virtual_pointer is pointer and c.pointer_connected
    assert c.latest is sample and list(c.history) == history and c.mission is mission
    assert not w.flight_3d_dialog.timer.isActive()
    assert json.loads(w.station_path.read_text())["station"] == "video"
    assert not any(widget.isVisible() for widget in
                   (w.usb_strip, w.poll_button, w.health, w.mission_label, w.banner, *w.metrics.values()))

    w.set_station_mode("base")
    qtbot.wait(60)
    refresh(w)
    assert {key for key, card in w.cards.items() if card.isVisible()} == BASE_CARDS
    assert w.usb_strip.isVisible() and w.poll_button.isVisible() and w.health.isVisible()
    assert c.virtual_pointer is pointer and c.latest is sample


def test_secondary_window_dispatches_shared_shortcuts_once(station, qtbot):
    w, c = station, station.controller
    assert all(
        action in w.companion.actions()
        for action in (*w.legacy_actions.values(), *w.flight_actions.values(), w.settings_action)
    )
    c.connect("pointer", VIRTUAL_POINTER_DEVICE)
    qtbot.waitUntil(lambda: c.pointer_connected)
    refresh(w)
    w.companion.activateWindow()
    w.companion.raise_()
    qtbot.wait(80)
    fired = []
    w.legacy_actions["right"].triggered.connect(lambda: fired.append("right"))
    QTest.keySequence(w.companion, w.legacy_actions["right"].shortcut())
    qtbot.waitUntil(lambda: fired == ["right"])
    c.tick()
    c.virtual_pointer.advance(1)
    assert c.virtual_pointer.pose == pytest.approx((5, 0))
    qtbot.wait(40)
    assert fired == ["right"]
    QTest.keySequence(w.companion, w.settings_action.shortcut())
    qtbot.waitUntil(lambda: w.settings_dialog is not None)
    assert w.settings_dialog.isVisible()
    w.settings_dialog.reject()
    assert c.mode == "LIVE" and c.pointer_connected


def test_companion_cannot_hide_base_information_and_owner_stops_all_timers(station, qtbot):
    w, c = station, station.controller
    assert c.timer.isActive()
    assert w.mount.animation.isActive()
    assert w.flight_3d_dialog.timer.isActive()
    assert w.network_panel.timer.isActive()
    assert not w.companion.close()
    qtbot.wait(40)
    assert w.companion.isVisible() and not getattr(c, "_shutdown", False)
    assert "Away station" in w.companion.statusBar().currentMessage()
    w.close()
    qtbot.wait(40)
    assert not w.isVisible() and not w.companion.isVisible()
    assert c._shutdown and not c.timer.isActive()
    assert not w.mount.animation.isActive()
    assert not w.flight_3d_dialog.timer.isActive()
    assert not w.network_panel.timer.isActive()


def test_escape_preserves_embedded_flight_view_but_dismisses_standalone(station, qtbot):
    w = station
    embedded = w.flight_3d_dialog
    QTest.keyClick(embedded.view, Qt.Key.Key_Escape)
    qtbot.wait(30)
    assert embedded.isVisible() and embedded.timer.isActive()
    w.open_flight_3d()
    assert embedded.isVisible()

    standalone = Flight3DDialog(w.controller, w)
    qtbot.addWidget(standalone)
    standalone.show()
    qtbot.wait(30)
    assert standalone.isVisible() and standalone.timer.isActive()
    QTest.keyClick(standalone.view, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not standalone.isVisible())
    assert not standalone.timer.isActive()
    assert embedded.isVisible() and embedded.timer.isActive()


def test_open_embedded_flight_synchronizes_a_new_reference_immediately(station):
    from rocket_gnc_monitor.trajectory import Trajectory

    w = station
    w.flight_3d_dialog.timer.stop()
    w.controller.reference = Trajectory(
        [[0, 0, 0, 0], [5, 20, 10, 100]],
        dict(schema_version=1, frame="ENU", units="m,s", altitude_datum="launch_relative", origin=[0, 0, 0]),
    )
    w.open_flight_3d()
    assert w.flight_3d_dialog.reference is w.controller.reference
    assert w.flight_3d_dialog.scene.end == 5
    assert w.flight_3d_dialog.view.frame.time == 0
    assert w.flight_3d_dialog.play_button.isEnabled()
    assert not any(w.controller.workers.values())
