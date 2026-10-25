#!/usr/bin/env python3
"""Two-computer network check using conspicuously SIMULATION-tagged fixtures.

No serial or USB device is opened. Start this on both hosts with --peer pointing
at the other host's HTTP telemetry endpoint. Video uses that endpoint's port + 1.
Results are transport/codec evidence, never RF/camera or field qualification.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import platform
import queue
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rocket_gnc_monitor.media import HEIGHT, WIDTH
from rocket_gnc_monitor.network.media import MediaService
from rocket_gnc_monitor.network.media_receiver import MediaReceiver, _http
from rocket_gnc_monitor.network.runtime import NetworkRuntime
from rocket_gnc_monitor.station_profile import StationProfile

MISSION = "99999999-9999-4999-8999-999999999999"


class SyntheticCapture:
    kind, source_mode, error = "camera", "simulation", ""

    def __init__(self):
        self.listeners = set()

    def add_frame_listener(self, callback):
        self.listeners.add(callback)

    def remove_frame_listener(self, callback):
        self.listeners.discard(callback)


def observation(runtime, sequence):
    raw = f"SIMULATION network fixture {sequence}".encode()
    return dict(
        observation_id=f"{runtime.node_id}:{runtime.boot_id}:{sequence}",
        receiver=dict(station=runtime.station, board_id="synthetic-check", connection_id=runtime.boot_id,
                      rssi_dbm=None, rx_rssi_dbm=None, decoder_gaps="0", rejected_packets="0"),
        vehicle=dict(id="balius", basis="operator_assignment", target_epoch=None),
        packet=dict(protocol="synthetic-network-check", protocol_version="1", decoder_version="fixture-1",
                    type="telemetry", sequence=str(sequence), device_boot_id=None, device_time_ms=None,
                    raw_b64=base64.b64encode(raw).decode(), raw_sha256=hashlib.sha256(raw).hexdigest()),
        timing=dict(received_mono_ms=runtime.mono_ms(), received_time_utc=None, clock_quality="unknown",
                    clock_uncertainty_ms=None),
        values=dict(state=dict(code=None, name=None), position=dict(lat_deg=None, lon_deg=None, alt_m=None,
                    alt_datum="unknown", fix=False), baro_altitude=dict(value_m=0, datum="agl_launch"),
                    velocity_enu_m_s=None, orientation=None,
                    flight_status=dict(motor_ignited=None, motor_burning=None, parachute_deployed=None,
                                       evidence_source=None), native_schema="synthetic-network-check/1", native={}),
        quality=dict(validation="accepted", attribution_confidence="operator_assignment",
                     warnings=["SIMULATION fixture; no hardware measurement"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="Keep publishing until duration expires")
    parser.add_argument("--peer", help="Other host's telemetry HTTP endpoint, e.g. http://10.211.55.2:54100")
    parser.add_argument("--station", choices=("base", "away1"), default="away1")
    parser.add_argument("--port", type=int, default=54100)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    settings = dict(network_bind_host=args.bind, network_discovery=False, network_source_mode="simulation",
                    network_accept_source_modes=["simulation"])
    directory = tempfile.TemporaryDirectory(prefix="rocket-network-check-")
    video_profile = StationProfile(station=args.station, role="video")
    media = MediaService(video_profile, allowed_source_modes=("simulation",), port_start=55100, port_end=55120)
    telem = NetworkRuntime(Path(directory.name) / "telem", StationProfile(station=args.station), MISSION,
                           dict(settings, network_port=args.port))
    video = NetworkRuntime(Path(directory.name) / "video", video_profile, MISSION,
                           dict(settings, network_port=args.port + 1), media_session_backend=media)
    media.notify = video.update_streams
    media.set_context(video.info(), video.boot_monotonic)
    capture = SyntheticCapture()
    media.set_source("digital", capture)
    stop = threading.Event()
    pixels = bytes([220, 30, 20]) * (WIDTH * HEIGHT)

    def produce():
        while not stop.wait(1 / 30):
            for callback in tuple(capture.listeners):
                callback(pixels, time.monotonic())

    threading.Thread(target=produce, daemon=True).start()
    telem.start()
    video.start()
    if args.peer:
        telem.add_endpoint(args.peer)
    receivers = []
    received = set()
    decoder_pixels = {}
    errors = []
    started = time.monotonic()
    last_event = last_try = 0
    count = 0
    try:
        while time.monotonic() - started < args.duration:
            now = time.monotonic()
            if now - last_event >= .1:
                count += 1
                telem.publish_observation(observation(telem, count))
                last_event = now
            for peer in telem.peers_snapshot():
                for item in peer.get("latest", []):
                    received.add(item["observation_id"])
            for event in telem.drain_events(2000):
                if event.get("kind") == "telemetry.observation":
                    received.add(event["payload"]["observation_id"])
            if args.peer and not receivers and now - last_try >= 1:
                last_try = now
                host = urlsplit(args.peer)
                endpoint = f"http://{host.hostname}:{(host.port or 80) + 1}"
                try:
                    info = _http(endpoint, "GET", "/info")
                    streams = _http(endpoint, "GET", "/streams")["streams"]
                    stream = next(item for item in streams if item["channel_id"] == "balius.digital")
                    if not any(item["available"] for item in stream["renditions"]):
                        raise ValueError("Source is still starting")
                    receivers = [MediaReceiver(endpoint, video.info(), info, stream, rendition)
                                 for rendition in ("preview", "main")]
                except (OSError, ValueError, KeyError, StopIteration) as exc:
                    errors.append(type(exc).__name__)
            for receiver in receivers:
                try:
                    raw, _ = receiver.frames.get_nowait()
                    decoder_pixels[receiver.rendition] = list(raw[:3])
                except queue.Empty:
                    pass
            time.sleep(.02)
        report = dict(fixture="SIMULATION; synthetic telemetry and RGB capture; no devices opened",
                      platform=platform.platform(), duration_s=round(time.monotonic() - started, 3),
                      station=args.station, telemetry_endpoint=telem.local_endpoint, video_endpoint=video.local_endpoint,
                      telemetry_published=count, telemetry_received=len(received),
                      peers=[dict(endpoint=p["endpoint"], state=p["state"], rtt_ms=p.get("rtt_ms"))
                             for p in telem.peers_snapshot()],
                      media={r.rendition: dict(frames=r.received_frames, error=r.error,
                                               pixel=decoder_pixels.get(r.rendition)) for r in receivers},
                      timing=media.streams(), initial_connection_errors=errors[-5:],
                      qualification="Transport/codec check only; radio, camera, latency and two-hour endurance unqualified")
        report["passed"] = bool(not args.peer or len(received) >= 5 and len(receivers) == 2 and all(
            r.received_frames >= 5 and (decoder_pixels.get(r.rendition) or [0])[0] > 180 for r in receivers))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"passed": report["passed"], "report": str(args.report),
                          "telemetry_received": len(received), "media": report["media"]}))
        return 0 if report["passed"] else 1
    finally:
        stop.set()
        for receiver in receivers:
            receiver.stop()
        media.close()
        video.stop()
        telem.stop()
        directory.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
