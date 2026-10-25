import importlib.util
from pathlib import Path
import pytest


def test_windows_arm_host_with_x64_python_selects_x64_runtime(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/build.py"
    spec = importlib.util.spec_from_file_location("rocket_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(module.platform, "machine", lambda: "ARM64")
    monkeypatch.setattr(module.sysconfig, "get_platform", lambda: "win-amd64")
    assert module.process_architecture() == "x64"
    monkeypatch.setattr(module.sysconfig, "get_platform", lambda: "win-arm64")
    with pytest.raises(SystemExit, match="x86 Python"):
        module.process_architecture()


@pytest.fixture
def builder(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/build.py"
    spec = importlib.util.spec_from_file_location("rocket_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "VENDOR", tmp_path / "vendor")
    (tmp_path / "simulation_bridge/src").mkdir(parents=True)
    (tmp_path / "simulation_bridge/src/RocketBridge.java").write_text("test bridge")
    return module


def jar_fixture(path):
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        for name in (
            "info/openrocket/core/document/OpenRocketDocument.class",
            "info/openrocket/core/startup/Application.class",
            "com/google/inject/Guice.class",
        ):
            archive.writestr(name, b"test")


def release_fixture(builder, monkeypatch, digest=None, tag="v6.2"):
    import io
    import json

    source = builder.ROOT / "release.jar"
    jar_fixture(source)
    checksum = digest or "sha256:" + builder.sha(source)
    metadata = dict(
        tag_name=tag,
        draft=False,
        prerelease=False,
        assets=[
            dict(
                name=f"OpenRocket-MIT-{tag}.jar",
                size=source.stat().st_size,
                digest=checksum,
                browser_download_url=f"https://github.com/{builder.RELEASE_REPOSITORY}/releases/download/{tag}/OpenRocket-MIT-{tag}.jar",
            )
        ],
    )
    requests = []

    def urlopen(request, **kwargs):
        requests.append(request.full_url)
        return io.BytesIO(json.dumps(metadata).encode())

    monkeypatch.setattr(builder.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(builder, "download", lambda url, path: path.write_bytes(source.read_bytes()))
    return requests, source


def fake_compile(*args):
    if args[0] == "jar":
        Path(args[args.index("--file") + 1]).write_bytes(b"compiled bridge")


def test_download_latest_resolves_named_jar_and_records_checksum(builder, monkeypatch):
    import json

    requests, source = release_fixture(builder, monkeypatch, tag="v7.12")
    monkeypatch.setattr(builder, "run", fake_compile)
    builder.bridge(release="latest")
    assert requests == [f"https://api.github.com/repos/{builder.RELEASE_REPOSITORY}/releases/latest"]
    assert (builder.VENDOR / "openrocket.jar").read_bytes() == source.read_bytes()
    manifest = json.loads((builder.VENDOR / "engine.json").read_text())
    assert manifest["source"]["release_tag"] == "v7.12"
    assert manifest["source"]["checksum_verified"]
    assert manifest["engine_sha256"] == builder.sha(source)


def test_download_pinned_release_and_local_path_with_spaces(builder, monkeypatch):
    import json

    requests, source = release_fixture(builder, monkeypatch)
    monkeypatch.setattr(builder, "run", fake_compile)
    builder.bridge(release="v6.2")
    assert requests[0].endswith("/releases/tags/v6.2")
    local = builder.ROOT / "My OpenRocket.jar"
    local.write_bytes(source.read_bytes())
    builder.bridge(engine=str(local))
    manifest = json.loads((builder.VENDOR / "engine.json").read_text())
    assert manifest["source"] == dict(kind="local_file", path=str(local))
    assert (builder.VENDOR / "openrocket.jar").read_bytes() == local.read_bytes()


@pytest.mark.parametrize("failure", ["checksum", "compile"])
def test_failed_engine_update_preserves_staged_bundle(builder, monkeypatch, failure):
    import subprocess

    builder.VENDOR.mkdir()
    for name in ("openrocket.jar", "rocket-bridge.jar", "engine.json"):
        (builder.VENDOR / name).write_bytes(b"original")
    release_fixture(builder, monkeypatch, digest="sha256:" + "0" * 64 if failure == "checksum" else None)

    def failed_compile(*args):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(builder, "run", failed_compile)
    with pytest.raises((SystemExit, subprocess.CalledProcessError)):
        builder.bridge(release="latest")
    for name in ("openrocket.jar", "rocket-bridge.jar", "engine.json"):
        assert (builder.VENDOR / name).read_bytes() == b"original"
    assert list((builder.ROOT / "build").iterdir()) == []


def test_mutually_exclusive_engine_sources_and_missing_release_asset(builder, monkeypatch):
    with pytest.raises(SystemExit, match="not both"):
        builder.bridge(engine="engine.jar", release="latest")
    import io

    monkeypatch.setattr(
        builder.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b'{"tag_name":"v6.2","assets":[]}')
    )
    with pytest.raises(SystemExit, match="exactly one OpenRocket-MIT-v6.2.jar"):
        builder.release_asset("latest")


def test_package_forwards_engine_options_before_freezing(builder, monkeypatch):
    calls = []

    def stop_after_bridge(engine, release):
        calls.append((engine, release))
        raise RuntimeError("stop before packaging")

    monkeypatch.setattr(builder, "bridge", stop_after_bridge)
    with pytest.raises(RuntimeError, match="stop before packaging"):
        builder.package("", "", "latest")
    assert calls == [("", "latest")]


@pytest.fixture
def verifier():
    path = Path(__file__).resolve().parents[1] / "scripts/verify_package.py"
    spec = importlib.util.spec_from_file_location("rocket_verify_package", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def station_report(verifier, station, profile=None):
    profile = profile or verifier.LEGACY_PROFILES[station]
    channels = verifier.profile_channels(profile)
    base_video = profile["station"] == "base" and profile["role"] == "video"
    local = verifier.profile_local_channels(profile)
    serial_roles = verifier.profile_serial_roles(profile)
    iris_boards = verifier.profile_iris_boards(profile)
    report = dict(
        profile=profile,
        local_video_channels=local,
        camera_inputs=local,
        visible_camera_inputs=local if station == "video" else [],
        station_mode=station,
        source_mode="LIVE",
        shared_controller=True,
        window_count=2 if station == "base" else 1,
        visible_cards=["video_wall"] if base_video else local if station == "video" else sorted(verifier.STATION_CARDS[station]),
        hardware_open=False,
        shortcuts_shared=True,
        video_inputs_visible=station == "video",
        telemetry_controls_visible=station != "video",
        video_wall=None,
        serial_roles=serial_roles,
        visible_serial_roles=serial_roles if station != "video" else [],
        iris_link_boards=iris_boards,
        iris_simulated_targets=dict.fromkeys(iris_boards),
        iris_switch_buttons_enabled=dict.fromkeys(iris_boards, False),
    )
    if base_video:
        sources = verifier.profile_remote_sources(profile)
        report["video_wall"] = dict(
            primary_channels=channels,
            visible_primary_channels=channels,
            thumbnail_sources=sources,
            visible_thumbnail_sources=sources,
            selected_sources=verifier.profile_default_sources(profile),
            remote_available=False,
        )
    return report


@pytest.mark.parametrize("station", ["base", "away", "video"])
def test_package_station_verification_rejects_missing_windows_and_open_hardware(verifier, station):
    report = station_report(verifier, station)
    verifier.validate_station_report(report, station)
    for field, invalid in (
        ("window_count", 0),
        ("hardware_open", True),
        ("shared_controller", False),
        ("source_mode", "DEMO"),
        ("shortcuts_shared", False),
        ("visible_cards", []),
        ("visible_cards", [*report["visible_cards"], "unexpected-panel"]),
        ("video_inputs_visible", station != "video"),
        ("telemetry_controls_visible", station == "video"),
        ("profile", dict(station="away4", role="video", vehicle="iris")),
        ("local_video_channels", ["digital"]),
        ("camera_inputs", ["digital", "digital"]),
        ("visible_camera_inputs", [] if station == "video" else ["digital"]),
        ("serial_roles", ["telemetry", "pointer"] if station == "base" else ["telemetry", "uplink", "pointer"]),
        ("visible_serial_roles", [] if station != "video" else ["telemetry"]),
    ):
        with pytest.raises(SystemExit, match=f"Packaged {station} station workspace failed"):
            verifier.validate_station_report(dict(report, **{field: invalid}), station)


@pytest.mark.parametrize("site,boards,serial_roles", [
    ("base", ["telemetry"], ["telemetry"]),
    ("away4", ["downlink"], ["telemetry", "pointer"]),
])
def test_package_iris_telemetry_requires_station_specific_boards_and_inactive_placeholders(verifier, site, boards, serial_roles):
    profile = dict(station=site, role="telemetry", vehicle="iris")
    layout = verifier.profile_layout(profile)
    report = station_report(verifier, layout, profile)
    assert report["serial_roles"] == serial_roles
    assert report["iris_link_boards"] == boards
    verifier.validate_station_report(report, layout, profile)
    for field, invalid in (
        ("serial_roles", ["telemetry", "pointer"] if site == "base" else ["telemetry", "uplink", "pointer"]),
        ("visible_serial_roles", ["telemetry", "pointer"] if site == "base" else ["telemetry", "uplink", "pointer"]),
        ("iris_link_boards", ["downlink"] if site == "base" else ["downlink", "uplink"]),
        ("iris_simulated_targets", dict.fromkeys(boards, "sustainer")),
        ("iris_switch_buttons_enabled", dict.fromkeys(boards, True)),
    ):
        with pytest.raises(SystemExit, match="station workspace failed"):
            verifier.validate_station_report(dict(report, **{field: invalid}), layout, profile)


@pytest.mark.parametrize("vehicle,count", [("balius", 8), ("iris", 8)])
def test_package_base_video_wall_requires_exact_visible_channels_and_local_digital(verifier, vehicle, count):
    import copy

    profile = dict(station="base", role="video", vehicle=vehicle)
    report = station_report(verifier, "video", profile)
    verifier.validate_station_report(report, "video", profile)
    assert len(report["video_wall"]["thumbnail_sources"]) == count
    if vehicle == "iris":
        assert report["video_wall"]["selected_sources"]["analog2"] == ["away4", "analog2"]
        assert ["away4", "analog"] not in report["video_wall"]["thumbnail_sources"]
        assert ["away1", "analog2"] not in report["video_wall"]["thumbnail_sources"]
    with pytest.raises(SystemExit, match="station workspace failed"):
        verifier.validate_station_report(dict(report, local_video_channels=["digital", "analog"]), "video", profile)
    for field, invalid in (
        ("primary_channels", ["digital"]),
        ("visible_primary_channels", []),
        ("thumbnail_sources", report["video_wall"]["thumbnail_sources"][:-1]),
        ("visible_thumbnail_sources", report["video_wall"]["thumbnail_sources"][:-1]),
        ("selected_sources", {"digital": ["local", "analog"]}),
        ("remote_available", True),
    ):
        damaged = copy.deepcopy(report)
        damaged["video_wall"][field] = invalid
        with pytest.raises(SystemExit, match="video wall inventory"):
            verifier.validate_station_report(damaged, "video", profile)


def test_package_iris_away4_requires_booster_analog_camera_controls(verifier):
    profile = verifier.EXTRA_PROFILES["station-away-iris"]
    report = station_report(verifier, "video", profile)
    verifier.validate_station_report(report, "video", profile)
    for field in ("local_video_channels", "camera_inputs", "visible_camera_inputs", "visible_cards"):
        with pytest.raises(SystemExit, match="station workspace failed"):
            verifier.validate_station_report(dict(report, **{field: ["digital", "analog"]}), "video", profile)


@pytest.mark.parametrize("station,analog", [("away2", "analog"), ("away4", "analog2")])
def test_package_iris_demo_rejects_a_missing_failed_or_wrong_analog_receiver(verifier, station, analog):
    profile = dict(station=station, role="video", vehicle="iris")
    report = dict(
        profile=profile, local_video_channels=["digital", analog],
        mode="DEMO", station_mode="video", hardware_open=False, demo_progress_samples=30,
        video_streams={channel: dict(frames=60, error="") for channel in ("digital", analog)},
    )
    verifier.validate_video_demo(report, profile)
    for streams in (
        {key: value for key, value in report["video_streams"].items() if key != analog},
        dict(report["video_streams"], **{analog: dict(frames=0, error="")}),
        dict(report["video_streams"], **{analog: dict(frames=60, error="Decoder failed")}),
        {"digital": dict(frames=60, error=""), "analog2" if analog == "analog" else "analog": dict(frames=60, error="")},
    ):
        with pytest.raises(SystemExit, match="demo channels failed"):
            verifier.validate_video_demo(dict(report, video_streams=streams), profile)


def test_package_setup_requires_all_pages_and_no_controller(verifier):
    report = dict(
        profile=verifier.SETUP_PROFILE, local_channels=["digital"], controller_created=False,
        finish_text="Open station", pages=[dict(name=name) for name in ("station", "role", "vehicle", "launch_site")],
    )
    verifier.validate_setup_report(report)
    for field, invalid in (("controller_created", True), ("pages", report["pages"][:2]), ("local_channels", ["digital", "analog"])):
        with pytest.raises(SystemExit, match="startup wizard failed"):
            verifier.validate_setup_report(dict(report, **{field: invalid}))
