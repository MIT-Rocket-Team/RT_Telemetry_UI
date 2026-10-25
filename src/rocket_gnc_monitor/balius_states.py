"""Current Balius states for the main Base workspace.

The operator provisionally assigned consecutive wire codes 0–4. Replace this
table when the flight-computer enum is finalized; timers are not extra states.
Legacy workspaces retain their existing state interpretation.
"""

from collections.abc import Mapping

BALIUS_STATE_NAMES = ("GROUND_TESTING", "PREFLIGHT", "FLIGHT", "APOGEE", "LOW_BATTERY")


def balius_state_name(sample):
    """Resolve the provisional wire state without importing legacy state names."""
    if sample is None:
        return "NO TELEMETRY"
    details = getattr(sample, "details", {})
    if not isinstance(details, Mapping):
        return "UNKNOWN"
    if "state_code" in details:
        code = details["state_code"]
        return BALIUS_STATE_NAMES[code] if type(code) is int and 0 <= code < len(BALIUS_STATE_NAMES) else "UNKNOWN"
    name = str(getattr(sample, "phase", "")).upper().replace(" ", "_").replace("-", "_")
    if name == "PRE_FLIGHT":
        name = "PREFLIGHT"
    return name if name in BALIUS_STATE_NAMES else "UNKNOWN"
