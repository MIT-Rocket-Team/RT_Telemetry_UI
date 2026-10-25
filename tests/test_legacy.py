import csv
import pytest
from rocket_gnc_monitor.domain import Mission
from rocket_gnc_monitor.legacy import import_legacy, numeric_list
from rocket_gnc_monitor.recording import SessionReader


def test_documented_csv_import_retains_time_quality_and_unknown_columns(tmp_path):
    source = tmp_path / "telemetry.csv"
    row = dict(
        timestamp="100",
        flight_time="15000",
        pktnum="1",
        barofilteredalt="200",
        gpsalt="220",
        lat="42",
        lon="-71",
        state="state.PRE_FLIGHT",
        servos="[1000, 1010, 1020, 1030]",
        cell_voltages="[np.float64(4.0), np.float64(4.1), 4.2]",
        gps_fix="3",
        extra="preserve",
    )
    with source.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        row.update(timestamp="101", flight_time="16000", pktnum="2", state="state.FLIGHT")
        writer.writerow(row)
    path = import_legacy(source, tmp_path, Mission())
    reader = SessionReader(path)
    try:
        assert reader.manifest["complete"] and not reader.manifest["raw_packets_available"]
        assert reader.count == 2 and reader.duration == 1
        sample = reader.at(1)
        assert sample.source == "LEGACY_CSV" and sample.t == 16
        assert sample.battery == pytest.approx(12.3)
        assert sample.details["legacy_csv"]["extra"] == "preserve"
        assert reader.events(1)[0][1]["data"]["flight_zero"] == 16
        assert (path / "legacy-source.csv").read_bytes() == source.read_bytes()
    finally:
        reader.close()


def test_legacy_array_never_executes_input():
    assert numeric_list("[np.float64(-1.5), 2]") == [-1.5, 2]
    for value in ["[__import__('os').getcwd()]", "[[1,2]]", "[True]"]:
        with pytest.raises(ValueError):
            numeric_list(value)
