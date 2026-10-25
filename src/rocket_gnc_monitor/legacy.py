"""Import the audited legacy monitor's CSV columns without executing field text."""

from __future__ import annotations
import csv
import hashlib
import math
from pathlib import Path
import shutil
from .domain import write_json
from .legacy_sample import decode_row, numeric_list as numeric_list
from .recording import SessionRecorder


def import_legacy(path, parent, mission):
    source = Path(path)
    if source.stat().st_size > 200_000_000:
        raise ValueError("Legacy CSV exceeds 200 MB")
    if "badpackets" in source.name.lower():
        raise ValueError("Known bad-packet logs cannot be promoted to telemetry samples")
    recorder = None
    try:
        with source.open(newline="", encoding="utf-8-sig") as handle:
            rows = csv.DictReader(handle)
            required = {
                "timestamp",
                "flight_time",
                "pktnum",
                "barofilteredalt",
                "gpsalt",
                "lat",
                "lon",
                "state",
                "servos",
            }
            if not required.issubset(rows.fieldnames or []):
                raise ValueError(
                    "Not the documented Zephyrus CSV layout; missing "
                    + ", ".join(sorted(required - set(rows.fieldnames or [])))
                )
            recorder = SessionRecorder(parent, mission, "LEGACY_CSV", time_aligned=False)
            first = None
            previous = -math.inf
            previous_phase = None
            count = 0
            for row in rows:
                sample = decode_row(row)
                timestamp = sample.utc
                if timestamp < previous:
                    raise ValueError(f"Nonmonotonic CSV time at row {count + 2}")
                if first is None:
                    first = timestamp
                sample.received = recorder.start + timestamp - first
                previous = timestamp
                phase = sample.phase
                if recorder.error:
                    raise ValueError(recorder.error)
                # File import may wait for disk; unlike live acquisition it must never drop rows.
                recorder.queue.put(("sample", sample.received, sample.to_dict()), timeout=5)
                if phase == "Flight" and previous_phase == "Preflight":
                    recorder.queue.put(
                        (
                            "event",
                            sample.received,
                            {"name": "Imported flight transition", "data": {"flight_zero": sample.t}},
                        ),
                        timeout=5,
                    )
                previous_phase = phase
                count += 1
            if not count:
                raise ValueError("CSV contains no samples")
        recorder.close()
        if recorder.error:
            raise ValueError(recorder.error)
        shutil.copyfile(source, recorder.path / "legacy-source.csv")
        with source.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        recorder.manifest.update(
            duration=previous - first,
            started_utc=first,
            imported_rows=count,
            imported_source_sha256=digest,
            raw_packets_available=False,
        )
        write_json(recorder.path / "manifest.json", recorder.manifest)
        return recorder.path
    except Exception:
        if recorder:
            recorder.error = "Legacy import incomplete"
            recorder.close()
            recorder.manifest.update(complete=False, error=recorder.error)
            write_json(recorder.path / "manifest.json", recorder.manifest)
        raise
