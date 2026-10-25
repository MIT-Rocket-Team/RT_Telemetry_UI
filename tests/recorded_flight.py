"""Test-only decoding of the three original receiver logs."""

from __future__ import annotations
from bisect import bisect_right
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
from rocket_gnc_monitor.domain import Sample, finite, to_enu
from rocket_gnc_monitor.legacy_sample import decode_row


class RecordedFlight:
    def __init__(self, station="GS2", resource_root=None):
        directory = Path(resource_root) if resource_root else Path(__file__).parent / "fixtures" / "recorded_flight"
        manifest = json.loads((directory / "manifest.json").read_text())
        if station not in manifest:
            raise ValueError("Choose GS1, GS2 or GS3")
        self.station = station
        self.metadata = manifest[station]
        data = gzip.decompress((directory / self.metadata["file"]).read_bytes())
        if hashlib.sha256(data).hexdigest() != self.metadata["sha256"]:
            raise ValueError(f"The bundled {station} recording failed its integrity check")
        self.rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        if len(self.rows) != self.metadata["rows"]:
            raise ValueError("Recorded flight row count does not match its manifest")
        self.start_utc = float(self.rows[0]["timestamp"])
        self.times = [float(row["timestamp"]) - self.start_utc for row in self.rows]
        if any(not finite(t) for t in self.times) or any(b < a for a, b in zip(self.times, self.times[1:])):
            raise ValueError("Recorded flight has invalid receive timestamps")
        self.duration = self.times[-1]
        self.launch_index = next(i for i, row in enumerate(self.rows) if row["state"] == "state.FLIGHT")
        self.launch_time = self.times[self.launch_index]
        self.flight_zero = float(self.rows[self.launch_index]["flight_time"]) / 1000
        launch = self.rows[self.launch_index]
        self.origin = (float(launch["lat"]), float(launch["lon"]), 0.0)
        self.cue = max(0.0, self.launch_time - 5)

    def index_at(self, elapsed):
        return max(0, bisect_right(self.times, elapsed) - 1)

    def sample(self, index, received=None):
        sample = decode_row(self.rows[index], source="REPLAY", received=received)
        sample.details.update(
            recording_station=self.station,
            recording_row=index + 2,
            recording_source=self.metadata["source_filename"],
            recording_source_sha256=self.metadata["sha256"],
            position_frame="Recorded GPS offsets from launch; reported barometric height",
        )
        # A local display frame, not a surveyed ellipsoid/MSL origin. Preserve every
        # original field even when a corrupt GPS point cannot sensibly be plotted.
        if sample.gps_fix >= 3 and all(
            finite(v) for v in (sample.latitude, sample.longitude, sample.altitude)
        ):
            east, north, _ = to_enu(sample.latitude, sample.longitude, 0.0, self.origin)
            if math.hypot(east, north) <= 20_000:
                sample.enu = [east, north, sample.altitude]
            else:
                sample.details["position_warning"] = "Recorded GPS >20 km from launch; omitted from track"
        else:
            sample.details["position_warning"] = "Recorded 3D GPS unavailable; omitted from track"
        return sample


def sample_fixture(t, sequence, canards=4):
    # Repeatable fictional flight; no physics-validity claim.
    t = min(max(t, 0), 140)
    height = max(0, 1500 * math.sin(math.pi * t / 140))
    velocity = 1500 * math.pi / 140 * math.cos(math.pi * t / 140) if 0 < t < 140 else 0
    east, north = t * 3.8, t * 1.7
    angles = [18 * math.sin(t * 0.24), 4 * math.sin(t * 0.11), 2 * math.cos(t * 0.16)]
    actuators = {
        f"Canard {i + 1}": {
            "demand": 6 * math.sin(t * 1.1 + i),
            "measured": 5.8 * math.sin(t * 1.1 + i - 0.06),
        }
        for i in range(canards)
    }
    actuators.update(
        {
            f"Tab {i + 1}": {
                "demand": 4 * math.sin(t * 0.7 + i),
                "measured": 3.9 * math.sin(t * 0.7 + i - 0.05),
            }
            for i in range(4)
        }
    )
    return Sample(
        t=t,
        sequence=sequence,
        source="LIVE",
        altitude=height,
        velocity=velocity,
        attitude=angles,
        rates=[4.32 * math.cos(t * 0.24), 0.44 * math.cos(t * 0.11), -0.32 * math.sin(t * 0.16)],
        acceleration=[0, 0, 9.8 + 3 * math.sin(t)],
        enu=[east, north, height],
        battery=12.4 - t * 0.007,
        rssi=-64 - 7 * math.sin(t / 20),
        phase="Ascent" if t < 70 else "Descent" if t < 140 else "Landed",
        actuators=actuators,
        details={"attitude_kind": "Synthetic Euler angles", "schema_version": 1},
    )


def video_fixture(directory, duration=8, pattern="testsrc2"):
    """Generate a local video file so tests exercise the ordinary file input."""
    import subprocess
    from rocket_gnc_monitor.media import ffmpeg, popen_options

    path = Path(directory) / "camera-fixture.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"{pattern}=size=960x540:rate=30", "-t", str(duration),
                    "-c:v", "mpeg4", "-q:v", "4", str(path)], check=True, **popen_options())
    return str(path)


def recorded_session(directory, station="GS2"):
    """Write preserved receiver data through the production session exporter."""
    from rocket_gnc_monitor.domain import Mission
    from rocket_gnc_monitor.flight import samples_session

    flight = RecordedFlight(station)
    path = Path(directory) / station
    samples_session(path, Mission(), (flight.sample(i) for i in range(len(flight.rows))),
                    flight.times, "REPLAY", flight.flight_zero, True, {})
    return path, flight
