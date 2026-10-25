"""Read-only rocket attitude and effect states for an illustrative 3D instrument.

The mesh has a right-handed body frame with +Z toward the nose. Legacy Zephyrus
``sample.attitude`` values are independent body-rate integrals in degrees, not
Euler angles. Preserve them as readouts, but never use them to rotate the mesh.
An adapter may provide ``sample.details['orientation']`` with ``frame`` equal
to ``'body_to_ENU'``, ``body_axis`` equal to ``'+Z'`` and a unit quaternion in
``quaternion_wxyz``. This explicit internal contract is not a wire definition.
Only that validated orientation can rotate the telemetry model. Derived display
angles use ``Rx(yaw) @ Ry(pitch) @ Rz(roll)`` and are not the legacy integrals.
These display rotations must never be used for estimation or antenna commands.

An adapter may supply ``sample.details['flight_status']`` with exact Python bool
values ``motor_ignited`` (ignition occurred), ``motor_burning`` (burning now) and
``parachute_deployed`` (deployment confirmed). Missing/malformed fields are
unknown. This is an internal adapter contract, not a Zephyrus wire definition.
Ignition history does not establish whether a motor is still burning. Rocket
phase, pyro firing bits and continuity are never treated as event confirmation.

OpenRocket frames already contain a body-to-ENU matrix; it is copied directly.
Callers must set ``events_available=False`` when their reference lacks a usable
simulation event timeline. For partial timelines, ``motor_events_available``
and ``parachute_events_available`` independently identify usable effect history:
IGNITION/BURNOUT for the motor and RECOVERY_DEVICE_DEPLOYMENT for the parachute.
A path-aligned frame remains explicitly illustrative and does not produce
claimed R/P/Y measurements.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from numbers import Real

import numpy as np


def _number(value):
    if isinstance(value, Real) and not isinstance(value, (bool, np.bool_)):
        try:
            return float(value) if math.isfinite(value) else None
        except (OverflowError, ValueError):
            pass
    return None


def _matrix(value=None):
    """Return an owned, read-only proper rotation, or a neutral identity."""
    try:
        result = np.asarray(value, dtype=float)
        valid = (result.shape == (3, 3) and np.isfinite(result).all()
                 and np.allclose(result.T @ result, np.eye(3), atol=1e-6, rtol=0)
                 and math.isclose(float(np.linalg.det(result)), 1, abs_tol=1e-6))
    except (TypeError, ValueError, OverflowError):
        valid = False
    result = np.array(result if valid else np.eye(3), copy=True)
    result.setflags(write=False)
    return result, bool(valid)


def _neutral_rotation():
    return _matrix()[0]


@dataclass(frozen=True)
class RocketPose:
    rotation: np.ndarray = field(default_factory=_neutral_rotation)
    angles: tuple[float | None, float | None, float | None] = (None, None, None)
    angle_kind: str = "unknown"  # gyro_integrals, orientation, or unknown.
    motor: bool | None = None  # Current burning, not historical ignition.
    parachute: bool | None = None
    ignited: bool | None = None
    source: str = "LIVE"
    status: str = "LIVE · No telemetry · orientation unavailable"
    stale: bool = False
    attitude_known: bool = False
    inflation: float = 0.0
    time: float | None = None


def _angles(values):
    if not isinstance(values, (tuple, list, np.ndarray)):
        return (None, None, None)
    try:
        if len(values) == 3:
            return tuple(_number(value) for value in values)
    except TypeError:
        pass
    return (None, None, None)


def rotation_from_rpy(roll, pitch, yaw):
    """Display Euler convention for genuine orientations, never gyro integrals."""
    values = _angles((roll, pitch, yaw))
    if any(value is None for value in values):
        raise ValueError("Roll, pitch and yaw must be finite numbers in degrees")
    r, p, y = (math.radians(value % 360) for value in values)
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return _matrix(np.array([
        [cp * cr, -cp * sr, sp],
        [cy * sr + sy * sp * cr, cy * cr - sy * sp * sr, -sy * cp],
        [sy * sr - cy * sp * cr, sy * cr + cy * sp * sr, cy * cp],
    ]))[0]


def _rpy_from_rotation(rotation):
    """Inverse of the display convention; choose zero roll at its singularity."""
    pitch = math.asin(float(np.clip(rotation[0, 2], -1, 1)))
    if abs(math.cos(pitch)) > 1e-7:
        roll = math.atan2(-rotation[0, 1], rotation[0, 0])
        yaw = math.atan2(-rotation[1, 2], rotation[2, 2])
    else:
        roll = 0.0
        yaw = math.atan2(rotation[2, 1], rotation[1, 1])
    return tuple(math.degrees(value) for value in (roll, pitch, yaw))


def _flag(values, key):
    value = values.get(key)
    return value if type(value) is bool else None


def _orientation_rotation(value):
    """Validate the explicit adapter quaternion without guessing axes or units.

    A norm tolerance of 0.001 permits float rounding, not arbitrary unnormalized
    inputs. Accepted quaternions are normalized within that tolerance before
    conversion. All components must be real finite numbers, excluding bools.
    """
    if not isinstance(value, Mapping):
        return _neutral_rotation(), False
    frame, axis = value.get("frame"), value.get("body_axis")
    if not (isinstance(frame, str) and frame == "body_to_ENU"
            and isinstance(axis, str) and axis == "+Z"):
        return _neutral_rotation(), False
    quaternion = value.get("quaternion_wxyz")
    if not isinstance(quaternion, (list, tuple, np.ndarray)):
        return _neutral_rotation(), False
    try:
        if len(quaternion) != 4:
            return _neutral_rotation(), False
        components = tuple(_number(component) for component in quaternion)
    except TypeError:
        return _neutral_rotation(), False
    if any(component is None for component in components):
        return _neutral_rotation(), False
    norm = math.hypot(*components)
    if not math.isfinite(norm) or not math.isclose(norm, 1, rel_tol=0, abs_tol=0.001):
        return _neutral_rotation(), False
    w, x, y, z = (component / norm for component in components)
    return _matrix([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def _source(mode, sample):
    source = mode.upper() if isinstance(mode, str) else "UNKNOWN"
    if source not in {"LIVE", "DEMO", "REPLAY"}:
        source = "UNKNOWN"
    sample_source = getattr(sample, "source", "")
    if source == "LIVE" and isinstance(sample_source, str) and sample_source in {"DEMO", "REPLAY", "LEGACY_CSV"}:
        source = "REPLAY" if sample_source == "LEGACY_CSV" else sample_source
    return source


def telemetry_pose(sample, mode="LIVE", fresh=True):
    """Preserve gyro totals while requiring explicit orientation for rotation."""
    source = _source(mode, sample)
    if sample is None:
        return RocketPose(source=source, status=f"{source} · No telemetry · orientation unavailable")
    angles = _angles(getattr(sample, "attitude", None))
    angle_kind = "gyro_integrals" if any(value is not None for value in angles) else "unknown"
    details = getattr(sample, "details", {})
    details = details if isinstance(details, Mapping) else {}
    rotation, known = _orientation_rotation(details.get("orientation"))
    if known:
        angles = _rpy_from_rotation(rotation)
        angle_kind = "orientation"
    flight_status = details.get("flight_status", {})
    flight_status = flight_status if isinstance(flight_status, Mapping) else {}
    motor, parachute, ignited = (_flag(flight_status, key) for key in
                                ("motor_burning", "parachute_deployed", "motor_ignited"))
    stale = source == "LIVE" and not fresh
    state = "STALE · last telemetry" if stale else "Telemetry"
    if known:
        attitude = "Orientation quaternion · body-to-ENU · +Z nose"
    elif angle_kind == "gyro_integrals":
        attitude = "Orientation unavailable · gyro integrals are not attitude"
    else:
        attitude = "Orientation unavailable · neutral model"
    if "orientation" in details and not known:
        attitude += " · Invalid orientation metadata"
    status = f"{source} · {state} · {attitude}"
    if ignited is True and motor is None:
        status += " · Ignition reported; current burn unknown"
    return RocketPose(
        rotation=rotation, angles=angles, angle_kind=angle_kind, motor=motor, parachute=parachute, ignited=ignited,
        source=source, status=status, stale=bool(stale), attitude_known=known,
        inflation=1.0 if parachute is True else 0.0, time=_number(getattr(sample, "t", None)),
    )


def simulation_pose(frame, events_available=True, *, motor_events_available=None,
                    parachute_events_available=None):
    """Adapt an explicitly selected OpenRocket frame without altering its axes.

    Optional per-effect availability uses exact bools; ``None`` retains the
    original global event-availability behavior. Callers with a partial event
    list should pass both flags explicitly. A global unavailable timeline always
    overrides the flags, so default frame booleans cannot imply confirmed OFF.
    """
    if frame is None:
        return RocketPose(source="SIMULATION", status="OpenRocket reference · No simulation frame")
    rotation, valid = _matrix(getattr(frame, "rotation", None))
    attitude = getattr(frame, "attitude", "")
    attitude = attitude if isinstance(attitude, str) else ""
    known = valid and attitude.startswith("Simulation attitude")
    reliable_events = events_available is True and getattr(frame, "state", "") != "FLIGHT EVENTS UNAVAILABLE"
    motor_reliable = reliable_events and (motor_events_available is None or motor_events_available is True)
    parachute_reliable = reliable_events and (parachute_events_available is None or parachute_events_available is True)
    flags = {"motor": getattr(frame, "powered", None), "parachute": getattr(frame, "recovery", None)}
    motor = _flag(flags, "motor") if motor_reliable else None
    parachute = _flag(flags, "parachute") if parachute_reliable else None
    inflation = _number(getattr(frame, "inflation", None))
    inflation = min(1.0, max(0.0, inflation)) if parachute is True and inflation is not None else 0.0
    description = attitude or "Orientation source unavailable"
    if not valid:
        description = "Orientation unavailable · neutral model"
    status = f"OpenRocket reference · {description}"
    if not reliable_events:
        status += " · Flight events unavailable"
    else:
        if not motor_reliable:
            status += " · Motor event history unavailable"
        if not parachute_reliable:
            status += " · Parachute event history unavailable"
    return RocketPose(
        rotation=rotation, angles=_rpy_from_rotation(rotation) if known else (None, None, None),
        angle_kind="orientation" if known else "unknown",
        motor=motor, parachute=parachute, ignited=True if motor is True else None,
        source="SIMULATION", status=status, attitude_known=known, inflation=inflation,
        time=_number(getattr(frame, "time", None)),
    )
