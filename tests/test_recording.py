import json
import pytest
from rocket_gnc_monitor.domain import Mission, demo_sample
from rocket_gnc_monitor.recording import SessionRecorder, SessionReader, read_raw


def test_session_replay_seek_export_and_raw_recovery(tmp_path):
    recorder = SessionRecorder(tmp_path, Mission(), "DEMO")
    for i in range(20):
        s = demo_sample(i * 0.05, i)
        s.received = recorder.start + i * 0.05
        recorder.sample(s)
    recorder.raw("telemetry_rx", b"abc\x00\xff")
    recorder.event("flight zero", {"flight_zero": 0.5})
    recorder.close()
    assert not recorder.error
    reader = SessionReader(recorder.path)
    assert reader.count == 20 and reader.manifest["complete"]
    assert reader.at(0.3).sequence == 6
    assert reader.at(0.05).sequence == 1
    assert len(reader.between(0.05, 0.15)) in {1, 2}
    reader.export_csv(tmp_path / "out.csv")
    assert "sample_json" in (tmp_path / "out.csv").read_text()
    reader.close()
    chunks = list(read_raw(recorder.path / "raw.bin"))
    assert len(chunks) == 1 and chunks[0][2:] == (1, b"abc\x00\xff")
    with (recorder.path / "raw.bin").open("ab") as handle:
        handle.write(b"RGM")
    with pytest.raises(ValueError, match="Incomplete"):
        list(read_raw(recorder.path / "raw.bin"))


def test_incomplete_manifest_still_allows_committed_replay(tmp_path):
    recorder = SessionRecorder(tmp_path, Mission(), "DEMO")
    recorder.sample(demo_sample(0, 1))
    recorder.close()
    path = recorder.path / "manifest.json"
    data = json.loads(path.read_text())
    data["complete"] = False
    path.write_text(json.dumps(data))
    reader = SessionReader(recorder.path)
    assert reader.count == 1 and not reader.manifest["complete"]
    reader.close()


def test_close_drains_a_large_pending_batch(tmp_path):
    from rocket_gnc_monitor.domain import demo_sample

    r = SessionRecorder(tmp_path, Mission(), "DEMO")
    sample = demo_sample(1, 1)
    for i in range(2500):
        sample.sequence = i
        sample.received = r.start + i * 0.01
        r.sample(sample)
    r.close()
    assert not r.error and not r.thread.is_alive()
    reader = SessionReader(r.path)
    try:
        assert reader.count == 2500 and reader.manifest["complete"]
    finally:
        reader.close()
