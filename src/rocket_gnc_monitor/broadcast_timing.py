"""Bounded presentation histories and explicit clock/event evidence."""

from __future__ import annotations

import itertools
import math
import threading
from collections import deque
from dataclasses import dataclass, field

from .domain import finite

FPS = 30
GUARD = 1 / FPS
FRAME_BYTES = 960 * 540 * 3
RESERVED_BYTES = 4 * 1920 * 1080 * 4 + 4 * FRAME_BYTES + 24 * 1024 * 1024  # images + telemetry


@dataclass(frozen=True)
class Calibration:
    delay: float = 0.0
    jitter: float = 0.0
    method: str = ""
    measured_at: str = ""
    residual_ms: float | None = None

    def validate(self):
        if not all(finite(x) and 0 <= x <= 10 for x in (self.delay, self.jitter)):
            raise ValueError("Path delay and jitter must be between 0 and 10 seconds")
        if not self.method.strip() or not self.measured_at.strip():
            raise ValueError("Record the calibration method and measurement date")
        if not finite(self.residual_ms) or self.residual_ms < 0:
            raise ValueError("Record the measured residual error in milliseconds")


@dataclass(frozen=True)
class TimingConfig:
    delay: float = 0.5
    calibrated: bool = False
    paths: dict = field(default_factory=dict)
    budget: int = 256 * 1024 * 1024

    def validate(self, cameras=()):
        if not finite(self.delay) or not GUARD <= self.delay <= 10:
            raise ValueError("Broadcast delay must be between 0.034 and 10 seconds")
        if type(self.budget) is not int or not 64 * 1024 * 1024 <= self.budget <= 256 * 1024 * 1024:
            raise ValueError("Broadcast memory budget must be between 64 and 256 MiB")
        for path in self.paths.values():
            path.validate()
        if self.calibrated and any(
            self.delay + 1e-9 < p.delay + p.jitter + GUARD for p in self.paths.values()
        ):
            raise ValueError("Broadcast delay must cover every calibrated path, jitter and one output frame")
        if self.required_bytes(cameras) > self.budget:
            raise ValueError("These cameras and delay exceed the broadcast memory budget")

    def path_delay(self, name):
        if not self.calibrated:
            return 0.0
        path = self.paths.get(name)
        return path.delay if path else None

    def required_bytes(self, cameras):
        return RESERVED_BYTES + sum(
            FRAME_BYTES * (math.ceil(self.retention(name) * FPS) + 2) for name in set(cameras)
        )

    def retention(self, name):
        return max(
            1.15,
            max(0, self.delay - (self.path_delay(name) or 0))
            + (self.paths[name].jitter if name in self.paths else 0)
            + GUARD
            + 0.1,
        )


class FrameHistory:
    """Receiver callback never waits. Immutable bytes are shared, not recopied.

    Memory limit reserves room for all retained composition/camera image copies.
    Main-thread snapshots hold frames only until the next render completes.
    """

    def __init__(self, budget=256 * 1024 * 1024, retention=1.0):
        self.limit = budget - RESERVED_BYTES
        self.retention = retention
        self.path_retention = {}
        self.frames = {}
        self.bytes = self.dropped = self.offered = 0
        self.lock = threading.Lock()

    def offer(self, frame):
        if not self.lock.acquire(blocking=False):
            self.dropped += 1
            return False
        try:
            self.offered += 1
            if not isinstance(frame.pixels, bytes) or len(frame.pixels) != frame.width * frame.height * 3:
                self.dropped += 1
                return False
            queue = self.frames.setdefault(frame.key, deque())
            if queue and frame.arrival <= queue[-1].arrival:
                self.dropped += 1
                return False
            for key, history in self.frames.items():
                cutoff = frame.arrival - self.path_retention.get(key, self.retention)
                while len(history) > 1 and history[1].arrival < cutoff:
                    self.bytes -= len(history.popleft().pixels)
            if self.bytes + len(frame.pixels) > self.limit:
                self.dropped += 1
                return False
            queue.append(frame)
            self.bytes += len(frame.pixels)
            return True
        finally:
            self.lock.release()

    def at(self, key, program_time, path_delay=0.0):
        if path_delay is None:
            return None
        with self.lock:
            return next(
                (f for f in reversed(self.frames.get(key, ())) if f.arrival - path_delay <= program_time),
                None,
            )

    def latest(self, key):
        with self.lock:
            items = self.frames.get(key)
            return items[-1] if items else None

    def continuous(self, key, now, duration=1.0):
        with self.lock:
            items = [f.arrival for f in self.frames.get(key, ()) if f.arrival >= now - duration - 0.1]
            return bool(
                len(items) > 1
                and now - items[0] >= duration
                and now - items[-1] < 0.5
                and all(b - a < 0.5 for a, b in itertools.pairwise(items))
            )

    def retain(self, keys):
        with self.lock:
            for key in set(self.frames) - set(keys):
                self.bytes -= sum(len(f.pixels) for f in self.frames.pop(key))

    def clear(self):
        self.retain(())


class TelemetryHistory:
    def __init__(self):
        self.histories = {}
        self.identities = set()
        self.order = deque()

    def offer(self, record):
        if record.observation_id in self.identities:
            return False
        self.identities.add(record.observation_id)
        self.order.append(record.observation_id)
        while len(self.order) > 24000:
            self.identities.discard(self.order.popleft())
        if record.node not in self.histories and len(self.histories) >= 8:
            self.histories.pop(next(iter(self.histories)))
        history = self.histories.setdefault(record.node, deque(maxlen=2000))
        if history and (history[-1].boot, history[-1].connection) != (record.boot, record.connection):
            history.clear()
        if history and record.arrival < history[-1].arrival:
            return False
        history.append(record)
        return True

    def at(self, node, program_time, delay):
        if delay is None:
            return None
        return next(
            (r for r in reversed(self.histories.get(node, ())) if r.arrival - delay <= program_time), None
        )

    def clear(self):
        self.histories.clear()
        self.identities.clear()
        self.order.clear()


@dataclass(frozen=True)
class ClockRevision:
    effective: float
    state: str
    anchor: float | None = None
    held: float | None = None
    basis: str = "operator"


class BroadcastClock:
    def __init__(self):
        self.revisions = [ClockRevision(float("-inf"), "unavailable")]

    def revise(self, effective, state, anchor=None, held=None, basis="operator"):
        self.revisions.append(ClockRevision(effective, state, anchor, held, basis))
        self.revisions = self.revisions[-1000:]

    def at(self, program_time):
        revision = next(
            (r for r in reversed(self.revisions) if r.effective <= program_time),
            ClockRevision(float("-inf"), "unavailable"),
        )
        if revision.state == "hold":
            return revision.held, "hold", revision.basis
        if revision.anchor is None:
            return None, "unavailable", revision.basis
        elapsed = program_time - revision.anchor
        state = "awaiting_launch" if revision.state == "countdown" and elapsed >= 0 else revision.state
        return elapsed, state, revision.basis

    def hold(self, at):
        seconds, state, basis = self.at(at)
        if state not in {"countdown", "awaiting_launch"}:
            raise ValueError("Only a planned countdown can be held")
        self.revise(at, "hold", held=seconds, basis=basis)

    def resume(self, at):
        seconds, state, basis = self.at(at)
        if state != "hold" or seconds is None:
            raise ValueError("No held countdown")
        self.revise(at, "countdown", anchor=at - seconds, basis=basis)


class EventLedger:
    def __init__(self):
        self.events = {}

    def mark(self, event, stamp, evidence, *, correction=False, uncertain=False):
        old = self.events.get(event)
        if old and not correction:
            # Repeated received states confirm the milestone, not its exact time.
            return False
        self.events[event] = (stamp, evidence, bool(uncertain or (old and correction)))
        return True

    def at(self, stamp):
        completed = tuple(k for k, (t, _, _) in self.events.items() if t <= stamp)
        corrected = tuple(k for k in completed if self.events[k][2])
        return completed, corrected


def next_deadline(start, finished):
    return start + (math.floor((finished - start) * FPS) + 1) / FPS
