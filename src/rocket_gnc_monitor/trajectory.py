"""Normalized ENU trajectories, weather retrieval and isolated simulation jobs."""

from __future__ import annotations
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
import numpy as np
from .settings import AppSettings, runtime_root, validate_engine_jar
from .domain import demo_sample, finite, validate_wind, wind_from, write_json
from .media import popen_options


MOTION_COLUMNS = (
    "qw",
    "qx",
    "qy",
    "qz",
    "velocity_east_m_s",
    "velocity_north_m_s",
    "velocity_up_m_s",
    "roll_rate_rad_s",
    "pitch_rate_rad_s",
    "yaw_rate_rad_s",
)


class Trajectory:
    def __init__(self, points, manifest, motion=None):
        self.points = np.asarray(points, dtype=float)
        self.manifest = manifest
        self.motion = None if motion is None else np.asarray(motion, dtype=float)
        if self.points.ndim != 2 or self.points.shape[1] != 4 or not 2 <= len(self.points) <= 200000:
            raise ValueError("Trajectory needs 2–200,000 rows of time/east/north/up")
        if not np.isfinite(self.points).all() or not (np.diff(self.points[:, 0]) > 0).all():
            raise ValueError("Trajectory must be finite with strictly increasing time")
        if self.motion is not None and (
            self.motion.shape != (len(self.points), len(MOTION_COLUMNS))
            or np.isinf(self.motion).any()
            or manifest.get("visuals_schema_version") != 1
            or manifest.get("attitude_frame") != "body_to_ENU"
            or manifest.get("body_axis") != "+Z"
        ):
            raise ValueError(
                "Trajectory motion requires v1 body-to-ENU quaternions, +Z nose and matching rows"
            )
        events = manifest.get("flight_events", [])
        if (
            not isinstance(events, list)
            or len(events) > 10000
            or any(
                not isinstance(e, dict)
                or not finite(e.get("time"))
                or not isinstance(e.get("type"), str)
                or not e["type"]
                or not all(
                    isinstance(e.get(k, ""), str) and len(e.get(k, "")) <= 1000
                    for k in ("type", "source", "source_id")
                )
                for e in events
            )
        ):
            raise ValueError("Invalid trajectory flight events")
        if (
            manifest.get("schema_version") != 1
            or manifest.get("frame") != "ENU"
            or manifest.get("units") != "m,s"
        ):
            raise ValueError("Trajectory manifest must declare v1, ENU and m,s units")
        origin = manifest.get("origin")
        if not manifest.get("synthetic") and (
            not isinstance(origin, list)
            or len(origin) != 3
            or not all(finite(v) for v in origin)
            or not -90 <= origin[0] <= 90
            or not -180 <= origin[1] <= 180
        ):
            raise ValueError("Reference needs a finite [latitude, longitude, ellipsoid altitude] origin")

    @classmethod
    def demo(cls):
        return cls(
            [[t, *demo_sample(t, int(t)).enu] for t in np.linspace(0, 140, 281)],
            dict(
                schema_version=1,
                frame="ENU",
                units="m,s",
                name="Illustrative demo trajectory",
                synthetic=True,
                altitude_datum="launch_relative",
                origin=None,
            ),
        )

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > 40_000_000:
            raise ValueError("Trajectory file exceeds 40 MB")
        manifest = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = csv.DictReader(handle)
            has_motion = all(key in (rows.fieldnames or []) for key in MOTION_COLUMNS)
            if not has_motion and any(key in (rows.fieldnames or []) for key in MOTION_COLUMNS):
                raise ValueError("Trajectory motion columns must be complete")
            points, motion = [], []
            for row in rows:
                points.append([float(row[key]) for key in ("time_s", "east_m", "north_m", "up_m")])
                if has_motion:
                    motion.append([float(row[key]) if row[key] else math.nan for key in MOTION_COLUMNS])
        manifest["csv_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return cls(points, manifest, motion if has_motion else None)

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["time_s", "east_m", "north_m", "up_m"]
                + (list(MOTION_COLUMNS) if self.motion is not None else [])
            )
            for index, point in enumerate(self.points):
                extra = (
                    [v if math.isfinite(v) else "" for v in self.motion[index]]
                    if self.motion is not None
                    else []
                )
                writer.writerow([*point, *extra])
        write_json(path.with_suffix(".json"), self.manifest)

    def at(self, t):
        if t < self.points[0, 0] or t > self.points[-1, 0]:
            return None
        return [float(np.interp(t, self.points[:, 0], self.points[:, i])) for i in range(1, 4)]


def weather_profile(latitude, longitude, altitude_msl, when):
    """The caller supplies site elevation MSL explicitly, independently of GPS ellipsoid altitude."""
    instant = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("Weather time must include a time zone")
    instant = instant.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if (instant - now).total_seconds() > 15 * 86400:
        raise ValueError("Requested date is beyond the forecast window; use manual wind")
    levels = [1000, 925, 850, 700, 500, 300, 200, 100]
    fields = [
        f"{name}_{level}hPa"
        for level in levels
        for name in ("wind_speed", "wind_direction", "geopotential_height")
    ]
    endpoint = "https://api.open-meteo.com/v1/forecast"
    if (now - instant).total_seconds() > 5 * 86400:
        endpoint = "https://historical-forecast-api.open-meteo.com/v1/forecast"
    params = dict(
        latitude=latitude,
        longitude=longitude,
        start_date=instant.date().isoformat(),
        end_date=instant.date().isoformat(),
        hourly=",".join(fields),
        wind_speed_unit="ms",
        timezone="UTC",
    )
    request = urllib.request.Request(
        endpoint + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "RocketGNCMonitor/0.1"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError("Weather response too large")
    payload = json.loads(raw)
    hourly = payload.get("hourly", {})
    timestamps = [datetime.fromisoformat(v).replace(tzinfo=timezone.utc) for v in hourly.get("time", [])]
    if not timestamps:
        raise ValueError("Provider returned no data for the requested date")
    index = min(range(len(timestamps)), key=lambda i: abs((timestamps[i] - instant).total_seconds()))
    if abs((timestamps[index] - instant).total_seconds()) > 3600:
        raise ValueError("No weather hour within one hour of the selected time")
    layers = []
    for level in levels:
        values = [
            hourly.get(f"{name}_{level}hPa", [None] * len(timestamps))[index]
            for name in ("wind_speed", "wind_direction", "geopotential_height")
        ]
        if any(v is None or not math.isfinite(v) for v in values):
            continue
        speed, direction, geopotential = values
        # Geopotential -> geometric height; then explicit MSL site subtraction.
        height = 6356766 * geopotential / (6356766 - geopotential) - altitude_msl
        if height < 0:
            continue
        east, north = wind_from(speed, direction)
        layers.append(dict(height=round(height, 2), east=east, north=north))
    layers.sort(key=lambda layer: layer["height"])
    validate_wind(layers)
    return dict(
        layers=layers,
        raw=payload,
        source="Open-Meteo · " + timestamps[index].isoformat(),
        request=params,
        endpoint=endpoint,
        retrieved_utc=now.isoformat(),
    )


def bundled_motor_files():
    directory = runtime_root().parent / "resources" / "motors"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    files = []
    for name, metadata in manifest.items():
        path = directory / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]:
            raise ValueError(f"Bundled motor curve failed its integrity check: {name}")
        files.append(path)
    return files


class SimulationJob:
    def __init__(self, mission, directory, settings=None):
        self.mission, self.directory = mission, Path(directory)
        self.settings = (settings or AppSettings()).validate()
        self.process = None
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def run(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        root = runtime_root()
        java = root / "java" / "bin" / ("java.exe" if sys.platform == "win32" else "java")
        if not java.exists():
            raise ValueError("Bundled Java runtime missing. Build with make runtime and make bridge.")
        jars = [self.settings.engine_path, root / "rocket-bridge.jar"]
        if not all(p.exists() for p in jars):
            raise ValueError(
                "OpenRocket engine/bridge missing. Check the JAR in Settings or rebuild the bundled engine."
            )
        validate_engine_jar(jars[0])
        model = Path(self.mission.model)
        if not model.is_file():
            raise ValueError("Select a valid .ork model")
        if model.stat().st_size > 40_000_000:
            raise ValueError("Model exceeds 40 MB")
        if zipfile.is_zipfile(model):
            with zipfile.ZipFile(model) as archive:
                if sum(info.file_size for info in archive.infolist()) > 100_000_000:
                    raise ValueError("Expanded model exceeds 100 MB")
        # Copy inputs, preserving exact job provenance independently of the original model.
        import shutil

        shutil.copyfile(model, self.directory / "model.ork")
        motors = []
        motor_hashes = {}
        bundled = bundled_motor_files()
        for i, filename in enumerate([*bundled, *self.mission.motor_files]):
            source = Path(filename)
            if (
                not source.is_file()
                or source.suffix.lower() not in {".eng", ".rse"}
                or source.stat().st_size > 10_000_000
            ):
                raise ValueError("Motor resources must be .eng/.rse files of at most 10 MB")
            destination = self.directory / f"motor_{i}{source.suffix.lower()}"
            shutil.copyfile(source, destination)
            motors.append(str(destination.resolve()))
            motor_hashes[destination.name] = hashlib.sha256(destination.read_bytes()).hexdigest()
        request = dict(
            schema_version=1,
            model=str((self.directory / "model.ork").resolve()),
            motor_files=motors,
            bundled_motor_count=len(bundled),
            simulation_index=self.mission.simulation_index,
            latitude=self.mission.latitude,
            longitude=self.mission.longitude,
            altitude=self.mission.altitude_msl,
            origin_ellipsoid_altitude=self.mission.altitude,
            rail_length=self.mission.rail_length,
            rail_tilt=self.mission.rail_tilt,
            rail_heading=self.mission.rail_heading,
            seed=self.mission.seed,
            wind=self.mission.wind,
            output=str(self.directory.resolve()),
            nominal=True,
            inertia_override=False,
        )
        write_json(self.directory / "request.json", request)
        command = [
            str(java),
            "-Djava.awt.headless=true",
            "-Dopenrocket.bypass.presets=true",
            "-Djava.util.prefs.PreferencesFactory=RocketBridge$MemoryPreferencesFactory",
            "-Djava.util.prefs.userRoot=" + str(self.directory / "preferences"),
            "-cp",
            (";" if sys.platform == "win32" else ":").join(map(str, jars)),
            "RocketBridge",
            str((self.directory / "request.json").resolve()),
        ]
        # A repeated job directory must not report a previous run's error.
        error_path = self.directory / "error.json"
        error_path.unlink(missing_ok=True)
        with (self.directory / "worker.log").open("wb") as log:
            self.process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **popen_options())
            started = time.monotonic()
            while self.process.poll() is None:
                if self.cancelled.wait(0.1) or time.monotonic() - started > self.settings.simulation_timeout:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
                    raise ValueError(
                        f"Simulation cancelled or exceeded {self.settings.simulation_timeout} seconds"
                    )
                if log.tell() > 20_000_000:
                    self.process.kill()
                    self.process.wait()
                    raise ValueError("Simulation log exceeded size limit")
        if self.cancelled.is_set():
            raise ValueError("Simulation cancelled")
        if self.process.returncode:
            if error_path.is_file():
                try:
                    error = json.loads(error_path.read_text(encoding="utf-8"))
                    message = error.get("message")
                except (ValueError, OSError):
                    message = None
                if isinstance(message, str) and message.strip():
                    raise ValueError("OpenRocket: " + message[:2000])
            tail = (self.directory / "worker.log").read_text(errors="replace")[-1200:]
            raise ValueError("OpenRocket failed: " + tail)
        result = Trajectory.load(self.directory / "trajectory.csv")
        result.manifest.update(
            engine_path=str(jars[0]),
            simulation_timeout=self.settings.simulation_timeout,
            engine_sha256=hashlib.sha256(jars[0].read_bytes()).hexdigest(),
            model_sha256=hashlib.sha256((self.directory / "model.ork").read_bytes()).hexdigest(),
            motor_sha256=motor_hashes,
            request=request,
            job_directory=str(self.directory),
        )
        result.save(self.directory / "trajectory.csv")
        return result
