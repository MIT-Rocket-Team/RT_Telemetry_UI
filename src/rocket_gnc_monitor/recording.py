"""Bounded asynchronous session recorder and indexed, read-only replay."""

from __future__ import annotations
from dataclasses import asdict
import csv
import json
import math
import os
from pathlib import Path
import queue
import sqlite3
import struct
import threading
import time
import uuid
import zlib
from . import __version__
from .domain import Sample, write_json
from .zephyrus import CSV_FIELDS, legacy_values
from .protocol import ZephyrusDecoder

RAW_HEADER = struct.Struct("<4sddBI")
RAW_ROLES = {"telemetry_rx": 1, "pointer_rx": 2, "pointer_tx": 3, "telemetry_tx": 4}


class SessionRecorder:
    def __init__(self, parent, mission, mode, flight_zero=0.0, time_aligned=True):
        self.path = Path(parent) / (time.strftime("session_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6])
        self.path.mkdir(parents=True)
        self.start = time.monotonic()
        self.queue = queue.Queue(maxsize=16000)
        self.error = ""
        self.dropped = 0
        self.closed = False
        self.manifest = dict(
            schema_version=1,
            app_version=__version__,
            mode=mode,
            complete=False,
            started_utc=time.time(),
            flight_zero=flight_zero,
            time_aligned=time_aligned,
            mission=asdict(mission),
            video_clock="Host receive timing; exposure time unknown",
            raw_format="RGM1",
        )
        write_json(self.path / "manifest.json", self.manifest)
        self.thread = threading.Thread(target=self._run, name="session-recorder", daemon=True)
        self.thread.start()

    def put(self, kind, data, received=None):
        if self.closed:
            return
        try:
            self.queue.put_nowait((kind, time.monotonic() if received is None else received, data))
        except queue.Full:
            self.dropped += 1
            self.error = f"Recording buffer full: {self.dropped} records lost"

    def sample(self, sample):
        self.put("sample", sample.to_dict(), sample.received)

    def event(self, name, data=None):
        self.put("event", {"name": name, "data": data or {}})

    def raw(self, role, data):
        self.put("raw", (role, bytes(data), time.time()))

    def close(self):
        self.closed = True
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self.error = "Recorder did not finish; session remains incomplete"

    def _run(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path / "session.sqlite")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript("""
                CREATE TABLE samples(id INTEGER PRIMARY KEY, elapsed REAL, t REAL, data TEXT);
                CREATE INDEX sample_time ON samples(elapsed);
                CREATE TABLE events(id INTEGER PRIMARY KEY, elapsed REAL, data TEXT);
                CREATE INDEX event_time ON events(elapsed);
                CREATE TABLE raw_index(id INTEGER PRIMARY KEY, elapsed REAL, role TEXT, offset INTEGER, length INTEGER);
                CREATE TABLE snapshot_state(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT);
            """)
            connection.commit()
            last_commit = time.monotonic()
            with (
                (self.path / "raw.bin").open("wb") as raw,
                (self.path / "telemetry.csv").open("w", newline="", encoding="utf-8") as telemetry,
                (self.path / "telemetry_badpackets.csv").open("w", newline="", encoding="utf-8") as rejected,
            ):
                good_csv, bad_csv = (
                    csv.DictWriter(telemetry, CSV_FIELDS),
                    csv.DictWriter(rejected, CSV_FIELDS),
                )
                good_csv.writeheader()
                bad_csv.writeheader()

                def bad_packet(payload, trailer, count):
                    sample = ZephyrusDecoder().decode(payload, trailer)
                    bad_csv.writerow(legacy_values(sample, count, rejected=True))

                raw_decoder = ZephyrusDecoder(on_rejected=bad_packet)
                while not self.closed or not self.queue.empty():
                    try:
                        kind, received, data = self.queue.get(timeout=0.1)
                    except queue.Empty:
                        kind = None
                    if kind:
                        # Microsecond session timestamps avoid cancellation noise at seek boundaries.
                        elapsed = max(0.0, round(received - self.start, 6))
                        if kind == "sample":
                            sample = Sample.from_dict(data)
                            good_csv.writerow(legacy_values(sample, sample.details.get("bad_packets", 0)))
                            connection.execute(
                                "INSERT INTO samples(elapsed,t,data) VALUES(?,?,?)",
                                (elapsed, data["t"], json.dumps(data, allow_nan=False)),
                            )
                        elif kind == "event":
                            connection.execute(
                                "INSERT INTO events(elapsed,data) VALUES(?,?)",
                                (elapsed, json.dumps(data, allow_nan=False)),
                            )
                        elif kind == "raw":
                            role, payload, utc = data
                            if role == "telemetry_rx":
                                raw_decoder.feed(payload)
                            offset = raw.tell()
                            header = RAW_HEADER.pack(b"RGM1", elapsed, utc, RAW_ROLES[role], len(payload))
                            raw.write(header + payload + struct.pack("<I", zlib.crc32(header + payload)))
                            connection.execute(
                                "INSERT INTO raw_index(elapsed,role,offset,length) VALUES(?,?,?,?)",
                                (elapsed, role, offset, len(payload)),
                            )
                        self.queue.task_done()
                    if time.monotonic() - last_commit >= 0.25 or (self.closed and self.queue.empty()):
                        # Data must reach disk before a committed index can point at it.
                        for handle in (telemetry, rejected):
                            handle.flush()
                            os.fsync(handle.fileno())
                        raw.flush()
                        os.fsync(raw.fileno())
                        # The online SQLite backup carries matching immutable file-prefix lengths.
                        # Saving a flight can then copy committed data while acquisition continues.
                        state = dict(
                            lengths={
                                "raw.bin": raw.tell(),
                                "telemetry.csv": telemetry.buffer.tell(),
                                "telemetry_badpackets.csv": rejected.buffer.tell(),
                            },
                            manifest={
                                **self.manifest,
                                "duration": time.monotonic() - self.start,
                                "dropped": self.dropped,
                                "error": self.error,
                            },
                        )
                        connection.execute(
                            "INSERT OR REPLACE INTO snapshot_state VALUES(1,?)",
                            (json.dumps(state, allow_nan=False),),
                        )
                        connection.commit()
                        last_commit = time.monotonic()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.manifest.update(
                complete=not bool(self.error),
                duration=time.monotonic() - self.start,
                dropped=self.dropped,
                error=self.error,
            )
            write_json(self.path / "manifest.json", self.manifest)
        except Exception as exc:
            self.error = f"Recording failed: {exc}"
        finally:
            if connection:
                connection.close()


class SessionReader:
    def __init__(self, path):
        self.path = Path(path)
        self.manifest = json.loads((self.path / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("schema_version") != 1:
            raise ValueError("Unsupported session version")
        self.db = sqlite3.connect((self.path / "session.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
        self.duration = self.db.execute("SELECT COALESCE(MAX(elapsed),0) FROM samples").fetchone()[0]
        self.count = self.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        if not self.count:
            # The video computer has no telemetry PCB. Its final manifest is
            # written after every video segment closes; flight snapshots carry
            # their own bounded capture duration. Preserve historical sample-
            # based seeking for sessions that do contain telemetry.
            declared = self.manifest.get("duration", 0)
            if isinstance(declared, bool) or not isinstance(declared, (int, float)):
                declared = 0
            try:
                declared = max(0, declared) if math.isfinite(declared) else 0
            except OverflowError:
                declared = 0
            event_end = self.db.execute("SELECT COALESCE(MAX(elapsed),0) FROM events").fetchone()[0]
            self.duration = max(self.duration, declared, event_end)
        self.integrity = self.db.execute("PRAGMA quick_check").fetchone()[0]
        if self.integrity != "ok":
            self.close()
            raise ValueError("Session database integrity check failed")

    def between(self, begin, end, limit=20000):
        rows = self.db.execute(
            "SELECT elapsed,data FROM samples WHERE elapsed>? AND elapsed<=? ORDER BY elapsed,id LIMIT ?",
            (begin, end, limit),
        ).fetchall()
        return [(t, Sample.from_dict(json.loads(data))) for t, data in rows]

    def at(self, elapsed):
        row = self.db.execute(
            "SELECT data FROM samples WHERE elapsed<=? ORDER BY elapsed DESC,id DESC LIMIT 1", (elapsed,)
        ).fetchone()
        return Sample.from_dict(json.loads(row[0])) if row else None

    def events(self, elapsed, begin=-1):
        return [
            (t, json.loads(data))
            for t, data in self.db.execute(
                "SELECT elapsed,data FROM events WHERE elapsed>? AND elapsed<=? ORDER BY elapsed,id",
                (begin, elapsed),
            )
        ]

    def export_csv(self, path):
        with Path(path).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "elapsed_s",
                    "onboard_s",
                    "sequence",
                    "source",
                    "altitude_m",
                    "velocity_m_s",
                    "latitude_deg",
                    "longitude_deg",
                    "gps_altitude_m",
                    "battery_v",
                    "rssi_dbm",
                    "sample_json",
                ]
            )
            for elapsed, text in self.db.execute("SELECT elapsed,data FROM samples ORDER BY elapsed,id"):
                s = json.loads(text)
                writer.writerow(
                    [elapsed]
                    + [
                        s.get(k)
                        for k in (
                            "t",
                            "sequence",
                            "source",
                            "altitude",
                            "velocity",
                            "latitude",
                            "longitude",
                            "gps_altitude",
                            "battery",
                            "rssi",
                        )
                    ]
                    + [text]
                )

    def close(self):
        self.db.close()


def read_raw(path):
    """Yield complete CRC-checked chunks; reject a damaged tail explicitly."""
    with Path(path).open("rb") as handle:
        while header := handle.read(RAW_HEADER.size):
            if len(header) != RAW_HEADER.size:
                raise ValueError("Incomplete raw capture header")
            magic, elapsed, utc, role, length = RAW_HEADER.unpack(header)
            if magic != b"RGM1" or length > 4 * 1024 * 1024:
                raise ValueError("Invalid raw capture header")
            payload, checksum = handle.read(length), handle.read(4)
            if len(payload) != length or len(checksum) != 4:
                raise ValueError("Incomplete raw capture payload")
            if zlib.crc32(header + payload) != struct.unpack("<I", checksum)[0]:
                raise ValueError("Raw capture CRC mismatch")
            yield elapsed, utc, role, payload
