"""Bounded declarative telemetry codecs; physical = raw * scale + offset, with no executable expressions."""

import copy
import math
import re
import struct

from .contracts import resource, validate

SOURCES = frozenset(
    {
        "uptime_ms",
        "packet_sequence",
        "gps.fix",
        "gps.latitude_deg",
        "gps.longitude_deg",
        "gps.height_m",
        "barometer.height_m",
        "velocity.up_ms",
        "state.code",
        "gyro.roll_rate_deg_s",
        "gyro.pitch_rate_deg_s",
        "gyro.yaw_rate_deg_s",
        "acceleration.x_ms2",
        "acceleration.y_ms2",
        "acceleration.z_ms2",
        "battery.voltage_v",
    }
)
TYPES = {"i8": "b", "u8": "B", "i16": "h", "u16": "H", "i32": "i", "u32": "I", "f32": "f", "f64": "d"}


def integer(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value != int(value):
        raise ValueError("Expected an integer")
    return int(value)


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Expected a finite codec number")
    return float(value)


class TelemetryCodec:
    def __init__(self, definition):
        try:
            self._init(copy.deepcopy(definition))
        except (KeyError, TypeError, OverflowError, struct.error) as exc:
            raise ValueError("Invalid telemetry codec declaration") from exc

    def _init(self, definition):
        issues = validate(resource("codec.schema.json"), definition)
        if issues:
            raise ValueError(f"Invalid telemetry codec: {issues}")
        if definition["format"] != "openrocket.telemetry-codec" or integer(definition["version"]) != 1:
            raise ValueError("Unsupported codec format/version")
        if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", definition["id"]) is None:
            raise ValueError("Invalid codec ID")
        self.definition = definition
        self.length = integer(definition["payloadBytes"])
        if not 1 <= self.length <= 4096:
            raise ValueError("Codec payload must be 1..4096 bytes")
        if definition["byteOrder"] not in ("little", "big"):
            raise ValueError("Unknown codec byte order")
        self.order = "<" if definition["byteOrder"] == "little" else ">"
        if re.fullmatch(r"(?:[0-9a-fA-F]{2}){0,16}", definition["syncHex"]) is None:
            raise ValueError("Invalid codec sync")
        self.sync = bytes.fromhex(definition["syncHex"])
        self.trailer = definition["receiverTrailer"]
        if self.trailer not in ("none", "legacy-v1"):
            raise ValueError("Unsupported receiver trailer")
        occupied = set()

        def reserve(offset, width):
            if offset < 0 or offset + width > self.length:
                raise ValueError("Field outside payload")
            indices = set(range(offset, offset + width))
            if indices & occupied:
                raise ValueError("Overlapping codec fields/checksum")
            occupied.update(indices)

        if not isinstance(definition["fields"], list) or len(definition["fields"]) > 256:
            raise ValueError("At most 256 codec fields")
        names = set()
        for field in definition["fields"]:
            if (
                re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", field["name"]) is None
                or field["name"] in names
            ):
                raise ValueError("Invalid/duplicate codec field")
            names.add(field["name"])
            if field["source"] not in SOURCES or field["type"] not in TYPES:
                raise ValueError("Unknown source binding or field type")
            reserve(integer(field["byteOffset"]), struct.calcsize(self.order + TYPES[field["type"]]))
            if number(field["scale"]) == 0 or not isinstance(field["unit"], str) or len(field["unit"]) > 64:
                raise ValueError("Invalid field scale/unit")
            number(field["offset"])
            if type(field.get("optional", False)) is not bool:
                raise ValueError("Invalid optional flag")
        checksum = definition["checksum"]
        if checksum["algorithm"] not in ("none", "sum8", "crc16"):
            raise ValueError("Unsupported checksum")
        if checksum["algorithm"] != "none":
            width = 1 if checksum["algorithm"] == "sum8" else 2
            offset = integer(checksum["byteOffset"])
            reserve(offset, width)
            start, end = integer(checksum["start"]), integer(checksum["endExclusive"])
            if not 0 <= start < end <= self.length or start < offset + width and end > offset:
                raise ValueError("Invalid checksum range")
            if checksum["algorithm"] == "crc16":
                if not 1 <= integer(checksum["polynomial"]) <= 65535:
                    raise ValueError("Invalid CRC16 polynomial")
                for key in ("initial", "xorOut"):
                    if not 0 <= integer(checksum[key]) <= 65535:
                        raise ValueError("Invalid CRC16 parameters")
                for key in ("reflectInput", "reflectOutput"):
                    if type(checksum[key]) is not bool:
                        raise ValueError("Invalid CRC16 reflection flag")

    @property
    def frame_bytes(self):
        return len(self.sync) + self.length + (0 if self.trailer == "none" else 14)

    def checksum(self, payload):
        c = self.definition["checksum"]
        data = payload[integer(c["start"]) : integer(c["endExclusive"])]
        if c["algorithm"] == "sum8":
            return sum(data) & 255
        reverse = lambda value: int(f"{value:016b}"[::-1], 2)
        crc, poly = integer(c["initial"]), integer(c["polynomial"])
        if c["reflectInput"]:
            poly = reverse(poly)
        for byte in data:
            if c["reflectInput"]:
                crc ^= byte
                for _ in range(8):
                    crc = (crc >> 1) ^ (poly if crc & 1 else 0)
            else:
                crc ^= byte << 8
                for _ in range(8):
                    crc = ((crc << 1) ^ (poly if crc & 0x8000 else 0)) & 65535
        if c["reflectInput"] != c["reflectOutput"]:
            crc = reverse(crc)
        return (crc ^ integer(c["xorOut"])) & 65535

    def encode(self, values):
        payload, missing = bytearray(self.length), set()
        for field in self.definition["fields"]:
            value = values.get(field["source"])
            if value is None or not math.isfinite(value):
                if not field.get("optional", False):
                    raise ValueError("Missing required observation " + field["source"])
                missing.add(field["name"])
                continue
            raw = (value - field["offset"]) / field["scale"]
            if not math.isfinite(raw):
                raise ValueError("Non-finite encoded field")
            if field["type"][0] != "f":
                raw = round(raw)  # ties to even, matching Java Math.rint
            try:
                struct.pack_into(
                    self.order + TYPES[field["type"]], payload, integer(field["byteOffset"]), raw
                )
            except (struct.error, OverflowError) as exc:
                raise ValueError("Encoded field overflow " + field["name"]) from exc
        c = self.definition["checksum"]
        if c["algorithm"] != "none":
            struct.pack_into(
                self.order + ("B" if c["algorithm"] == "sum8" else "H"),
                payload,
                integer(c["byteOffset"]),
                self.checksum(payload),
            )
        return bytes(payload), frozenset(missing)

    def decode(self, payload):
        if len(payload) != self.length:
            raise ValueError("Wrong codec payload size")
        c = self.definition["checksum"]
        if c["algorithm"] != "none":
            actual = struct.unpack_from(
                self.order + ("B" if c["algorithm"] == "sum8" else "H"), payload, integer(c["byteOffset"])
            )[0]
            if actual != self.checksum(payload):
                raise ValueError("Invalid codec checksum")
        values = {}
        for field in self.definition["fields"]:
            raw = struct.unpack_from(
                self.order + TYPES[field["type"]], payload, integer(field["byteOffset"])
            )[0]
            value = raw * field["scale"] + field["offset"]
            values[field["name"]] = value if math.isfinite(value) else None
        return values

    def frame(self, payload, receiver_trailer):
        if len(payload) != self.length or len(receiver_trailer) != (0 if self.trailer == "none" else 14):
            raise ValueError("Invalid codec envelope lengths")
        return self.sync + payload + receiver_trailer
