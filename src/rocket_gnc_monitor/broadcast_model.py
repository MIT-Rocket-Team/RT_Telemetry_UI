"""Immutable presentation contracts. Nothing here can command flight hardware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .domain import finite
from .network.observation import observation_to_sample
from .rocket_pose import telemetry_pose
from .zephyrus_states import ZEPHYRUS_STATE_SCHEMA

EVENTS = ("LIFTOFF", "BURNOUT", "APOGEE", "DROGUE", "MAIN", "LANDING")
SCENES = ("Standby", "Prelaunch", "Flight", "Recovery", "Replay", "End card")


def _openrocket_mission_details(native, state, mode):
    """Recognize the received Mission contract, not just a SIMULATION label."""
    details = native.get("details") or {}
    if not (
        isinstance(details, dict)
        and mode == "simulation"
        and native.get("source") == "SIMULATION"
        and details.get("mission") is True
        and isinstance(details.get("packetId"), str)
        and details["packetId"]
        and state.get("schema") == ZEPHYRUS_STATE_SCHEMA
    ):
        return {}
    return details


def _mission_elapsed(details):
    """MissionTelemetrySource/MissionRun txTimeUs is launch-relative, not uptime."""
    stamp = details.get("txTimeUs")
    if not isinstance(stamp, str):
        return None
    # Bound before conversion, including malformed/untrusted native metadata.
    digits = stamp.removeprefix("-")
    if len(stamp) > 20 or not digits.isascii() or not digits.isdigit():
        return None
    return int(stamp) / 1_000_000


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
        if (
            not scope.mission
            or scope.vehicle == "unknown"
            or vehicle != scope.vehicle
            or info.get("mission_id") != scope.mission
            or info.get("vehicle_mode") != scope.family
            or info.get("source_mode") != scope.mode
            or info.get("role") != "telem"
            or info.get("station") not in {"gs1", "gs2", "gs3", "gs4"}
        ):
            return None
        arrival = event.get("received_local_mono")
        if not finite(arrival):
            return None
        values = payload["values"]
        state = values.get("state") or {}
        native = values.get("native") if values.get("native_schema") == "rocket-telemetry.sample/1" else {}
        native = native if isinstance(native, dict) else {}
        mission = _openrocket_mission_details(native, state, scope.mode)
        mission_elapsed = _mission_elapsed(mission)
        # OpenRocket's standard observation source emits barometer height above
        # launch and signed velocityUp(). Older publishers preserve that contract
        # in native Mission metadata but leave the canonical datum/vector empty.
        # FC-generated samples use a different basis and are not qualified here.
        standard_mission = bool(mission) and state.get("basis") == "flight-events"
        baro = values.get("baro_altitude") or {}
        datum = {"agl_launch": "above_launch", "msl": "msl", "wgs84_ellipsoid": "ellipsoid"}.get(
            baro.get("datum"), "above_launch" if standard_mission else datum_override
        )
        altitude = baro.get("value_m") if datum != "unknown" and finite(baro.get("value_m")) else None
        vector = values.get("velocity_enu_m_s")
        speed = vector[2] if isinstance(vector, list) and len(vector) == 3 else None
        if not finite(speed) and (standard_mission or vertical_contract):
            speed = native.get("velocity")
        speed = speed if finite(speed) else None
        age = event.get("source_age_ms")
        age = max(0, age / 1000) if finite(age) else None
        sample = observation_to_sample(
            payload,
            received_monotonic=arrival,
            source_age_ms=(age or 0) * 1000,
            source_label=info["station"].upper(),
        )
        sample.details["network_source_mode"] = scope.mode
        sample.details["network_station"] = info["station"]
        name = state.get("name") if isinstance(state.get("name"), str) else "UNKNOWN"
        return cls(
            payload["observation_id"],
            info["node_id"],
            info["boot_id"],
            payload["receiver"]["connection_id"],
            info["station"],
            vehicle,
            arrival,
            age,
            event.get("timing_basis", "unknown"),
            altitude,
            datum,
            speed,
            name,
            state.get("schema", "provisional"),
            telemetry_pose(sample, fresh=True, state_name=name),
            mission_elapsed,
        )


@dataclass(frozen=True)
class BroadcastSelection:
    scene: str = "Standby"
    main: str | None = None
    inset: str | None = None  # None, "orientation", or a logical camera.
    title: str = "MIT ROCKET TEAM"
    caption: str = ""
    main_crop: tuple[float, float, float, float] = (0, 0, 0, 0)  # left/top/right/bottom fractions

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
    clock_basis: str = ""
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
    camera_crop: tuple[float, float, float, float] = (0, 0, 0, 0)


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
