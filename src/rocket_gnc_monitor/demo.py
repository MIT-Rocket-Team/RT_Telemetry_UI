"""Offline playback of the three original Zephyrus test-flight receiver logs."""

from __future__ import annotations
from bisect import bisect_right
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import sys
from .domain import finite, to_enu
from .legacy_sample import decode_row


class DemoFlight:
    def __init__(self, station="GS2", resource_root=None):
        root = resource_root or (
            Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
        )
        directory = Path(root) / "resources" / "demo"
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
            raise ValueError("Demo recording row count does not match its manifest")
        self.start_utc = float(self.rows[0]["timestamp"])
        self.times = [float(row["timestamp"]) - self.start_utc for row in self.rows]
        if any(not finite(t) for t in self.times) or any(b < a for a, b in zip(self.times, self.times[1:])):
            raise ValueError("Demo recording has invalid receive timestamps")
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
        sample = decode_row(self.rows[index], source="DEMO", received=received)
        sample.details.update(
            demo_station=self.station,
            demo_row=index + 2,
            demo_source=self.metadata["source_filename"],
            demo_source_sha256=self.metadata["sha256"],
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
