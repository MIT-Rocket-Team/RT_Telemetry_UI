from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys


def resolve_profile(args, data_dir):
    """Apply explicit CLI choices to defaults the operator opted to remember."""
    from dataclasses import replace
    from .station_profile import load_startup_choices, profile_for_layout

    profile, _ = load_startup_choices(data_dir)
    if args.station:
        profile = profile_for_layout(args.station, profile)
    changes = {
        name: value for name, value in
        (("station", args.site), ("role", args.role), ("vehicle", args.vehicle),
         ("launch_site", getattr(args, "launch_site", None)))
        if value is not None
    }
    return replace(profile, **changes)


def station_board_report(window):
    """Capture real board controls without inferring a connected hardware target."""
    panel = window.iris_links
    return dict(
        serial_roles=list(window.controller.workers),
        visible_serial_roles=[role for role, (selector, _, _) in window.port_widgets.items() if selector.isVisible()],
        iris_link_boards=list(panel.selectors) if panel is not None else [],
        iris_hardware_targets=dict.fromkeys(panel.selectors) if panel is not None else {},
        iris_switch_buttons_enabled={board: button.isEnabled() for board, button in panel.buttons.items()} if panel is not None else {},
        launch_station=window.launch_station,
        selected_away_pointer=window.controller.selected_away_station,
        away_pointer_choices=[window.launch_links.selector.itemData(i) for i in range(1, window.launch_links.selector.count())]
        if window.launch_links else [],
        local_pointer_controls_visible=window.virtual_connect.isVisible() or window.point_button.isVisible(),
        wifi_selector_visible=window.wifi_panel is not None and window.wifi_panel.isVisible(),
        launch_uplink_switch=dict(
            visible=window.launch_uplink is not None and window.launch_uplink.isVisible(),
            hardware_state=None,
            switch_enabled=window.launch_uplink.toggle.isEnabled() if window.launch_uplink else False,
        ),
        rocket_state_badge=dict(
            visible=window.rocket_state_badge.isVisible(),
            state=window.rocket_state_badge.state_name,
            color=window.rocket_state_badge.background_color,
            source=window.rocket_state_badge.status_label.text(),
        ),
        rocket_pose=dict(
            renderer=type(window.attitude.view).__name__,
            visible=window.attitude.isVisible(),
            source=window.attitude.source.currentData(),
            azimuth_slider_visible=window.attitude.azimuth.isVisible(),
            camera_azimuth=window.attitude.view.azimuth,
            camera_elevation=window.attitude.view.elevation,
            effects=list(window.attitude.view.effect_text),
        ),
    )


def argument_parser():
    parser = argparse.ArgumentParser(
        description="Rocket GNC Monitor",
        epilog=("Station, role, vehicle and launch-site options preselect the startup wizard. "
                "Add --skip-setup to open directly. For example:\n"
                "  %(prog)s --site away3 --role telemetry --vehicle iris --launch-site urrg --skip-setup"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--mission-id", help="Shared station-network mission UUID; never inferred from the mission name")
    parser.add_argument("--network-port", type=int, help="Local HTTP/WebSocket port (default 8766)")
    parser.add_argument("--network-bind", help="Local network bind address (default 0.0.0.0)")
    parser.add_argument("--peer", action="append", help="Manual station HTTP endpoint; repeat for multiple peers")
    parser.add_argument("--no-network", action="store_true", help="Start without the station network service")
    parser.add_argument("--no-discovery", action="store_true", help="Disable multicast discovery; use manual peers")
    parser.add_argument("--accept-network-replay", action="store_true", help="Explicitly allow replay sources for network testing")
    parser.add_argument(
        "--station", choices=["launch", "launch-v2", "base", "away", "video"], metavar="{launch,launch-v2,away,video}",
        help="Base station workspace: launch or base uses no tabs; launch-v2 uses tabs; away/video keep their existing roles",
    )
    parser.add_argument("--site", choices=["launch", "base", "away1", "away2", "away3", "away4"],
                        metavar="{launch,away1,away2,away3,away4}",
                        help="Base station or numbered away station (1–4); launch and base name the same site")
    parser.add_argument("--role", choices=["telemetry", "video"], help="Computer's display and control role")
    parser.add_argument("--vehicle", choices=["balius", "iris"], help="Rocket and default mission name")
    parser.add_argument("--launch-site", choices=["urrg", "custom"],
                        help="URRG sets the launch pad and selected station's coordinates automatically")
    parser.add_argument("--skip-setup", action="store_true", help="Open directly with defaults and explicit station choices")
    parser.add_argument("--remember-setup", action=argparse.BooleanOptionalAction, default=None,
                        help="Remember (or forget) startup choices; otherwise use the wizard checkbox")
    startup = parser.add_mutually_exclusive_group()
    startup.add_argument("--replay-smoke", action="store_true", help="Verify file replay and recorded video inputs, then exit")
    startup.add_argument("--startup-smoke", action="store_true", help="Verify locked Live startup and exit")
    startup.add_argument("--station-smoke", action="store_true", help="Verify station workspace layout and exit")
    startup.add_argument("--setup-smoke", action="store_true", help="Render the startup wizard without opening instruments")
    startup.add_argument(
        "--virtual-pointer-smoke",
        type=Path,
        help="Rehearse a georeferenced trajectory with the virtual pointer and exit",
    )
    startup.add_argument(
        "--flight-3d-smoke", type=Path, help="Verify 3D attitude, ignition and recovery playback"
    )
    startup.add_argument(
        "--flight-smoke", action="store_true", help="Verify portable flight files and the URRG preset"
    )
    parser.add_argument(
        "--screenshot", type=Path, help="Save a native window rendering during the smoke test"
    )
    parser.add_argument(
        "--simulation-smoke",
        type=Path,
        help="Test the private engine using a model and synthetic launch coordinates",
    )
    parser.add_argument("--mission-manifest", type=Path, help="Private OpenRocket Mission connection manifest")
    return parser


def verification_session(directory, mission, channels):
    """Create an explicit on-disk test fixture, then use ordinary replay input.

    This is invoked only by the packaging verification flag. No generated stream
    type or synthetic telemetry mode is registered with the monitor.
    """
    import csv
    import shutil
    import subprocess
    from .domain import Sample, write_json
    from .media import ffmpeg, popen_options
    from .recording import SessionRecorder

    directory.mkdir(parents=True, exist_ok=True)
    video = directory / "camera-fixture.mp4"
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=960x540:rate=30", "-t", "6", "-c:v", "mpeg4",
                    "-q:v", "4", str(video)], check=True, **popen_options())
    recorder = SessionRecorder(directory, mission, "LIVE")
    try:
        for channel in channels:
            folder = recorder.path / f"video_{channel}"
            folder.mkdir()
            shutil.copyfile(video, folder / "segment.mp4")
            with (folder / "segments.csv").open("w", newline="") as handle:
                csv.writer(handle).writerow(["segment.mp4", 0, 6])
            recorder.put("event", {"name": "Video recording started", "data": {
                "directory": folder.name, "kind": "file", "stream": channel}}, recorder.start)
        for index in range(61):
            recorder.sample(Sample(t=index / 10, sequence=index, source="LIVE", altitude=float(index),
                                   received=recorder.start + index / 10, phase="PREFLIGHT",
                                   details={"fixture": "Package replay verification"}))
    finally:
        recorder.close()
    if recorder.error:
        raise RuntimeError(recorder.error)
    manifest_path = recorder.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(duration=6, verification_fixture=True, raw_packets_available=False)
    write_json(manifest_path, manifest)
    video.unlink()
    return recorder.path


def main():
    args = argument_parser().parse_args()
    if args.mission_manifest:
        from .mission_application import main as mission_main
        return mission_main(args.mission_manifest)
    if args.simulation_smoke:
        from .domain import Mission, write_json
        from .trajectory import SimulationJob

        directory = args.data_dir or Path.cwd() / "simulation-smoke"
        profile = resolve_profile(args, directory)
        trajectory = SimulationJob(
            Mission(site_configured=True, model=str(args.simulation_smoke)), directory
        ).run()
        write_json(
            directory / "simulation-smoke-report.json",
            dict(
                profile=profile.to_dict(), local_channels=list(profile.local_channels),
                rows=len(trajectory.points),
                apogee_m=float(trajectory.points[:, 3].max()),
                manifest=trajectory.manifest,
            ),
        )
        return 0
    from PySide6.QtCore import QStandardPaths, QTimer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontInfo, QFontDatabase
    from .fonts import configure_fonts

    app = QApplication(sys.argv[:1])
    font_id = configure_fonts(app)
    app.setApplicationName("RocketGNCMonitor-v0a")
    app.setOrganizationName("MITRocketTeam")
    data = args.data_dir or Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    )
    profile = resolve_profile(args, data)
    from .station_profile import load_startup_choices, save_startup_choices

    _, remember_choices = load_startup_choices(data)
    if args.remember_setup is not None:
        remember_choices = args.remember_setup
    smoke = any((
        args.replay_smoke, args.startup_smoke, args.station_smoke, args.virtual_pointer_smoke,
        args.flight_3d_smoke, args.flight_smoke, args.setup_smoke,
    ))
    if args.setup_smoke or not (args.skip_setup or smoke):
        from .startup_wizard import StartupWizard

        wizard = StartupWizard(profile, remember_choices=remember_choices)
        if args.setup_smoke:
            data.mkdir(parents=True, exist_ok=True)
            pages = []

            def setup_check(index=0):
                try:
                    names = ("station", "role", "vehicle", "launch_site")
                    name = names[index]
                    app.processEvents()
                    wizard.grab().save(str(data / f"setup-{name}.png"))
                    pages.append(dict(name=name, title=wizard.currentPage().title()))
                    if index < len(names) - 1:
                        wizard.next()
                        QTimer.singleShot(150, lambda: setup_check(index + 1))
                        return
                    if args.screenshot:
                        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                        wizard.grab().save(str(args.screenshot))
                    report = dict(
                        profile=wizard.profile.to_dict(), local_channels=list(wizard.profile.local_channels),
                        pages=pages, controller_created=False,
                        mission_name=f"{wizard.profile.vehicle_label} Launch",
                        summary=wizard.summary.text(),
                        remember_choices=wizard.remember_choices,
                        finish_text=wizard.button(wizard.WizardButton.FinishButton).text(),
                    )
                    (data / "setup-smoke-report.json").write_text(json.dumps(report, indent=2))
                    wizard.reject()
                    app.exit(0)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    wizard.reject()
                    app.exit(1)

            wizard.show()
            QTimer.singleShot(500, setup_check)
            return app.exec()
        if wizard.exec() != wizard.DialogCode.Accepted:
            return 0
        profile = wizard.profile
        remember_choices = wizard.remember_choices
    preference_error = None
    if not smoke and (not args.skip_setup or args.remember_setup is not None):
        try:
            save_startup_choices(profile, data, remember_choices)
        except OSError as exc:
            preference_error = f"Startup preference could not be updated: {exc}"
    # Do not instantiate an instrument owner, controller or device workers until
    # setup has been accepted (or explicitly bypassed for automation).
    from .station_workspace import StationWindow as MainWindow

    network_config = None
    if not smoke and not args.no_network:
        from .network_bridge import network_configuration
        try:
            network_config = network_configuration(data, mission_id=args.mission_id,
                network_port=args.network_port, network_bind_host=args.network_bind,
                peers=args.peer, network_discovery=False if args.no_discovery else None,
                accept_replay=args.accept_network_replay)
        except ValueError as exc:
            argument_parser().error(str(exc))
    window = MainWindow(data, profile=profile, auto_place=not args.station_smoke, network_config=network_config)
    if preference_error:
        window.statusBar().showMessage(preference_error, 12000)
    profile_fields = dict(
        profile=profile.to_dict(), local_channels=list(profile.local_channels),
        local_video_channels=list(window.controller.video_streams),
    )
    if smoke:
        from dataclasses import asdict

        profile_fields["startup_mission"] = asdict(window.controller.mission)
    window.show()
    if args.station_smoke:
        def station_check():
            try:
                window.resize(1920, 1020)
                window.companion.resize(1920, 1020)
                app.processEvents()
                window.grab().save(str(data / "display-1.png"))
                if window.companion.isVisible():
                    window.companion.grab().save(str(data / "display-2.png"))
                wall = window.video_wall
                wall_report = None if wall is None else dict(
                    primary_channels=list(wall.primary_tiles),
                    thumbnail_sources=[list(key) for key in wall.thumbnails],
                    visible_primary_channels=[key for key, tile in wall.primary_tiles.items() if tile.isVisible()],
                    visible_thumbnail_sources=[list(key) for key, tile in wall.thumbnails.items() if tile.isVisible()],
                    selected_sources={key: list(source) for key, source in wall.selected_sources.items()},
                    remote_available=any(feed.available for (station, _), feed in wall.feeds.items() if station != "local"),
                )
                report = dict(
                    **profile_fields,
                    **station_board_report(window),
                    station_mode=window.station_mode, source_mode=window.controller.mode,
                    shared_controller=window.controller is window.companion.controller,
                    window_count=sum(w.isVisible() for w in (window, window.companion)),
                    visible_cards=[key for key, card in window.cards.items() if card.isVisible()],
                    hardware_open=any(window.controller.workers.values()),
                    shortcuts_shared=all(a in window.companion.actions() for a in window.legacy_actions.values()),
                    video_inputs_visible=all(
                        window.video_widgets[stream]["camera"].isVisible()
                        for stream in window.video_widgets
                    ),
                    camera_inputs=list(window.video_widgets),
                    visible_camera_inputs=[
                        stream for stream, widgets in window.video_widgets.items()
                        if widgets["camera"].isVisible()
                    ],
                    video_wall=wall_report,
                    telemetry_controls_visible=any(
                        widget.isVisible() for widget in
                        (window.usb_strip, window.poll_button, window.health, *window.metrics.values())
                    ),
                )
                (data / "station-smoke-report.json").write_text(json.dumps(report, indent=2))
                window.close()
            except Exception:
                import traceback
                traceback.print_exc()
                window.close()
                app.exit(1)
        QTimer.singleShot(700, station_check)
        return app.exec()
    if args.flight_3d_smoke:
        from .trajectory import Trajectory

        c = window.controller
        c.timer.stop()
        c.reference = Trajectory.load(args.flight_3d_smoke)
        assert c.reference.motion is not None
        window.open_flight_3d()
        dialog = window.flight_3d_dialog
        dialog.timer.stop()
        burns = dialog.scene.burns
        deployments = dialog.scene.deployments
        assert burns and deployments
        snapshots = [
            ("ignition", (burns[0][0] + burns[0][1]) / 2),
            ("burnout", burns[0][1]),
            ("parachute", deployments[0]["time"] + 1),
        ]
        frames = {}
        for name, stamp in snapshots:
            dialog.playback.seek(stamp)
            dialog.last_labels = 0
            dialog.tick()
            app.processEvents()
            frame = dialog.view.frame
            frames[name] = dict(
                time=frame.time,
                powered=frame.powered,
                recovery=frame.recovery,
                attitude=frame.attitude,
                state=frame.state,
            )
            dialog.grab().save(str(data / (name + ".png")))
        assert frames["ignition"]["powered"] and not frames["burnout"]["powered"]
        assert frames["parachute"]["recovery"]
        assert all("Simulation attitude" in frame["attitude"] for frame in frames.values())
        report = dict(
            **profile_fields,
            frames=frames, motion_rows=len(c.reference.motion), hardware_open=any(c.workers.values())
        )
        (data / "flight-3d-smoke-report.json").write_text(json.dumps(report, indent=2))
        window.close()
        return 0
    if args.virtual_pointer_smoke:
        from .trajectory import Trajectory
        from .virtual_pointer import VIRTUAL_POINTER_DEVICE

        c = window.controller
        c.timer.stop()
        c.reference = Trajectory.load(args.virtual_pointer_smoke)
        lat, lon, altitude = c.reference.manifest["origin"]
        c.mission.pointer_latitude = lat - 0.001
        c.mission.pointer_longitude = lon - 0.001
        c.mission.pointer_altitude = altitude
        c.mission.pointer_site_configured = True
        c.connect("pointer", VIRTUAL_POINTER_DEVICE)
        c.tick()
        c.manual_point(90, 30)
        c.tick()
        c.virtual_pointer.advance(2)
        assert c.virtual_pointer.pose == (90, 30)
        c.start_virtual_trajectory()
        c.advance_virtual_flight(1, 1e20)
        assert c.virtual_flight.time > c.virtual_flight.start
        c.seek_virtual_trajectory((c.virtual_flight.start + c.virtual_flight.end) / 2)
        c.tick()
        c.virtual_pointer.advance(4)
        window.pages.setCurrentIndex(2)
        window.last_ui = 0
        window.refresh()
        app.processEvents()
        report = dict(
            **profile_fields,
            mode=c.mode,
            virtual_connected=bool(c.virtual_pointer),
            physical_ports_open=any(
                worker and worker is not c.virtual_pointer for worker in c.workers.values()
            ),
            samples=len(c.history),
            telemetry_locked=not window.record_button.isEnabled(),
            pose=c.virtual_pointer.pose,
            target=c.virtual_pointer.target,
            trajectory_time=c.virtual_flight.time,
            range_m=c.virtual_flight.distance,
            paused=not c.virtual_flight.playing,
            model_label=c.reference.manifest.get("name"),
        )
        (data / "virtual-pointer-smoke-report.json").write_text(json.dumps(report, indent=2))
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
        c.disconnect("pointer")
        assert c.virtual_pointer is None and c.virtual_flight is None
        window.close()
        return (
            0
            if report["virtual_connected"] and not report["physical_ports_open"] and report["samples"] == 0
            else 1
        )
    if args.flight_smoke:
        from .flight import save_flight, load_flight
        from .domain import Sample
        from .trajectory import Trajectory
        from .ui import MissionDialog
        from .domain import to_enu

        c = window.controller
        dialog = MissionDialog(c.mission)
        dialog.location.preset.setCurrentIndex(dialog.location.preset.findData("URRG"))
        dialog.pointer_location.format.setCurrentIndex(dialog.pointer_location.format.findData("mgrs"))
        dialog.pointer_location.mgrs.setText("18T UN 20615 30290")
        dialog.fields["pointer_site_configured"].setChecked(True)
        dialog.accept()
        assert dialog.mission.pointer_location_code == "18TUN2061530290"
        assert dialog.mission.pointer_latitude == dialog.mission.latitude
        pointer_mgrs = dialog.mission.pointer_location_code
        dialog.mission.save(data / "antenna-mgrs.json")
        from .domain import Mission

        dialog = MissionDialog(Mission.load(data / "antenna-mgrs.json"))
        dialog.pointer_location.format.setCurrentIndex(dialog.pointer_location.format.findData("relative"))
        dialog.pointer_location.heading.setValue(90)
        dialog.pointer_location.distance.setValue(500)
        dialog.pointer_location.height_difference.setValue(5)
        dialog.accept()
        mission = dialog.mission
        mission.model = str(window.resource_root / "resources" / "examples" / "simple.ork")
        reference = Trajectory(
            [[0, 0, 0, 0], [1, 0, 0, 10]],
            dict(schema_version=1, frame="ENU", units="m,s", origin=[mission.latitude, mission.longitude, 0]),
        )
        archive = data / "planning.rktflight"
        save_flight(archive, mission, reference)
        planning = load_flight(archive, data / "opened")
        c.apply_flight(planning)
        assert c.mode == "LIVE" and c.reader is None and c.reference is not None
        assert c.mission.launch_location_code == "18TUN2061530290"
        assert c.mission.pointer_location_format == "relative"
        antenna_enu = to_enu(
            c.mission.latitude,
            c.mission.longitude,
            c.mission.altitude,
            (c.mission.pointer_latitude, c.mission.pointer_longitude, c.mission.pointer_altitude),
        )
        assert abs(antenna_enu[0] - 500) < 1e-4 and abs(antenna_enu[1]) < 1e-4
        assert c.mission.pointer_altitude == c.mission.altitude + 5
        assert Path(c.mission.model).is_file()
        samples = [Sample(t=float(i), sequence=i, source="LIVE", altitude=10 * i,
                          phase="PREFLIGHT", details={"fixture": "Portable flight verification"})
                   for i in range(3)]
        archive = data / "recorded-flight.rktflight"
        save_flight(
            archive, mission, reference, samples=samples, elapsed=[0, 1, 2], position=1,
            mode="LIVE", flight_zero=0, time_aligned=True, scope="Verification recording",
        )
        loaded = load_flight(archive, data / "opened")
        c.apply_flight(loaded)
        window.task_done("flight_loaded", loaded)
        window.last_ui = 0
        window.refresh()
        report = dict(
            **profile_fields,
            mode=c.mode,
            samples=c.reader.count,
            paused=not c.replay_playing,
            hardware_open=any(c.workers.values()),
            site=c.mission.launch_site_name,
            code=c.mission.launch_location_code,
            pointer_location=dict(
                mgrs=pointer_mgrs,
                format=c.mission.pointer_location_format,
                heading=c.mission.pointer_launch_heading,
                distance=c.mission.pointer_launch_distance,
                height_difference=c.mission.pointer_height_difference,
                launch_enu=antenna_enu,
            ),
            model_exists=Path(c.mission.model).is_file(),
            reference_rows=len(c.reference.points),
            latest_sequence=c.latest.sequence,
        )
        (data / "flight-smoke-report.json").write_text(json.dumps(report, indent=2))
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
        window.close()
        return (
            0
            if report["samples"] == len(samples) and report["latest_sequence"] == 1 and report["paused"] and not report["hardware_open"]
            else 1
        )
    if args.replay_smoke or args.startup_smoke:
        from time import monotonic

        if args.replay_smoke:
            path = verification_session(data, window.controller.mission, window.controller.video_channels)
            window.controller.open_replay(path)
            window.controller.seek(1)
            window.controller.replay_speed = 0.1
            window.toggle_replay()
        smoke_started = monotonic()
        smoke_deadline = smoke_started + 30

        def complete_report():
            from .location import decode_mgrs, decode_plus_code

            c = window.controller
            ready = c.reader is not None and c.latest is not None and all(
                state.worker and state.worker.received_frames > 1 and not state.worker.error
                for state in c.video_streams.values()
            )
            # Native video startup can take longer under CPU emulation. Wait for
            # the same required evidence, with a bound, instead of a fixed race.
            if args.replay_smoke and monotonic() < smoke_deadline:
                failed = any(
                    state.error or (state.worker and state.worker.error)
                    for state in c.video_streams.values()
                )
                if not ready and not failed:
                    QTimer.singleShot(250, complete)
                    return
            mgrs_location = decode_mgrs("15TWG0000049776")
            plus_location = decode_plus_code("8FVC9G8F+6X")
            window.open_settings()
            settings_check = dict(
                visible=window.settings_dialog.isVisible(),
                shortcut=window.settings_action.shortcut().toString(),
                bundled_engine=c.settings.engine_path.is_file(),
                engine_status=window.settings_dialog.engine_status.text(),
            )
            window.settings_dialog.reject()
            report = dict(
                **profile_fields,
                **station_board_report(window),
                smoke_elapsed_s=monotonic() - smoke_started,
                smoke_readiness_timeout=bool(args.replay_smoke and not ready and monotonic() >= smoke_deadline),
                settings=settings_check,
                samples=len(c.history),
                mode=c.mode,
                station_mode=window.station_mode,
                video_frames=c.video.received_frames if c.video else 0,
                video_error=c.video.error if c.video else "",
                video_streams={
                    stream: dict(
                        frames=state.worker.received_frames if state.worker else 0,
                        error=state.worker.error if state.worker else state.error,
                    )
                    for stream, state in c.video_streams.items()
                },
                hardware_open=any(c.workers.values()),
                replay_samples=c.reader.count if c.reader else 0,
                replay_playing=c.replay_playing,
                replay_position=c.replay_time if c.reader else None,
                latest_sequence=c.latest.sequence if c.latest else None,
                latest_recorded_utc=c.latest.utc if c.latest else None,
                controls_locked=not window.record_button.isEnabled() and not window.point_button.isEnabled(),
                font_family=QFontInfo(app.font()).family(),
                bundled_font_families=QFontDatabase.applicationFontFamilies(font_id),
                location_checks={
                    "mgrs": [mgrs_location.latitude, mgrs_location.longitude],
                    "pluscode": [plus_location.latitude, plus_location.longitude],
                },
            )
            data.mkdir(parents=True, exist_ok=True)
            (data / "smoke-report.json").write_text(json.dumps(report, indent=2))
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.screenshot))
            window.close()
            valid = (
                report["mode"] == "LIVE"
                and report["samples"] == 0
                and all(v["frames"] == 0 for v in report["video_streams"].values())
                and report["controls_locked"]
                if args.startup_smoke
                else report["mode"] == "REPLAY"
                and report["replay_samples"] > 0
                and report["latest_sequence"] >= 10
                and report["replay_playing"]
                and all(v["frames"] > 1 and not v["error"] for v in report["video_streams"].values())
            )
            app.exit(0 if valid and not report["hardware_open"] else 1)

        def complete():
            try:
                complete_report()
            except Exception:
                import traceback
                traceback.print_exc()
                window.close()
                app.exit(1)

        QTimer.singleShot(1000, complete)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
