import csv
import gzip
import hashlib
import json
from pathlib import Path
import time
import pytest
from rocket_gnc_monitor.controller import Controller
from rocket_gnc_monitor.demo import DemoFlight
from rocket_gnc_monitor.legacy_sample import numeric_list
from rocket_gnc_monitor.zephyrus import CSV_FIELDS, legacy_values


@pytest.mark.parametrize(
    "station,count,digest",
    [
        ("GS1", 4366, "2417e0b5664a5ed559ba31e4861ec527e7e2fba6ad752c289dcde9b9d02a7512"),
        ("GS2", 5464, "528df6e7bd5235b5198dce8ac4380f04788c257e5d86f25222118e96a76fc398"),
        ("GS3", 4939, "d4765ef36ee810dc2948a8a96fe7bb899a7ebe544bb2a2a23da113edd5987968"),
    ],
)
def test_original_recordings_are_lossless_and_all_rows_decode(station, count, digest):
    root = Path(__file__).resolve().parents[1]
    raw = gzip.decompress((root / "resources" / "demo" / f"ZEPH_TEST_FLIGHT_{station}.csv.gz").read_bytes())
    assert hashlib.sha256(raw).hexdigest() == digest
    demo = DemoFlight(station)
    assert len(demo.rows) == count
    assert demo.flight_zero == 747.766
    phases = set()
    for index, row in enumerate(demo.rows):
        sample = demo.sample(index)
        assert sample.details["legacy_csv"] == row
        assert set(legacy_values(sample)) == set(CSV_FIELDS)
        assert sample.utc == float(row["timestamp"])
        assert sample.sequence == int(row["pktnum"])
        assert sample.t == float(row["flight_time"]) / 1000
        assert sample.source == "DEMO"
        assert not any(name.startswith(("Canard", "Tab")) for name in sample.actuators)
        json.dumps(sample.to_dict(), allow_nan=False)
        phases.add(sample.phase)
    assert phases == {"Ground testing", "Preflight", "Flight", "Post-apogee", "Main"}
    launch = demo.sample(demo.launch_index)
    assert launch.phase == "Flight" and launch.altitude == pytest.approx(143.70726013183594)
    assert launch.enu == pytest.approx([0, 0, launch.altitude])
    first = legacy_values(demo.sample(0))
    assert first["enabled_status"] == [True] * 6
    assert first["pyros"] == [2] * 6
    assert first["servos"] == [941, 1500, 1495, 1430]
    assert first["servos_deg"] == [-67.08, 0, -0.5, -7]
    assert first["converter_voltages"][0] == pytest.approx(3.0016)
    assert first["temp"] == float(demo.rows[0]["temp"])


def test_corrupt_gps_is_preserved_but_cannot_overwrite_the_track():
    demo = DemoFlight("GS3")
    index = next(i for i, row in enumerate(demo.rows) if float(row["lat"]) < 42.5)
    sample = demo.sample(index)
    assert sample.latitude == float(demo.rows[index]["lat"])
    assert sample.enu is None and "20 km" in sample.details["position_warning"]
    assert numeric_list("[np.True_, numpy.False_, True]", boolean=True) == [True, False, True]
    with pytest.raises(ValueError):
        numeric_list("[np.bool_(__import__('os').getcwd())]", boolean=True)


@pytest.fixture
def demo_controller(qapp, tmp_path, monkeypatch):
    c = Controller(tmp_path)
    c.timer.stop()
    # These tests focus on telemetry; the session-flow test exercises real video.
    monkeypatch.setattr(c, "start_video", lambda *args, **kwargs: None)
    c.switch_mode("DEMO")
    yield c
    c.shutdown()


def test_playback_preserves_receive_gaps_and_packet_order(demo_controller):
    c = demo_controller
    assert c.demo.station == "GS2" and c.demo_time == c.demo.cue
    assert c.latest.phase == "Preflight" and c.latest.t < c.flight_zero
    assert c.reference is None
    gap = max(range(1, len(c.demo.times)), key=lambda i: c.demo.times[i] - c.demo.times[i - 1])
    c.seek_demo(c.demo.times[gap - 1])
    before = c.latest
    c.advance_demo(4, time.monotonic())
    assert c.latest is before  # Do not upsample or invent telemetry through a radio gap.
    c.seek_demo(c.demo.times[gap - 1])
    c.advance_demo(c.demo.times[gap] - c.demo_time, time.monotonic())
    assert c.latest.utc == float(c.demo.rows[gap]["timestamp"])
    c.seek_demo(c.demo.cue)
    c.advance_demo(40, time.monotonic())
    assert c.latest.phase == "Post-apogee"
    assert max(s.altitude for s in c.history) == pytest.approx(5549.2275390625)
    c.play_demo(False)
    before = c.latest
    c.advance_demo(5, time.monotonic())
    assert c.latest is before
    c.play_demo(True)
    c.seek_demo(c.demo.duration - 0.001)
    c.advance_demo(1, time.monotonic())
    assert not c.demo_playing and c.latest.phase == "Main"
    assert c.latest.details["demo_row"] == len(c.demo.rows) + 1
    c.play_demo(True)
    assert c.demo_time == c.demo.cue and c.demo_playing


def test_station_switch_and_live_return_preserve_mission_and_isolate_sources(demo_controller):
    c = demo_controller
    mission = c.mission
    c.select_demo("GS1")
    assert c.latest.details["demo_station"] == "GS1"
    assert all(s.details["demo_station"] == "GS1" for s in c.history)
    assert len(c.track) > 0
    assert all(-360 <= value <= 360 for value in c.tracking_target())
    c.select_demo("GS3")
    with pytest.raises(ValueError, match="GPS"):
        c.tracking_target()
    c.switch_mode("LIVE")
    assert c.mission is mission and not c.mission.site_configured
    assert c.latest is None and not c.track and not c.history and c.demo is None
    assert not any(c.workers.values()) and not c.time_aligned


def test_slow_seek_preserves_recorded_packet_ages(demo_controller, monkeypatch):
    c = demo_controller
    clock = [1000.0]
    sample = c.demo.sample

    def slow_sample(*args, **kwargs):
        clock[0] += 0.01
        return sample(*args, **kwargs)

    monkeypatch.setattr("rocket_gnc_monitor.controller.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(c.demo, "sample", slow_sample)
    c.seek_demo(c.demo.cue)
    assert clock[0] - 1000 > c.mission.freshness
    first = c.demo_index - len(c.history)
    for index, received in enumerate(c.history, start=first):
        assert c.last_tick - received.received == pytest.approx(c.demo_time - c.demo.times[index])


def test_demo_recording_keeps_csv_fields_and_provenance(demo_controller, tmp_path, qtbot):
    from rocket_gnc_monitor.recording import SessionReader

    c = demo_controller
    path = c.start_recording(tmp_path)
    with pytest.raises(ValueError, match="Stop logging"):
        c.select_demo("GS1")
    with pytest.raises(ValueError, match="Stop logging"):
        c.seek_demo(0)
    c.advance_demo(0.2, time.monotonic())
    last = c.latest
    c.stop_recording()
    qtbot.waitUntil(lambda: all(future.done() for _, future in c.futures), timeout=6000)
    reader = SessionReader(path)
    try:
        assert reader.manifest["complete"] and not reader.manifest["raw_packets_available"]
        assert reader.manifest["demo_recording"]["station"] == "GS2"
        assert reader.at(reader.duration).details["legacy_csv"] == last.details["legacy_csv"]
        with (path / "telemetry.csv").open() as handle:
            row = list(csv.DictReader(handle))[-1]
        assert row["temp"] == last.details["legacy_csv"]["temp"]
        assert row["bms_protection_status"] == last.details["legacy_csv"]["bms_protection_status"]
        assert row["timestamp"] == last.details["legacy_csv"]["timestamp"]
    finally:
        reader.close()
