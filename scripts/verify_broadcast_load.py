"""Exercise the full synthetic Iris wall plus Program/inset and a warm candidate.

This local-only load probe does not qualify OBS, audio, physical radios or YouTube.
It intentionally keeps telemetry unavailable rather than inventing an Iris adapter.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication
from verify_broadcast import FixtureCapture

from rocket_gnc_monitor.broadcast_model import BroadcastSelection
from rocket_gnc_monitor.network.media import MediaService
from rocket_gnc_monitor.network.runtime import NetworkRuntime
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--output", type=Path, default=ROOT / "build/broadcast-iris-load")
    args = parser.parse_args()
    if args.duration < 30:
        parser.error("Use at least 30 seconds to warm all camera paths")
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    mission = str(uuid.uuid4())
    settings = {
        "network_port": 0,
        "network_bind_host": "127.0.0.1",
        "network_discovery": False,
        "network_source_mode": "simulation",
        "network_accept_source_modes": ["simulation"],
    }
    publishers = []
    for index in range(1, 5):
        profile = StationProfile(station=f"away{index}", role="video", vehicle="iris")
        service = MediaService(
            profile,
            port_start=56200 + index * 32,
            port_end=56231 + index * 32,
            bind_host="127.0.0.1",
            allowed_source_modes=("simulation",),
            viewers_per_stream=4,
        )
        cameras = {}
        for channel in profile.local_channels:
            camera = FixtureCapture()
            service.set_source(channel, camera)
            cameras[channel] = camera
        runtime = NetworkRuntime(
            args.output / f"away{index}", profile, mission, settings, media_session_backend=service
        )
        service.notify = runtime.update_streams
        runtime.start()
        publishers.append((runtime, service, cameras))
    window = StationWindow(
        args.output / "base",
        profile=StationProfile(station="base", role="video", vehicle="iris"),
        auto_place=False,
        network_config={
            **settings,
            "mission_simulation": True,
            "mission_id": mission,
            "receive_source_mode": "simulation",
            "peers": [],
        },
    )
    window.open_broadcast()
    c = window.broadcast
    c.panel.hide()
    c.panel.vehicle.setCurrentIndex(c.panel.vehicle.findData("sustainer"))
    started = time.monotonic()
    state = {"phase": 0, "added": set(), "metrics": [], "last_metric": 0, "error": None, "counter": 0}

    def tick():
        now = time.monotonic()
        elapsed = now - started
        state["counter"] += 1
        for index, (runtime, _, cameras) in enumerate(publishers, 1):
            if runtime.local_endpoint and runtime.local_endpoint not in state["added"]:
                window.station_network.runtime.add_endpoint(runtime.local_endpoint)
                state["added"].add(runtime.local_endpoint)
            runtime.drain_events()
            for channel, camera in cameras.items():
                frame = QImage(960, 540, QImage.Format.Format_RGB888)
                frame.fill(QColor(20 + index * 20, 35, 60))
                p = QPainter(frame)
                p.setPen(QColor("white"))
                font = p.font()
                font.setPixelSize(32)
                p.setFont(font)
                p.drawText(40, 250, f"LOCAL TEST GS{index} {channel.upper()} — {state['counter']:06}")
                p.fillRect(int(elapsed * 140 % 850), 350, 100, 80, QColor("#ae1f39"))
                p.end()
                camera.emit(bytes(frame.constBits()), now)
        viewer = window.station_network.viewer
        main = "camera:away1:digital"
        inset = "camera:away2:digital"
        candidate = "camera:away3:digital"
        if state["phase"] == 0 and all(name in c.session.sources for name in (main, inset, candidate)):
            viewer.request_source("away1", "digital")
            c.stage(BroadcastSelection("Flight", main, title="IRIS LOCAL LOAD TEST"))
            state["phase"] = 1
        elif state["phase"] == 1 and c.session.readiness(c.session.preview, now)[0]:
            c.take()
            state["phase"] = 2
        elif state["phase"] == 2 and c.session.program.main == main:
            c.stage(BroadcastSelection("Flight", main, inset, title="IRIS LOCAL LOAD TEST"))
            state["phase"] = 3
        elif state["phase"] == 3 and c.session.readiness(c.session.preview, now)[0]:
            c.take()
            state["phase"] = 4
        elif state["phase"] == 4 and c.session.program.inset == inset:
            c.stage(BroadcastSelection("Flight", candidate, inset, title="IRIS LOCAL LOAD TEST"))
            state["phase"] = 5
        elif state["phase"] == 5 and c.session.readiness(c.session.preview, now)[0]:
            state["phase"] = 6
            state["loaded_at"], state["loaded_frames"] = now, c.rendered
        if now - state["last_metric"] >= 10:
            snapshot = c.last_snapshot
            mains = [worker for key, worker in viewer.workers.items() if key[2] == "main"]
            metric = {
                "elapsed": elapsed,
                "phase": state["phase"],
                "rendered": c.rendered,
                "render_ms": c.render_ms,
                "missed_slots": c.skipped,
                "buffer_bytes": c.session.frames.bytes,
                "broadcast_drops": c.session.frames.dropped,
                "rss_highwater_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "main_receivers": len(mains),
                "healthy_main_receivers": sum(bool(w.last_frame and not w.error) for w in mains),
                "total_receivers": len(viewer.workers),
                "archive_drops": sum(r.ingress_dropped for r, _, _ in publishers)
                + window.station_network.runtime.ingress_dropped,
                "program_has_main_and_inset": bool(snapshot and snapshot.main and snapshot.inset),
                "preview_ready": c.session.readiness(c.session.preview, now)[0],
            }
            state["metrics"].append(metric)
            state["last_metric"] = now
            (args.output / "progress.json").write_text(json.dumps(metric, indent=2))
            print(json.dumps(metric), flush=True)
        if elapsed >= args.duration:
            finish()

    def finish():
        timer.stop()
        now = time.monotonic()
        fps = (c.rendered - state.get("loaded_frames", c.rendered)) / max(
            0.001, now - state.get("loaded_at", now)
        )
        loaded = [m for m in state["metrics"] if m["phase"] == 6]
        result = {
            "duration_s": now - started,
            "program_fps": fps,
            "error": state["error"],
            "metrics": state["metrics"],
            "scope": "Synthetic four-station Iris SRT wall, Program/inset and warm Preview; no OBS/audio/YouTube/field radios",
        }
        result["passed"] = bool(
            loaded
            and fps >= 28
            and not state["error"]
            and all(
                m["healthy_main_receivers"] == 5
                and m["program_has_main_and_inset"]
                and m["preview_ready"]
                and not m["archive_drops"]
                for m in loaded
            )
        )
        c.last_program.save(str(args.output / "program.png"))
        c.last_preview.save(str(args.output / "preview.png"))
        window.close()
        for runtime, service, _ in publishers:
            service.close()
            runtime.stop()
        (args.output / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k != "metrics"}), flush=True)
        app.exit(0 if result["passed"] else 1)

    def safe_tick():
        try:
            tick()
        except Exception as exc:  # noqa: BLE001 - terminate and report failed Qt probe
            state["error"] = f"{type(exc).__name__}: {exc}"
            finish()

    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(33)
    timer.timeout.connect(safe_tick)
    timer.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
