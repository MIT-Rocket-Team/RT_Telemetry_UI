"""Exercise ordinary OpenRocket received telemetry without operator overrides."""

import base64
import struct
from dataclasses import replace

import pytest

from rocket_gnc_monitor.broadcast_model import BroadcastScope, BroadcastSelection, BroadcastTelemetry, EVENTS
from rocket_gnc_monitor.broadcast_session import BroadcastSession
from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.mission_network import MissionNetwork
from test_broadcast_core import event
from test_mission_network import base, join, mission
from test_mission_station import delivery, frame, station
from test_network_observation import observation


def received(tx_us=12_500_000, altitude=123.25, velocity=-17.5):
    raw = bytearray(frame(ticks=tx_us // 1000 + 1000, altitude=altitude))
    raw[65] = 3  # Zephyrus APOGEE
    struct.pack_into("<f", raw, 124, velocity)
    raw[129] = sum(raw[2:129]) & 255
    incoming = {**delivery(raw, tx=tx_us), "stateBasis": "flight-events"}
    value = station().receive(incoming, 15_125_000)[0]
    return incoming, Sample.from_dict(value)


def mission_event(tx_us=12_500_000, **kwargs):
    incoming, sample = received(tx_us, **kwargs)
    item = event()
    item["peer"]["source_mode"] = "simulation"
    item["payload"] = observation(sample, raw=base64.b64decode(incoming["data"]))
    return item


def test_standard_mission_fields_and_packet_clock_work_without_overrides():
    item = mission_event()
    # This is exactly what existing OpenRocket publishers send: typed legacy
    # sample with canonical datum/vector still unknown, and boot uptime != T+.
    assert item["payload"]["values"]["baro_altitude"]["datum"] == "unknown"
    assert item["payload"]["values"]["velocity_enu_m_s"] is None
    assert item["payload"]["values"]["native"]["t"] == 13.5
    record = BroadcastTelemetry.from_event(item, BroadcastScope("mission", mode="simulation"))
    assert (record.altitude, record.datum, record.vertical_speed) == (123.25, "above_launch", -17.5)
    assert record.mission_elapsed == 12.5
    assert record.state == "APOGEE"


@pytest.mark.parametrize("stamp", [None, True, 12.5, "1.5", "nan", "-", "9" * 30])
def test_invalid_mission_timestamps_never_fall_back_to_device_uptime(stamp):
    item = mission_event()
    item["payload"]["values"]["native"]["details"]["txTimeUs"] = stamp
    record = BroadcastTelemetry.from_event(item, BroadcastScope("mission", mode="simulation"))
    assert record.mission_elapsed is None
    assert record.altitude == 123.25 and record.vertical_speed == -17.5


def test_absent_measurements_remain_blank_without_losing_the_packet_clock():
    item = mission_event()
    item["payload"]["values"]["baro_altitude"]["value_m"] = None
    item["payload"]["values"]["native"]["velocity"] = None
    record = BroadcastTelemetry.from_event(item, BroadcastScope("mission", mode="simulation"))
    assert record.altitude is record.vertical_speed is None
    assert record.mission_elapsed == 12.5


def test_simulation_label_or_fc_basis_does_not_qualify_standard_source_fields():
    item = mission_event()
    scope = BroadcastScope("mission", mode="simulation")
    item["payload"]["values"]["native"]["details"]["mission"] = False
    record = BroadcastTelemetry.from_event(item, scope)
    assert record.altitude is record.vertical_speed is record.mission_elapsed is None
    item = mission_event()
    item["payload"]["values"]["state"]["basis"] = "flight-computer"
    record = BroadcastTelemetry.from_event(item, scope)
    assert record.altitude is record.vertical_speed is None
    assert record.mission_elapsed == 12.5  # Clock mapping is shared by Mission sources.


def test_canonical_declared_fields_take_precedence_over_legacy_adapter():
    item = mission_event()
    item["payload"]["values"]["baro_altitude"]["datum"] = "msl"
    item["payload"]["values"]["velocity_enu_m_s"] = [900, 800, -4.5]
    record = BroadcastTelemetry.from_event(item, BroadcastScope("mission", mode="simulation"))
    assert record.datum == "msl" and record.vertical_speed == -4.5
    item["peer"]["source_mode"] = "live"
    record = BroadcastTelemetry.from_event(item, BroadcastScope("mission"))
    assert record.vertical_speed == -4.5 and record.mission_elapsed is None


@pytest.mark.parametrize("stamp", [-1_000_000, 0, 12_500_000])
def test_delayed_snapshot_uses_packet_time_and_freezes_clock_on_pause(stamp):
    session = BroadcastSession(BroadcastScope("mission", mode="simulation"))
    record = BroadcastTelemetry.from_event(mission_event(stamp), session.scope)
    session.accept(record)
    session.node = record.node
    selection = BroadcastSelection("Flight")
    # A newer packet must not pull either the displayed clock or values ahead
    # of the half-second Program delay, even at accelerated simulation speed.
    session.accept(replace(record, observation_id="next", arrival=10.4, mission_elapsed=100, altitude=999))
    snap = session.snapshot(selection, 10.6, EVENTS)
    assert snap.view.altitude_m == 123.25 and snap.view.vertical_speed_m_s == -17.5
    assert snap.view.mission_elapsed_s == stamp / 1e6
    assert snap.view.clock_state == ("countdown" if stamp < 0 else "elapsed")
    assert snap.view.clock_basis == "SIMULATION PACKET TIME"
    assert snap.view.telemetry_status == "fresh" and not snap.view.telemetry_notice
    session.program = selection
    session.set_paused(True, 10.6)
    assert session.snapshot(selection, 20, EVENTS).view.mission_elapsed_s == stamp / 1e6
    session.set_scope(replace(session.scope, epoch="next-run"))
    assert session.snapshot(selection, 21, EVENTS).view.mission_elapsed_s is None


def test_base_video_receives_mission_telemetry_for_broadcast_without_qualification(qtbot, tmp_path):
    publisher = MissionNetwork(tmp_path / "publisher")
    window = None
    try:
        configured = mission()
        publisher.configure(configured, discovery=False)
        service = publisher.services["gs1", "telemetry"][0]
        window = base(qtbot, tmp_path / "base", "video")
        join(qtbot, window, service)
        window.open_broadcast()
        broadcast = window.broadcast
        broadcast.refresh_sources()
        incoming, sample = received()
        publisher.begin([])
        publisher.advance("12700000", [{"id": "gs1", "deliveries": [incoming], "videoState": []}],
                          [{"id": "gs1", "samples": [sample.to_dict()], "tracking": False, "rejected": 0}])
        qtbot.waitUntil(lambda: broadcast.session.node == service.node_id, timeout=5000)
        records = broadcast.session.telemetry.histories[service.node_id]
        record = records[-1]
        snap = broadcast.session.snapshot(BroadcastSelection("Flight"), record.arrival + .5, EVENTS)
        assert not broadcast.vertical_contract and broadcast.datum_override == "unknown"
        assert snap.view.telemetry_source == "GS1" and snap.view.telemetry_status == "fresh"
        assert snap.view.altitude_m == 123.25 and snap.view.altitude_datum == "above_launch"
        assert snap.view.vertical_speed_m_s == -17.5 and snap.view.mission_elapsed_s == 12.5
        assert not broadcast.render(snap).isNull()
        broadcast.session.stage(BroadcastSelection("Flight"))
        broadcast.panel.refresh()
        assert "T+00:00:12.5" in broadcast.panel.clock_status.text()
        assert "SIMULATION PACKET TIME" in broadcast.panel.clock_status.text()
    finally:
        if window:
            window.close()
        publisher.close()
