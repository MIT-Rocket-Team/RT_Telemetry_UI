"""Legacy Zephyrus uplinks and CSV fields, preserved from the team's rocket.py."""

from __future__ import annotations
import math
import struct
import numpy as np
from .domain import finite

COMMANDS = {
    "arm_pyros": 0x01,
    "advance_state": 0x02,
    "set_airbrakes_angle": 0x03,
    "pd_activate": 0x04,
    "set_roll_control_servo_angle": 0x05,
    "fire_pyros": 0x06,
    "disarm_pyros": 0x07,
    "zero_servos": 0x08,
    "zero_pitchYawRoll": 0x09,
    "zero_alt": 0x0A,
    "zero_velo": 0x0B,
    "EMERG_DEPLOY_PISTON": 0x10,
    "EMERG_DEPLOY_BP_WELLS": 0x11,
    "EMERG_DEPLOY_TD": 0x12,
    "EMERG_DEPLOY_ALL": 0x13,
    "set_vtx_power": 0x14,
    "update_converters": 0x15,
    "bmsprotections": 0x16,
}


def rocket_packet(command, value=None):
    if command == "zero_roll":
        command = "zero_pitchYawRoll"
    if command not in COMMANDS:
        raise ValueError("Unknown rocket command")
    data = bytearray(16)
    data[0], data[13] = 0xAA, COMMANDS[command]
    if command in {"set_airbrakes_angle", "set_roll_control_servo_angle"}:
        if not finite(value):
            raise ValueError("Angle must be a finite number")
        struct.pack_into("<f", data, 9, value)
    elif command in {"arm_pyros", "fire_pyros", "disarm_pyros"}:
        if not value or any(type(i) is not int or not 0 <= i < 6 for i in value):
            raise ValueError("Select one or more pyro channels (0–5)")
        for channel in value:
            data[12] |= 1 << channel
    elif command == "update_converters":
        if not isinstance(value, (list, tuple)) or len(value) != 6:
            raise ValueError("Expected six converter settings")
        data[12] = sum(1 << i for i, enabled in enumerate(value) if enabled)
    elif command in {"advance_state", "set_vtx_power", "bmsprotections"}:
        limit = 255 if command == "advance_state" else 3 if command == "set_vtx_power" else 1
        if not isinstance(value, int) or not 0 <= value <= limit:
            raise ValueError("Invalid command value")
        data[12] = value
    struct.pack_into(">H", data, 14, sum(data[1:14]) % 65536)
    return bytes(data)


CSV_FIELDS = [
    "timestamp",
    "pyros",
    "servos",
    "servos_deg",
    "accelerometer",
    "barofilteredalt",
    "temp",
    "gyro",
    "gps_fix",
    "lat",
    "lon",
    "gpsalt",
    "gps_horiz_prec",
    "gps_vert_prec",
    "gps_num_sat",
    "flight_time",
    "yaw_gyro_int",
    "pitch_gyro_int",
    "roll_gyro_int",
    "state",
    "pktnum",
    "rssi",
    "armed_pyros",
    "fired_pyros",
    "badpackets",
    "rxrssi",
    "accel_integrated_velo",
    "baro_max_alt",
    "gps_max_alt",
    "pyro_resistances",
    "cell_voltages",
    "total_current",
    "converter_voltages",
    "converter_currents",
    "bms_protections_enabled",
    "bms_protection_status",
    "bms_temp",
    "enabled_status",
    "angleFromVertical",
    "gnd_lat",
    "gnd_lon",
    "gnd_fix",
    "gnd_alt",
]
PHASES = ["Ground testing", "Preflight", "Flight", "Post-apogee", "Main", "End"]
PHASE_ENUM = ["GROUND_TESTING", "PRE_FLIGHT", "FLIGHT", "POST_APOGEE", "MAIN", "END"]


def legacy_values(sample, badpackets=0, rejected=False):
    d = sample.details
    if "legacy_values" in d:
        # Decoded recordings already contain calibrated values and receiver status.
        return dict(d["legacy_values"])
    drives = [sample.actuators.get(f"Legacy servo {i + 1}", {}).get("drive") for i in range(4)]
    angles = [(v - 1500) / 500 * (60 if i < 2 else 50) if finite(v) else None for i, v in enumerate(drives)]
    if not rejected:
        angles = [float(np.round(v, 2)) if finite(v) else None for v in angles]
    # The original good-packet CSV includes two unused channels, always zero.
    padding = [] if rejected else [0, 0]
    roll, pitch, yaw = sample.attitude or [None] * 3
    angle_vertical = (
        math.degrees(math.acos(max(-1, min(1, math.cos(math.radians(pitch)) * math.cos(math.radians(yaw))))))
        if finite(pitch) and finite(yaw)
        else None
    )
    raw_temp = d.get("raw_temperature")
    temperature = (2000 + (raw_temp - 0x91E3 * 256) * 0x6FEC / (1 << 23)) / 100 if finite(raw_temp) else None
    volts = d.get("rail_voltages", [None] * 6)
    enabled = [
        abs(v - nominal) < 1 if finite(v) else None for v, nominal in zip(volts, [3, 3.3, 5, 7.4, 8.4, 28])
    ]
    phase = "state." + PHASE_ENUM[PHASES.index(sample.phase)] if sample.phase in PHASES else sample.phase
    values = [
        sample.utc,
        d.get("pyro_continuity", [None] * 6),
        drives,
        angles,
        sample.acceleration,
        sample.altitude,
        temperature,
        sample.rates,
        sample.gps_fix,
        sample.latitude,
        sample.longitude,
        sample.gps_altitude,
        d.get("gps_horizontal_accuracy"),
        d.get("gps_vertical_accuracy"),
        d.get("satellites"),
        d.get("device_ticks_raw", sample.t * 1000),
        yaw,
        pitch,
        roll,
        phase,
        sample.sequence,
        sample.rssi,
        [(d.get("armed_bits", 0) >> i) & 1 for i in range(6)] + padding,
        [(d.get("fired_bits", 0) >> i) & 1 for i in range(6)] + padding,
        badpackets,
        d.get("onboard_rssi"),
        sample.velocity,
        d.get("baro_max_altitude"),
        d.get("gps_max_altitude"),
        d.get("pyro_resistance", [None] * 6) + padding,
        d.get("cells", [None] * 3),
        d.get("current"),
        volts,
        d.get("rail_currents", [None] * 6),
        d.get("bms_protections_enabled"),
        d.get("bms_protection"),
        d.get("bms_temperature"),
        enabled,
        angle_vertical,
        float(np.round(d["receiver_latitude"], 5)) if finite(d.get("receiver_latitude")) else None,
        float(np.round(d["receiver_longitude"], 5)) if finite(d.get("receiver_longitude")) else None,
        d.get("receiver_fix") == 3,
        float(np.round(d["receiver_height_wire"] / 1000, 2))
        if finite(d.get("receiver_height_wire"))
        else None,
    ]
    return dict(zip(CSV_FIELDS, values))
