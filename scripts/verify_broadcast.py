"""Unattended local broadcast rehearsal. No OBS/YouTube credentials or stream calls.

Exercises real HTTP discovery, canonical telemetry, H.264/SRT decoding, shared
wall/Program receivers and the Qt compositor. Writes numbered frame captures,
a short local reference movie, metrics and a machine-readable result.
"""

from __future__ import annotations

import argparse
import json
import platform
import resource
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from rocket_gnc_monitor.broadcast_model import BroadcastSelection
from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.media import ffmpeg
from rocket_gnc_monitor.network.media import MediaService
from rocket_gnc_monitor.network.observation import observation_from_sample
from rocket_gnc_monitor.network.runtime import NetworkRuntime
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow


class FixtureCapture:
    kind, source_mode, error = "camera", "simulation", ""

    def __init__(self):
        self.listeners = set()

    def add_frame_listener(self, callback):
        self.listeners.add(callback)

    def remove_frame_listener(self, callback):
        self.listeners.discard(callback)

    def emit(self, data, stamp):
        for callback in tuple(self.listeners):
            callback(data, stamp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--output", type=Path, default=ROOT / "build/broadcast-verification")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument(
        "--faults",
        action="store_true",
        help="Inject a camera gap and explicit simulation pause; use duration >= 30",
    )
    args = parser.parse_args()
    if args.faults and args.duration < 30:
        parser.error("Fault rehearsal needs at least 30 seconds")
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    mission = str(uuid.uuid4())
    settings = {
        "network_port": 0,
        "network_bind_host": "127.0.0.1",
        "network_discovery": False,
        "network_source_mode": "simulation",
        "network_accept_source_modes": ["simulation"],
    }
    media = MediaService(
        StationProfile(station="away1", role="video"),
        port_start=56000,
        port_end=56012,
        allowed_source_modes=("simulation",),
    )
    camera = FixtureCapture()
    media.set_source("digital", camera)
    video = NetworkRuntime(
        args.output / "video",
        StationProfile(station="away1", role="video"),
        mission,
        settings,
        media_session_backend=media,
    )
    media.notify = video.update_streams
    telem = NetworkRuntime(args.output / "telemetry", StationProfile(station="away1"), mission, settings)
    video.start()
    telem.start()
    window = StationWindow(
        args.output / "base",
        profile=StationProfile(station="base", role="video"),
        auto_place=False,
        network_config={
            "network_port": 0,
            "network_bind_host": "127.0.0.1",
            "network_discovery": False,
            "mission_simulation": True,
            "mission_id": mission,
            "receive_source_mode": "simulation",
            "peers": [],
        },
    )
    if args.visible:
        window.show()
    window.open_broadcast()
    if not args.visible:
        window.broadcast.panel.hide()
    c = window.broadcast
    c.settings.title = "LOCAL REHEARSAL · SIMULATION"
    started = time.monotonic()
    connection = str(uuid.uuid4())
    state = {
        "counter": 1,
        "taken": False,
        "rendered_start": 0,
        "metrics": [],
        "error": None,
        "frames_saved": 0,
        "fault_checks": set(),
        "telem_at": 0,
        "metric_at": 0,
        "added": set(),
        "phase": "warming",
    }
    frame_dir = args.output / "frames"
    frame_dir.mkdir(exist_ok=True)
    program_at = [0.0]
    image_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fixture-images")
    pending_image = [None]

    def output(image):
        now = time.monotonic()
        # A numbered 3 fps image sequence creates a small reference movie while
        # metrics measure the actual independent 30 Hz composition cadence.
        if (
            state["taken"]
            and state["frames_saved"] < 30
            and now - program_at[0] >= 1 / 3
            and (pending_image[0] is None or pending_image[0].done())
        ):
            pending_image[0] = image_writer.submit(
                QImage(image).save, str(frame_dir / f"{state['frames_saved']:04}.png")
            )
            state["frames_saved"] += 1
            program_at[0] = now

    c.program_frame.connect(output)

    def tick():
        now = time.monotonic()
        elapsed = now - started
        for runtime in (video, telem):
            if runtime.local_endpoint and runtime.local_endpoint not in state["added"]:
                window.station_network.runtime.add_endpoint(runtime.local_endpoint)
                state["added"].add(runtime.local_endpoint)
            runtime.drain_events()
        count = state["counter"]
        state["counter"] += 1
        image = QImage(960, 540, QImage.Format.Format_RGB888)
        image.fill(QColor("#142d44"))
        painter = QPainter(image)
        painter.setPen(QColor("white"))
        font = QFont("Arial")
        font.setPixelSize(30)
        painter.setFont(font)
        painter.drawText(60, 220, "LOCAL SIMULATION — NOT FLIGHT VIDEO")
        painter.drawText(60, 270, f"FRAME {count:07}   CAPTURE {elapsed:09.3f} s")
        painter.fillRect(int(count * 5 % 900), 330, 60, 90, QColor("#ae1f39"))
        painter.end()
        if not args.faults or not (12 <= elapsed < 16 or 22 <= elapsed < 27):
            camera.emit(bytes(image.constBits()), now)
        if args.faults:
            paused = 22 <= elapsed < 27
            for runtime in (video, telem):
                runtime.update_status(
                    {
                        "application": {
                            "state": "paused" if paused else "running",
                            "source_mode": "simulation",
                            "simulation": True,
                            "mission_time_us": str(
                                int(min(elapsed, 22) * 1e6) if paused else int(elapsed * 1e6)
                            ),
                        },
                        "telemetry_board": None,
                        "pointer": None,
                        "video_inputs": None,
                        "network": {},
                        "clock": {"quality": "simulation", "uncertainty_ms": None},
                    }
                )
            snapshot = c.last_snapshot
            if snapshot:
                notice = snapshot.view.camera_notice
                if 12 <= elapsed < 16 and notice == "VIDEO DELAYED":
                    state["fault_checks"].add("delay_notice")
                if 14 <= elapsed < 17 and snapshot.main is None:
                    state["fault_checks"].add("loss_slate")
                if 18 <= elapsed < 21 and snapshot.main is not None and not notice:
                    state["fault_checks"].add("recovery")
                if 22 <= elapsed < 27 and snapshot.view.paused:
                    state["fault_checks"].add("explicit_pause")
                    state.setdefault("pause_clock", snapshot.view.mission_elapsed_s)
                if (
                    25 <= elapsed < 27
                    and snapshot.main is not None
                    and snapshot.view.paused
                    and snapshot.view.mission_elapsed_s == state.get("pause_clock")
                ):
                    state["fault_checks"].add("paused_frame_and_clock")
                if elapsed > 29 and not snapshot.view.paused and snapshot.main is not None:
                    state["fault_checks"].add("resume")
                    if snapshot.view.mission_elapsed_s < elapsed - state["take_time"] + started - 3:
                        state["fault_checks"].add("resume_excludes_pause")
        if now - state["telem_at"] >= 0.05:
            state["telem_at"] = now
            sample = Sample(
                elapsed,
                count,
                "SIMULATION",
                received=now,
                altitude=100 + elapsed,
                velocity=1,
                phase="FLIGHT",
                details={"state_code": 2, "state_schema": "zephyrus-v1"},
            )
            payload = observation_from_sample(
                sample,
                raw=None,
                station="gs1",
                node_id=telem.node_id,
                boot_id=telem.boot_id,
                connection_id=connection,
                observation_counter=count,
                boot_monotonic=telem.boot_monotonic,
                vehicle_mode="balius",
                baro_datum="agl_launch",
                raw_unavailable_reason="Numbered controlled local test fixture",
            )
            telem.publish_observation(payload)
        logical = "camera:away1:digital"
        if not state["taken"] and logical in c.session.sources:
            if c.session.preview.main != logical:
                c.set_qualification(
                    "unknown", True, "Controlled fixture: scalar velocity is signed vertical m/s"
                )
                c.stage(
                    BroadcastSelection(
                        "Flight", logical, title=c.settings.title, caption="CONTROLLED LOCAL TEST"
                    )
                )
            if c.session.readiness(c.session.preview, now)[0]:
                c.take()
                c.clock_action("on_program", event="LIFTOFF")
                state["taken"] = True
                state["rendered_start"] = c.rendered
                state["take_time"] = now
                state["phase"] = "program"
        if now - state["metric_at"] >= 10:
            viewer = window.station_network.viewer
            snapshot = c.last_snapshot
            metric = {
                "elapsed": elapsed,
                "rendered": c.rendered,
                "render_ms": c.render_ms,
                "missed_slots": c.skipped,
                "buffer_bytes": c.session.frames.bytes,
                "broadcast_drops": c.session.frames.dropped,
                "rss_highwater_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "receiver_frames": sum(w.received_frames for w in viewer.workers.values()),
                "main_receivers": sum(k[2] == "main" for k in viewer.workers),
                "telemetry_status": snapshot.view.telemetry_status if snapshot else None,
                "archive_drops": telem.ingress_dropped
                + video.ingress_dropped
                + window.station_network.runtime.ingress_dropped,
            }
            state["metrics"].append(metric)
            state["metric_at"] = now
            (args.output / "progress.json").write_text(json.dumps(metric, indent=2))
            print(json.dumps(metric), flush=True)
        if elapsed >= args.duration:
            timer.stop()
            finish()

    def finish():
        elapsed = time.monotonic() - started
        render_duration = time.monotonic() - state.get("take_time", time.monotonic())
        fps = (c.rendered - state["rendered_start"]) / max(0.001, render_duration)
        snapshot = c.last_snapshot
        result = {
            "duration_s": elapsed,
            "program_fps": fps,
            "rendered": c.rendered,
            "missed_slots": c.skipped,
            "buffer_bytes": c.session.frames.bytes,
            "broadcast_drops": c.session.frames.dropped,
            "telemetry_status": snapshot.view.telemetry_status if snapshot else None,
            "taken": state["taken"],
            "machine": platform.platform(),
            "python": sys.version,
            "metrics": state["metrics"],
            "scope": "Local SRT and telemetry fixture; excludes OBS capture/audio and real YouTube delivery",
        }
        result["fault_checks"] = sorted(state["fault_checks"])
        result["error"] = state["error"]
        result["passed"] = bool(
            state["taken"]
            and fps >= 28
            and snapshot
            and snapshot.view.telemetry_status == "fresh"
            and not state["error"]
            and not any(m["archive_drops"] for m in state["metrics"])
        )
        if args.faults:
            result["passed"] &= state["fault_checks"] == {
                "delay_notice",
                "loss_slate",
                "recovery",
                "explicit_pause",
                "paused_frame_and_clock",
                "resume_excludes_pause",
                "resume",
            }
        if snapshot:
            c.last_program.save(str(args.output / "program-1080p.png"))
        window.close()
        media.close()
        video.stop()
        telem.stop()
        (args.output / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k != "metrics"}), flush=True)
        app.exit(0 if result["passed"] else 1)

    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(33)

    def safe_tick():
        try:
            tick()
        except Exception as exc:  # noqa: BLE001 - fail the rehearsal instead of leaving a Qt timer running
            state["error"] = f"{type(exc).__name__}: {exc}"
            timer.stop()
            finish()

    timer.timeout.connect(safe_tick)
    timer.start()
    status = app.exec()
    image_writer.shutdown(wait=True)
    if state["frames_saved"]:
        subprocess.run(
            [
                ffmpeg(),
                "-y",
                "-loglevel",
                "error",
                "-framerate",
                "3",
                "-i",
                str(frame_dir / "%04d.png"),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-r",
                "30",
                "-pix_fmt",
                "yuv420p",
                str(args.output / "local-reference.mkv"),
            ],
            check=True,
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
