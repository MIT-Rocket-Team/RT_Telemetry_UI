import base64
import hashlib
import json
import os
import time
import uuid

import pytest

from rocket_gnc_monitor.network.observation import observation_from_sample, observation_to_sample
from rocket_gnc_monitor.network.protocol import validate_observation
from rocket_gnc_monitor.protocol import TelemetryDecoder
from rocket_gnc_monitor.recording import ObservationArchive
from test_protocol import frame


NODE, BOOT, CONNECTION = (str(uuid.uuid4()) for _ in range(3))


def observation(sample=None, raw=None, **kwargs):
    raw = frame() if raw is None else raw
    sample = sample or TelemetryDecoder().feed(raw, received=15.125, utc=1700000000.5)[0]
    options = dict(raw=raw, station="gs1", node_id=NODE, boot_id=BOOT,
                   connection_id=CONNECTION, observation_counter=1, boot_monotonic=10,
                   vehicle_mode="balius")
    options.update(kwargs)
    return observation_from_sample(sample, **options)


def test_complete_frame_preserved_after_fragmentation_and_checksum_rejection():
    seen = []
    decoder = TelemetryDecoder(on_accepted=lambda sample, raw: seen.append((sample, raw)))
    good = frame(23)
    broken = bytearray(frame(22))
    broken[52] ^= 1
    assert decoder.feed(b"noise" + broken + good[:113], received=11, utc=21) == []
    result = decoder.feed(good[113:], received=12.5, utc=22.5)
    assert len(result) == len(seen) == 1
    assert seen[0][1] == good
    assert seen[0][1][130:] == good[130:]
    assert result[0].received == 12.5 and result[0].utc == 22.5
    assert result[0].details["bad_packets"] >= 1


def test_canonical_observation_retains_exact_bytes_native_fields_and_receiver_provenance():
    sample = TelemetryDecoder().feed(frame(), received=15.125, utc=1700000000.5)[0]
    payload = observation(sample, decoder_gaps=7, rejected_packets=3)
    validate_observation(payload)
    assert base64.b64decode(payload["packet"]["raw_b64"]) == frame()
    assert payload["packet"]["raw_sha256"] == hashlib.sha256(frame()).hexdigest()
    assert payload["values"]["native"] == sample.to_dict()
    assert payload["receiver"]["rssi_dbm"] == sample.rssi
    assert payload["receiver"]["rx_rssi_dbm"] == sample.details["onboard_rssi"]
    assert payload["receiver"]["decoder_gaps"] == "7"
    assert payload["receiver"]["rejected_packets"] == "3"
    assert payload["timing"]["received_mono_ms"] == "5125"
    assert payload["timing"]["received_time_utc"] == "2023-11-14T22:13:20.500Z"
    assert payload["packet"]["device_boot_id"] is None
    assert payload["values"]["orientation"] is None
    assert payload["values"]["velocity_enu_m_s"] is None
    assert all(payload["values"]["flight_status"][key] is None for key in
               ("motor_ignited", "motor_burning", "parachute_deployed"))
    sample.details["cells"][0] = 0
    assert payload["values"]["native"]["details"]["cells"][0] == 4


def test_iris_desired_target_does_not_establish_identity_or_device_boot():
    sample = TelemetryDecoder().feed(frame(), received=15)[0]
    sample.details.update(desired_target="sustainer", boot_index=23)
    payload = observation(sample, vehicle_mode="iris")
    assert payload["vehicle"] == {"id": "unknown", "basis": "unknown", "target_epoch": None}
    assert payload["packet"]["device_boot_id"] is None
    assert payload["values"]["native"]["details"]["boot_index"] == 23


@pytest.mark.parametrize("code,name", [(0, "GROUND_TESTING"), (1, "PREFLIGHT"), (2, "FLIGHT"),
                                      (3, "APOGEE"), (4, "LOW_BATTERY"), (253, None)])
def test_balius_provisional_state_names_preserve_original_numeric_code(code, name):
    sample = TelemetryDecoder().feed(frame(), received=15)[0]
    sample.details["state_code"] = code
    payload = observation(sample)
    assert payload["values"]["state"] == {"code": code, "name": name}
    assert payload["values"]["native"]["phase"] == "Flight"


def test_three_receptions_never_collapse_even_when_rocket_bytes_repeat():
    payloads = [observation(station=f"gs{n}", node_id=str(uuid.uuid4())) for n in (1, 2, 3)]
    assert len({p["observation_id"] for p in payloads}) == 3
    assert len({p["packet"]["raw_sha256"] for p in payloads}) == 1
    assert observation(observation_counter=2)["observation_id"] != observation()["observation_id"]
    receiver_changed = bytearray(frame())
    receiver_changed[130] += 1  # Receiver trailer deliberately remains part of the digest.
    assert observation(raw=receiver_changed)["packet"]["raw_sha256"] != observation()["packet"]["raw_sha256"]


def test_no_fix_or_unknown_bytes_never_become_fabricated_measurements():
    sample = TelemetryDecoder().feed(frame(latitude=100), received=15)[0]
    payload = observation(sample)
    assert payload["values"]["position"] == dict(lat_deg=None, lon_deg=None, alt_m=None, alt_datum="unknown", fix=False)
    with pytest.raises(ValueError, match="reason"):
        observation_from_sample(sample, raw=None, station="gs1",
            node_id=NODE, boot_id=BOOT, connection_id=CONNECTION, observation_counter=1, boot_monotonic=10,
            vehicle_mode="balius")
    with pytest.raises(ValueError, match="complete telemetry"):
        observation(raw=b"not an accepted packet", sample=sample)


@pytest.mark.parametrize("station", ["GS1", "GS2", "GS3"])
def test_preserved_real_receiver_logs_keep_all_native_fields_without_inventing_wire_bytes(station):
    from recorded_flight import RecordedFlight
    flight = RecordedFlight(station)
    sample = flight.sample(flight.launch_index, received=15)
    payload = observation_from_sample(sample, raw=None, raw_unavailable_reason="Original receiver log is decoded CSV",
        station=station, node_id=NODE, boot_id=BOOT, connection_id=CONNECTION,
        observation_counter=1, boot_monotonic=10, vehicle_mode="balius")
    validate_observation(payload)
    assert payload["values"]["native"] == sample.to_dict()
    assert payload["packet"]["raw_b64"] is None
    assert payload["packet"]["raw_sha256"] is None
    assert payload["receiver"]["station"] == station.lower()


def test_remote_display_uses_local_clock_and_keeps_native_gyro_data_unqualified():
    payload = observation()
    payload["values"]["native"]["details"]["orientation"] = {
        "frame": "body_to_ENU", "body_axis": "+Z", "quaternion_wxyz": [1, 0, 0, 0]}
    sample = observation_to_sample(payload, received_monotonic=10000, source_age_ms=200, source_label="GS1")
    assert sample.received == 9999.8
    assert sample.source == "NETWORK"
    assert sample.details["read_only"] is True
    assert sample.details["display_source"] == "GS1"
    assert sample.attitude == [1, 2, 3]
    assert "orientation" not in sample.details
    assert sample.phase == "FLIGHT"
    assert sample.utc == 1700000000.5
    assert sample.details["network_native"]["details"]["orientation"]


def test_archive_snapshots_before_ui_mutation_and_preserves_remote_envelopes(tmp_path):
    archive = ObservationArchive(tmp_path, metadata={"node_id": NODE})
    payload = observation()
    assert archive.put(payload)
    assert archive.event("telemetry.observation", {"event_seq": "9", "payload": payload})
    payload["values"]["state"]["code"] = 222
    archive.close()
    assert not archive.error
    rows = [json.loads(line) for line in (archive.path / "observations.jsonl").read_text().splitlines()]
    assert rows[0]["payload"]["values"]["state"]["code"] == 2
    assert rows[1]["payload"]["event_seq"] == "9"
    assert rows[1]["payload"]["payload"]["observation_id"] == payload["observation_id"]
    manifest = json.loads((archive.path / "manifest.json").read_text())
    assert manifest["complete"] and manifest["written"] == 2


def test_archive_overflow_is_explicit_and_does_not_block_acquisition(tmp_path):
    archive = ObservationArchive(tmp_path, max_queue_bytes=1)
    assert not archive.put(observation())
    assert not archive.put(observation(observation_counter=2))
    archive.close()
    rows = [json.loads(line) for line in (archive.path / "observations.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["kind"] == "archive.gap"
    assert rows[0]["payload"]["lost_records"] == 2
    assert rows[0]["payload"]["first_observation_id"].endswith(":1")
    assert rows[0]["payload"]["last_observation_id"].endswith(":2")
    assert archive.dropped == 2 and "buffer full" in archive.error
    assert not json.loads((archive.path / "manifest.json").read_text())["complete"]


def test_archive_disk_failure_is_visible_and_does_not_raise_into_serial_worker(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied")
    archive = ObservationArchive(blocked)
    archive.close()
    assert archive.failed and "failed" in archive.error
    assert not archive.put(observation())


@pytest.mark.skipif(os.name == "nt", reason="POSIX serial loopback")
def test_serial_observation_precedes_ui_and_reconnect_changes_connection_identity():
    import pty
    from rocket_gnc_monitor.devices import SerialWorker
    master, slave = pty.openpty()
    captures = []
    worker = None
    try:
        for generation in (1, 2):
            events = []

            def sink(sample, raw, context):
                assert not any(event[2] == "sample" for event in events)
                captures.append((sample, raw, context))
                if generation == 2:
                    raise OSError("archive unavailable")

            worker = SerialWorker("telemetry", os.ttyname(slave), generation,
                                  lambda *event: events.append(event), lambda *_: None,
                                  on_observation=sink)
            deadline = time.monotonic() + 2
            while not any(event[2] == "connected" for event in events) and time.monotonic() < deadline:
                time.sleep(.01)
            worker.set_polling(True)
            sent_at = time.monotonic()
            os.write(master, frame(generation))
            deadline = time.monotonic() + 2
            while not any(event[2] == "sample" for event in events) and time.monotonic() < deadline:
                time.sleep(.01)
            assert any(event[2] == "sample" for event in events)
            assert captures[-1][0].received >= sent_at
            assert captures[-1][1] == frame(generation)
            assert captures[-1][2]["observation_counter"] == 1
            if generation == 2:
                assert any(event[2] == "observation_error" for event in events)
                assert not any(event[2] == "error" for event in events)
            worker.stop()
            worker = None
        assert captures[0][2]["connection_id"] != captures[1][2]["connection_id"]
        assert all(str(uuid.UUID(c[2]["connection_id"])) == c[2]["connection_id"] for c in captures)
    finally:
        if worker:
            worker.stop()
        os.close(master)
        os.close(slave)
