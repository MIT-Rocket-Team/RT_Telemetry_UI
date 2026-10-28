"""Explicit Mission participant using the real station workspace, never serial hardware.

The socket thread owns decoding/policy; Qt receives one acknowledged boundary at a
 time. Native session clocks remain host clocks, with a separate mission mapping.
"""
from __future__ import annotations

import base64
import json
import queue
import struct
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication

from .controller import Controller
from .domain import Sample
from .mission_video import MissionProfile, MissionVideoPublisher
from .mission_worker import run
from .rocket_protocol import rocket_packet
from .station_workspace import StationWindow
from .tracking_policy import tracking_target


class MissionController(Controller):
    def __init__(self, *args, manifest, **kwargs):
        super().__init__(*args, video_registry={s["id"]: s["id"] for s in manifest.get("videoStreams", [])}, **kwargs)
        self.mode = "SIMULATION"
        self.manifest = manifest
        self.station_config = manifest["station"]
        self.mission.name = manifest.get('title', 'Mission Simulation')
        launch = manifest.get('launchSite')
        if launch:
            self.mission.latitude = launch['latitudeDeg']
            self.mission.longitude = launch['longitudeDeg']
            self.mission.altitude = launch['ellipsoidalHeightM']
            self.mission.altitude_msl = launch['mslHeightM']
            self.mission.site_configured = True
        self.mission.pointer_latitude, self.mission.pointer_longitude, self.mission.pointer_altitude = self.station_config['origin']
        self.mission.pointer_site_configured = self.station_config.get('pointerEnabled', False)
        self.mission.freshness = self.station_config.get('freshnessS', 2.0)
        self.mission_time_us = -1_000_000
        self.mission_connected = True
        self.mission_paused = False
        self.operator_actions = queue.Queue(maxsize=256)
        self.polling = True
        self.pointer_status = "SIMULATION · awaiting mission boundary"
        self.states.update({role: "Simulation" for role in self.states})
        self.start_recording(self.data_dir / "sessions")

    @property
    def ground_connected(self):
        return self.mission_connected

    @property
    def pointer_connected(self):
        return self.mission_connected and self.station_config.get("pointerEnabled", False)

    @property
    def command_connected(self):
        return self.ground_connected and self.station_config["id"] == "launch"

    @property
    def command_block_reason(self):
        if self.station_config.get("owner") != "interactive-ui":
            return "This simulation window is an observer"
        if not self.command_connected or not self.manifest.get("coupled", False):
            return "FC commands require the coupled flight computer at Base"
        return ""

    @property
    def display_fresh(self):
        return self.rocket_link_state()[0] == "RECEIVING"

    def rocket_link_state(self, now=None):
        if self.latest is None:
            return "WAITING", None
        age = (self.mission_time_us - int(self.latest.details["receiptTimeUs"])) / 1e6
        return ("RECEIVING" if 0 <= age <= self.mission.freshness else "STALE"), age

    def switch_mode(self, mode):
        if mode != "SIMULATION":
            raise ValueError("Open a separate station window to use LIVE or REPLAY")

    def connect(self, *args, **kwargs):
        raise ValueError("Mission stations use software connections configured in OpenRocket")

    def start_video(self, *args, **kwargs):
        raise ValueError('Mission video sources are configured in OpenRocket')

    def start_virtual_trajectory(self):
        raise ValueError('The OpenRocket mission owns the simulation clock and vehicle')

    def open_replay(self, path):
        raise ValueError('Open a separate station window to replay a saved flight')

    def disconnect(self, *args, **kwargs):
        self.enqueue("disconnect")

    def set_polling(self, enabled):
        self.enqueue("connect" if enabled else "disconnect")
        self.polling = bool(enabled)

    def enqueue(self, kind, **values):
        if self.mission_paused:
            raise ValueError("Resume the mission before issuing a fresh command")
        if self.station_config.get("owner") != "interactive-ui":
            raise ValueError("Observers cannot issue mission commands")
        action = dict(id="ui-" + str(uuid.uuid4()), kind=kind,
                      siteId=self.station_config["id"], createdUs=str(self.mission_time_us),
                      expiresUs=str(self.mission_time_us + 500_000), hostArrivalNanos=str(time.monotonic_ns()), **values)
        try:
            self.operator_actions.put_nowait(action)
        except queue.Full:
            raise ValueError("Mission command queue is full; let the mission advance") from None

    def hold(self, reason="Operator hold"):
        self.enqueue("hold")

    def start_tracking(self):
        self.enqueue("start-tracking")

    def dispatch_pointer(self, packet, angles, command):
        opcode = packet[1]
        if opcode == 5:
            self.enqueue("zero")
        elif opcode == 0:
            azimuth, elevation = struct.unpack("<ff", packet[2:10])
            self.enqueue("point", azimuthDeg=azimuth, elevationDeg=elevation)
        else:
            # Jog uses the measured plant pose supplied by the simulator.
            azimuth, elevation = self.pointer_sent or (0.0, 0.0)
            da, de = {1: (0, 5), 2: (0, -5), 3: (-5, 0), 4: (5, 0)}[opcode]
            self.enqueue("point", azimuthDeg=(azimuth + da) % 360,
                         elevationDeg=max(0, min(90, elevation + de)))

    def send_rocket(self, command, value=None):
        if self.command_block_reason:
            raise ValueError(self.command_block_reason)
        if command == "disarm_pyros":
            raise ValueError("Opcode 0x07 is not implemented by the simulated flight computer")
        if command == "advance_state":
            value = (self.latest.details.get("state_code", 0) if self.latest else 0) + 1
        self.enqueue("uplink", dataHex=rocket_packet(command, value).hex())

    def tracking_target(self):
        sample = self.latest
        if sample is None:
            raise ValueError("No accepted mission telemetry")
        # GUI receipt clocks are host-based; only this policy input uses mission time.
        copy = Sample.from_dict(sample.to_dict())
        copy.received = int(sample.details["receiptTimeUs"]) / 1e6
        return tracking_target(copy, now=self.mission_time_us / 1e6,
                               freshness=self.mission.freshness,
                               origin=tuple(self.station_config["origin"]),
                               policy=self.station_config.get("policy", "legacy-ui.v1"),
                               height_reference=sample.details.get("heightReference"),
                               accepted_source="SIMULATION")


class BoundaryBridge(QObject):
    boundary = Signal(object)
    finished = Signal(str)
    pause_state = Signal(bool)
    ownership = Signal(object)
    close_requested = Signal(object)

    def __init__(self, window, manifest):
        super().__init__()
        self.window, self.manifest = window, manifest
        self.failure = ""
        self.closed = False
        self.on_finished = None
        self.boundary.connect(self.apply)
        self.finished.connect(self.finish)
        title = window.windowTitle()
        def pause_display(paused):
            window.setWindowTitle(title + (" · PAUSED" if paused else ""))
            if window.video_wall:
                window.video_wall.mission_paused = paused
        self.pause_state.connect(pause_display)
        self.ownership.connect(self.change_owner)
        self.close_requested.connect(self.close_session)
        self.publisher = MissionVideoPublisher(window, manifest.get("videoStreams", []), window.controller.data_dir,
                                              record=manifest.get("recordEncodedVideo", False)) if manifest.get("role") == "video" and window.station_network else None
        self.mapping = (window.controller.data_dir / "mission-clock.jsonl").open("w", encoding="utf-8")

    @Slot(object)
    def change_owner(self, value):
        body, done = value
        try:
            c = self.window.controller
            c.station_config['owner'] = body['owner']
            c.tracking = False
            c.latest = None
            c.pointer_status = 'SIMULATION · ownership changed; restart tracking after resume'
            while not c.operator_actions.empty():
                c.operator_actions.get_nowait()
            c.log('Mission ownership changed', body)
            c.changed.emit()
        finally:
            done.set()

    def before(self, batches):
        controller = self.window.controller
        total = sum(len(b.get("deliveries", [])) + len(b.get("actions", [])) for b in batches)
        for batch in batches:
            if batch["id"] != controller.station_config["id"]:
                raise ValueError("Application received another station's bytes")
            while total < 256:
                try:
                    action = controller.operator_actions.get_nowait()
                except queue.Empty:
                    break
                # Uplinks enter Java's ordered FC queue at the following boundary.
                if action["kind"] == "uplink":
                    batch.setdefault("operatorActions", []).append(action)
                else:
                    batch.setdefault("actions", []).append(action)
                total += 1

    def after(self, time_us, batches, result, stations):
        result["operatorActions"] = [a for b in batches for a in b.get("operatorActions", [])]
        done = threading.Event()
        self.boundary.emit((time_us, batches, result, done))
        if not done.wait(8):
            raise ValueError("Station display did not acknowledge the mission boundary")
        if self.failure:
            raise ValueError(self.failure)

    @Slot(object)
    def apply(self, value):
        time_us, batches, result, done = value
        c = self.window.controller
        try:
            c.mission_time_us = time_us
            host, utc = time.monotonic(), time.time()
            self.mapping.write(json.dumps({"missionTimeUs": str(time_us), "hostMonotonic": host, "utc": utc}) + "\n")
            self.mapping.flush()
            for batch, station in zip(batches, result["stations"], strict=True):
                if self.publisher:
                    self.publisher.update(time_us, batch.get("videoState", []))
                    for key, source in self.publisher.sources.items():
                        if source.state is not None:
                            self.window.show_frame(key, source.pixels)
                if batch.get("pointerPose"):
                    pose = batch["pointerPose"]
                    c.pointer_sent = (pose["encoderAzimuthDeg"], pose["encoderElevationDeg"])
                raw_by_packet = {d["packetId"]: base64.b64decode(d["data"], validate=True)
                                 for d in batch.get("deliveries", [])}
                for raw in raw_by_packet.values():
                    c.recorder.raw("telemetry_rx", raw)
                for sample_value in station["samples"]:
                    sample = Sample.from_dict(sample_value)
                    sample.details["receiptTimeUs"] = str(time_us)
                    sample.received, sample.utc = host, utc
                    if not sample.details.get("outOfOrder"):
                        c.accept(sample, record=True)
                    else:
                        c.recorder.sample(sample)
                    if self.window.station_network:
                        self.window.station_network.observation(sample, raw_by_packet[sample.details["packetId"]],
                                                               {"connection_id": self.manifest["connectionId"]})
                for write in station["writes"]:
                    c.recorder.raw("pointer_tx", base64.b64decode(write["data"], validate=True))
                c.tracking, c.pointer_status = station["tracking"], "SIMULATION · " + station["reason"]
                c.stats.update(accepted=station["acceptedTotal"], rejected=c.stats["rejected"] + station["rejected"])
                for action in station["actions"]:
                    c.log("Mission action", action)
            if c.recorder.error:
                raise ValueError(c.recorder.error)
            c.changed.emit()
        except Exception as exc:  # noqa: BLE001 -- fail the participant at the thread/Qt boundary
            self.failure = str(exc)
        finally:
            done.set()

    @Slot(str)
    def finish(self, failure):
        self.failure = failure or self.failure
        self.close_session()
        if self.on_finished:
            self.on_finished(self.failure)
        else:
            QApplication.instance().exit(1 if self.failure else 0)

    @Slot(object)
    def close_session(self, done=None):
        if self.closed:
            if done:
                done.set()
            return
        self.closed = True
        if self.publisher:
            try:
                self.publisher.close()
            except ValueError as exc:
                self.failure = str(exc)
        self.mapping.close()
        self.window.close()
        if done:
            done.set()


def launch(manifest, *, on_finished=None):
    if manifest["host"] != "127.0.0.1":
        raise ValueError("Mission IPC must use loopback")
    site = manifest["station"]["id"]
    profile = MissionProfile(mission_streams=tuple(manifest.get("videoStreams", [])),station="base" if site == "launch" else "away" + site[-1],
                             role="video" if manifest.get("role") == "video" else "telemetry",
                             vehicle=manifest.get("vehicleMode", "balius"), launch_site="urrg")
    network = manifest.get("network")
    window = StationWindow(manifest["dataDirectory"], profile=profile, auto_place=False,
                           network_config=network,
                           controller_factory=lambda *a, **kw: MissionController(*a, manifest=manifest, **kw))
    window.setWindowTitle("MISSION SIMULATION · " + profile.window_title)
    bridge = BoundaryBridge(window, manifest)
    bridge.on_finished = on_finished

    def configured(stations):
        network = window.station_network
        deadline = time.monotonic() + 5
        while network and not network.runtime.local_endpoint and time.monotonic() < deadline:
            time.sleep(.01)
        if network and not network.runtime.local_endpoint:
            raise ValueError("Station network could not bind its service")
        return {"endpoint": network.runtime.local_endpoint, "identity": network.runtime.info()} if network else {}

    def control(kind, body):
        if kind == 'close':
            done = threading.Event()
            bridge.close_requested.emit(done)
            if not done.wait(8):
                raise ValueError('Station did not close its recording')
            return {'sessionClosed': True, 'failure': bridge.failure}
        if kind in {"pause", "resume"}:
            window.controller.mission_paused = kind == "pause"
            if kind == 'pause':
                while not window.controller.operator_actions.empty():
                    window.controller.operator_actions.get_nowait()
            bridge.pause_state.emit(kind == "pause")
            if bridge.publisher:
                bridge.publisher.pause(kind == "pause")
            return {}
        if kind == 'ownership':
            done = threading.Event()
            bridge.ownership.emit((body, done))
            if not done.wait(8):
                raise ValueError('Station did not acknowledge ownership transfer')
            return {}
        if kind != "network-peers":
            raise ValueError("Unsupported application control")
        for endpoint in body["endpoints"]:
            window.station_network.runtime.add_endpoint(endpoint)
        return {}

    def serve():
        failure = ""
        try:
            run(manifest, before_advance=bridge.before, after_advance=bridge.after,
                configured=configured, control=control)
        except Exception as exc:  # noqa: BLE001 -- fail the participant at the thread/Qt boundary
            failure = str(exc)
        bridge.finished.emit(failure)

    thread = threading.Thread(target=serve, name="mission-connection", daemon=True)
    thread.start()
    window.show()
    return bridge


def main(path):
    path = Path(path)
    if path.stat().st_size > 1_048_576:
        raise ValueError("Mission manifest exceeds 1 MiB")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    bridge = launch(manifest)
    code = app.exec()
    return 1 if bridge.failure else code
