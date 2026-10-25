"""The observed Zephyrus ground station and antenna pointer wire formats."""

from __future__ import annotations
import math
import struct
from .domain import Sample, finite


class ZephyrusDecoder:
    FRAME_SIZE = 144

    def __init__(self, on_rejected=None):
        self.on_rejected = on_rejected
        self.buffer = bytearray()
        self.accepted = self.rejected = self.discarded = self.gaps = 0
        self.last_sequence = None
        self.last_tick = None
        self.wrap_offset = 0
        self.boot = 0

    def feed(self, chunk: bytes):
        self.buffer.extend(chunk)
        output = []
        while len(self.buffer) >= 2:
            at = self.buffer.find(b"\xab\xab")
            if at < 0:
                keep = 1 if self.buffer[-1] == 0xAB else 0
                self.discarded += len(self.buffer) - keep
                self.buffer[:] = self.buffer[-1:] if keep else b""
                break
            if at:
                self.discarded += at
                del self.buffer[:at]
            if len(self.buffer) < self.FRAME_SIZE:
                break
            payload = bytes(self.buffer[2:130])
            if sum(payload[:127]) % 256 != payload[127]:
                self.rejected += 1
                if self.on_rejected:
                    self.on_rejected(payload, bytes(self.buffer[130:144]), self.rejected)
                del self.buffer[0]  # Rescan; never discard a potentially overlapping valid sync.
                continue
            trailer = bytes(self.buffer[130:144])
            del self.buffer[:144]
            try:
                sample = self.decode(payload, trailer)
            except (ValueError, struct.error, OverflowError):
                self.rejected += 1
                continue
            self.accepted += 1
            sample.details["bad_packets"] = self.rejected
            output.append(sample)
        return output

    def decode(self, p, g):
        unpack = lambda fmt, off: struct.unpack_from("<" + fmt, p, off)[0]
        seq, ticks = unpack("H", 84), unpack("I", 80)
        if self.last_tick is not None and ticks < self.last_tick:
            if self.last_tick - ticks > 2**31:
                self.wrap_offset += 2**32
            else:
                self.boot += 1
                self.wrap_offset = 0
                self.last_sequence = None
        if self.last_sequence is not None:
            delta = (seq - self.last_sequence) % 65536
            if 1 < delta < 32768:
                self.gaps += delta - 1
        self.last_sequence, self.last_tick = seq, ticks
        latitude, longitude = unpack("i", 32) * 1e-7, unpack("i", 36) * 1e-7
        gps_fix = p[31]
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            latitude = longitude = None
            gps_fix = 0

        def number(fmt, offset):
            value = unpack(fmt, offset)
            return value if finite(value) else None

        servo_bits = int.from_bytes(p[10:16], "little")
        cells = [unpack("h", 87 + 2 * i) / 1000 for i in range(3)]
        attitude = [number("f", i) for i in (64, 68, 72)]
        gyro = [unpack("h", 25 + 2 * i) * 0.03051757812 * (-1 if i == 1 else 1) for i in range(3)]
        details = {
            "state_code": p[63],
            "schema_version": 1,
            "boot_index": self.boot,
            "device_ticks_raw": ticks,
            "time_basis": "Onboard millis()/1000 uptime; align to launch separately",
            "attitude_kind": "Body gyro integrals · not Euler attitude",
            "receiver_integrity": "Trailer has no independent checksum",
            "receiver_fix": g[1],
            "receiver_latitude": struct.unpack_from("<i", g, 2)[0] * 1e-7,
            "receiver_longitude": struct.unpack_from("<i", g, 6)[0] * 1e-7,
            "receiver_height_wire": struct.unpack_from("<i", g, 10)[0],
            "receiver_height_note": "Located receiver firmware sends integer metres; old UI divides by 1000. Not used as mount origin.",
            "gps_horizontal_accuracy": unpack("I", 44) / 1000,
            "gps_vertical_accuracy": unpack("I", 48) / 1000,
            "satellites": p[52],
            "cells": cells,
            "current": unpack("h", 93) / -1000,
            "bms_temperature": p[95] / 2,
            "bms_protection": p[96],
            "bms_protections_enabled": p[97],
            "pyro_continuity": [(int.from_bytes(p[:2], "little") >> (2 * i)) & 3 for i in range(6)],
            "armed_bits": p[2],
            "fired_bits": p[3],
            "pyro_resistance": [v / 10 for v in p[4:10]],
            "rail_voltages": [unpack("h", 98 + 2 * i) * 0.0016 for i in range(6)],
            "rail_currents": [unpack("h", 110 + 2 * i) * 0.000625 for i in range(6)],
            "onboard_rssi": unpack("b", 86) - 99,
            "raw_pressure": int.from_bytes(p[53:56], "little"),
            "raw_temperature": int.from_bytes(p[56:59], "little"),
            "baro_max_altitude": unpack("H", 76),
            "gps_max_altitude": unpack("H", 78),
        }
        return Sample(
            t=(ticks + self.wrap_offset) / 1000,
            sequence=seq,
            source="LIVE",
            altitude=number("f", 59),
            velocity=number("f", 122),
            latitude=latitude,
            longitude=longitude,
            gps_altitude=number("f", 40),
            gps_fix=gps_fix,
            attitude=attitude if all(finite(v) for v in attitude) else None,
            rates=gyro,
            acceleration=[
                int.from_bytes(p[i : i + 3], "little", signed=True) / 12800 * 9.80665 for i in (16, 19, 22)
            ],
            battery=sum(cells),
            rssi=struct.unpack("b", g[:1])[0] - 99,
            phase={
                0: "Ground testing",
                1: "Preflight",
                2: "Flight",
                3: "Post-apogee",
                4: "Main",
                5: "End",
            }.get(p[63], f"Unknown state {p[63]}"),
            details=details,
            actuators={f"Legacy servo {i + 1}": {"drive": (servo_bits >> (12 * i)) & 4095} for i in range(4)},
        )


def pointer_packet(azimuth=0.0, elevation=0.0, opcode=0):
    if opcode not in range(6):
        raise ValueError("Unsupported pointer command")
    if not all(finite(v) for v in (azimuth, elevation)):
        raise ValueError("Pointer angles must be finite")
    payload = bytes([opcode]) + (struct.pack("<ff", azimuth, elevation) if opcode == 0 else bytes(8))
    return b"\xaa" + payload + bytes([sum(payload) % 256])


def validate_route(previous, requested, mission):
    az, el = requested
    if not mission.pointer_az_min <= az <= mission.pointer_az_max:
        raise ValueError("Azimuth outside the configured envelope")
    if not mission.pointer_el_min <= el <= mission.pointer_el_max:
        raise ValueError("Elevation outside the configured envelope")
    if not mission.pointer_full_rotation:
        if previous is None:
            raise ValueError("Establish reference zero before a cable-limited move")
        delta = (az - previous[0] + 180) % 360 - 180
        if math.isclose(abs(delta), 180):
            raise ValueError("Ambiguous 180° route; use an intermediate setpoint")
        end = previous[0] + delta
        if (
            not mission.pointer_az_min <= min(previous[0], end)
            or max(previous[0], end) > mission.pointer_az_max
        ):
            raise ValueError("Firmware shortest path crosses the configured cable envelope")
