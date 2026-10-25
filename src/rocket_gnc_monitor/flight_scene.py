"""Read-only OpenRocket flight playback: ENU positions, body +Z attitude and events."""

from dataclasses import dataclass
import math
import time
import numpy as np


def slerp(a, b, fraction):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if not np.isfinite([a, b]).all() or min(np.linalg.norm(a), np.linalg.norm(b)) < 1e-12:
        return None
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    dot = float(a @ b)
    if dot < 0:
        b, dot = -b, -dot
    dot = min(1.0, dot)
    if dot > 0.9995:
        result = a + fraction * (b - a)
    else:
        theta = math.acos(dot)
        result = (math.sin((1 - fraction) * theta) * a + math.sin(fraction * theta) * b) / math.sin(theta)
    return result / np.linalg.norm(result)


def quaternion_matrix(q):
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def path_rotation(velocity):
    z = velocity / max(np.linalg.norm(velocity), 1e-12)
    if np.linalg.norm(z) < 0.5:
        return np.eye(3)
    helper = np.array([1, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1, 0])
    y = np.cross(z, helper)
    y /= np.linalg.norm(y)
    return np.column_stack((np.cross(y, z), y, z))


class Playback:
    def __init__(self, start, end, clock=time.monotonic):
        self.start, self.end, self.clock = start, end, clock
        self.anchor, self.stamp, self.speed, self.playing = start, clock(), 1.0, False

    def time(self):
        value = self.anchor + ((self.clock() - self.stamp) * self.speed if self.playing else 0)
        if value >= self.end:
            self.anchor, self.playing = self.end, False
        return max(self.start, min(self.end, value))

    def seek(self, value):
        if not math.isfinite(value):
            raise ValueError("Playback time must be finite")
        self.anchor, self.stamp = max(self.start, min(self.end, value)), self.clock()

    def play(self, enabled):
        now = self.time()
        self.playing = enabled
        self.seek(now)

    def set_speed(self, speed):
        if not math.isfinite(speed) or not 0.01 <= speed <= 20:
            raise ValueError("Playback speed must be between 0.01 and 20")
        now = self.time()
        self.speed = speed
        self.seek(now)


@dataclass
class FlightFrame:
    time: float
    position: np.ndarray
    velocity: np.ndarray
    rotation: np.ndarray
    attitude: str
    powered: bool
    recovery: bool
    inflation: float
    state: str


class FlightScene:
    def __init__(self, trajectory):
        self.trajectory = trajectory
        self.points = trajectory.points
        self.events = sorted(trajectory.manifest.get("flight_events", []), key=lambda e: e["time"])
        self.start, self.end = self.points[[0, -1], 0]
        self.end = min(
            self.end,
            next(
                (e["time"] for e in self.events if e["type"] == "GROUND_HIT" and e["time"] >= self.start),
                self.end,
            ),
        )
        self.low = np.minimum(self.points[:, 1:].min(axis=0), 0)
        self.high = np.maximum(self.points[:, 1:].max(axis=0), 0)
        self.center = (self.low + self.high) / 2
        self.span = max(1.0, float((self.high - self.low).max()))
        launch = next((e["time"] for e in self.events if e["type"] == "LAUNCH"), self.start)
        ignitions = {}
        self.burns = []
        for event in self.events:
            source = event.get("source_id") or event.get("source", "")
            if event["type"] == "IGNITION":
                ignitions.setdefault(source, event["time"])
            elif event["type"] == "BURNOUT":
                begin = ignitions.pop(source, launch)
                if event["time"] > begin:
                    self.burns.append((begin, event["time"]))
        self.burns.extend((begin, math.inf) for begin in ignitions.values())
        self.deployments = [e for e in self.events if e["type"] == "RECOVERY_DEVICE_DEPLOYMENT"]
        self.velocities = np.gradient(self.points[:, 1:], self.points[:, 0], axis=0)

    def frame(self, requested):
        if not math.isfinite(requested):
            raise ValueError("Frame time must be finite")
        t = max(self.start, min(self.end, requested))
        j = min(len(self.points) - 1, int(np.searchsorted(self.points[:, 0], t)))
        i = max(0, j - 1)
        if self.points[j, 0] == t:
            i = j
        dt = self.points[j, 0] - self.points[i, 0]
        f = (t - self.points[i, 0]) / dt if dt else 0.0
        position = self.points[i, 1:] * (1 - f) + self.points[j, 1:] * f
        velocity = self.velocities[i] * (1 - f) + self.velocities[j] * f
        q = None
        undersampled = False
        motion = self.trajectory.motion
        if motion is not None:
            if np.isfinite(motion[[i, j], 4:7]).all():
                velocity = motion[i, 4:7] * (1 - f) + motion[j, 4:7] * f
            rates = motion[[i, j], 7:]
            undersampled = (
                np.isfinite(rates).all() and max(np.linalg.norm(rates, axis=1)) * dt >= math.pi and 0 < f < 1
            )
            if not undersampled:
                q = slerp(motion[i, :4], motion[j, :4], f)
        prior = [e for e in self.events if e["time"] <= t]
        recovery = any(e["type"] == "RECOVERY_DEVICE_DEPLOYMENT" for e in prior)
        held = recovery or any(e["type"] == "TUMBLE" for e in prior)
        powered = any(start <= t < end for start, end in self.burns)
        landed = any(e["type"] == "GROUND_HIT" for e in prior)
        deployment = next((e["time"] for e in self.deployments if e["time"] <= t), None)
        inflation = min(1.0, max(0.05, (t - deployment) / 0.7)) if deployment is not None else 0.0
        attitude = (
            ("Simulation attitude · held after recovery/tumble" if held else "Simulation attitude")
            if q is not None
            else (
                "Path-aligned illustration · attitude undersampled"
                if undersampled
                else "Path-aligned illustration · attitude unavailable"
            )
        )
        state = (
            "LANDED"
            if landed
            else "PARACHUTE DEPLOYED"
            if recovery
            else "MOTOR BURNING"
            if powered
            else "COAST / PAD"
        )
        if not self.events:
            state = "FLIGHT EVENTS UNAVAILABLE"
        return FlightFrame(
            t,
            position,
            velocity,
            quaternion_matrix(q) if q is not None else path_rotation(velocity),
            attitude,
            powered,
            recovery,
            inflation,
            state,
        )
