import gzip
import hashlib
import json
from pathlib import Path
import pytest
from recorded_flight import RecordedFlight
from rocket_gnc_monitor.legacy_sample import numeric_list
from rocket_gnc_monitor.rocket_protocol import CSV_FIELDS, legacy_values


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
    raw = gzip.decompress((root / "tests" / "fixtures" / "recorded_flight" / f"TEST_FLIGHT_{station}.csv.gz").read_bytes())
    assert hashlib.sha256(raw).hexdigest() == digest
    recording = RecordedFlight(station)
    assert len(recording.rows) == count
    assert recording.flight_zero == 747.766
    phases = set()
    for index, row in enumerate(recording.rows):
        sample = recording.sample(index)
        assert sample.details["legacy_csv"] == row
        assert set(legacy_values(sample)) == set(CSV_FIELDS)
        assert sample.utc == float(row["timestamp"])
        assert sample.sequence == int(row["pktnum"])
        assert sample.t == float(row["flight_time"]) / 1000
        assert sample.source == "REPLAY"
        assert not any(name.startswith(("Canard", "Tab")) for name in sample.actuators)
        json.dumps(sample.to_dict(), allow_nan=False)
        phases.add(sample.phase)
    assert phases == {"Ground testing", "Preflight", "Flight", "Post-apogee", "Main"}
    launch = recording.sample(recording.launch_index)
    assert launch.phase == "Flight" and launch.altitude == pytest.approx(143.70726013183594)
    assert launch.enu == pytest.approx([0, 0, launch.altitude])
    first = legacy_values(recording.sample(0))
    assert first["enabled_status"] == [True] * 6
    assert first["pyros"] == [2] * 6
    assert first["servos"] == [941, 1500, 1495, 1430]
    assert first["servos_deg"] == [-67.08, 0, -0.5, -7]
    assert first["converter_voltages"][0] == pytest.approx(3.0016)
    assert first["temp"] == float(recording.rows[0]["temp"])


def test_corrupt_gps_is_preserved_but_cannot_overwrite_the_track():
    recording = RecordedFlight("GS3")
    index = next(i for i, row in enumerate(recording.rows) if float(row["lat"]) < 42.5)
    sample = recording.sample(index)
    assert sample.latitude == float(recording.rows[index]["lat"])
    assert sample.enu is None and "20 km" in sample.details["position_warning"]
    assert numeric_list("[np.True_, numpy.False_, True]", boolean=True) == [True, False, True]
    with pytest.raises(ValueError):
        numeric_list("[np.bool_(__import__('os').getcwd())]", boolean=True)
