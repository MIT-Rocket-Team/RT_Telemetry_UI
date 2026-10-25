"""Lossless accepted-serial observations for station-network protocol v1.

The native schema preserves the complete Sample object: scalar velocity is in
m/s, acceleration in m/s², rates in deg/s, and attitude contains body gyro
integrals in degrees, not Euler angles. Details/actuators retain their decoded
names and units from protocol.TelemetryDecoder. Device boot_index is an inferred
decoder epoch, never an authenticated rocket boot identifier.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import uuid

from ..balius_states import BALIUS_STATE_NAMES
from ..domain import Sample, finite


def _counter(value, field):
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return str(value)


def _uuid(value):
    return str(uuid.UUID(str(value)))


def observation_from_sample(
    sample, *, raw, station, node_id, boot_id, connection_id,
    observation_counter, boot_monotonic, vehicle_mode,
    decoder_gaps=0, rejected_packets=None, raw_unavailable_reason=None,
    baro_datum="unknown",
):
    """Build Section 7 payload without inferring hardware identity or attitude.

    Call only for an accepted packet, before UI delivery. The caller supplies a
    per-application-boot observation counter (not the resetting serial counter).
    ``raw`` is the exact serial frame, including sync and receiver trailer.
    ``boot_monotonic`` is this publishing application's clock origin.
    """
    station = str(station).lower()
    station = "launch" if station == "base" else station
    if station not in {"launch", "gs1", "gs2", "gs3", "gs4"}:
        raise ValueError("Unknown receiver station")
    if vehicle_mode not in {"balius", "iris"}:
        raise ValueError("Unknown vehicle mode")
    if baro_datum not in {"wgs84_ellipsoid", "msl", "agl_launch", "unknown"}:
        raise ValueError("Unknown barometric altitude datum")
    if not finite(sample.received) or not finite(boot_monotonic) or sample.received < boot_monotonic:
        raise ValueError("Observation receipt predates publishing boot")
    received_mono_ms = str(int((sample.received - boot_monotonic) * 1000))
    received_time_utc = None
    if finite(sample.utc):
        received_time_utc = datetime.fromtimestamp(sample.utc, timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")

    warnings = [
        "Receiver trailer has no independent checksum.",
        "Device boot identity is unavailable; native boot_index is only an inferred decoder epoch.",
        "Body gyro integrals are not a validated body-to-ENU orientation.",
    ]
    if raw is None:
        if not raw_unavailable_reason:
            raise ValueError("Unavailable original bytes require an explicit reason")
        raw_b64 = raw_sha256 = None
        warnings.append(f"Original bytes unavailable: {raw_unavailable_reason}")
    else:
        raw = bytes(raw)
        if len(raw) != 144 or raw[:2] != b"\xab\xab" or sum(raw[2:129]) % 256 != raw[129]:
            raise ValueError("Expected an accepted complete telemetry serial frame")
        raw_b64 = base64.b64encode(raw).decode("ascii")
        raw_sha256 = hashlib.sha256(raw).hexdigest()

    code = sample.details.get("state_code")
    code = code if type(code) is int else None
    state_name = sample.phase or None
    if vehicle_mode == "balius":
        state_name = BALIUS_STATE_NAMES[code] if code is not None and 0 <= code < len(BALIUS_STATE_NAMES) else None
        warnings.append("Balius state names use provisional wire codes 0–4.")
    else:
        warnings.append("Iris vehicle attribution is unknown until packet identity or a qualified switch acknowledgement is available.")

    position_valid = (
        type(sample.gps_fix) is int and sample.gps_fix >= 2
        and finite(sample.latitude) and -90 <= sample.latitude <= 90
        and finite(sample.longitude) and -180 <= sample.longitude <= 180
    )
    altitude_valid = position_valid and sample.gps_fix >= 3 and finite(sample.gps_altitude)
    native = sample.to_dict()
    # Validate and detach from the mutable live sample before returning it to a
    # worker queue. No native field is discarded to reduce network traffic.
    native = json.loads(json.dumps(native, allow_nan=False))
    device_time = sample.details.get("device_ticks_raw")
    device_time = str(device_time) if type(device_time) is int and device_time >= 0 else None
    rejected = sample.details.get("bad_packets", 0) if rejected_packets is None else rejected_packets
    return {
        "observation_id": f"{_uuid(node_id)}:{_uuid(boot_id)}:{_counter(observation_counter, 'observation_counter')}",
        "receiver": {
            "station": station,
            "board_id": "downlink",
            "connection_id": _uuid(connection_id),
            "rssi_dbm": sample.rssi if finite(sample.rssi) else None,
            "rx_rssi_dbm": sample.details.get("onboard_rssi") if finite(sample.details.get("onboard_rssi")) else None,
            "decoder_gaps": _counter(decoder_gaps, "decoder_gaps"),
            "rejected_packets": _counter(rejected, "rejected_packets"),
        },
        "vehicle": {
            "id": "balius" if vehicle_mode == "balius" else "unknown",
            "basis": "operator_assignment" if vehicle_mode == "balius" else "unknown",
            "target_epoch": None,
        },
        "packet": {
            "protocol": "rocket-telemetry",
            "protocol_version": "1",
            "decoder_version": "rocket-telemetry/1",
            "type": "telemetry",
            "sequence": _counter(sample.sequence, "sequence"),
            "device_boot_id": None,
            "device_time_ms": device_time,
            "raw_b64": raw_b64,
            "raw_sha256": raw_sha256,
        },
        "timing": {
            "received_mono_ms": received_mono_ms,
            "received_time_utc": received_time_utc,
            "clock_quality": "unknown",
            "clock_uncertainty_ms": None,
        },
        "values": {
            "state": {"code": code, "name": state_name},
            "position": {
                "lat_deg": sample.latitude if position_valid else None,
                "lon_deg": sample.longitude if position_valid else None,
                "alt_m": sample.gps_altitude if altitude_valid else None,
                "alt_datum": "unknown",
                "fix": bool(position_valid),
            },
            "baro_altitude": {"value_m": sample.altitude if finite(sample.altitude) else None, "datum": baro_datum},
            "velocity_enu_m_s": None,
            "orientation": None,
            "flight_status": {
                "motor_ignited": None,
                "motor_burning": None,
                "parachute_deployed": None,
                "evidence_source": None,
            },
            "native_schema": "rocket-telemetry.sample/1",
            "native": native,
        },
        "quality": {
            "validation": "accepted",
            "attribution_confidence": "operator_assignment" if vehicle_mode == "balius" else "unknown",
            "warnings": warnings,
        },
    }


def observation_to_sample(payload, *, received_monotonic, source_age_ms, source_label):
    """Read-only display projection; never feed this into local-board control.

    Source age is a lower bound computed within the publisher's boot clock. Its
    elapsed age is projected onto *this* receiver's monotonic clock; absolute
    monotonic values from different machines are never compared.
    """
    if not finite(received_monotonic) or not finite(source_age_ms) or source_age_ms < 0:
        raise ValueError("Remote display requires valid local receipt time and source age")
    values = payload["values"]
    native = values.get("native", {}) if values.get("native_schema") == "rocket-telemetry.sample/1" else {}
    native = native if isinstance(native, dict) else {}
    native_details = native.get("details", {})
    native_details = native_details if isinstance(native_details, dict) else {}
    details = {}
    # Only fields with the known display contract enter legacy calculations.
    # Everything else remains lossless in network_native and the receive log.
    numeric_details = (
        "schema_version", "boot_index", "device_ticks_raw", "bad_packets", "receiver_fix",
        "receiver_latitude", "receiver_longitude", "receiver_height_wire",
        "gps_horizontal_accuracy", "gps_vertical_accuracy", "satellites", "current",
        "bms_temperature", "bms_protection", "bms_protections_enabled", "armed_bits",
        "fired_bits", "onboard_rssi", "raw_pressure", "raw_temperature",
        "baro_max_altitude", "gps_max_altitude",
    )
    integer_details = {
        "schema_version", "boot_index", "device_ticks_raw", "bad_packets", "receiver_fix",
        "satellites", "bms_protection", "bms_protections_enabled", "armed_bits", "fired_bits",
    }
    for key in numeric_details:
        value = native_details.get(key)
        if finite(value) and (key not in integer_details or type(value) is int):
            details[key] = value
    for key in ("time_basis", "attitude_kind", "receiver_integrity", "receiver_height_note"):
        if isinstance(native_details.get(key), str):
            details[key] = native_details[key]
    for key, count in (("cells", 3), ("pyro_continuity", 6), ("pyro_resistance", 6),
                       ("rail_voltages", 6), ("rail_currents", 6)):
        value = native_details.get(key)
        if isinstance(value, list) and len(value) == count:
            details[key] = [item if finite(item) else None for item in value]
    state = values.get("state", {})
    code = state.get("code")
    if type(code) is int:
        details["state_code"] = code
    details.update(
        display_source=str(source_label),
        remote_observation_id=payload["observation_id"],
        read_only=True,
        source_age_is_lower_bound=True,
        network_native=json.loads(json.dumps(native, allow_nan=False)),
        network_vehicle=dict(payload.get("vehicle", {})),
    )
    orientation = values.get("orientation")
    # The baseline does not publish orientation. Future explicit frames must
    # name the nose axis; do not guess an axis from an arbitrary quaternion.
    if isinstance(orientation, dict):
        axes = orientation.get("body_axes")
        quaternion = orientation.get("quaternion_wxyz")
        axis_supported = axes == "right_handed_nose_+Z" or (
            isinstance(axes, dict) and axes.get("nose") == "+Z" and axes.get("handedness") == "right"
        )
        if (axis_supported and isinstance(quaternion, list) and len(quaternion) == 4
                and all(finite(v) for v in quaternion)
                and abs(math.hypot(*quaternion) - 1) <= .001):
            details["orientation"] = {"frame": "body_to_ENU", "body_axis": "+Z", "quaternion_wxyz": list(quaternion)}
    flight = values.get("flight_status", {})
    details["flight_status"] = {
        key: flight.get(key) if type(flight.get(key)) is bool else None
        for key in ("motor_ignited", "motor_burning", "parachute_deployed")
    }
    details["flight_status"]["evidence_source"] = flight.get("evidence_source")
    position = values.get("position", {})
    fixed = position.get("fix") is True
    latitude, longitude = position.get("lat_deg"), position.get("lon_deg")
    fixed = fixed and finite(latitude) and -90 <= latitude <= 90 and finite(longitude) and -180 <= longitude <= 180
    utc = 0.0
    utc_text = payload.get("timing", {}).get("received_time_utc")
    if isinstance(utc_text, str):
        try:
            stamp = datetime.fromisoformat(utc_text.replace("Z", "+00:00"))
            if stamp.tzinfo is not None:
                utc = stamp.timestamp()
        except (ValueError, OverflowError):
            pass

    def vector(key):
        data = native.get(key)
        return list(data) if isinstance(data, list) and len(data) == 3 and all(finite(v) for v in data) else None

    actuators = {}
    if isinstance(native.get("actuators"), dict):
        for name, entry in native["actuators"].items():
            if isinstance(name, str) and isinstance(entry, dict):
                actuators[name] = {key: value for key, value in entry.items() if finite(value)}
    device_time = payload.get("packet", {}).get("device_time_ms")
    t = native.get("t")
    if not finite(t):
        t = int(device_time) / 1000 if isinstance(device_time, str) and device_time.isdecimal() else 0.0
    sequence = payload.get("packet", {}).get("sequence")
    sequence = int(sequence) if isinstance(sequence, str) and sequence.isdecimal() else 0
    baro = values.get("baro_altitude", {}).get("value_m")
    gps_altitude = position.get("alt_m")
    return Sample(
        t=t, sequence=sequence, source="NETWORK",
        received=received_monotonic - source_age_ms / 1000, utc=utc,
        altitude=baro if finite(baro) else None,
        velocity=native.get("velocity") if finite(native.get("velocity")) else None,
        latitude=latitude if fixed else None, longitude=longitude if fixed else None,
        gps_altitude=gps_altitude if fixed and finite(gps_altitude) else None,
        gps_fix=3 if fixed else 0,
        attitude=vector("attitude"), rates=vector("rates"), acceleration=vector("acceleration"),
        battery=native.get("battery") if finite(native.get("battery")) else None,
        rssi=payload.get("receiver", {}).get("rssi_dbm") if finite(payload.get("receiver", {}).get("rssi_dbm")) else None,
        phase=state.get("name") if isinstance(state.get("name"), str) else "Unknown",
        actuators=actuators, details=details,
    )
