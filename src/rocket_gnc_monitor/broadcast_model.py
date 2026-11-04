"""Immutable presentation contracts. Nothing here can command flight hardware."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .domain import finite
from .network.observation import observation_to_sample
from .rocket_pose import telemetry_pose

EVENTS = ("LIFTOFF", "BURNOUT", "APOGEE", "DROGUE", "MAIN", "LANDING")
SCENES = ("Standby", "Prelaunch", "Flight", "Recovery", "Replay", "End card")


@dataclass(frozen=True)
class BroadcastScope:
    mission: str | None
    family: str = "balius"
    vehicle: str = "balius"
    mode: str = "live"
    epoch: str = ""


@dataclass(frozen=True)
class CameraKey:
    mission: str
    vehicle: str
    mode: str
    node: str
    boot: str
    station: str
    stream: str
    epoch: str
    rendition: str = "main"


@dataclass(frozen=True)
class CameraSource:
    logical: str
    label: str
    key: CameraKey
    station: str
    channel: str
    available: bool = True
    reason: str = ""


@dataclass(frozen=True)
class BroadcastFrame:
    key: CameraKey
    pixels: bytes
    arrival: float
    width: int = 960
    height: int = 540


@dataclass(frozen=True)
class BroadcastTelemetry:
    observation_id: str
    node: str
    boot: str
    connection: str
    station: str
    vehicle: str
    arrival: float
    age_at_arrival: float | None
    timing_basis: str
    altitude: float | None
    datum: str
    vertical_speed: float | None
    state: str
    state_schema: str
    pose: Any = None
    mission_elapsed: float | None = None

    @classmethod
    def from_event(cls, event, scope, *, datum_override="unknown", vertical_contract=False):
        info, payload = event["peer"], event["payload"]
        vehicle = payload.get("vehicle", {}).get("id", "unknown")
        if (not scope.mission or scope.vehicle == "unknown" or vehicle != scope.vehicle
                or info.get("mission_id") != scope.mission
                or info.get("vehicle_mode") != scope.family or info.get("source_mode") != scope.mode
                or info.get("role") != "telem" or info.get("station") not in {"gs1", "gs2", "gs3", "gs4"}):
            return None
        arrival = event.get("received_local_mono")
        if not finite(arrival):
            return None
        values = payload["values"]
        baro = values.get("baro_altitude") or {}
        datum = {"agl_launch": "above_launch", "msl": "msl", "wgs84_ellipsoid": "ellipsoid"}.get(
            baro.get("datum"), datum_override)
        altitude = baro.get("value_m") if datum != "unknown" and finite(baro.get("value_m")) else None
        # The existing schema's ENU field is null. Only an explicitly qualified
        # native scalar adapter may supply vertical speed; never guess a schema.
        native = values.get("native") or {}
        speed = native.get("velocity") if vertical_contract and values.get("native_schema") == "rocket-telemetry.sample/1" else None
        speed = speed if finite(speed) else None
        age = event.get("source_age_ms")
        age = max(0, age / 1000) if finite(age) else None
        sample = observation_to_sample(payload, received_monotonic=arrival,
                                       source_age_ms=(age or 0) * 1000, source_label=info["station"].upper())
        sample.details["network_source_mode"] = scope.mode
        sample.details["network_station"] = info["station"]
        state = values.get("state") or {}
        name = state.get("name") if isinstance(state.get("name"), str) else "UNKNOWN"
        return cls(payload["observation_id"], info["node_id"], info["boot_id"],
                   payload["receiver"]["connection_id"], info["station"], vehicle, arrival, age,
                   event.get("timing_basis", "unknown"), altitude, datum, speed, name,
                   state.get("schema", "provisional"), telemetry_pose(sample, fresh=True, state_name=name))


@dataclass(frozen=True)
class BroadcastSelection:
    scene: str = "Standby"
    main: str | None = None
    inset: str | None = None  # None, "orientation", or a logical camera.
    title: str = "MIT ROCKET TEAM"
    caption: str = ""

    @property
    def cameras(self):
        if self.scene in {"Standby", "End card"}:
            return ()
        return tuple(dict.fromkeys(x for x in (self.main, self.inset) if x and x != "orientation"))


@dataclass(frozen=True)
class FrameView:
    mission: str = "MIT ROCKET TEAM"
    mode: str = "LIVE SOURCE"
    camera_label: str = "NO CAMERA SELECTED"
    telemetry_source: str = "UNAVAILABLE"
    altitude_m: float | None = None
    altitude_datum: str = "unknown"
    vertical_speed_m_s: float | None = None
    vertical_speed_valid: bool = False
    flight_state: str = "UNAVAILABLE"
    mission_elapsed_s: float | None = None
    clock_state: str = "unavailable"
    telemetry_status: str = "unavailable"
    telemetry_age_s: float | None = None
    completed_events: tuple[str, ...] = ()
    uncertain_events: tuple[str, ...] = ()
    event_labels: tuple[str, ...] = EVENTS
    camera_notice: str = ""
    telemetry_notice: str = ""
    timing_label: str = "SYNC UNCALIBRATED"
    scene: str = "Standby"
    caption: str = ""
    paused: bool = False


@dataclass(frozen=True)
class BroadcastSnapshot:
    output_time: float
    program_time: float
    view: FrameView
    main: BroadcastFrame | None = None
    inset: BroadcastFrame | None = None
    pose: Any = None
    ready: bool = False
    reason: str = ""

