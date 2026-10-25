import pytest

from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.legacy_sample import decode_row
from rocket_gnc_monitor.rocket_state_badge import (
    RocketStateBadge, STATE_COLORS, UNKNOWN_COLORS, telemetry_state_name,
)


def sample(**kwargs):
    return Sample(t=0, sequence=1, source="LIVE", **kwargs)


@pytest.mark.parametrize("code,name,color", [
    (0, "GROUND_TESTING", "#2dbe70"),
    (1, "PREFLIGHT", "#1d5fd1"),
    (2, "FLIGHT", "#c83232"),
    (3, "POST_APOGEE", "#ed941e"),
    (4, "MAIN", "#ed941e"),
    (5, "END", "#ed941e"),
])
def test_actual_wire_states_and_recovery_colors(qtbot, code, name, color):
    badge = RocketStateBadge()
    qtbot.addWidget(badge)
    badge.update_state(sample(details={"state_code": code}, phase="Ground testing"))
    assert badge.state_name == name
    assert badge.state_label.text() == name
    assert badge.background_color == color
    assert badge.status_label.text() == "LIVE · Telemetry"


@pytest.mark.parametrize("field", ["legacy_values", "legacy_csv"])
@pytest.mark.parametrize("raw,name", [
    ("state.GROUND_TESTING", "GROUND_TESTING"),
    ("state.PRE_FLIGHT", "PREFLIGHT"),
    ("state.FLIGHT", "FLIGHT"),
    ("state.POST_APOGEE", "POST_APOGEE"),
    ("state.MAIN", "MAIN"),
    ("state.END", "END"),
    ("state.FUTURE_STATE", "FUTURE_STATE"),
    ("", "UNKNOWN"), (None, "UNKNOWN"), (False, "UNKNOWN"),
    (0, "UNKNOWN"), ("0", "UNKNOWN"), ([], "UNKNOWN"),
    ("<b>FLIGHT</b>", "UNKNOWN"),
])
def test_raw_recorded_state_overrides_synthetic_ground_testing_code(field, raw, name):
    item = sample(details={field: {"state": raw}, "state_code": 0}, phase="Ground testing")
    assert telemetry_state_name(item) == name


def test_actual_csv_unknown_state_never_appears_as_ground_testing(qtbot):
    item = decode_row({"timestamp": "1", "flight_time": "0", "pktnum": "1",
                       "state": "state.NEW_FIRMWARE_STATE"})
    assert item.details["state_code"] == 0  # Existing import fallback must not be trusted here.
    badge = RocketStateBadge()
    qtbot.addWidget(badge)
    badge.update_state(item, mode="DEMO")
    assert badge.state_name == "NEW_FIRMWARE_STATE"
    assert badge.background_color == UNKNOWN_COLORS[0]
    assert badge.status_label.text() == "DEMO · Telemetry"


@pytest.mark.parametrize("code", [-1, 6, 255, 256, None, True, False, "0", "FLIGHT", 0.0, 2.0, [], {}, float("nan")])
def test_unknown_or_malformed_wire_code_does_not_fall_back_to_phase(code):
    assert telemetry_state_name(sample(details={"state_code": code}, phase="Flight")) == "UNKNOWN"


@pytest.mark.parametrize("details", [None, [], "state", {"legacy_values": None}, {"legacy_csv": "FLIGHT"}])
def test_malformed_details_do_not_appear_as_a_valid_phase(details):
    assert telemetry_state_name(sample(details=details, phase="Flight")) == "UNKNOWN"


@pytest.mark.parametrize("phase,name", [
    ("Ground testing", "GROUND_TESTING"), ("PRE_FLIGHT", "PREFLIGHT"),
    ("Preflight", "PREFLIGHT"), ("Flight", "FLIGHT"),
    ("Post-apogee", "POST_APOGEE"), ("Main", "MAIN"), ("End", "END"),
    ("Boost", "UNKNOWN"), ("Coast", "UNKNOWN"), ("Descent", "UNKNOWN"),
    ("Landed", "UNKNOWN"), (None, "UNKNOWN"), ("Trajectory Flight", "UNKNOWN"),
])
def test_phase_fallback_accepts_only_telemetry_state_names(phase, name):
    assert telemetry_state_name(sample(phase=phase)) == name


def test_stale_state_remains_visible_and_no_sample_resets_black(qtbot):
    badge = RocketStateBadge()
    qtbot.addWidget(badge)
    item = sample(details={"state_code": 3})
    badge.update_state(item)
    badge.update_state(item, fresh=False)
    assert badge.state_name == "POST_APOGEE"
    assert badge.background_color == STATE_COLORS["POST_APOGEE"][0]
    assert badge.status_label.text() == "LIVE · STALE · last telemetry"
    badge.update_state(None, fresh=False)
    assert badge.state_name == "NO TELEMETRY"
    assert badge.background_color == "#000000"
    assert badge.status_label.text() == "LIVE · No telemetry received"


@pytest.mark.parametrize("mode,source,expected", [
    ("DEMO", "LEGACY_CSV", "DEMO"), ("REPLAY", "LIVE", "REPLAY"),
    ("LIVE", "DEMO", "DEMO"), ("LIVE", "REPLAY", "REPLAY"),
    ("LIVE", "LEGACY_CSV", "REPLAY"), ("unknown", "LIVE", "UNKNOWN SOURCE"),
])
def test_source_captions_never_call_recorded_samples_live(qtbot, mode, source, expected):
    badge = RocketStateBadge()
    qtbot.addWidget(badge)
    badge.update_state(Sample(t=0, sequence=1, source=source, details={"state_code": 2}), mode=mode)
    assert badge.status_label.text() == f"{expected} · Telemetry"
