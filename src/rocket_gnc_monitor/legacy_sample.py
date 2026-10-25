"""Decode saved Zephyrus CSV rows without evaluating Python/NumPy expressions."""

from __future__ import annotations
import ast
import math
from .domain import Sample, finite
from .zephyrus import CSV_FIELDS, PHASES, PHASE_ENUM


def numeric_list(text, *, boolean=False):
    if len(text) > 4096:
        raise ValueError("Legacy numeric array too large")

    def value(node):
        if isinstance(node, ast.Constant):
            if boolean and isinstance(node.value, bool):
                return node.value
            if finite(node.value):
                return float(node.value)
        if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) <= 32:
            return [value(item) for item in node.elts]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            result = value(node.operand)
            if finite(result):
                return -result if isinstance(node.op, ast.USub) else result
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if boolean and node.value.id in {"np", "numpy"} and node.attr in {"True_", "False_"}:
                return node.attr == "True_"
        if (
            isinstance(node, ast.Call)
            and not node.keywords
            and len(node.args) == 1
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"np", "numpy"}
            and node.func.attr in {"float32", "float64", "int32", "int64"}
        ):
            return value(node.args[0])
        raise ValueError("Unsupported legacy numeric literal")

    result = value(ast.parse(text, mode="eval").body)
    if not isinstance(result, list) or not all(
        finite(item) or (boolean and isinstance(item, bool)) for item in result
    ):
        raise ValueError("Expected a legacy numeric list")
    return result


def decode_row(row, *, source="LEGACY_CSV", received=None):
    def number(key):
        try:
            value = float(row.get(key, ""))
            return value if math.isfinite(value) else None
        except (ValueError, TypeError):
            return None

    v = {key: number(key) for key in CSV_FIELDS}
    for key in (
        "gps_fix",
        "gps_num_sat",
        "flight_time",
        "pktnum",
        "badpackets",
        "bms_protections_enabled",
        "bms_protection_status",
    ):
        v[key] = int(v[key]) if v[key] is not None else None
    integer_arrays = {"pyros", "servos", "armed_pyros", "fired_pyros"}
    for key, length in dict(
        pyros=6,
        servos=4,
        servos_deg=4,
        accelerometer=3,
        gyro=3,
        armed_pyros=8,
        fired_pyros=8,
        pyro_resistances=8,
        cell_voltages=3,
        converter_voltages=6,
        converter_currents=6,
        enabled_status=6,
    ).items():
        try:
            items = numeric_list(row.get(key, "[]"), boolean=key == "enabled_status")
            if key in integer_arrays:
                items = [int(item) for item in items]
        except (ValueError, SyntaxError, TypeError):
            items = []
        v[key] = (items + [None] * length)[:length]
    v["gnd_fix"] = {"True": True, "False": False, "1": True, "0": False}.get(row.get("gnd_fix"))
    v["state"] = row.get("state", "Unknown")
    timestamp, ticks, sequence = (v[key] for key in ("timestamp", "flight_time", "pktnum"))
    if any(value is None for value in (timestamp, ticks, sequence)):
        raise ValueError("Invalid CSV timestamp, flight time or packet number")
    phase_key = v["state"].split(".")[-1]
    phase = PHASES[PHASE_ENUM.index(phase_key)] if phase_key in PHASE_ENUM else "Unknown"
    lat, lon = v["lat"], v["lon"]
    valid = finite(lat) and finite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180
    attitude = [v[key] for key in ("roll_gyro_int", "pitch_gyro_int", "yaw_gyro_int")]
    details = {
        "legacy_csv": dict(row),
        "legacy_values": v,
        "attitude_kind": "Body gyro integrals · not Euler attitude · recorded CSV",
        "quality": "Recorded decoded CSV; raw packet checksum unavailable",
        "state_code": PHASES.index(phase) if phase in PHASES else 0,
        "device_ticks_raw": ticks,
    }
    for field, key in {
        "gps_horizontal_accuracy": "gps_horiz_prec",
        "gps_vertical_accuracy": "gps_vert_prec",
        "satellites": "gps_num_sat",
        "onboard_rssi": "rxrssi",
        "bad_packets": "badpackets",
        "baro_max_altitude": "baro_max_alt",
        "gps_max_altitude": "gps_max_alt",
        "cells": "cell_voltages",
        "current": "total_current",
        "rail_voltages": "converter_voltages",
        "rail_currents": "converter_currents",
        "bms_protections_enabled": "bms_protections_enabled",
        "bms_protection": "bms_protection_status",
        "bms_temperature": "bms_temp",
        "receiver_latitude": "gnd_lat",
        "receiver_longitude": "gnd_lon",
        "pyro_continuity": "pyros",
    }.items():
        details[field] = v[key]
    details["receiver_fix"] = 3 if v["gnd_fix"] else 0
    details["receiver_height_wire"] = v["gnd_alt"] * 1000 if finite(v["gnd_alt"]) else None
    details["pyro_resistance"] = v["pyro_resistances"][:6]
    for field, key in (("armed_bits", "armed_pyros"), ("fired_bits", "fired_pyros")):
        details[field] = sum((1 << i) for i, item in enumerate(v[key][:6]) if item == 1)
    sample = Sample(
        t=ticks / 1000,
        sequence=sequence,
        source=source,
        utc=timestamp,
        altitude=v["barofilteredalt"],
        velocity=v["accel_integrated_velo"],
        latitude=lat if valid else None,
        longitude=lon if valid else None,
        gps_fix=(v["gps_fix"] or 0) if valid else 0,
        gps_altitude=v["gpsalt"],
        battery=sum(v["cell_voltages"]) if all(finite(x) for x in v["cell_voltages"]) else None,
        rssi=v["rssi"],
        phase=phase,
        attitude=attitude if all(finite(x) for x in attitude) else None,
        rates=v["gyro"] if all(finite(x) for x in v["gyro"]) else None,
        acceleration=v["accelerometer"] if all(finite(x) for x in v["accelerometer"]) else None,
        actuators={f"Legacy servo {i + 1}": {"drive": x} for i, x in enumerate(v["servos"]) if finite(x)},
        details=details,
    )
    if received is not None:
        sample.received = received
    return sample
