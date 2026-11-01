import pytest

from rocket_gnc_monitor.balius_states import balius_state_name
from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.rocket_state_badge import RocketStateBadge, telemetry_state_name


@pytest.mark.parametrize("code,name,color", [
    (0, "GROUND_TESTING", "#2dbe70"), (1, "PRE_FLIGHT", "#1d5fd1"),
    (2, "FLIGHT", "#c83232"), (3, "APOGEE", "#ed941e"),
    (4, "MAIN", "#ed941e"), (5, "END", "#ed941e"),
    (255, "UNKNOWN", "#000000"), (True, "UNKNOWN", "#000000"),
])
def test_explicit_zephyrus_schema_drives_both_badge_and_base_state(qtbot, code, name, color):
    sample = Sample(t=1, sequence=1, source="NETWORK", details={
        "state_code": code, "state_schema": "zephyrus-v1", "network_source_mode": "simulation"})
    assert balius_state_name(sample) == telemetry_state_name(sample) == name
    badge = RocketStateBadge()
    qtbot.addWidget(badge)
    badge.update_state(sample)
    assert badge.state_name == name and badge.background_color == color
    assert badge.status_label.text() == "SIMULATION · NETWORK · Telemetry"


@pytest.mark.parametrize("code,name", [(1, "PREFLIGHT"), (3, "APOGEE"), (4, "LOW_BATTERY"), (5, "UNKNOWN")])
def test_unmarked_live_balius_keeps_its_existing_state_mapping(code, name):
    assert balius_state_name(Sample(t=0, sequence=1, source="LIVE", details={"state_code": code})) == name
