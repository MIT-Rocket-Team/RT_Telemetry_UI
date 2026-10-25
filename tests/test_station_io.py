import json
import socket
import threading
import time

import pytest

from rocket_gnc_monitor.station_io import (
    LAN_PROTOCOL,
    MAX_KISS_FRAME,
    MAX_SNAPSHOT,
    APRSReceiver,
    KissDecoder,
    SnapshotDecoder,
    StationLink,
    controller_snapshot,
    encode_snapshot,
    parse_aprs_position,
    validate_snapshot,
)


def address(call, ssid=0, last=False):
    return bytes(ord(c) << 1 for c in call.ljust(6)) + bytes([0x60 | (ssid << 1) | last])


def frame(payload="!4903.50N/07201.75W>123/045/A=001234rocket", call="N0CALL", ssid=7):
    return address("APRS") + address(call, ssid, True) + b"\x03\xf0" + payload.encode("ascii")


def kiss(raw, command=0):
    body = bytes([command]) + raw
    return b"\xc0" + body.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc") + b"\xc0"


def message(name="base"):
    return {
        "protocol": LAN_PROTOCOL,
        "version": 1,
        "name": name,
        "mode": "LIVE",
        "mission": "Zephyrus",
        "utc": time.time(),
        "sample_age_s": 0.5,
        "telemetry": {
            "altitude": 1523.5,
            "velocity": 198.5,
            "latitude": 42.6,
            "longitude": -76.5,
            "phase": "Coast",
        },
        "pointer": {
            "connected": True,
            "tracking": False,
            "virtual": False,
            "azimuth": 32.0,
            "elevation": 44.0,
        },
        "links": {"telemetry": "Connected", "pointer": "Connected", "rocket": "RECEIVING"},
        "aprs": None,
    }


def wait_for(check, timeout=3):
    end = time.monotonic() + timeout
    while not check():
        if time.monotonic() > end:
            raise AssertionError("Network condition did not become true")
        time.sleep(0.01)


def test_kiss_split_escaped_noise_bounded_and_recovery():
    decoder = KissDecoder()
    raw = frame() + b"\xc0\xdb"
    packet = b"noise" + kiss(raw)
    actual = []
    for byte in packet:
        actual.extend(decoder.feed(bytes([byte])))
    assert actual == [raw]
    assert decoder.feed(kiss(frame(), command=1)) == []
    assert decoder.feed(kiss(frame(), command=0x20)) == [frame()]
    assert decoder.feed(b"\xc0\x00\xdb\x01bad\xc0") == []
    assert decoder.feed(b"\xc0\x00" + b"x" * (MAX_KISS_FRAME + 20)) == []
    assert len(decoder.buffer) <= MAX_KISS_FRAME
    assert decoder.feed(kiss(frame())) == [frame()]


def test_aprs_uncompressed_position_timestamp_altitude_and_invalid():
    fix = parse_aprs_position(frame(), now=123)
    assert fix.callsign == "N0CALL-7"
    assert fix.latitude == pytest.approx(49 + 3.50 / 60)
    assert fix.longitude == pytest.approx(-(72 + 1.75 / 60))
    assert fix.altitude_m == pytest.approx(1234 * 0.3048)
    assert fix.received == 123
    fix = parse_aprs_position(frame("@092345z4903.50S\\07201.75E>/A=-00100"))
    assert fix.latitude < 0 and fix.longitude > 0
    assert fix.altitude_m == pytest.approx(-30.48)
    assert parse_aprs_position(frame("=0000.00N/00000.00E>test")).altitude_m is None
    repeated = address("APRS") + address("N0CALL", 7) + address("WIDE1", 1, True) + frame()[14:]
    assert parse_aprs_position(repeated).callsign == "N0CALL-7"
    for payload in (
        "!4960.00N/07201.75W>bad",
        "!490 .50N/07201.75W>ambiguous",
        "!4903.50N/18101.75W>bad",
        "!4903.50N/07260.00W>bad",
        "@badtime4903.50N/07201.75W>bad",
        ":N0CALL   :message",
        "!/compressed",
    ):
        assert parse_aprs_position(frame(payload)) is None
    assert parse_aprs_position(frame()[:13]) is None
    assert parse_aprs_position(frame().replace(b"\x03\xf0", b"\x03\xff")) is None


def test_snapshot_fragmentation_and_untrusted_input_limits():
    decoder = SnapshotDecoder()
    packet = encode_snapshot(message())
    assert decoder.feed(packet[:12]) == []
    decoded = decoder.feed(packet[12:] + packet)
    assert len(decoded) == 2 and decoded[0]["name"] == "base"
    with pytest.raises(ValueError):
        decoder.feed(b"x" * (MAX_SNAPSHOT + 1))
    assert len(decoder.buffer) == 0
    assert decoder.feed(packet)[0]["telemetry"]["altitude"] == 1523.5
    for change in (
        {"command": "advance_state"},
        {"version": True},
        {"utc": float("nan")},
        {"utc": 10**1000},
        {"pointer": {"connected": "yes"}},
        {"telemetry": {"latitude": 999}},
        {"links": {"telemetry": "<b>Connected</b>\n"}},
        {"sample_age_s": -1},
    ):
        with pytest.raises(ValueError):
            validate_snapshot({**message(), **change})
    with pytest.raises(ValueError):
        decoder.feed(b'{"protocol":"rocket-gnc-station","version":1,"utc":NaN}\n')
    with pytest.raises(ValueError):
        decoder.feed(b'{"command":"pyro"}\n')


def test_aprs_tcp_receiver_filters_and_never_transmits():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        receiver = APRSReceiver()
        try:
            receiver.start("127.0.0.1", listener.getsockname()[1], "N0CALL-7")
            with listener.accept()[0] as peer:
                peer.settimeout(0.1)
                raw = kiss(frame(call="OTHER")) + kiss(frame()) + kiss(frame(":N0CALL   :message"))
                peer.sendall(raw[:9])
                peer.sendall(raw[9:])
                wait_for(lambda: receiver.snapshot()["frames"] == 3)
                result = receiver.snapshot()
                assert result["positions"] == 1 and result["unsupported"] == 1
                assert result["latest"].callsign == "N0CALL-7"
                with pytest.raises(socket.timeout):
                    peer.recv(1)  # No KISS configuration or transmit commands.
            wait_for(lambda: receiver.snapshot()["state"] == "Disconnected")
            assert receiver.snapshot()["latest"] is not None
        finally:
            receiver.stop()
        assert receiver.snapshot()["state"] == "Off"


def test_lan_two_stations_exchange_read_only_and_reconnect():
    base, away = StationLink(), StationLink()
    base.publish(message("base"))
    away.publish(message("away"))
    try:
        base.listen("127.0.0.1", 0)
        wait_for(lambda: base.snapshot()["state"] == "Listening")
        port = base.snapshot()["port"]
        away.connect("127.0.0.1", port)
        wait_for(lambda: base.snapshot()["remote"] and away.snapshot()["remote"])
        assert base.snapshot()["remote"]["name"] == "away"
        assert away.snapshot()["remote"]["telemetry"]["altitude"] == 1523.5
        copy = away.snapshot()
        copy["remote"]["telemetry"]["altitude"] = 0
        assert away.snapshot()["remote"]["telemetry"]["altitude"] == 1523.5
        with pytest.raises(ValueError):
            base.publish({**message(), "command": "send_rocket"})
        away.stop()
        wait_for(lambda: base.snapshot()["state"] == "Listening")
        wait_for(lambda: not away.active)
        away.publish(message("away-reconnected"))
        away.connect("127.0.0.1", port)
        wait_for(
            lambda: base.snapshot()["remote"] and base.snapshot()["remote"]["name"] == "away-reconnected"
        )
    finally:
        away.stop()
        base.stop()
    assert not base.active and not away.active


def test_lan_rejects_command_then_accepts_new_valid_peer():
    base = StationLink()
    base.publish(message())
    try:
        base.listen("127.0.0.1", 0)
        wait_for(lambda: base.snapshot()["port"] is not None)
        port = base.snapshot()["port"]
        with socket.create_connection(("127.0.0.1", port)) as rogue:
            rogue.sendall(b'{"command":"advance_state"}\n')
            wait_for(lambda: base.snapshot()["error"] != "")
            assert base.snapshot()["remote"] is None
        with socket.create_connection(("127.0.0.1", port)) as peer:
            peer.sendall(encode_snapshot(message("valid")))
            wait_for(lambda: base.snapshot()["remote"] is not None)
            assert base.snapshot()["remote"]["name"] == "valid"
    finally:
        base.stop()


def test_endpoint_errors_do_not_start_network_workers():
    aprs, lan = APRSReceiver(), StationLink()
    for operation in (
        lambda: aprs.start("", 8001),
        lambda: aprs.start(port=0),
        lambda: aprs.start(callsign="CALL-42"),
        lambda: lan.connect("127.0.0.1", True),
    ):
        with pytest.raises(ValueError):
            operation()
    assert not aprs.active and not lan.active


def test_panel_preferences_offline_and_controller_isolation(qtbot, tmp_path):
    from rocket_gnc_monitor.controller import Controller
    from rocket_gnc_monitor.domain import Sample
    from rocket_gnc_monitor.station_panel import StationNetworkPanel

    controller = Controller(tmp_path)
    controller.timer.stop()
    controller.latest = Sample(12.5, 10, "legacy", altitude=250, latitude=42.5, longitude=-76.5)
    panel = StationNetworkPanel(controller)
    qtbot.addWidget(panel)
    panel.resize(600, 230)
    panel.show()
    try:
        assert not panel.aprs.active and not panel.lan.active
        assert panel.remote_snapshot is None
        assert "Off" in panel.aprs_status.text()
        original = controller.latest
        payload = controller_snapshot(controller, "away")
        assert payload["telemetry"]["altitude"] == 250
        assert payload["links"]["rocket"] == "DISCONNECTED"
        assert controller.latest is original and controller.workers == {"telemetry": None, "pointer": None}
        panel.station_name.setText("URRG away")
        panel.peer_host.setText("192.168.1.100")
        panel._save_preferences()
        saved = json.loads((tmp_path / "station_network.json").read_text())
        assert saved["station_name"] == "URRG away" and "connected" not in saved
        restored = StationNetworkPanel(controller)
        qtbot.addWidget(restored)
        try:
            assert restored.peer_host.text() == "192.168.1.100"
            assert not restored.aprs.active and not restored.lan.active
        finally:
            restored.shutdown()
    finally:
        panel.shutdown()
        controller.shutdown()
    assert not panel.timer.isActive()
    assert not any(t.name in {"StationLink", "APRSReceiver"} and t.is_alive() for t in threading.enumerate())
