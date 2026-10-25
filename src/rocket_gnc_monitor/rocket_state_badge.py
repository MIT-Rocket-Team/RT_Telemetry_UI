"""Prominent rocket state, derived only from received or recorded telemetry."""

from collections.abc import Mapping
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from .zephyrus import PHASE_ENUM


STATE_COLORS = {
    "GROUND_TESTING": ("#2dbe70", "#07160e"),
    "PREFLIGHT": ("#1d5fd1", "#ffffff"),
    "FLIGHT": ("#c83232", "#ffffff"),
    "POST_APOGEE": ("#ed941e", "#201306"),
    "MAIN": ("#ed941e", "#201306"),
    "END": ("#ed941e", "#201306"),
}
UNKNOWN_COLORS = ("#000000", "#ffffff")


def _normalized_state(value):
    if not isinstance(value, str):
        return "UNKNOWN"
    value = value.strip()
    if value.lower().startswith("state."):
        value = value[6:]
    value = value.upper().replace("-", "_").replace(" ", "_")
    if value == "PRE_FLIGHT":
        return "PREFLIGHT"
    # Preserve a short, well-formed future state name on black, never markup.
    return value if re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", value) else "UNKNOWN"


def telemetry_state_name(sample):
    """Keep raw recording states authoritative, including unknown values.

    Older CSV imports manufacture state_code=0 for an unrecognized state. It
    must never turn that unknown recorded state into green GROUND_TESTING.
    """
    if sample is None:
        return "NO TELEMETRY"
    details = getattr(sample, "details", {})
    if not isinstance(details, Mapping):
        return "UNKNOWN"
    for field in ("legacy_values", "legacy_csv"):
        if field in details:
            values = details[field]
            if not isinstance(values, Mapping):
                return "UNKNOWN"
            if "state" in values:
                return _normalized_state(values["state"])
    if "state_code" in details:
        code = details["state_code"]
        if type(code) is int and 0 <= code < len(PHASE_ENUM):
            return _normalized_state(PHASE_ENUM[code])
        return "UNKNOWN"
    # This fallback is for telemetry samples without the wire/recording detail.
    # Flight trajectory names such as Boost, Coast, Descent are not states.
    phase = _normalized_state(getattr(sample, "phase", None))
    return phase if phase in STATE_COLORS else "UNKNOWN"


class RocketStateBadge(QWidget):
    """A state-machine indication, never a connection or command-ready signal."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rocketStateBadge")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(330)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(1)
        self.title_label = QLabel("ROCKET STATE")
        self.title_label.setObjectName("stateHeading")
        self.state_label = QLabel()
        self.state_label.setObjectName("stateName")
        self.status_label = QLabel()
        self.status_label.setObjectName("stateSource")
        for label in (self.title_label, self.state_label, self.status_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(label)
        self.state_name = "NO TELEMETRY"
        self.background_color = None
        self.update_state(None)

    def update_state(self, sample, mode="LIVE", fresh=True):
        self.state_name = telemetry_state_name(sample)
        background, foreground = STATE_COLORS.get(self.state_name, UNKNOWN_COLORS)
        if background != self.background_color:
            self.background_color = background
            self.setStyleSheet(
                f"QWidget#rocketStateBadge {{ background: {background}; border: 1px solid #526074; border-radius: 9px; }}"
                f"QWidget#rocketStateBadge QLabel {{ background: transparent; color: {foreground}; border: none; }}"
                "QLabel#stateHeading { font-size: 10px; font-weight: 600; letter-spacing: 1px; }"
                "QLabel#stateName { font-size: 28px; font-weight: 700; }"
                "QLabel#stateSource { font-size: 11px; }"
            )
        self.state_label.setText(self.state_name)
        source = mode.upper() if isinstance(mode, str) else "UNKNOWN SOURCE"
        if source not in {"LIVE", "DEMO", "REPLAY"}:
            source = "UNKNOWN SOURCE"
        recorded_source = getattr(sample, "source", "")
        if source == "LIVE" and isinstance(recorded_source, str) and recorded_source in {"DEMO", "REPLAY", "LEGACY_CSV"}:
            source = "REPLAY" if recorded_source == "LEGACY_CSV" else recorded_source
        status = ("No telemetry received" if sample is None else
                  "Telemetry" if fresh else "STALE · last telemetry")
        self.status_label.setText(f"{source} · {status}")
        self.setAccessibleName(f"Rocket state: {self.state_name}")
        self.setAccessibleDescription(self.status_label.text())
        self.setToolTip("Rocket state from telemetry. This does not indicate command readiness.\n"
                        + self.status_label.text())
