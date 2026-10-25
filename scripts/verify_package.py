"""Exercise the packaged application and its private runtimes with a minimal PATH."""

from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "RocketGNCMonitor-v0a"
STATION_CARDS = {
    "base": {
        "flight3d", "trajectory", "altitude", "attitude", "antenna", "pointing",
        "gnc_rates", "gnc_angles", "actuators", "telemetry", "gps", "servos", "cells",
        "commands", "power", "bms", "recovery", "mission", "sessions", "diagnostics", "network",
    },
    "away": {"antenna", "pointing", "altitude", "attitude", "telemetry", "gps"},
    "video": {"digital", "analog"},
}
LEGACY_PROFILES = {
    "base": dict(station="base", role="telemetry", vehicle="balius", launch_site="custom"),
    "away": dict(station="away1", role="telemetry", vehicle="balius", launch_site="custom"),
    "video": dict(station="away1", role="video", vehicle="balius", launch_site="custom"),
}
EXTRA_PROFILES = {
    "station-away-iris": dict(station="away4", role="video", vehicle="iris", launch_site="custom"),
    "station-base-video-balius": dict(station="base", role="video", vehicle="balius", launch_site="custom"),
    "station-base-video-iris": dict(station="base", role="video", vehicle="iris", launch_site="custom"),
    "station-base-telemetry-iris": dict(station="base", role="telemetry", vehicle="iris", launch_site="custom"),
    "station-away4-telemetry-iris": dict(station="away4", role="telemetry", vehicle="iris", launch_site="custom"),
}
IRIS_DEMO_PROFILE = dict(station="away2", role="video", vehicle="iris", launch_site="custom")
IRIS_BOOSTER_DEMO_PROFILE = dict(station="away4", role="video", vehicle="iris", launch_site="custom")
SETUP_PROFILE = dict(station="base", role="video", vehicle="iris", launch_site="urrg")


def profile_channels(profile):
    return ["digital", "analog", "analog2"] if profile["vehicle"] == "iris" else ["digital", "analog"]


def profile_layout(profile):
    return "video" if profile["role"] == "video" else "base" if profile["station"] == "base" else "away"


def profile_serial_roles(profile):
    return ["telemetry"] if profile["station"] == "base" else ["telemetry", "pointer"]


def profile_iris_boards(profile):
    if profile["vehicle"] != "iris" or profile["role"] != "telemetry":
        return []
    return ["telemetry"] if profile["station"] == "base" else ["downlink"]


def same_inventory(actual, expected):
    return isinstance(actual, list) and len(actual) == len(expected) and set(actual) == set(expected)


def profile_local_channels(profile):
    if profile["station"] == "base" and profile["role"] == "video":
        return ["digital"]
    if profile["vehicle"] == "iris" and profile["station"] != "base":
        return ["digital", "analog2" if profile["station"] == "away4" else "analog"]
    return profile_channels(profile)


def profile_remote_sources(profile):
    return [
        [f"away{i}", channel] for i in range(1, 5)
        for channel in profile_local_channels(dict(profile, station=f"away{i}", role="video"))
    ]


def profile_default_sources(profile):
    remote = profile_remote_sources(profile)
    return {
        channel: ["local", channel] if channel == "digital"
        else next(source for source in remote if source[1] == channel)
        for channel in profile_channels(profile)
    }


def profile_arguments(profile):
    return ["--site", profile["station"], "--role", profile["role"], "--vehicle", profile["vehicle"],
            "--launch-site", profile.get("launch_site", "custom")]


def valid_profile_report(report, profile):
    local = profile_local_channels(profile)
    return report.get("profile") == profile and report.get("local_video_channels") == local


def validate_station_report(report, station, profile=None):
    """Require the packaged workspace's shared state and hardware isolation."""
    profile = profile or LEGACY_PROFILES[station]
    channels = profile_channels(profile)
    base_video = profile["station"] == "base" and profile["role"] == "video"
    local = profile_local_channels(profile)
    serial_roles = profile_serial_roles(profile)
    iris_boards = profile_iris_boards(profile)
    cards = {"video_wall"} if base_video else set(local) if station == "video" else STATION_CARDS[station]
    if (
        not valid_profile_report(report, profile)
        or report.get("station_mode") != station
        or report.get("source_mode") != "LIVE"
        or report.get("shared_controller") is not True
        or report.get("window_count") != (2 if station == "base" else 1)
        or not isinstance(report.get("visible_cards"), list)
        or set(report["visible_cards"]) != cards
        or report.get("hardware_open") is not False
        or report.get("shortcuts_shared") is not True
        or report.get("video_inputs_visible") is not (station == "video")
        or report.get("telemetry_controls_visible") is not (station != "video")
        or report.get("camera_inputs") != local
        or report.get("visible_camera_inputs") != (local if station == "video" else [])
        or not same_inventory(report.get("serial_roles"), serial_roles)
        or not same_inventory(report.get("visible_serial_roles"), serial_roles if station != "video" else [])
        or not same_inventory(report.get("iris_link_boards"), iris_boards)
        or report.get("iris_simulated_targets") != dict.fromkeys(iris_boards)
        or report.get("iris_switch_buttons_enabled") != dict.fromkeys(iris_boards, False)
    ):
        raise SystemExit(f"Packaged {station} station workspace failed")
    wall = report.get("video_wall")
    if not base_video:
        if wall is not None:
            raise SystemExit(f"Packaged {station} station workspace failed: unexpected video wall")
        return
    sources = profile_remote_sources(profile)
    selected = profile_default_sources(profile)
    if (
        not isinstance(wall, dict)
        or wall.get("primary_channels") != channels
        or wall.get("visible_primary_channels") != channels
        or wall.get("thumbnail_sources") != sources
        or wall.get("visible_thumbnail_sources") != sources
        or wall.get("selected_sources") != selected
        or wall.get("remote_available") is not False
    ):
        raise SystemExit(f"Packaged {station} station workspace failed: video wall inventory")


def validate_setup_report(report):
    if (
        report.get("profile") != SETUP_PROFILE
        or report.get("local_channels") != ["digital"]
        or report.get("controller_created") is not False
        or report.get("finish_text") != "Open station"
        or not isinstance(report.get("pages"), list)
        or [page.get("name") for page in report["pages"]] != ["station", "role", "vehicle", "launch_site"]
    ):
        raise SystemExit("Packaged startup wizard failed")


def validate_video_demo(report, profile):
    if (
        not valid_profile_report(report, profile)
        or report.get("mode") != "DEMO"
        or report.get("station_mode") != "video"
        or report.get("hardware_open") is not False
        or report.get("demo_progress_samples", 0) <= 20
        or not isinstance(report.get("video_streams"), dict)
        or set(report["video_streams"]) != set(profile_local_channels(profile))
        or any(v.get("frames", 0) <= 0 or v.get("error") for v in report["video_streams"].values())
    ):
        raise SystemExit("Packaged Video station demo channels failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path)
    parser.add_argument(
        "--model", type=Path, help="Also verify a supplied .ork with the packaged motor library"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "package-verification")
    parser.add_argument("--mac-arch", choices=("arm64", "x86_64"),
                        help="Run every macOS package check through this architecture slice")
    args = parser.parse_args()
    if args.mac_arch and sys.platform != "darwin":
        parser.error("--mac-arch is only supported on macOS")
    package = (
        args.package
        or ROOT / "dist" / (APP_NAME + ".app" if sys.platform == "darwin" else APP_NAME)
    ).resolve()
    executable = (
        package / "Contents" / "MacOS" / APP_NAME
        if sys.platform == "darwin"
        else package / (APP_NAME + ".exe" if os.name == "nt" else APP_NAME)
    )
    contents = package / "Contents" / "Resources" if sys.platform == "darwin" else package / "_internal"
    architecture = args.mac_arch or platform.machine()
    if sys.platform == "darwin" and (package / "Contents" / "Helpers").is_dir():
        helper = package / "Contents" / "Helpers" / f"RocketGNCMonitor-{architecture}.app"
        contents = helper / "Contents" / "Resources"
    launch = [str(executable)]
    if args.mac_arch:
        launch = ["/usr/bin/arch", "-" + args.mac_arch, *launch]
    model = contents / "resources" / "examples" / "simple.ork"
    if not executable.is_file() or not model.is_file():
        raise SystemExit("Package executable/example model missing")
    environment = dict(os.environ)
    environment["PATH"] = (
        (str(Path(os.environ["SystemRoot"]) / "System32") + ";" + os.environ["SystemRoot"])
        if os.name == "nt"
        else "/usr/bin:/bin"
    )
    for key in ("PYTHONPATH", "PYTHONHOME", "JAVA_HOME", "IMAGEIO_FFMPEG_EXE"):
        environment.pop(key, None)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    commands = [
        (
            "station-" + station,
            [
                "--station-smoke",
                "--station",
                station,
                "--data-dir",
                str(output / ("station-" + station)),
            ],
        )
        for station in STATION_CARDS
    ] + [
        (
            "flight",
            [
                "--flight-smoke",
                "--data-dir",
                str(output / "flight"),
                "--screenshot",
                str(output / "flight.png"),
            ],
        ),
        (
            "startup",
            [
                "--startup-smoke",
                "--data-dir",
                str(output / "startup"),
                "--screenshot",
                str(output / "startup.png"),
            ],
        ),
        (
            "demo",
            ["--smoke-test", "--station", "video", "--data-dir", str(output / "demo"),
             "--screenshot", str(output / "window.png")],
        ),
        ("simulation", ["--simulation-smoke", str(model), "--data-dir", str(output / "simulation")]),
        (
            "flight-3d",
            [
                "--flight-3d-smoke",
                str(output / "simulation" / "trajectory.csv"),
                "--data-dir",
                str(output / "flight-3d"),
            ],
        ),
        (
            "virtual-pointer",
            [
                "--virtual-pointer-smoke",
                str(output / "simulation" / "trajectory.csv"),
                "--site", "away1",
                "--data-dir",
                str(output / "virtual-pointer"),
                "--screenshot",
                str(output / "virtual-pointer.png"),
            ],
        ),
    ]
    commands.extend(
        (name, ["--station-smoke", *profile_arguments(profile), "--data-dir", str(output / name)])
        for name, profile in EXTRA_PROFILES.items()
    )
    commands.extend([
        ("setup", ["--setup-smoke", *profile_arguments(SETUP_PROFILE), "--data-dir", str(output / "setup")]),
        ("demo-iris", ["--smoke-test", *profile_arguments(IRIS_DEMO_PROFILE),
                       "--data-dir", str(output / "demo-iris"),
                       "--screenshot", str(output / "demo-iris.png")]),
        ("demo-iris-booster", ["--smoke-test", *profile_arguments(IRIS_BOOSTER_DEMO_PROFILE),
                               "--data-dir", str(output / "demo-iris-booster"),
                               "--screenshot", str(output / "demo-iris-booster.png")]),
    ])
    if args.model:
        commands.append(
            (
                "selected-model",
                [
                    "--simulation-smoke",
                    str(args.model.resolve()),
                    "--data-dir",
                    str(output / "selected-model"),
                ],
            )
        )
    for name, arguments in commands:
        run = subprocess.run(
            [*launch, *arguments], cwd=package, env=environment, capture_output=True, timeout=150
        )
        (output / (name + ".log")).write_bytes(run.stdout + run.stderr)
        if run.returncode:
            raise SystemExit(f"{name} failed ({run.returncode}); see {output}")
    demo = json.loads((output / "demo" / "smoke-report.json").read_text())
    startup = json.loads((output / "startup" / "smoke-report.json").read_text())
    flight = json.loads((output / "flight" / "flight-smoke-report.json").read_text())
    virtual = json.loads((output / "virtual-pointer" / "virtual-pointer-smoke-report.json").read_text())
    scene = json.loads((output / "flight-3d" / "flight-3d-smoke-report.json").read_text())
    iris_demo = json.loads((output / "demo-iris" / "smoke-report.json").read_text())
    iris_booster_demo = json.loads((output / "demo-iris-booster" / "smoke-report.json").read_text())
    setup = json.loads((output / "setup" / "setup-smoke-report.json").read_text())
    validate_setup_report(setup)
    for page in ("station", "role", "vehicle", "launch_site"):
        if not (output / "setup" / f"setup-{page}.png").is_file():
            raise SystemExit(f"Packaged startup wizard {page} screenshot missing")
    stations = {}
    for station in STATION_CARDS:
        folder = output / ("station-" + station)
        report = json.loads((folder / "station-smoke-report.json").read_text())
        validate_station_report(report, station)
        for display in range(1, report["window_count"] + 1):
            if not (folder / f"display-{display}.png").is_file():
                raise SystemExit(f"Packaged {station} station display {display} screenshot missing")
        stations[station] = report
    profile_stations = {}
    for name, profile in EXTRA_PROFILES.items():
        folder = output / name
        report = json.loads((folder / "station-smoke-report.json").read_text())
        validate_station_report(report, profile_layout(profile), profile)
        for display in range(1, report["window_count"] + 1):
            if not (folder / f"display-{display}.png").is_file():
                raise SystemExit(f"Packaged {name} display {display} screenshot missing")
        profile_stations[name] = report
    if (
        scene["hardware_open"]
        or not scene["frames"]["ignition"]["powered"]
        or scene["frames"]["burnout"]["powered"]
        or not scene["frames"]["parachute"]["recovery"]
    ):
        raise SystemExit("Packaged 3D flight events failed")
    if (
        not virtual["virtual_connected"]
        or virtual["physical_ports_open"]
        or virtual["samples"]
        or not virtual["telemetry_locked"]
    ):
        raise SystemExit("Packaged virtual pointer rehearsal failed")
    if (
        not startup["settings"]["visible"]
        or startup["settings"]["shortcut"] != "Ctrl+,"
        or not startup["settings"]["bundled_engine"]
    ):
        raise SystemExit("Packaged Settings window or bundled engine missing")
    if (
        flight["mode"] != "REPLAY"
        or not flight["paused"]
        or flight["hardware_open"]
        or flight["site"] != "URRG"
        or flight["code"] != "18TUN2061530290"
        or not flight["model_exists"]
        or flight["reference_rows"] != 2
        or flight["pointer_location"]["mgrs"] != "18TUN2061530290"
        or flight["pointer_location"]["format"] != "relative"
    ):
        raise SystemExit("Packaged flight-file / URRG round trip failed")
    if startup.get("font_family") != "Lucida Grande" or "Lucida Grande" not in startup.get(
        "bundled_font_families", []
    ):
        raise SystemExit("Packaged Lucida Grande font was not loaded")
    for name, expected in [("mgrs", (41.999997975128, -93)), ("pluscode", (47.3655625, 8.5249375))]:
        actual = startup.get("location_checks", {}).get(name, [])
        if len(actual) != 2 or any(not math.isclose(a, b, abs_tol=1e-7) for a, b in zip(actual, expected)):
            raise SystemExit(f"Packaged {name} conversion failed")
    if (
        startup["mode"] != "LIVE"
        or not startup["controls_locked"]
        or startup["hardware_open"]
        or startup["samples"]
        or any(v["frames"] for v in startup["video_streams"].values())
    ):
        raise SystemExit("Packaged application did not start in locked Live mode")
    simulation = json.loads((output / "simulation" / "simulation-smoke-report.json").read_text())
    validate_video_demo(demo, LEGACY_PROFILES["video"])
    validate_video_demo(iris_demo, IRIS_DEMO_PROFILE)
    validate_video_demo(iris_booster_demo, IRIS_BOOSTER_DEMO_PROFILE)
    demo_manifest = json.loads((contents / "resources" / "demo" / "manifest.json").read_text())
    if demo.get("demo_recording") != dict(station="GS2", **demo_manifest["GS2"]) or not demo.get(
        "latest_demo_row"
    ):
        raise SystemExit("Packaged demo did not play the bundled Zephyrus GS2 recording")
    if iris_demo.get("demo_recording") != dict(station="GS2", **demo_manifest["GS2"]) or not iris_demo.get("latest_demo_row"):
        raise SystemExit("Packaged Iris demo did not play the bundled Zephyrus GS2 recording")
    if iris_booster_demo.get("demo_recording") != dict(station="GS2", **demo_manifest["GS2"]) or not iris_booster_demo.get("latest_demo_row"):
        raise SystemExit("Packaged Iris booster-station demo did not play the bundled Zephyrus GS2 recording")
    # Exercise every receiver log in the actual packaged process, not the source tree.
    receiver_demos = {}
    for station in ("GS1", "GS3"):
        result = subprocess.run(
            [*launch, "--smoke-test", "--station", "video", "--demo-station", station,
             "--data-dir", str(output / station)],
            cwd=package,
            env=environment,
            capture_output=True,
            timeout=60,
        )
        (output / (station + ".log")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise SystemExit(f"Packaged {station} demo failed")
        report = json.loads((output / station / "smoke-report.json").read_text())
        if report.get("demo_recording") != dict(station=station, **demo_manifest[station]):
            raise SystemExit(f"Packaged {station} recording identity mismatch")
        validate_video_demo(report, LEGACY_PROFILES["video"])
        receiver_demos[station] = report
    if simulation["rows"] < 2 or not math.isfinite(simulation["apogee_m"]) or simulation["apogee_m"] <= 0:
        raise SystemExit("Packaged simulation did not produce a usable example trajectory")
    if simulation["manifest"]["name"] != "OpenRocket nominal · A simple model rocket":
        raise SystemExit("Packaged simulation returned a damaged model label; check UTF-8 encoding")
    selected_model = None
    if args.model:
        selected_model = json.loads((output / "selected-model" / "simulation-smoke-report.json").read_text())
        if (
            selected_model["rows"] < 2
            or not math.isfinite(selected_model["apogee_m"])
            or selected_model["apogee_m"] <= 0
        ):
            raise SystemExit("Packaged simulation did not produce a usable trajectory for the supplied model")
    (output / "report.json").write_text(
        json.dumps(
            dict(
                package=str(package),
                architecture=architecture,
                minimal_path=True,
                check_count=len(commands) + len(receiver_demos),
                station_workspaces=stations,
                station_profiles=profile_stations,
                setup=setup,
                startup=startup,
                flight=flight,
                virtual_pointer=virtual,
                flight_3d=scene,
                demo=demo,
                iris_demo=iris_demo,
                iris_booster_demo=iris_booster_demo,
                receiver_demos=receiver_demos,
                simulation=simulation,
                selected_model=selected_model,
            ),
            indent=2,
        )
    )
    print(f"{len(commands) + len(receiver_demos)} packaged checks passed: startup wizard, station profiles, video wall, Sustainer/Booster playback and OpenRocket: {output}")


if __name__ == "__main__":
    main()
