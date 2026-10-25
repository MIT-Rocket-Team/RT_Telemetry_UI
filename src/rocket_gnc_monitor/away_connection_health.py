"""Display health for four away stations, supplied by a future connection adapter.

No network probes or wire protocol are implemented here. Link reports expire
after a configurable three seconds by default; that is an internal freshness
bound pending the station protocol, not a measured LTU-XR timeout.

The rocket bar's 60 ms reference follows the GS3 log's 59.522 ms median receive
interval (67.739 ms at the 95th percentile). This calibrates the bar to the
recorded cadence, not to measured packet latency. The ping reference of 10 ms
is provisional because those logs contain no LTU-XR round-trip measurements.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable, Literal


AWAY_STATIONS = ("away1", "away2", "away3", "away4")


@dataclass(frozen=True)
class AwayConnectionView:
    online: bool
    status: Literal["offline", "good", "fair", "bad"]
    ping_ms: float | None
    sample_age_ms: float | None
    ping_quality: float
    rocket_quality: float


@dataclass
class _Observation:
    online: bool = False
    link_received: float | None = None
    ping_ms: float | None = None
    last_sample_time: float | None = None


def _number(value, name, *, optional=False, positive=False):
    if value is None and optional:
        return None
    try:
        valid = (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            and (value > 0 if positive else value >= 0)
        )
    except OverflowError:
        valid = False
    if not valid:
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError(f"{name} must be a finite {qualifier} number")
    return float(value)


class AwayConnectionHealth:
    """Store independent link and rocket observations on a monotonic clock.

    ``observe_rocket`` takes the receiver's latest sample age in milliseconds.
    Its age then increases locally, including while no new reports arrive.
    Updating the link or ping never makes an old rocket sample fresh again.
    No synchronized wall clock or vehicle flight-time counter is used.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        ping_full_scale_ms=10,
        rocket_full_scale_ms=60,
        link_timeout_s=3,
    ):
        if not callable(clock):
            raise ValueError("clock must be callable")
        self.clock = clock
        self.ping_full_scale_ms = _number(ping_full_scale_ms, "Ping reference", positive=True)
        self.rocket_full_scale_ms = _number(rocket_full_scale_ms, "Rocket reference", positive=True)
        self.link_timeout_s = _number(link_timeout_s, "Link timeout", positive=True)
        self._stations = {station: _Observation() for station in AWAY_STATIONS}

    def _observation(self, station):
        if not isinstance(station, str) or station not in self._stations:
            raise ValueError("Station must be away1, away2, away3 or away4")
        return self._stations[station]

    def observe_link(self, station, *, online: bool, ping_ms: float | None = None):
        observation = self._observation(station)
        if not isinstance(online, bool):
            raise ValueError("online must be a boolean")
        ping_ms = _number(ping_ms, "Ping", optional=True)
        observation.online = online
        observation.link_received = self.clock()
        observation.ping_ms = ping_ms if online else None

    def observe_rocket(self, station, *, sample_age_ms: float | None):
        observation = self._observation(station)
        sample_age_ms = _number(sample_age_ms, "Sample age", optional=True)
        observation.last_sample_time = (
            self.clock() - sample_age_ms / 1000 if sample_age_ms is not None else None
        )

    @staticmethod
    def _quality(value, reference):
        return reference / max(value, reference) if value is not None else 0.0

    def view(self, station) -> AwayConnectionView:
        observation = self._observation(station)
        now = self.clock()
        online = (
            observation.online
            and observation.link_received is not None
            and now - observation.link_received < self.link_timeout_s
        )
        if not online:
            return AwayConnectionView(False, "offline", None, None, 0.0, 0.0)
        age_ms = (
            max(0.0, (now - observation.last_sample_time) * 1000)
            if observation.last_sample_time is not None else None
        )
        status = (
            "bad" if age_ms is None or age_ms >= 1000
            else "fair" if age_ms >= 250 else "good"
        )
        return AwayConnectionView(
            True,
            status,
            observation.ping_ms,
            age_ms,
            self._quality(observation.ping_ms, self.ping_full_scale_ms),
            self._quality(age_ms, self.rocket_full_scale_ms),
        )

    def clear(self, station):
        self._observation(station)
        self._stations[station] = _Observation()
