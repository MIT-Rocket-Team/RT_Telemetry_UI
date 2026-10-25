"""Portable, versioned flight archives; background snapshots never pause acquisition."""

from __future__ import annotations
import copy
from contextlib import closing
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
import zipfile

from . import __version__
from .domain import Mission, Sample, finite, write_json
from .recording import SessionReader, SessionRecorder
from .trajectory import Trajectory

FORMAT = "rocket-gnc-flight"
MAX_BYTES = 64 * 1024**3
MAX_FILES = 100_000
JSON_LIMIT = 16 * 1024**2


@dataclass
class LoadedFlight:
    path: Path
    directory: Path
    mission: Mission
    reference: Trajectory | None
    session: Path | None
    metadata: dict


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def relative_name(name):
    path = PurePosixPath(name)
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or any(p in {"", ".", ".."} for p in name.split("/"))
        or any(ord(c) < 32 for c in name)
    ):
        raise ValueError("Invalid path in flight file")
    return path


def read_json(path):
    if path.stat().st_size > JSON_LIMIT:
        raise ValueError("Flight metadata exceeds 16 MB")
    return json.loads(path.read_text(encoding="utf-8"))


def copy_prefix(source, destination, length=None):
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Flight resource is missing or is a symbolic link: {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if length is None:
        length = source.stat().st_size
    if type(length) is not int or not 0 <= length <= MAX_BYTES:
        raise ValueError("Invalid flight resource length")
    with source.open("rb") as src, destination.open("wb") as dst:
        remaining = length
        while remaining:
            chunk = src.read(min(1024**2, remaining))
            if not chunk:
                raise ValueError(f"Flight resource changed during save: {source.name}")
            dst.write(chunk)
            remaining -= len(chunk)


def snapshot_session(source, destination, active=False):
    """Back up the committed database, then copy precisely the matching raw/CSV prefixes."""
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True)
    deadline = time.monotonic() + 5
    while True:
        if active and not (source / "session.sqlite").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
            continue
        if (source / "session.sqlite").is_symlink():
            raise ValueError("Session database must not be a symbolic link")
        with closing(
            sqlite3.connect((source / "session.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
        ) as src:
            with closing(sqlite3.connect(destination / "session.sqlite")) as dst:
                src.backup(dst, pages=256, sleep=0.01)
                has_state = dst.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshot_state'"
                ).fetchone()
                row = (
                    dst.execute("SELECT data FROM snapshot_state WHERE id=1").fetchone()
                    if has_state
                    else None
                )
        if row or not active:
            break
        if time.monotonic() >= deadline:
            raise ValueError("Recording has not committed its first snapshot; try saving again")
        time.sleep(0.05)
    state = json.loads(row[0]) if row else None
    manifest = state["manifest"] if active else read_json(source / "manifest.json")
    lengths = state["lengths"] if state else {}
    for name in ("raw.bin", "telemetry.csv", "telemetry_badpackets.csv"):
        if (source / name).exists():
            copy_prefix(source / name, destination / name, lengths.get(name))
    for name in ("legacy-source.csv", "reference.csv", "reference.json"):
        if (source / name).exists():
            copy_prefix(source / name, destination / name)
    if active:
        manifest.update(complete=False, snapshot=True, snapshot_utc=time.time())
    video_files = 0
    with closing(sqlite3.connect(destination / "session.sqlite")) as db:
        video_starts = {}
        for elapsed, text in db.execute("SELECT elapsed,data FROM events ORDER BY elapsed,id"):
            event = json.loads(text)
            if event.get("name") == "Video recording started":
                video_starts[event.get("data", {}).get("directory", "video")] = elapsed
    for name, start in video_starts.items():
        if len(relative_name(name).parts) != 1:
            raise ValueError("Invalid video directory in session")
        folder = source / name
        index = folder / "segments.csv"
        if not index.exists():
            continue
        if folder.is_symlink() or index.is_symlink() or index.stat().st_size > JSON_LIMIT:
            raise ValueError("Invalid video segment index")
        # An active FFmpeg segment isn't listed until it is closed. Ignore a partial index line.
        text = index.read_text(encoding="utf-8")
        if active and text and not text.endswith("\n"):
            text = text.rsplit("\n", 1)[0] + "\n" if "\n" in text else ""
        rows = []
        for row in csv.reader(io.StringIO(text)):
            if len(row) != 3:
                raise ValueError("Invalid video segment index")
            filename, begin, end = row
            if len(relative_name(filename).parts) != 1:
                raise ValueError("Invalid video segment filename")
            begin, end = float(begin), float(end)
            if not all(math.isfinite(v) for v in (begin, end)) or not 0 <= begin <= end:
                raise ValueError("Invalid video segment timing")
            if active and start + end > manifest.get("duration", 0):
                continue
            copy_prefix(folder / filename, destination / name / filename)
            rows.append([filename, begin, end])
            video_files += 1
        if rows:
            with (destination / name / "segments.csv").open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows(rows)
    manifest["flight_video_scope"] = "Finalized segments only" if active else "Recorded segments"
    manifest["flight_video_segments"] = video_files
    write_json(destination / "manifest.json", manifest)
    return manifest


def samples_session(destination, mission, samples, elapsed, mode, flight_zero, time_aligned, metadata):
    """Store decoded samples without inventing raw packets or camera footage."""
    recorder = SessionRecorder(destination.parent, mission, mode, flight_zero, time_aligned)
    try:
        recorder.manifest.update(metadata, raw_packets_available=False)
        for index, sample in enumerate(samples):
            recorder.queue.put(("sample", recorder.start + elapsed[index], sample.to_dict()), timeout=10)
        recorder.close()
        if recorder.error:
            raise ValueError(recorder.error)
        manifest = read_json(recorder.path / "manifest.json")
        manifest["duration"] = elapsed[-1] if elapsed else 0
        write_json(recorder.path / "manifest.json", manifest)
        recorder.path.rename(destination)
    finally:
        if not recorder.closed:
            recorder.close()


def save_flight(
    path,
    mission,
    reference=None,
    *,
    session=None,
    active=False,
    samples=(),
    elapsed=(),
    mode="LIVE",
    flight_zero=0,
    time_aligned=False,
    position=0,
    scope="Mission and simulation",
    demo=None,
    pending_close=None,
):
    """Create an atomic ZIP64 archive. Arguments are detached from mutable UI state."""
    path = Path(path).expanduser().resolve()
    mission = copy.deepcopy(mission).validate()
    mission.pointer_calibrated = False
    path.parent.mkdir(parents=True, exist_ok=True)
    if pending_close is not None:
        _, error, _ = pending_close.result()
        if error:
            raise ValueError(error)
    with tempfile.TemporaryDirectory(prefix=".flight-", dir=path.parent) as temporary:
        root = Path(temporary)
        payload = root / "payload"
        payload.mkdir()
        if mission.model:
            source = Path(mission.model)
            destination = payload / "assets" / "rocket.ork"
            copy_prefix(source, destination)
            mission.model = "assets/rocket.ork"
        motors = []
        for index, source in enumerate(mission.motor_files):
            source = Path(source)
            if source.suffix.lower() not in {".eng", ".rse"}:
                raise ValueError("Custom motors must be .eng or .rse files")
            name = f"assets/motor_{index:03d}{source.suffix.lower()}"
            copy_prefix(source, payload / name)
            motors.append(name)
        mission.motor_files = motors
        mission.save(payload / "mission.json")
        if reference is not None:
            reference.save(payload / "reference.csv")
        session_path = payload / "session"
        if session is not None:
            snapshot_session(session, session_path, active)
        elif demo is not None:
            samples_session(
                session_path,
                mission,
                (demo.sample(i) for i in range(len(demo.rows))),
                demo.times,
                "DEMO",
                demo.flight_zero,
                True,
                dict(
                    demo_recording=dict(station=demo.station, **demo.metadata),
                    demo_video="No launch video supplied",
                    export_scope=scope,
                ),
            )
        elif samples:
            samples_session(
                session_path,
                mission,
                samples,
                elapsed,
                mode,
                flight_zero,
                time_aligned,
                dict(export_scope=scope),
            )
        count = 0
        if session_path.exists():
            reader = SessionReader(session_path)
            try:
                count = reader.count
                position = max(0, min(position, reader.duration))
            finally:
                reader.close()
            # This is our private staging copy, with no writer. Make it a standalone
            # database instead of shipping machine-specific SQLite shared-memory files.
            with closing(sqlite3.connect(session_path / "session.sqlite")) as db:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                db.execute("PRAGMA journal_mode=DELETE")
        files = {}
        for file in sorted(payload.rglob("*")):
            if file.is_file():
                files[file.relative_to(payload).as_posix()] = dict(
                    size=file.stat().st_size, sha256=digest(file)
                )
        metadata = dict(
            format=FORMAT,
            schema_version=1,
            app_version=__version__,
            saved_utc=time.time(),
            scope=scope,
            source_mode=mode,
            samples=count,
            session="session" if session_path.exists() else None,
            position=position,
            flight_zero=flight_zero,
            time_aligned=time_aligned,
            video_scope="Finalized segments only; save again after stopping logging for the last segment"
            if active
            else "Recorded video if available",
            files=files,
        )
        write_json(payload / "flight.json", metadata)
        archive = root / "flight.tmp"
        with zipfile.ZipFile(
            archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True
        ) as output:
            for file in sorted(payload.rglob("*")):
                if file.is_file():
                    output.write(
                        file,
                        file.relative_to(payload).as_posix(),
                        compress_type=zipfile.ZIP_STORED if file.suffix == ".mkv" else zipfile.ZIP_DEFLATED,
                    )
        with archive.open("rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(archive, path)
    return dict(path=str(path), **metadata)


def load_flight(path, cache):
    """Validate and extract fully before the controller replaces the current flight."""
    path, cache = Path(path).resolve(), Path(cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / uuid.uuid4().hex
    destination.mkdir()
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES or sum(item.file_size for item in entries) > MAX_BYTES:
                raise ValueError("Flight archive exceeds supported size (64 GB / 100,000 files)")
            seen = set()
            for item in entries:
                relative_name(item.filename)
                key = item.filename.casefold()
                kind = stat.S_IFMT(item.external_attr >> 16)
                if key in seen or item.is_dir() or kind not in {0, stat.S_IFREG} or item.flag_bits & 1:
                    raise ValueError("Flight archive contains duplicate, linked or unsupported entries")
                seen.add(key)
            info = archive.getinfo("flight.json")
            if info.file_size > JSON_LIMIT:
                raise ValueError("Flight metadata exceeds 16 MB")
            metadata = json.loads(archive.read(info))
            if metadata.get("format") != FORMAT or metadata.get("schema_version") != 1:
                raise ValueError("Unsupported flight file version")
            if not isinstance(metadata.get("scope"), str) or type(metadata.get("time_aligned")) is not bool:
                raise ValueError("Invalid flight metadata")
            files = metadata.get("files")
            if not isinstance(files, dict) or set(files) | {"flight.json"} != {i.filename for i in entries}:
                raise ValueError("Flight file inventory does not match archive")
            if sum(item.file_size for item in entries) > shutil.disk_usage(cache).free:
                raise ValueError("Not enough disk space to open this flight")
            for name, expected in files.items():
                item = archive.getinfo(name)
                if expected.get("size") != item.file_size:
                    raise ValueError(f"Flight file size mismatch: {name}")
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                checksum = hashlib.sha256()
                with archive.open(item) as src, target.open("wb") as dst:
                    while chunk := src.read(1024**2):
                        checksum.update(chunk)
                        dst.write(chunk)
                if checksum.hexdigest() != expected.get("sha256"):
                    raise ValueError(f"Flight file checksum mismatch: {name}")
        mission = Mission.load(destination / "mission.json")

        def asset(name):
            relative_name(name)
            if not name.startswith("assets/") or name not in files:
                raise ValueError("Flight references an asset outside the archive")
            return str(destination / name)

        mission.model = asset(mission.model) if mission.model else ""
        mission.motor_files = [asset(name) for name in mission.motor_files]
        reference = Trajectory.load(destination / "reference.csv") if "reference.csv" in files else None
        session = None
        if metadata.get("session") is not None:
            if metadata["session"] != "session":
                raise ValueError("Invalid flight session path")
            session = destination / "session"
            reader = SessionReader(session)
            try:
                # Validate all sample/event JSON before touching the current UI state.
                for elapsed, text in reader.db.execute("SELECT elapsed,data FROM samples"):
                    if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
                        raise ValueError("Invalid flight sample time")
                    sample = Sample.from_dict(json.loads(text))
                    if not finite(sample.t) or type(sample.sequence) is not int or not finite(sample.utc):
                        raise ValueError("Invalid flight onboard time")
                    for field in (
                        "altitude",
                        "velocity",
                        "latitude",
                        "longitude",
                        "gps_altitude",
                        "battery",
                        "rssi",
                    ):
                        value = getattr(sample, field)
                        if value is not None and not finite(value):
                            raise ValueError(f"Invalid flight sample {field}")
                    for field in ("attitude", "rates", "acceleration", "enu"):
                        value = getattr(sample, field)
                        if value is not None and (
                            not isinstance(value, list)
                            or len(value) != 3
                            or not all(finite(v) for v in value)
                        ):
                            raise ValueError(f"Invalid flight sample {field}")
                    if not isinstance(sample.details, dict) or not isinstance(sample.actuators, dict):
                        raise ValueError("Invalid flight sample fields")
                for _, event in reader.events(float("inf")):
                    if not isinstance(event.get("name"), str) or not isinstance(event.get("data"), dict):
                        raise ValueError("Invalid flight event")
                    if "flight_zero" in event["data"] and not finite(event["data"]["flight_zero"]):
                        raise ValueError("Invalid flight time-alignment event")
                    if event["name"] == "Pointer command sent":
                        angles = event["data"].get("angles")
                        if angles is not None and (
                            not isinstance(angles, list)
                            or len(angles) != 2
                            or not all(finite(a) for a in angles)
                        ):
                            raise ValueError("Invalid pointer event")
                if not finite(reader.manifest.get("flight_zero", 0)):
                    raise ValueError("Invalid session time alignment")
                if (session / "reference.csv").exists():
                    Trajectory.load(session / "reference.csv")
                for index in session.glob("video*/segments.csv"):
                    if index.stat().st_size > JSON_LIMIT:
                        raise ValueError("Video index exceeds 16 MB")
                    with index.open(newline="", encoding="utf-8") as handle:
                        for row in csv.reader(handle):
                            if len(row) != 3 or len(relative_name(row[0]).parts) != 1:
                                raise ValueError("Invalid video segment index")
                            begin, end = float(row[1]), float(row[2])
                            if not finite(begin) or not finite(end) or not 0 <= begin <= end:
                                raise ValueError("Invalid video segment timing")
                            if not (index.parent / row[0]).is_file():
                                raise ValueError("Flight video segment missing")
            finally:
                reader.close()
            manifest = read_json(session / "manifest.json")
            manifest["mission"] = json.loads((destination / "mission.json").read_text(encoding="utf-8"))
            manifest["mission"].update(
                model=mission.model, motor_files=mission.motor_files, pointer_calibrated=False
            )
            write_json(session / "manifest.json", manifest)
            if reference:
                reference.save(session / "reference.csv")
        for key in ("position", "flight_zero"):
            value = metadata.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Invalid flight {key}")
        write_json(destination / "flight.json", metadata)
        return LoadedFlight(path, destination, mission, reference, session, metadata)
    except Exception as exc:
        shutil.rmtree(destination, ignore_errors=True)
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Could not open flight file: {exc}") from exc
