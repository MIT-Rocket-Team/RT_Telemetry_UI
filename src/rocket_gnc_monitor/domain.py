"""Qt-independent contracts, coordinate transformations and deterministic demo data."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import time


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass
class Sample:
    t: float
    sequence: int
    source: str
    received: float = field(default_factory=time.monotonic)
    utc: float = field(default_factory=time.time)
    altitude: float | None = None
    velocity: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    gps_altitude: float | None = None
    gps_fix: int = 0
    attitude: list[float] | None = None
    rates: list[float] | None = None
    acceleration: list[float] | None = None
    enu: list[float] | None = None
    battery: float | None = None
    rssi: float | None = None
    phase: str = "Unknown"
    actuators: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        allowed = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class Mission:
    name: str = "Untitled mission"
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    altitude_msl: float = 0.0
    site_configured: bool = False
    launch_location_format: str = "latlon"
    launch_location_code: str = ""
    launch_site_name: str = ""
    legacy_altitude: str = "unknown"
    canard_count: int = 4
    pointer_latitude: float = 0.0
    pointer_longitude: float = 0.0
    pointer_altitude: float = 0.0
    pointer_site_configured: bool = False
    pointer_location_format: str = "latlon"
    pointer_location_code: str = ""
    pointer_site_name: str = ""
    pointer_launch_heading: float = 0.0
    pointer_launch_distance: float = 0.0
    pointer_height_difference: float = 0.0
    pointer_calibrated: bool = False
    pointer_az_offset: float = 0.0
    pointer_el_offset: float = 0.0
    pointer_el_min: float = 0.0
    pointer_el_max: float = 80.0
    pointer_az_min: float = 0.0
    pointer_az_max: float = 359.0
    pointer_full_rotation: bool = False
    freshness: float = 2.0
    video_offset: float = 0.0
    wind: list[dict] = field(default_factory=lambda: [dict(height=0.0, east=0.0, north=0.0)])
    wind_source: str = "Manual · calm"
    weather_raw: dict = field(default_factory=dict)
    model: str = ""
    motor_files: list[str] = field(default_factory=list)
    simulation_index: int = 0
    rail_length: float = 2.0
    rail_tilt: float = 0.0
    rail_heading: float = 0.0
    seed: int = 42
    low_battery: float = 9.0

    def validate(self):
        if type(self.pointer_site_configured) is not bool:
            raise ValueError("Antenna location established must be true or false")
        if self.pointer_location_format not in {"latlon", "mgrs", "relative"}:
            raise ValueError("Unsupported antenna location format")
        if not isinstance(self.pointer_location_code, str) or len(self.pointer_location_code) > 80:
            raise ValueError("Invalid antenna location code")
        if not isinstance(self.pointer_site_name, str) or len(self.pointer_site_name) > 80:
            raise ValueError("Invalid antenna site name")
        if not all(finite(v) for v in (
            self.pointer_launch_heading, self.pointer_launch_distance, self.pointer_height_difference
        )):
            raise ValueError("Relative antenna coordinates must be finite")
        if not 0 <= self.pointer_launch_heading <= 360 or not 0 <= self.pointer_launch_distance <= 100_000:
            raise ValueError("Invalid relative antenna heading or horizontal distance")
        if not isinstance(self.launch_site_name, str) or len(self.launch_site_name) > 80:
            raise ValueError("Invalid launch site name")
        if self.launch_location_format not in {"latlon", "mgrs", "pluscode"}:
            raise ValueError("Unsupported launch location format")
        if not isinstance(self.launch_location_code, str) or len(self.launch_location_code) > 80:
            raise ValueError("Invalid launch location code")
        for name in ("latitude", "pointer_latitude"):
            if not finite(getattr(self, name)) or not -90 <= getattr(self, name) <= 90:
                raise ValueError(f"Invalid {name}")
        for name in ("longitude", "pointer_longitude"):
            if not finite(getattr(self, name)) or not -180 <= getattr(self, name) <= 180:
                raise ValueError(f"Invalid {name}")
        numeric = (
            "altitude",
            "altitude_msl",
            "pointer_altitude",
            "pointer_az_offset",
            "pointer_el_offset",
            "pointer_el_min",
            "pointer_el_max",
            "pointer_az_min",
            "pointer_az_max",
            "freshness",
            "video_offset",
            "rail_length",
            "rail_tilt",
            "rail_heading",
            "low_battery",
        )
        if not all(finite(getattr(self, n)) for n in numeric):
            raise ValueError("Mission numeric fields must be finite")
        if (
            type(self.canard_count) is not int
            or not 0 <= self.canard_count <= 16
            or not 0.1 <= self.freshness <= 60
        ):
            raise ValueError("Invalid canard count or freshness limit")
        if (
            type(self.seed) is not int
            or not 0 <= self.seed <= 2147483647
            or type(self.simulation_index) is not int
            or not 0 <= self.simulation_index <= 1000
        ):
            raise ValueError("Invalid simulation index or seed")
        if (
            not isinstance(self.motor_files, list)
            or len(self.motor_files) > 100
            or not all(isinstance(path, str) for path in self.motor_files)
        ):
            raise ValueError("Motor resources must be a list of up to 100 file paths")
        if not -90 <= self.pointer_el_min < self.pointer_el_max <= 90:
            raise ValueError("Elevation limits must be ordered within −90…90°")
        if not 0 <= self.pointer_az_min < self.pointer_az_max <= 360:
            raise ValueError("Azimuth envelope must be ordered within 0…360°")
        if self.legacy_altitude not in {"unknown", "ellipsoid", "gps_agl", "barometric_agl"}:
            raise ValueError("Unsupported altitude convention")
        if not 0 < self.rail_length <= 100 or not 0 <= self.rail_tilt < 90:
            raise ValueError("Invalid rail configuration")
        validate_wind(self.wind)
        if self.pointer_location_format == "relative":
            if not self.site_configured:
                raise ValueError("Establish the launch origin before using a relative antenna location")
            from .location import pointer_from_launch

            point, self.pointer_altitude = pointer_from_launch(
                (self.latitude, self.longitude, self.altitude),
                self.pointer_launch_heading, self.pointer_launch_distance, self.pointer_height_difference,
            )
            self.pointer_latitude, self.pointer_longitude = point.latitude, point.longitude
        return self

    def save(self, path):
        self.validate()
        write_json(path, {"schema_version": 1, **asdict(self)})

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.pop("schema_version", None) != 1:
            raise ValueError("Unsupported mission version")
        # Calibration is an operation-session assertion, never restored from disk.
        data["pointer_calibrated"] = False
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__}).validate()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def ecef(lat, lon, altitude):
    lat, lon = math.radians(lat), math.radians(lon)
    e2 = 6.69437999014e-3
    radius = 6378137.0 / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return (
        (radius + altitude) * math.cos(lat) * math.cos(lon),
        (radius + altitude) * math.cos(lat) * math.sin(lon),
        (radius * (1 - e2) + altitude) * math.sin(lat),
    )


def to_enu(lat, lon, altitude, origin):
    x, y, z = (a - b for a, b in zip(ecef(lat, lon, altitude), ecef(*origin)))
    p, l = map(math.radians, origin[:2])
    return [
        -math.sin(l) * x + math.cos(l) * y,
        -math.sin(p) * math.cos(l) * x - math.sin(p) * math.sin(l) * y + math.cos(p) * z,
        math.cos(p) * math.cos(l) * x + math.cos(p) * math.sin(l) * y + math.sin(p) * z,
    ]


def pointing(target, origin):
    east, north, up = to_enu(*target, origin)
    horizontal = math.hypot(east, north)
    distance = math.hypot(horizontal, up)
    if distance < 10:
        raise ValueError("Target is within 10 m of the mount")
    if horizontal < 1:
        raise ValueError("Target nearly overhead; azimuth is indeterminate")
    return math.degrees(math.atan2(east, north)) % 360, math.degrees(math.atan2(up, horizontal))


def target_position(sample, mission):
    if sample is None or sample.gps_fix < 3 or not mission.site_configured:
        raise ValueError("A configured origin and valid 3D GPS fix are required")
    if mission.legacy_altitude == "ellipsoid":
        altitude = sample.gps_altitude
    elif mission.legacy_altitude == "gps_agl" and finite(sample.gps_altitude):
        altitude = mission.altitude + sample.gps_altitude
    elif mission.legacy_altitude == "barometric_agl" and finite(sample.altitude):
        altitude = mission.altitude + sample.altitude
    else:
        raise ValueError("Set the verified legacy altitude convention in Mission")
    if not all(finite(v) for v in [sample.latitude, sample.longitude, altitude]):
        raise ValueError("Target coordinates are incomplete")
    return sample.latitude, sample.longitude, altitude


def wind_from(speed, direction):
    a = math.radians(direction)
    return -speed * math.sin(a), -speed * math.cos(a)


def validate_wind(layers):
    if not layers or len(layers) > 200:
        raise ValueError("Wind needs 1–200 layers")
    last = -math.inf
    for layer in layers:
        if not all(finite(layer.get(k)) for k in ("height", "east", "north")):
            raise ValueError("Wind layer values must be finite")
        if layer["height"] < 0 or layer["height"] <= last:
            raise ValueError("Wind heights must increase, in metres above launch")
        if math.hypot(layer["east"], layer["north"]) > 150:
            raise ValueError("Wind exceeds 150 m/s")
        last = layer["height"]


def demo_sample(t, sequence, canards=4):
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
        source="DEMO",
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
