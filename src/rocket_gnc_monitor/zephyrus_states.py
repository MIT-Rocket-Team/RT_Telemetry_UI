"""Zephyrus wire codes, matching OpenRocket's RTRocketState / FC definitions.

These are separate from provisional live Balius codes. State is always decoded
from received telemetry, never inferred by a receiving station from altitude.
"""

ZEPHYRUS_STATE_SCHEMA = "zephyrus-v1"
ZEPHYRUS_STATE_NAMES = ("GROUND_TESTING", "PRE_FLIGHT", "FLIGHT", "APOGEE", "MAIN", "END")


def zephyrus_state_name(code):
    return ZEPHYRUS_STATE_NAMES[code] if type(code) is int and 0 <= code < len(ZEPHYRUS_STATE_NAMES) else None
