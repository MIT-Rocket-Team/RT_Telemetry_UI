"""Strict, Qt-independent contracts for the station monitoring protocol v1.

Only monitoring data is supported. None of these contracts dispatches commands.
Optional extension fields are retained; required fields are checked explicitly.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import uuid
from datetime import datetime
from urllib.parse import urlsplit

PROTOCOL = {"major": 1, "minor": 0}
SERVICE_TYPE = "_rt-gs._tcp.local."
SUBPROTOCOL = "rt-gs.v1"
MAX_MESSAGE = 256 * 1024
MAX_REQUEST = 64 * 1024
MAX_RAW = 64 * 1024
EVENT_KINDS = ("telemetry.observation", "status.changed", "streams.changed")
SOURCE_MODES = ("live", "replay", "simulation")
STATIONS = ("launch", "gs1", "gs2", "gs3", "gs4")


class NetworkProtocolError(ValueError):
    def __init__(self, status, code, message, retryable=False):
        super().__init__(message)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable

    def body(self):
        return {"error": {"code": self.code, "message": self.message, "retryable": self.retryable}}


def require(condition, message):
    if not condition:
        raise NetworkProtocolError(422, "invalid_field", message)


def uuid_text(value, nullable=False):
    if value is None and nullable:
        return value
    require(isinstance(value, str), "UUID must be canonical text")
    try:
        valid = str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    require(valid, "UUID must be canonical lower-case text")
    return value


def counter(value):
    require(isinstance(value, str) and len(value) <= 30 and bool(re.fullmatch(r"0|[1-9][0-9]*", value)),
            "Counter must be a nonnegative decimal string")
    return int(value)


def number(value, nullable=True):
    if value is None and nullable:
        return value
    require(type(value) in (int, float) and math.isfinite(value), "Measurement must be finite or null")
    return value


def utc_text(value):
    if value is None:
        return value
    require(isinstance(value, str) and len(value) <= 40 and value.endswith("Z"), "UTC timestamp must use RFC 3339 Z")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise NetworkProtocolError(422, "invalid_field", "Invalid UTC timestamp") from exc
    return value


def obj(value, fields=()):
    require(isinstance(value, dict), "Expected a JSON object")
    require(set(fields) <= value.keys(), "Missing required fields: " + ", ".join(set(fields) - value.keys()))
    return value


def dumps(value):
    try:
        result = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError) as exc:
        raise NetworkProtocolError(422, "invalid_json", "Values must be finite JSON") from exc
    if len(result.encode("utf-8")) > MAX_MESSAGE:
        raise NetworkProtocolError(413, "message_too_large", "Message exceeds 256 KiB")
    return result


def loads(raw, maximum=MAX_MESSAGE):
    if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > maximum:
        raise NetworkProtocolError(413, "message_too_large", "Message exceeds its size limit")
    try:
        result = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise NetworkProtocolError(400, "invalid_json", "Malformed UTF-8 JSON") from exc
    return obj(result)


def endpoint(value):
    require(isinstance(value, str) and len(value) <= 2048, "Enter an HTTP endpoint")
    value = value.strip()
    if "://" not in value:
        value = "http://" + value
    parsed = urlsplit(value)
    require(parsed.scheme in {"http", "https"} and bool(parsed.hostname), "Enter an HTTP(S) endpoint")
    require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
            "Endpoint must not include credentials, query or fragment")
    require(parsed.path.rstrip("/") in {"", "/api/v1"}, "Endpoint path must be /api/v1 or empty")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise NetworkProtocolError(422, "invalid_endpoint", "Invalid endpoint port") from exc
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def validate_info(value):
    obj(value, ("protocol", "node_id", "boot_id", "mission_id", "generated_mono_ms", "station", "role",
                "vehicle_mode", "source_mode", "app_version", "supported_event_kinds", "capabilities", "limits"))
    version = obj(value["protocol"], ("major", "minor"))
    require(type(version["major"]) is int and type(version["minor"]) is int, "Invalid protocol version")
    if version["major"] != 1:
        raise NetworkProtocolError(409, "incompatible", "Unsupported station protocol major version")
    for name in ("node_id", "boot_id"):
        uuid_text(value[name])
    uuid_text(value["mission_id"], nullable=True)
    counter(value["generated_mono_ms"])
    require(value["station"] in STATIONS and value["role"] in {"telem", "video"}, "Invalid logical station slot")
    require(value["vehicle_mode"] in {"balius", "iris"}, "Invalid vehicle mode")
    require(value["source_mode"] in SOURCE_MODES, "Invalid source mode")
    require(isinstance(value["app_version"], str), "Invalid app version")
    require(isinstance(value["supported_event_kinds"], list) and all(isinstance(k, str) for k in value["supported_event_kinds"]), "Invalid supported events")
    obj(value["capabilities"])
    require(all(type(v) is bool for v in value["capabilities"].values()), "Capabilities must be booleans")
    obj(value["limits"])
    return value


def validate_envelope(value):
    obj(value, ("v", "kind", "node_id", "boot_id", "mission_id", "source_mode", "event_seq", "sent_mono_ms", "sent_time_utc", "payload"))
    require(type(value["v"]) is int and value["v"] == 1, "Unsupported envelope version")
    require(isinstance(value["kind"], str) and 0 < len(value["kind"]) <= 80, "Invalid message kind")
    uuid_text(value["node_id"])
    uuid_text(value["boot_id"])
    uuid_text(value["mission_id"], nullable=True)
    require(value["source_mode"] in SOURCE_MODES, "Invalid source mode")
    counter(value["sent_mono_ms"])
    if value["event_seq"] is not None:
        require(counter(value["event_seq"]) > 0, "Event sequence starts at 1")
    utc_text(value["sent_time_utc"])
    obj(value["payload"])
    return value


def event_fingerprint(event):
    """Delivery time changes on replay; immutable event content must not change."""
    stable = {key: value for key, value in event.items() if key not in {"sent_mono_ms", "sent_time_utc"}}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_observation(value):
    obj(value, ("observation_id", "receiver", "vehicle", "packet", "timing", "values", "quality"))
    identity = value["observation_id"]
    require(isinstance(identity, str) and len(identity.split(":")) == 3, "Invalid observation identity")
    node, boot, sequence = identity.split(":")
    uuid_text(node); uuid_text(boot); require(counter(sequence) > 0, "Observation counter starts at 1")
    receiver = obj(value["receiver"], ("station", "board_id", "connection_id", "rssi_dbm", "rx_rssi_dbm", "decoder_gaps", "rejected_packets"))
    require(receiver["station"] in STATIONS, "Invalid receiver station")
    uuid_text(receiver["connection_id"])
    require(isinstance(receiver["board_id"], str) and 0 < len(receiver["board_id"]) <= 80, "Invalid board identity")
    number(receiver["rssi_dbm"]); number(receiver["rx_rssi_dbm"])
    counter(receiver["decoder_gaps"]); counter(receiver["rejected_packets"])
    vehicle = obj(value["vehicle"], ("id", "basis", "target_epoch"))
    require(vehicle["id"] in {"balius", "sustainer", "booster", "unknown"}, "Invalid vehicle identity")
    require(vehicle["basis"] in {"packet_id", "confirmed_switch", "operator_assignment", "unknown"}, "Invalid attribution basis")
    require(vehicle["basis"] != "unknown" or vehicle["id"] == "unknown", "Unknown attribution cannot identify a vehicle")
    uuid_text(vehicle["target_epoch"], nullable=True)
    require(vehicle["basis"] != "confirmed_switch" or vehicle["target_epoch"] is not None, "Confirmed switch requires a target epoch")
    packet = obj(value["packet"], ("protocol", "protocol_version", "decoder_version", "type", "sequence", "device_boot_id", "device_time_ms", "raw_b64", "raw_sha256"))
    for key in ("protocol", "protocol_version", "decoder_version", "type"):
        require(isinstance(packet[key], str) and 0 < len(packet[key]) <= 160, "Invalid packet decoder identity")
    if packet["sequence"] is not None:
        counter(packet["sequence"])
    if packet["device_time_ms"] is not None:
        counter(packet["device_time_ms"])
    quality = obj(value["quality"], ("validation", "attribution_confidence", "warnings"))
    require(quality["validation"] == "accepted" and isinstance(quality["warnings"], list), "Only accepted packets are observations")
    if packet["raw_b64"] is None:
        require(packet["raw_sha256"] is None and bool(quality["warnings"]), "Missing raw bytes require a quality reason")
    else:
        try:
            raw = base64.b64decode(packet["raw_b64"], validate=True)
        except (ValueError, TypeError) as exc:
            raise NetworkProtocolError(422, "invalid_packet", "Invalid Base64 packet") from exc
        require(len(raw) <= MAX_RAW and base64.b64encode(raw).decode() == packet["raw_b64"], "Invalid packet size or padding")
        require(hashlib.sha256(raw).hexdigest() == packet["raw_sha256"], "Packet digest does not match raw bytes")
    timing = obj(value["timing"], ("received_mono_ms", "received_time_utc", "clock_quality", "clock_uncertainty_ms"))
    counter(timing["received_mono_ms"])
    utc_text(timing["received_time_utc"])
    require(timing["clock_quality"] in {"unknown", "estimated", "synchronized"}, "Invalid clock quality")
    number(timing["clock_uncertainty_ms"])
    require(timing["clock_uncertainty_ms"] is None or timing["clock_uncertainty_ms"] >= 0, "Clock uncertainty cannot be negative")
    vals = obj(value["values"], ("state", "position", "baro_altitude", "velocity_enu_m_s", "orientation", "flight_status", "native_schema", "native"))
    state = obj(vals["state"], ("code", "name")); obj(vals["native"])
    require(state["code"] is None or type(state["code"]) is int, "State code must retain the original integer")
    require(state["name"] is None or isinstance(state["name"], str), "Invalid state name")
    require(isinstance(vals["native_schema"], str) and bool(vals["native_schema"]), "Native schema must identify its decoder")
    position = obj(vals["position"], ("lat_deg", "lon_deg", "alt_m", "alt_datum", "fix"))
    for key, limit in (("lat_deg", 90), ("lon_deg", 180)):
        number(position[key])
        require(position[key] is None or -limit <= position[key] <= limit, "Invalid position")
    number(position["alt_m"])
    require(position["alt_datum"] in {"wgs84_ellipsoid", "msl", "agl_launch", "unknown"}, "Invalid altitude datum")
    require(position["fix"] is None or type(position["fix"]) is bool, "Fix must be boolean or null")
    baro = obj(vals["baro_altitude"], ("value_m", "datum"))
    number(baro["value_m"])
    require(baro["datum"] in {"wgs84_ellipsoid", "msl", "agl_launch", "unknown"}, "Invalid barometric altitude datum")
    vector = vals["velocity_enu_m_s"]
    if vector is not None:
        require(isinstance(vector, list) and len(vector) == 3, "Invalid ENU velocity")
        for item in vector:
            number(item, nullable=False)
    orientation = vals["orientation"]
    if orientation is not None:
        obj(orientation, ("quaternion_wxyz", "body_axes"))
        q = orientation["quaternion_wxyz"]
        require(isinstance(q, list) and len(q) == 4, "Invalid orientation quaternion")
        for item in q:
            number(item, nullable=False)
        require(abs(math.sqrt(sum(x*x for x in q)) - 1) <= .001, "Orientation quaternion is not unit length")
    flight = obj(vals["flight_status"], ("motor_ignited", "motor_burning", "parachute_deployed", "evidence_source"))
    for key in ("motor_ignited", "motor_burning", "parachute_deployed"):
        require(flight[key] is None or type(flight[key]) is bool, "Flight status must be boolean or null")
    dumps(value)
    return value


def validate_status(value):
    obj(value, ("application", "telemetry_board", "pointer", "video_inputs", "network", "clock"))
    obj(value["application"]); obj(value["network"]); obj(value["clock"])
    for key in ("telemetry_board", "pointer"):
        item = value[key]
        if item is not None:
            obj(item, ("availability", "changed_mono_ms", "reason"))
            require(item["availability"] in {"unsupported", "disconnected", "connected", "error"}, "Invalid hardware availability")
            counter(item["changed_mono_ms"])
            require(isinstance(item["reason"], str), "Hardware reason must be text")
    require(value["video_inputs"] is None or isinstance(value["video_inputs"], (list, dict)), "Invalid video input status")
    dumps(value)
    return value
