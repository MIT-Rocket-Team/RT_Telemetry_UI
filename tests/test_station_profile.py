import dataclasses
import json
from types import SimpleNamespace

import pytest

from rocket_gnc_monitor.__main__ import argument_parser, resolve_profile
from rocket_gnc_monitor.station_profile import (
    StationProfile, forget_startup_choices, load_profile, load_startup_choices,
    profile_for_layout, save_startup_choices,
)


@pytest.mark.parametrize("station", ["base", "away1", "away2", "away3", "away4"])
@pytest.mark.parametrize("role", ["telemetry", "video"])
@pytest.mark.parametrize("vehicle", ["balius", "iris"])
def test_topology_follows_station_role_and_vehicle(station, role, vehicle):
    profile = StationProfile(station, role, vehicle)
    assert profile.layout == ("video" if role == "video" else "base" if station == "base" else "away")
    assert profile.channels == (("digital", "analog", "analog2") if vehicle == "iris" else ("digital", "analog"))
    expected_local = profile.channels if station == "base" else ("digital", "analog2" if vehicle == "iris" and station == "away4" else "analog")
    assert profile.local_channels == (("digital",) if station == "base" and role == "video" else expected_local)
    assert profile.station_label == ("Launch station" if station == "base" else f"Away station {station[-1]}")
    assert profile.vehicle_label == vehicle.title()
    assert set(profile.channel_labels) == set(profile.channels)
    assert profile.channel_labels["analog"] == ("Sustainer Analog" if vehicle == "iris" else "Analog")
    assert set(profile.away_channels) == {"away1", "away2", "away3", "away4"}
    if vehicle == "iris":
        assert profile.away_channels["away4"] == ("digital", "analog2")
        assert all(profile.away_channels[site] == ("digital", "analog") for site in ("away1", "away2", "away3"))
        assert profile.telemetry_targets == (("Sustainer", "Booster") if station == "base" else
                                             ("Booster",) if station == "away4" else ("Sustainer",))
    else:
        assert profile.telemetry_targets == ("Balius",)


@pytest.mark.parametrize("field,value", [("station", "away5"), ("role", "pilot"), ("vehicle", "Balius"),
                                        ("vehicle", None), ("launch_site", "URRG"), ("launch_site", None)])
def test_profile_rejects_invalid_choices(field, value):
    with pytest.raises(ValueError):
        StationProfile(**{field: value})


def test_profile_is_immutable_and_round_trips_without_mission_changes(tmp_path):
    profile = StationProfile("away3", "video", "iris", "urrg")
    mission = tmp_path / "mission.json"
    mission.write_text('{"name":"Flight A"}')
    profile.save(tmp_path)
    assert StationProfile.load(tmp_path) == profile
    assert json.loads((tmp_path / "station-profile.json").read_text()) == profile.to_dict()
    assert mission.read_text() == '{"name":"Flight A"}'
    assert not list(tmp_path.glob(".station-profile-*"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.station = "base"


def test_saved_profile_before_launch_site_question_keeps_previous_defaults(tmp_path):
    (tmp_path / "station-profile.json").write_text(json.dumps({
        "station": "away4", "role": "video", "vehicle": "iris",
    }))
    assert load_profile(tmp_path) == StationProfile("away4", "video", "iris", "custom")


@pytest.mark.parametrize("contents", ["broken json", "[]", '{"station":"away9"}', '{"role":false}', '{"extra":1}'])
def test_invalid_saved_profile_returns_safe_defaults(tmp_path, contents):
    (tmp_path / "station-profile.json").write_text(contents)
    assert load_profile(tmp_path) == StationProfile()


@pytest.mark.parametrize("layout,station,role", [("launch", "base", "telemetry"), ("base", "base", "telemetry"), ("away", "away1", "telemetry"), ("video", "away1", "video")])
def test_legacy_layout_migration(tmp_path, layout, station, role):
    (tmp_path / "station-layout.json").write_text(json.dumps({"station": layout}))
    assert load_profile(tmp_path) == StationProfile(station, role)
    assert profile_for_layout(layout, StationProfile(vehicle="iris")) == StationProfile(station, role, "iris")


def test_cli_explicit_profile_fields_override_legacy_layout_and_opted_in_defaults(tmp_path):
    save_startup_choices(StationProfile("away4", "video", "iris", "urrg"), tmp_path, True)
    args = SimpleNamespace(station="away", site="base", role="video", vehicle=None)
    assert resolve_profile(args, tmp_path) == StationProfile("base", "video", "iris", "urrg")
    args = SimpleNamespace(station=None, site=None, role=None, vehicle=None)
    assert resolve_profile(args, tmp_path) == StationProfile("away4", "video", "iris", "urrg")
    args.launch_site = "custom"
    assert resolve_profile(args, tmp_path) == StationProfile("away4", "video", "iris", "custom")


def test_startup_ignores_legacy_automatic_preferences(tmp_path):
    StationProfile("away4", "video", "iris", "urrg").save(tmp_path)
    (tmp_path / "station-layout.json").write_text('{"station":"video"}')
    args = argument_parser().parse_args([])
    assert load_startup_choices(tmp_path) == (StationProfile(), False)
    assert resolve_profile(args, tmp_path) == StationProfile()


@pytest.mark.parametrize("contents", ["broken json", "[]", '{"station":"away9"}', '{"extra":1}'])
def test_damaged_opt_in_preferences_do_not_block_startup(tmp_path, contents):
    (tmp_path / "startup-choices.json").write_text(contents)
    assert load_startup_choices(tmp_path) == (StationProfile(), False)


def test_forgetting_only_removes_opt_in_choices(tmp_path):
    retained = {"station-profile.json": "legacy", "station-layout.json": "legacy layout",
                "mission.json": "mission", "settings.json": "settings"}
    for name, contents in retained.items():
        (tmp_path / name).write_text(contents)
    profile = StationProfile("away4", "video", "iris", "urrg")
    save_startup_choices(profile, tmp_path, True)
    assert load_startup_choices(tmp_path) == (profile, True)
    forget_startup_choices(tmp_path)
    forget_startup_choices(tmp_path)
    assert not (tmp_path / "startup-choices.json").exists()
    assert {path.name: path.read_text() for path in tmp_path.iterdir()} == retained


@pytest.mark.parametrize("station", ["launch", "base", "away1", "away2", "away3", "away4"])
def test_cli_accepts_each_station_number_and_launch_site(tmp_path, station):
    args = argument_parser().parse_args([
        "--site", station, "--role", "telemetry", "--vehicle", "balius",
        "--launch-site", "urrg", "--skip-setup",
    ])
    assert args.skip_setup
    assert resolve_profile(args, tmp_path) == StationProfile(station, "telemetry", "balius", "urrg")


@pytest.mark.parametrize("flag,value", [("--site", "away5"), ("--launch-site", "elsewhere")])
def test_cli_rejects_unknown_station_and_launch_site(flag, value):
    with pytest.raises(SystemExit) as result:
        argument_parser().parse_args([flag, value])
    assert result.value.code == 2


def test_launch_identity_accepts_new_name_and_preserves_existing_saved_profiles(tmp_path):
    profile = StationProfile("launch", "telemetry", "iris", "urrg")
    assert profile.station == "base"
    assert profile.station_label == "Launch station"
    assert profile == StationProfile("base", "telemetry", "iris", "urrg")
    save_startup_choices(profile, tmp_path, True)
    assert load_startup_choices(tmp_path) == (profile, True)
    assert profile.to_dict()["station"] == "base"
    args = argument_parser().parse_args(["--station", "launch", "--skip-setup"])
    assert resolve_profile(args, tmp_path) == profile
    help_text = argument_parser().format_help()
    assert "--site {launch,away1,away2,away3,away4}" in help_text
