"""Local synthetic away station for testing an installed Base Video app with OBS.

No Base window, hardware or YouTube connection is created. Stop with Ctrl-C.
control.json in the output folder can disable video/telemetry for recovery tests.
"""

import argparse
import json
import math
import signal
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication
from verify_broadcast import FixtureCapture

from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.network.media import MediaService
from rocket_gnc_monitor.network.observation import observation_from_sample
from rocket_gnc_monitor.network.runtime import NetworkRuntime
from rocket_gnc_monitor.station_profile import StationProfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
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
    media = MediaService(
        StationProfile(station="away1", role="video"),
        bind_host="127.0.0.1",
        port_start=56800,
        port_end=56815,
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
    telem = NetworkRuntime(args.output / "telemetry", StationProfile(station="away1"), mission, settings)
    media.notify = video.update_streams
    video.start()
    telem.start()
    started = time.monotonic()
    connection = str(uuid.uuid4())
    state = {"counter": 0, "announced": False, "telem_at": 0, "control_at": 0, "control": {}}
    control = args.output / "control.json"
    control.write_text(json.dumps({"video": True, "telemetry": True}))

    def tick():
        now = time.monotonic()
        elapsed = now - started
        for runtime in (video, telem):
            runtime.drain_events()
        if not state["announced"] and video.local_endpoint and telem.local_endpoint:
            info = {
                "mission": mission,
                "video": video.local_endpoint,
                "telemetry": telem.local_endpoint,
                "source": "SIMULATION — LOCAL OBS TEST ONLY",
            }
            (args.output / "endpoints.json").write_text(json.dumps(info, indent=2))
            print(json.dumps(info), flush=True)
            state["announced"] = True
        if now - state["control_at"] > 0.2:
            try:
                state["control"] = json.loads(control.read_text())
            except (OSError, ValueError):
                pass
            state["control_at"] = now
        state["counter"] += 1
        if state["control"].get("video", True):
            frame = QImage(960, 540, QImage.Format.Format_RGB888)
            frame.fill(QColor("#14334b"))
            p = QPainter(frame)
            p.setPen(QColor("white"))
            f = QFont("Arial")
            f.setPixelSize(30)
            p.setFont(f)
            p.drawText(36, 180, "LOCAL OBS REHEARSAL — NOT FLIGHT VIDEO")
            p.drawText(36, 230, f"FRAME {state['counter']:07}  SOURCE {elapsed:09.3f} s")
            p.fillRect(int(elapsed * 120 % 850), 330, 100, 70, QColor("#ae1f39"))
            p.fillRect(820, 450, 100, 60, QColor("white" if elapsed % 5 < 0.2 else "#14334b"))
            p.end()
            camera.emit(bytes(frame.constBits()), now)
        if state["control"].get("telemetry", True) and now - state["telem_at"] >= 0.05:
            state["telem_at"] = now
            sample = Sample(
                elapsed,
                state["counter"],
                "SIMULATION",
                received=now,
                altitude=150 + 20 * math.sin(elapsed / 20),
                velocity=math.cos(elapsed / 20),
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
                observation_counter=state["counter"],
                boot_monotonic=telem.boot_monotonic,
                vehicle_mode="balius",
                baro_datum="agl_launch",
                raw_unavailable_reason="Controlled local OBS rehearsal",
            )
            telem.publish_observation(payload)

    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(33)
    timer.timeout.connect(tick)
    timer.start()
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    try:
        return app.exec()
    finally:
        timer.stop()
        media.close()
        video.stop()
        telem.stop()


if __name__ == "__main__":
    raise SystemExit(main())
