"""Legacy-reference parity; all writes terminate at fake/OS pseudo-serial devices."""

import ast
import csv
import json
import os
from pathlib import Path
import re
import select
from types import SimpleNamespace
import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QMessageBox
from rocket_gnc_monitor.domain import Mission, pointing
from rocket_gnc_monitor.protocol import ZephyrusDecoder, pointer_packet
from rocket_gnc_monitor.recording import SessionRecorder, SessionReader
from rocket_gnc_monitor.ui import MainWindow
from rocket_gnc_monitor.zephyrus import COMMANDS, CSV_FIELDS, legacy_values, rocket_packet

REFERENCE = json.loads((Path(__file__).parent / "fixtures/legacy_reference.json").read_text())


def literal(value):
    value = re.sub(r"np\.(?:float64|float32|int64|int32)\(([^()]+)\)", r"\1", value)
    value = value.replace("np.True_", "True").replace("np.False_", "False")
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def test_all_rocket_commands_match_original_bytes():
    assert {v["command"] for v in REFERENCE["commands"]} == set(COMMANDS) | {"zero_roll"}
    for v in REFERENCE["commands"]:
        assert rocket_packet(v["command"], v["value"]).hex() == v["hex"], v


def test_all_pointer_commands_and_geometry_match_original():
    names = dict(up=1, down=2, left=3, right=4, zero=5)
    for v in REFERENCE["pointer_commands"]:
        packet = pointer_packet(*v["angles"]) if v["angles"] else pointer_packet(opcode=names[v["command"]])
        assert packet.hex() == v["hex"]
    for v in REFERENCE["pointing"]:
        assert pointing(v["target"], [*v["origin"][:2], 0]) == pytest.approx(v["angles"], abs=1e-7)


def test_every_good_csv_field_matches_original():
    assert CSV_FIELDS == REFERENCE["csv_fields"]
    row = REFERENCE["telemetry"][0]
    sample = ZephyrusDecoder().feed(bytes.fromhex(row["hex"]))[0]
    actual = legacy_values(sample)
    for key, expected in row["values"].items():
        if key == "timestamp":
            continue
        expected = literal(expected)
        assert actual[key] == (
            pytest.approx(expected) if isinstance(expected, (list, float)) else expected
        ), key


def test_logging_retains_good_bad_csv_and_raw_uplink(tmp_path):
    good, bad = [bytes.fromhex(t["hex"]) for t in REFERENCE["telemetry"]]
    r = SessionRecorder(tmp_path, Mission(), "LIVE")
    r.raw("telemetry_rx", good[:37])
    r.raw("telemetry_rx", good[37:] + bad)
    r.raw("telemetry_tx", rocket_packet("zero_alt"))
    r.sample(ZephyrusDecoder().feed(good)[0])
    r.close()
    assert not r.error
    for name in ("telemetry.csv", "telemetry_badpackets.csv"):
        with (r.path / name).open() as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            assert reader.fieldnames == REFERENCE["csv_fields"]
        assert len(rows) == 1
        assert rows[0]["pktnum"] == "321"
        assert rows[0]["badpackets"] == ("1" if "badpackets" in name else "0")
        # Rejected values consistently use the good-packet field mapping.
        assert rows[0]["baro_max_alt"] == "1500" and rows[0]["gnd_fix"] == "True"
    reader = SessionReader(r.path)
    assert reader.count == 1  # Rejected data never becomes live/replay telemetry.
    assert reader.db.execute("SELECT COUNT(*) FROM raw_index WHERE role='telemetry_tx'").fetchone()[0] == 1
    reader.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX pseudo-terminal test")
def test_all_shortcuts_work_across_views_and_serial_roles(qtbot, tmp_path, monkeypatch):
    import pty

    pairs = [pty.openpty(), pty.openpty()]
    devices = [os.ttyname(pair[1]) for pair in pairs]
    refreshes = []

    def fake_ports():
        refreshes.append(True)
        return [dict(device=d) for d in devices]

    monkeypatch.setattr("rocket_gnc_monitor.ui.ports", fake_ports)
    w = MainWindow(tmp_path)
    qtbot.addWidget(w)
    c = w.controller
    w.show()
    w.activateWindow()
    w.raise_()
    qtbot.wait(100)

    def refresh():
        w.last_ui = w.last_table = 0
        w.refresh()

    def key(name):
        refresh()
        action = w.legacy_actions[name]
        assert action.isEnabled(), name
        triggered = QSignalSpy(action.triggered)
        QTest.keySequence(w, action.shortcut())
        qtbot.waitUntil(lambda: triggered.count() > 0)

    def receive(fd, size):
        output = bytearray()
        while len(output) < size:
            assert select.select([fd], [], [], 2)[0]
            output.extend(os.read(fd, size - len(output)))
        return bytes(output)

    try:
        if os.environ.get("QT_QPA_PLATFORM") == "cocoa" and not QTest.qWaitForWindowActive(w, 2000):
            pytest.skip(
                "Native shortcut input requires an unlocked, active Mac desktop; offscreen test remains available"
            )
        assert sorted(
            a.shortcut().toString(QKeySequence.SequenceFormat.PortableText) for a in w.legacy_actions.values()
        ) == sorted(
            QKeySequence(s).toString(QKeySequence.SequenceFormat.PortableText) for s in REFERENCE["shortcuts"]
        )
        key("refresh")
        assert len(refreshes) == 2
        w.port_widgets["pointer"][0].setCurrentIndex(1)
        key("pointer")
        qtbot.waitUntil(lambda: c.pointer_connected)
        assert not c.ground_connected
        for i, name in enumerate(["up", "down", "left", "right", "zero"]):
            w.pages.setCurrentIndex(i + 1)
            key(name)
            assert receive(pairs[1][0], 11) == pointer_packet(
                opcode={"up": 1, "down": 2, "left": 3, "right": 4, "zero": 5}[name]
            )
            qtbot.waitUntil(lambda: c.pointer_pending is None)
        key("ground")
        qtbot.waitUntil(lambda: c.ground_connected)
        assert not c.polling
        # Rocket commands must work while polling is stopped.
        c.send_rocket("zero_alt")
        assert receive(pairs[0][0], 16) == rocket_packet("zero_alt")
        key("poll")
        assert c.polling
        frame = bytes.fromhex(REFERENCE["telemetry"][0]["hex"])
        os.write(pairs[0][0], frame)
        qtbot.waitUntil(lambda: c.latest is not None)
        refresh()
        assert w.rocket_panel.gps.item(1, 1).text() == "42.3601234"
        w.freeze_gps.click()
        assert c.frozen_ground["gnd_lat"] == 42.36013
        expected = pointing(
            (c.latest.latitude, c.latest.longitude, c.latest.altitude), (42.36013, -71.09335, 0)
        )
        assert c.tracking_target() == pytest.approx(expected)
        refresh()  # Frozen GPS text must render without a key mismatch.
        assert "FROZEN" in w.ground_gps.text()
        key("log")
        assert c.recorder is not None
        path = c.recorder.path
        os.write(pairs[0][0], frame)
        qtbot.waitUntil(lambda: c.stats["accepted"] >= 2)
        key("log")
        assert c.recorder is None
        qtbot.waitUntil(
            lambda: (
                (path / "manifest.json").exists()
                and json.loads((path / "manifest.json").read_text())["complete"]
            )
        )
        assert (path / "telemetry.csv").exists()
        key("poll")
        assert not c.polling
        key("ground")
        assert not c.ground_connected and c.pointer_connected
        key("pointer")
        assert not c.pointer_connected
        # Repeated refreshes must not attach duplicate connect callbacks.
        for _ in range(5):
            refresh()
        key("ground")
        qtbot.waitUntil(lambda: c.ground_connected)
        assert not c.polling
    finally:
        w.close()
        for pair in pairs:
            for fd in pair:
                os.close(fd)


def test_every_legacy_control_and_confirmation(qtbot, tmp_path, monkeypatch):
    w = MainWindow(tmp_path)
    qtbot.addWidget(w)
    c, p = w.controller, w.rocket_panel
    packets = []
    # A capture-only worker cannot reach a serial port.
    c.workers["telemetry"] = SimpleNamespace(send=lambda data, identifier: packets.append(data))
    c.states["telemetry"] = "Connected"
    p.set_controls_enabled()
    prompts = []
    answer = QMessageBox.StandardButton.No

    def confirm(*args):
        prompts.append(args[2])
        return answer

    monkeypatch.setattr(QMessageBox, "question", confirm)
    monkeypatch.setattr(QMessageBox, "critical", confirm)
    monkeypatch.setattr(QMessageBox, "warning", confirm)
    try:
        guarded = [
            "advance_state",
            "arm_pyros",
            "fire_pyros",
            "EMERG_DEPLOY_PISTON",
            "EMERG_DEPLOY_BP_WELLS",
            "EMERG_DEPLOY_TD",
            "EMERG_DEPLOY_ALL",
        ]
        p.pyro_selection[0].setChecked(True)
        p.pyro_selection[5].setChecked(True)
        for name in guarded:
            p.buttons[name].click()
        assert len(prompts) == 7 and not packets
        answer = QMessageBox.StandardButton.Yes
        for name in guarded:
            p.buttons[name].click()
            assert packets.pop() == rocket_packet(
                name, [0, 5] if "pyros" in name else 1 if name == "advance_state" else None
            )
        p.pyro_selection[0].setChecked(False)
        p.pyro_selection[5].setChecked(False)
        p.buttons["fire_pyros"].click()
        assert not packets and prompts[-1] == "Select at least one pyro"
        for name in ["zero_pitchYawRoll", "zero_alt", "zero_velo", "zero_servos", "pd_activate"]:
            p.buttons[name].click()
            assert packets.pop() == rocket_packet(name)
        for name in ["set_roll_control_servo_angle", "set_airbrakes_angle"]:
            p.servo_inputs[name].setText("-12.5")
            p.buttons[name].click()
            assert packets.pop() == rocket_packet(name, -12.5)
        for i, button in enumerate(p.vtx_buttons):
            button.click()
            assert packets.pop() == rocket_packet("set_vtx_power", i)
        p.rail_requests[0].click()
        assert packets.pop() == rocket_packet("update_converters", [False, True, True, True, True, True])
        assert not p.rail_requests[1].isEnabled()
        assert not p.power_master.isEnabled()
        c.states["telemetry"] = "Disconnected"
        p.set_controls_enabled()
        assert all(not b.isEnabled() for b in p.controls)
    finally:
        c.workers["telemetry"] = None
        w.close()
