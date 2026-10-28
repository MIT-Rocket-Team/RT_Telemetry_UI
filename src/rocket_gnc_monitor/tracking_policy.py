"""Shared receiver-only pointing policy. No trajectory, Qt, serial or host clock access."""
from .domain import finite, pointing


def tracking_target(sample, *, now, freshness, origin, policy="legacy-ui.v1",
                    height_reference=None, accepted_source="LIVE"):
    if sample is None or now - sample.received > freshness:
        raise ValueError("Rocket position is stale")
    if sample.source != accepted_source:
        raise ValueError("Tracking needs a live source")
    if origin is None:
        raise ValueError("Set the ground station GPS with Send to AntPtr first")
    if not sample.gps_fix or not all(finite(v) for v in (sample.latitude, sample.longitude)):
        raise ValueError("Rocket GPS position is not available")
    if policy == "legacy-ui.v1":
        altitude = sample.altitude
        # The physical UI freezes these coordinates at five decimals on Send to AntPtr.
        mount = (origin[0], origin[1], 0.0)
    elif policy == "geodetic.v1":
        ref = height_reference or {}
        altitude = sample.gps_altitude
        convention = ref.get("convention", "unknown")
        if convention == "relative-msl" and finite(ref.get("baselineMslM")):
            altitude = None if altitude is None else altitude + ref["baselineMslM"]
            convention = "MSL"
        if convention == "MSL" and finite(ref.get("geoidSeparationM")):
            altitude = None if altitude is None else altitude + ref["geoidSeparationM"]
        elif convention != "ellipsoidal":
            raise ValueError("Telemetry height reference is unknown")
        mount = origin
    else:
        raise ValueError("Unknown receiver tracking policy")
    if not finite(altitude):
        raise ValueError("Rocket height is not available")
    return pointing((sample.latitude, sample.longitude, altitude), mount)


class TrackingPolicy:
    """5 Hz command admission; stale observations latch hold until explicit restart."""
    PERIOD_US = 200_000
    EXPIRY_US = 500_000

    def __init__(self, *, origin, policy="legacy-ui.v1", freshness=2.0):
        self.origin, self.policy, self.freshness = origin, policy, freshness
        self.latest = None
        self.height_reference = None
        self.connected = self.polling = self.tracking = False
        self.next_tick = -1_000_000
        self.reason = "Tracking off"

    def target(self, time_us):
        return tracking_target(self.latest, now=time_us / 1e6, freshness=self.freshness,
                               origin=self.origin, policy=self.policy,
                               height_reference=self.height_reference, accepted_source="SIMULATION")

    def start(self, time_us):
        if not self.connected or not self.polling:
            raise ValueError("Connect the pointer and start polling first")
        self.target(time_us)
        self.tracking, self.reason = True, "Tracking"
        self.next_tick = time_us

    def hold(self, reason):
        self.tracking, self.reason = False, reason

    def advance(self, time_us):
        if not self.tracking or time_us < self.next_tick:
            return None
        self.next_tick = time_us + self.PERIOD_US
        try:
            return self.target(time_us)
        except ValueError as exc:
            self.hold(str(exc))
            return None
