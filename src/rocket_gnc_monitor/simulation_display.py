"""Validated, packet-associated simulation illustration; never a control input."""
import math

from .domain import finite

SCHEMA = "openrocket.received-display.v1"


def received_display(value, packet_id, tx_time_us):
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return None
    if (not isinstance(packet_id, str) or value.get("packet_id") != packet_id
            or value.get("tx_time_us") != tx_time_us
            or value.get("frame") != "body_to_ENU" or value.get("body_axis") != "+Z"):
        return None
    q = value.get("quaternion_wxyz")
    if (not isinstance(q, list) or len(q) != 4 or not all(finite(v) for v in q)
            or abs(math.hypot(*q) - 1) > .001):
        return None
    result = dict(schema=SCHEMA, packet_id=packet_id, tx_time_us=tx_time_us,
                  frame="body_to_ENU", body_axis="+Z", quaternion_wxyz=list(q))
    for key in ("motor_ignited", "motor_burning", "parachute_deployed"):
        value_flag = value.get(key)
        result[key] = value_flag if type(value_flag) is bool else None
    return result
