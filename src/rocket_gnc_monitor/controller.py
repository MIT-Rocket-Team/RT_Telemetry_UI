"""Application lifecycle; Qt sees bounded snapshots, never device or network reads."""

from __future__ import annotations
from collections import deque
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
import copy
from dataclasses import dataclass, field
import queue
from pathlib import Path
import time
from types import MappingProxyType
import uuid
from PySide6.QtCore import QObject, QTimer, Signal
from .domain import Mission, pointing, target_position, to_enu, finite
from .demo import DemoFlight
from .devices import SerialWorker, serial_device_key
from .protocol import pointer_packet
from .recording import SessionReader, SessionRecorder
from .trajectory import Trajectory, SimulationJob
from .media import DEFAULT_VIDEO_CHANNELS, VIDEO_STREAMS, VideoWorker
from .zephyrus import rocket_packet, legacy_values
from .settings import AppSettings
from .virtual_pointer import VirtualPointer, VirtualFlight, VIRTUAL_POINTER_DEVICE


@dataclass
class VideoStream:
    """One independent lifecycle queue; only its executor touches backend."""

    executor: ThreadPoolExecutor
    worker: VideoWorker | None = None
    backend: VideoWorker | None = None
    config: tuple | None = None
    generation: int = 0
    reserved_cameras: set = field(default_factory=set)
    replay_key: tuple | None = None
    replay_paused: bool = False
    lifecycle_future: Future | None = None
    error: str = ""


class Controller(QObject):
    changed = Signal()
    event = Signal(str)
    frame = Signal(bytes)  # First-stream compatibility for existing integrations.
    video_frame = Signal(str, bytes)
    video_reset = Signal(str)
    task_done = Signal(str, object)

    def __init__(self, data_dir, video_channels=None, video_labels=None, board_layout="legacy", vehicle="balius", initial_mission=None):
        super().__init__()
        if board_layout not in {"legacy", "base", "launch", "away"}:
            raise ValueError("Unknown board layout; choose legacy, launch or away")
        if vehicle not in {"balius", "iris"}:
            raise ValueError("Unknown vehicle; choose balius or iris")
        self.board_layout, self.vehicle = board_layout, vehicle
        serial_labels = {"telemetry": "Ground station" if board_layout == "legacy" else "Downlink telemetry"}
        if board_layout == "base":
            serial_labels["uplink"] = "Uplink commands"
        serial_labels["pointer"] = "Antenna pointer"
        # Launch has one local telemetry PCB. Its future uplink-enable switch
        # has no wire command yet; remote pointers use a separate station link.
        # Keep the old base layout only for legacy integrations.
        if board_layout == "launch":
            serial_labels = {"telemetry": "Telemetry board"}
        self._serial_labels = MappingProxyType(serial_labels)
        self._selected_away_station = None
        self._simulated_uplink_enabled = False
        channels = tuple(DEFAULT_VIDEO_CHANNELS if video_channels is None else video_channels)
        if not channels or any(channel not in VIDEO_STREAMS for channel in channels):
            raise ValueError("Video channels must be selected from digital, analog and analog2")
        if len(set(channels)) != len(channels):
            raise ValueError("Video channels must be unique")
        if "digital" not in channels:
            raise ValueError("Video channels must include digital")
        labels = {
            channel: "Analog 1" if channel == "analog" and "analog2" in channels else VIDEO_STREAMS[channel]
            for channel in channels
        }
        if video_labels is not None:
            if not isinstance(video_labels, Mapping) or set(video_labels) != set(channels):
                raise ValueError("Video labels must contain exactly the active video channels")
            if any(not isinstance(value, str) or not value.strip() for value in video_labels.values()):
                raise ValueError("Video labels must be nonempty text")
            labels = {channel: video_labels[channel].strip() for channel in channels}
        self._video_channels = channels
        self._video_labels = MappingProxyType(labels)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        self.settings_error = ""
        try:
            self.settings = AppSettings.load(self.settings_path)
        except ValueError as exc:
            self.settings = AppSettings()
            self.settings_error = str(exc)
        self.mission = copy.deepcopy(initial_mission).validate() if initial_mission is not None else Mission()
        self.mode = "LIVE"
        self.latest = None
        self.history = deque(maxlen=6000)
        self.track = deque(maxlen=6000)
        self.reference = None
        self.workers = dict.fromkeys(self.serial_labels)
        self.states = dict.fromkeys(self.serial_labels, "Disconnected")
        self.last_live_received = None
        self.polling = False
        self.messages = queue.Queue(maxsize=16000)
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gnc")
        self._video_streams = MappingProxyType({
            stream: VideoStream(ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"video-{stream}"))
            for stream in self.video_channels
        })
        self.last_replay_video = 0.0
        self._video_replay_cache = None
        self.futures = []
        self.generation = 0
        self.recorder = None
        self.reader = None
        self.last_session_path = None
        self.recording_close_future = None
        self.flight_busy = False
        self.last_flight_path = None
        self.simulation = None
        self.stats = dict(accepted=0, rejected=0, discarded=0, gaps=0)
        self.ui_drops = 0
        self.pointer_sent = None
        self.pointer_requested = None
        self.pointer_pending = None
        self.dispatched_commands = {}
        self.command_names = {}
        self.pointer_last_command = "—"
        self.rocket_commands = {}
        self.frozen_ground = None
        self.pointer_status = self.remote_pointer_status if board_layout == "launch" else "Disconnected"
        self.virtual_flight = None
        self.virtual_context = None
        self.last_virtual_command = 0.0
        self.tracking = False
        self.last_tracking = 0
        self.demo_time = 0
        self.demo = None
        self.demo_station = "GS2"
        self.demo_index = 0
        self.demo_replayed_rows = 0
        self.demo_playing = False
        self.demo_speed = 1.0
        self._reference_before_demo = None
        self.replay_time = 0
        self.replay_playing = False
        self.replay_speed = 1.0
        self.flight_zero = 0
        self.time_aligned = False
        self.last_tick = time.monotonic()
        self.alerts = {}
        self.alert_since = {}
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)

    def log(self, name, data=None):
        self.event.emit(name)
        if self.recorder:
            self.recorder.event(name, data)

    def submit(self, name, function):
        future = self.executor.submit(function)
        future.source_mode = self.mode
        self.futures.append((name, future))
        return future

    @property
    def serial_labels(self):
        return self._serial_labels

    @property
    def command_role(self):
        return "uplink" if self.board_layout == "base" else "telemetry" if self.board_layout in {"legacy", "launch"} else None

    @property
    def command_connected(self):
        role = self.command_role
        return (self.mode == "LIVE" and role is not None and self.workers.get(role) is not None
                and self.states.get(role) == "Connected")

    @property
    def command_block_reason(self):
        if self.board_layout == "away":
            return "Away stations cannot transmit rocket commands"
        if self.mode == "REPLAY":
            return "Replay is read-only"
        if self.mode == "LIVE" and self.board_layout == "launch":
            return "Launch uplink switch protocol not defined"
        if self.mode == "DEMO" and self.board_layout == "launch" and not self.simulated_uplink_enabled:
            return "Simulated launch uplink is OFF"
        if self.mode == "LIVE" and self.vehicle == "iris":
            return "Iris live command target protocol pending; commands are disabled"
        if self.mode == "LIVE" and not self.command_connected:
            return "Uplink board is disconnected" if self.board_layout == "base" else "Ground station is disconnected"
        return ""

    @property
    def can_command(self):
        return not self.command_block_reason

    @property
    def simulated_uplink_enabled(self):
        """DEMO-only intent; this never reports the physical board switch state."""
        return self._simulated_uplink_enabled

    def set_simulated_uplink(self, enabled):
        """Exercise the launch uplink enable switch without transmitting bytes."""
        if self.board_layout != "launch":
            raise ValueError("The simulated uplink switch belongs to the launch station")
        if self.mode != "DEMO":
            raise ValueError("The launch uplink switch can only be simulated in DEMO mode")
        if type(enabled) is not bool:
            raise ValueError("Simulated uplink enable must be a boolean")
        if enabled == self._simulated_uplink_enabled:
            return
        self._simulated_uplink_enabled = enabled
        self.log("Launch uplink switch simulated", {"enabled": enabled, "source": "DEMO", "placeholder": True})
        self.changed.emit()

    @property
    def remote_pointer_status(self):
        if self.selected_away_station is None:
            return "Choose an away-station antenna pointer · transport not configured"
        return f"Away station {self.selected_away_station[-1]} pointer · transport not configured"

    @property
    def selected_away_station(self):
        return self._selected_away_station

    def select_away_station(self, station):
        """Select a future remote route without opening a connection or sending data."""
        if self.board_layout != "launch":
            raise ValueError("Remote antenna-pointer selection belongs to the launch station")
        if station is not None and (not isinstance(station, str) or station not in {"away1", "away2", "away3", "away4"}):
            raise ValueError("Choose an away station from 1 to 4")
        if station == self.selected_away_station:
            return
        self._selected_away_station = station
        self.tracking = False
        self.virtual_flight = self.virtual_context = None
        self.pointer_sent = self.pointer_pending = self.pointer_requested = None
        self.dispatched_commands.clear()
        self.command_names.clear()
        self.pointer_last_command = "—"
        self.mission.pointer_calibrated = False
        self.frozen_ground = None
        self.pointer_status = self.remote_pointer_status
        self.log("Remote antenna pointer selected", {"station": station, "transport_configured": False})
        self.changed.emit()

    @property
    def video_channels(self):
        """The local input channels chosen when this controller was created."""
        return self._video_channels

    @property
    def video_labels(self):
        return self._video_labels

    @property
    def video_streams(self):
        """Mutable lifecycle state within an immutable set of local inputs."""
        return self._video_streams

    def _selected_video_channels(self, stream=None):
        if stream is None:
            return self.video_channels
        if stream not in self.video_streams:
            raise ValueError(f"Video channel {stream!r} is not active for this station")
        return (stream,)

    @property
    def video(self):
        """Legacy access to the first (Digital) stream."""
        return self.video_streams["digital"].worker

    @property
    def video_config(self):
        return self.video_streams["digital"].config

    @property
    def ground_connected(self):
        return (
            self.mode == "LIVE"
            and self.workers.get("telemetry") is not None
            and self.states.get("telemetry") == "Connected"
        )

    @property
    def pointer_connected(self):
        return (
            self.mode == "LIVE"
            and self.workers.get("pointer") is not None
            and self.states.get("pointer") == "Connected"
        )

    @property
    def virtual_pointer(self):
        worker = self.workers.get("pointer")
        return worker if isinstance(worker, VirtualPointer) else None

    def virtual_flight_context(self):
        m = self.mission
        return (
            id(self.reference),
            m.pointer_site_configured,
            m.pointer_latitude,
            m.pointer_longitude,
            m.pointer_altitude,
        )

    def prepare_virtual_flight(self):
        if self.board_layout == "launch":
            raise ValueError("The launch station has no local antenna pointer")
        if not self.pointer_connected or not self.virtual_pointer:
            raise ValueError("Connect the Virtual antenna pointer first")
        context = self.virtual_flight_context()
        if self.virtual_flight is None or context != self.virtual_context:
            flight = VirtualFlight(self.reference, self.mission)
            self.hold("Virtual trajectory loaded")
            self.virtual_flight, self.virtual_context = flight, context
        return self.virtual_flight

    def start_virtual_trajectory(self):
        flight = self.prepare_virtual_flight()
        self.hold("Virtual trajectory selected")
        if flight.time >= flight.end:
            flight.seek(flight.start)
        flight.playing = True
        self.last_virtual_command = 0.0
        self.last_tick = time.monotonic()
        self.log("VIRTUAL · trajectory playback started", {"origin": flight.origin, "time": flight.time})
        self.changed.emit()

    def seek_virtual_trajectory(self, seconds):
        flight = self.prepare_virtual_flight()
        self.hold("Virtual trajectory paused")
        flight.seek(seconds)
        self.send_virtual_target()
        self.changed.emit()

    def send_virtual_target(self):
        # A reference trajectory must never reach the physical serial transport.
        if not self.virtual_pointer or not self.pointer_connected:
            raise ValueError("Trajectory rehearsal requires the virtual pointer connection")
        flight = self.virtual_flight
        if flight and flight.angles and not self.pointer_pending:
            self.dispatch_pointer(pointer_packet(*flight.angles), flight.angles, "virtual trajectory")

    def advance_virtual_flight(self, seconds, now):
        flight = self.virtual_flight
        if flight is None or not self.virtual_pointer:
            return
        if self.virtual_context != self.virtual_flight_context():
            self.hold("Mission location or reference changed; restart virtual playback")
            self.virtual_flight = self.virtual_context = None
            return
        if flight.playing:
            flight.advance(seconds)
            if now - self.last_virtual_command >= 0.2 or not flight.playing:
                self.last_virtual_command = now
                self.send_virtual_target()
            if not flight.playing:
                self.log("VIRTUAL · trajectory playback completed")

    def set_polling(self, enabled):
        self.require_ground_station()
        self.polling = bool(enabled)
        self.workers["telemetry"].set_polling(self.polling)
        self.last_live_received = None
        if not enabled and self.tracking:
            self.hold("Polling stopped")
        self.log("Polling started" if enabled else "Polling stopped")
        self.changed.emit()

    def require_ground_station(self):
        if self.mode == "LIVE" and not self.ground_connected:
            raise ValueError("Ground station is disconnected")

    def rocket_link_state(self, now=None):
        """Radio reception evidence; the protocol has no bidirectional handshake."""
        if self.mode != "LIVE":
            return self.mode, None
        if not self.ground_connected:
            return "DISCONNECTED", None
        if not self.polling:
            return "PAUSED", None
        if self.last_live_received is None:
            return "WAITING", None
        age = max(0, (time.monotonic() if now is None else now) - self.last_live_received)
        return ("RECEIVING" if age <= self.mission.freshness else "STALE"), age

    def switch_mode(self, mode, force=False):
        if mode not in {"LIVE", "DEMO", "REPLAY"}:
            raise ValueError("Unknown source mode")
        if mode == self.mode and not force:
            return
        # Verify resources before altering the current source or closing a recording.
        demo = DemoFlight(self.demo_station) if mode == "DEMO" else None
        if self.mode == "DEMO":
            self.reference = self._reference_before_demo
        if mode == "DEMO":
            self._reference_before_demo = self.reference
            self.reference = None
        self.stop_recording()
        if self.reader:
            self.reader.close()
            self.reader = None
        self.last_session_path = None
        self._video_replay_cache = None
        self.tracking = False
        for role in self.workers:
            self.disconnect(role)
        self.generation += 1
        self.mode = mode
        self._simulated_uplink_enabled = False
        self.latest = None
        self.history.clear()
        self.track.clear()
        self.pointer_sent = self.pointer_pending = self.pointer_requested = None
        self.dispatched_commands.clear()
        self.command_names.clear()
        self.pointer_last_command = "—"
        self.alerts.clear()
        self.alert_since.clear()
        self.mission.pointer_calibrated = False
        self.states = {r: "Simulated" if mode == "DEMO" else "Disconnected" for r in self.workers}
        self.pointer_status = "Demo" if mode == "DEMO" else "Disconnected"
        if mode == "LIVE" and self.board_layout == "launch":
            self.pointer_status = self.remote_pointer_status
        self.replay_playing = False
        self.flight_zero = 0
        self.time_aligned = mode != "LIVE"
        self.demo = demo
        self.demo_playing = False
        self.stats = dict(accepted=0, rejected=0, discarded=0, gaps=0)
        if mode != "DEMO" and self.reference and self.reference.manifest.get("synthetic"):
            self.reference = None
        self.stop_video()
        for stream in self.video_channels:
            self.video_reset.emit(stream)
        if mode == "DEMO":
            self.seek_demo(self.demo.cue)
            self.demo_playing = True
            for stream in self.video_channels:
                self.start_video("demo", stream, stream=stream)
        self.log(f"Source changed to {mode}; physical pointer transport closed")
        self.changed.emit()

    def select_demo(self, station):
        if self.mode != "DEMO" or station == self.demo_station:
            return
        if self.recorder:
            raise ValueError("Stop logging before changing demo recordings")
        demo = DemoFlight(station)
        self.demo, self.demo_station = demo, station
        self.last_session_path = None
        self.seek_demo(demo.cue)
        self.demo_playing = True
        self.log(f"Zephyrus test flight · {station} selected")

    def seek_demo(self, elapsed):
        if self.mode != "DEMO" or self.demo is None:
            return
        if self.recorder:
            raise ValueError("Stop logging before seeking the demo")
        self.demo_time = max(0.0, min(float(elapsed), self.demo.duration))
        self.demo_index = self.demo.index_at(self.demo_time) + 1
        self.history.clear()
        self.track.clear()
        self.latest = None
        self.tracking = False
        self.pointer_sent = self.pointer_requested = None
        self.pointer_status = "Demo · simulated pointer"
        self.flight_zero, self.time_aligned = self.demo.flight_zero, True
        now = time.monotonic()
        for index in range(max(0, self.demo_index - self.history.maxlen), self.demo_index):
            sample = self.demo.sample(index, received=now - (self.demo_time - self.demo.times[index]))
            self.accept(sample)
        self.demo_replayed_rows = 0
        self.last_tick = time.monotonic()
        # History decoding is not elapsed flight time. Anchor the restored samples
        # to seek completion so slow machines preserve the recorded packet ages.
        seek_duration = self.last_tick - now
        for sample in self.history:
            sample.received += seek_duration
        self.changed.emit()

    def play_demo(self, playing=None):
        if self.mode != "DEMO":
            return
        target = not self.demo_playing if playing is None else playing
        if target and self.demo_time >= self.demo.duration:
            self.seek_demo(self.demo.cue)
        self.demo_playing = bool(target)
        self.last_tick = time.monotonic()
        if not self.demo_playing:
            self.hold("Demo paused")
        self.log("Demo playing" if self.demo_playing else "Demo paused")
        self.changed.emit()

    def advance_demo(self, seconds, now):
        if not self.demo_playing or self.demo is None:
            return
        self.demo_time = min(self.demo.duration, self.demo_time + seconds * self.demo_speed)
        while self.demo_index < len(self.demo.rows) and self.demo.times[self.demo_index] <= self.demo_time:
            self.accept(self.demo.sample(self.demo_index, received=now), record=True)
            self.demo_index += 1
            self.demo_replayed_rows += 1
        if self.demo_time >= self.demo.duration:
            self.demo_playing = False
            self.hold("Recording ended")
            self.log("Zephyrus recording ended; final received sample retained")

    def disconnect(self, role):
        if role not in self.serial_labels:
            raise ValueError(f"Unknown serial role: {role!r}")
        worker = self.workers.get(role)
        self.workers[role] = None
        if worker:
            worker.stop_event.set()
            self.submit(f"disconnect:{role}", worker.stop)
        self.states[role] = "Disconnected"
        if role == self.command_role:
            self.rocket_commands.clear()
        if role == "pointer":
            self.tracking = False
            self.virtual_flight = self.virtual_context = None
            self.pointer_pending = self.pointer_sent = None
            self.dispatched_commands.clear()
            self.command_names.clear()
            self.pointer_last_command = "—"
            self.pointer_status = "Disconnected"
            self.mission.pointer_calibrated = False
        elif role == "telemetry":
            self.frozen_ground = None
            self.polling = False
            self.last_live_received = None
            if self.tracking:
                self.hold("Ground station disconnected")

    def connect(self, role, device):
        if self.board_layout == "launch" and role != "telemetry":
            raise ValueError("The launch station has only one local telemetry board; no local antenna pointer or separate uplink board")
        if role not in self.serial_labels:
            raise ValueError(f"Unknown serial role: {role!r}")
        if self.mode != "LIVE":
            raise ValueError("Physical connections require LIVE mode")
        if not device:
            raise ValueError("Choose a serial device")
        if device == VIRTUAL_POINTER_DEVICE and role != "pointer":
            raise ValueError("The virtual device is an antenna pointer, not a ground station")
        for other, worker in self.workers.items():
            if other != role and worker and serial_device_key(worker.device) == serial_device_key(device):
                raise ValueError("One serial device cannot serve both board roles")
        self.disconnect(role)
        self.generation += 1
        token = self.generation
        self.states[role] = "Connecting"

        def raw(source, data):
            worker = self.workers.get(role)
            if worker and worker.generation == token and self.recorder:
                self.recorder.raw(source, data)

        self.workers[role] = (
            VirtualPointer(token, self.enqueue, raw)
            if device == VIRTUAL_POINTER_DEVICE
            else SerialWorker(role, device, token, self.enqueue, raw)
        )
        self.log(f"Connecting {role}: {device}")

    def enqueue(self, generation, role, kind, data):
        worker = self.workers.get(role)
        if (role == "telemetry" and worker and worker.generation == generation
                and kind == "sample" and self.polling and self.recorder):
            self.recorder.sample(data)
        try:
            self.messages.put_nowait((generation, role, kind, data))
        except queue.Full:
            self.ui_drops += 1

    def accept(self, sample, record=False):
        if self.ground_connected and self.polling and sample.source == "LIVE":
            self.last_live_received = sample.received
        if sample.enu is None and self.mission.site_configured and not sample.details.get("demo_station"):
            try:
                sample.enu = to_enu(
                    *target_position(sample, self.mission),
                    (self.mission.latitude, self.mission.longitude, self.mission.altitude),
                )
            except ValueError:
                pass
        previous = self.latest
        if (
            self.mode == "LIVE"
            and previous
            and sample.details.get("boot_index") != previous.details.get("boot_index")
        ):
            self.time_aligned = False
            self.history.clear()
            self.track.clear()
            if self.tracking:
                self.hold("Flight computer restarted")
            self.log("Flight computer time epoch changed; realign flight time")
        if self.mode == "LIVE" and sample.phase == "Flight" and previous and previous.phase == "Preflight":
            self.flight_zero, self.time_aligned = sample.t, True
            self.log(
                "Flight zero aligned to received FLIGHT state transition", {"flight_zero": self.flight_zero}
            )
        self.latest = sample
        self.history.append(sample)
        if sample.enu:
            self.track.append(sample.enu)
        if record and self.recorder:
            self.recorder.sample(sample)

    def set_zero(self):
        self.require_ground_station()
        if not self.latest:
            raise ValueError("No telemetry sample available")
        self.flight_zero = self.latest.t
        self.time_aligned = True
        self.log("Flight time manually aligned", {"flight_zero": self.flight_zero})

    def dispatch_pointer(self, packet, angles, command):
        if self.mode == "REPLAY":
            raise ValueError("Replay cannot transmit pointer commands")
        if self.mode == "LIVE" and self.board_layout == "launch":
            raise ValueError("Remote antenna-pointer transport not configured")
        if self.mode == "DEMO":
            self.pointer_sent = angles
            self.pointer_last_command = command
            self.pointer_status = "Sent " + command
            self.log("Pointer command sent", {"angles": angles, "command": command, "source": "DEMO"})
            return
        if not self.pointer_connected:
            raise ValueError("Antenna pointer is disconnected")
        if self.pointer_pending:
            raise ValueError("Previous pointer command is awaiting dispatch")
        command_id = uuid.uuid4().hex
        self.workers["pointer"].send(packet, command_id)
        self.dispatched_commands[command_id] = angles
        self.command_names[command_id] = command
        self.pointer_pending = (command_id, angles)
        self.pointer_status = "Sending " + command
        self.log(
            "Pointer command requested",
            {
                "id": command_id,
                "command": command,
                "angles": angles,
                "source": "VIRTUAL" if self.virtual_pointer else "LIVE",
            },
        )

    def send_rocket(self, command, value=None):
        if not self.can_command:
            raise ValueError(self.command_block_reason)
        if command == "advance_state":
            value = (self.latest.details.get("state_code", 0) if self.latest else 0) + 1
        packet = rocket_packet(command, value)
        if self.mode == "DEMO":
            self.log("Rocket command simulated", {"command": command, "value": value})
            return
        command_id = uuid.uuid4().hex
        self.workers[self.command_role].send(packet, command_id)
        self.rocket_commands[command_id] = {"command": command, "value": value}
        if self.board_layout == "base":
            self.rocket_commands[command_id]["serial_role"] = "uplink"
        self.log("Rocket command requested", self.rocket_commands[command_id])

    def freeze_ground_station(self):
        if self.board_layout == "launch":
            raise ValueError("The launch station has no local antenna pointer")
        if self.frozen_ground is not None:
            self.frozen_ground = None
            if self.tracking:
                self.hold("Ground GPS released")
            return
        self.require_ground_station()
        if not self.pointer_connected or self.latest is None:
            raise ValueError("Connect both boards and start polling first")
        values = legacy_values(self.latest)
        lat, lon = values["gnd_lat"], values["gnd_lon"]
        if (
            not finite(lat)
            or not finite(lon)
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
            or (lat == 0 and lon == 0)
        ):
            raise ValueError("Ground station GPS position is not available")
        self.frozen_ground = {key: values[key] for key in ("gnd_lat", "gnd_lon", "gnd_alt", "gnd_fix")}
        self.log("Ground GPS fixed for antenna pointer", self.frozen_ground)

    def point(self, azimuth, elevation):
        if self.mode == "LIVE" and self.board_layout == "launch":
            raise ValueError("Remote antenna-pointer transport not configured")
        packet = pointer_packet(azimuth, elevation)
        self.pointer_requested = (azimuth, elevation)
        self.dispatch_pointer(packet, (azimuth, elevation), "manual azimuth/elevation")

    def reference_zero(self):
        if self.virtual_pointer:
            self.hold("Virtual ZERO")
        self.dispatch_pointer(pointer_packet(opcode=5), (0.0, 0.0), "ZERO")

    def hold(self, reason="Operator hold"):
        self.tracking = False
        if self.virtual_flight:
            self.virtual_flight.playing = False
        worker = self.workers.get("pointer")
        if worker:
            try:
                _, _, command_id = worker.commands.get_nowait()
                self.dispatched_commands.pop(command_id, None)
                self.command_names.pop(command_id, None)
            except queue.Empty:
                pass
        if self.virtual_pointer:
            self.virtual_pointer.hold()
        self.pointer_pending = None
        self.pointer_status = f"Tracking off · {reason}"
        self.log(self.pointer_status)

    def jog(self, azimuth_delta=0, elevation_delta=0):
        if self.virtual_pointer:
            self.hold("Virtual manual movement")
        # These are the same firmware opcodes used by pointer.py in the old UI.
        commands = {(0, 5): (1, "UP"), (0, -5): (2, "DOWN"), (-5, 0): (3, "LEFT"), (5, 0): (4, "RIGHT")}
        try:
            opcode, name = commands[(azimuth_delta, elevation_delta)]
        except KeyError:
            raise ValueError("Use one 5-degree direction command at a time")
        angles = None
        if self.pointer_sent is not None:
            azimuth, elevation = self.pointer_sent
            angles = ((azimuth + azimuth_delta) % 360, elevation + elevation_delta)
        self.dispatch_pointer(pointer_packet(opcode=opcode), angles, name)

    def manual_point(self, azimuth, elevation):
        if self.virtual_pointer:
            self.hold("Virtual manual movement")
        self.point(azimuth, elevation)

    def start_tracking(self):
        if self.mode == "LIVE" and self.board_layout == "launch":
            raise ValueError("Remote antenna-pointer transport not configured")
        if self.virtual_pointer:
            raise ValueError("Use Follow trajectory for the virtual antenna pointer")
        if self.mode == "REPLAY":
            raise ValueError("Replay cannot drive tracking")
        if self.mode == "LIVE":
            self.require_ground_station()
            if not self.pointer_connected:
                raise ValueError("Antenna pointer is disconnected")
            if not self.polling:
                raise ValueError("Start polling first")
            self.tracking_target()
        self.tracking = True
        self.log("Tracking started" if self.mode == "LIVE" else "Demo tracking started")

    def tracking_target(self):
        if not self.latest or time.monotonic() - self.latest.received > self.mission.freshness:
            raise ValueError("Rocket position is stale")
        if self.mode == "DEMO":
            if not self.demo_playing:
                raise ValueError("Demo playback is paused")
            values = legacy_values(self.latest)
            if (
                not self.latest.enu
                or not values["gnd_fix"]
                or not all(finite(values[key]) for key in ("gnd_lat", "gnd_lon"))
            ):
                raise ValueError("Recording has no usable rocket / ground-station GPS")
            return pointing(
                (self.latest.latitude, self.latest.longitude, self.latest.altitude),
                (values["gnd_lat"], values["gnd_lon"], 0.0),
            )
        if self.latest.source != "LIVE":
            raise ValueError("Tracking needs a live source")
        if self.frozen_ground is None:
            raise ValueError("Set the ground station GPS with Send to AntPtr first")
        if not self.latest.gps_fix or not all(
            finite(v) for v in (self.latest.latitude, self.latest.longitude, self.latest.altitude)
        ):
            raise ValueError("Rocket GPS position is not available")
        # Preserve pointer.py: receiver lat/lon frozen at 5 decimals; pointer altitude 0;
        # rocket barometric altitude is the target height used by the original UI.
        origin = (self.frozen_ground["gnd_lat"], self.frozen_ground["gnd_lon"], 0.0)
        return pointing((self.latest.latitude, self.latest.longitude, self.latest.altitude), origin)

    def start_recording(self, parent):
        # A dedicated video computer can record its USB receivers without a telemetry PCB.
        if not any(state.worker and state.worker.last_frame for state in self.video_streams.values()):
            self.require_ground_station()
        if self.mode == "REPLAY":
            raise ValueError("Replay sessions are read-only")
        if self.recorder:
            raise ValueError("Already recording")
        self.recorder = SessionRecorder(parent, self.mission, self.mode, self.flight_zero, self.time_aligned)
        self.last_session_path = self.recorder.path
        self.recording_close_future = None
        if self.mode == "DEMO":
            self.recorder.manifest.update(
                demo_recording=dict(station=self.demo.station, **self.demo.metadata),
                demo_start_offset=self.demo_time,
                raw_packets_available=False,
                demo_video="Synthetic test pattern; no launch video supplied",
            )
        if self.reference:
            self.reference.save(self.recorder.path / "reference.csv")
        self.log("Recording started", {"mode": self.mode})
        for stream, state in self.video_streams.items():
            if state.config:
                self.start_video(*state.config, stream=stream)
        return self.recorder.path

    def stop_recording(self):
        if self.recorder:
            recorder, self.recorder = self.recorder, None
            recorder.event("Recording stopped")
            video_closes = [
                self.start_video(*state.config, stream=stream) if state.config else state.lifecycle_future
                for stream, state in self.video_streams.items()
            ]

            def close_recording():
                for future in video_closes:
                    if future:
                        try:
                            future.result()
                        except Exception as exc:
                            # A failed camera open must not strand the telemetry
                            # recorder or prevent the other feed from finalizing.
                            recorder.event("Video lifecycle error", {"error": str(exc)})
                return recorder.close(), recorder.error, str(recorder.path)

            self.recording_close_future = self.submit("recording_closed", close_recording)

    def save_flight(self, path):
        from .flight import save_flight

        if self.flight_busy:
            raise ValueError("A flight file is already being opened or saved")
        mission, reference = copy.deepcopy(self.mission), copy.deepcopy(self.reference)
        session = (
            self.recorder.path
            if self.recorder
            else self.reader.path
            if self.reader
            else self.last_session_path
        )
        active = self.recorder is not None
        demo = self.demo if self.mode == "DEMO" and session is None else None
        samples = copy.deepcopy(list(self.history)) if session is None and demo is None else []
        elapsed = [max(0, s.received - samples[0].received) for s in samples]
        position = (
            self.replay_time if self.reader else self.demo_time if demo else elapsed[-1] if elapsed else 0
        )
        scope = (
            "Recording snapshot"
            if active
            else "Recorded session"
            if session
            else f"Complete Zephyrus {demo.station} dataset"
            if demo
            else "Displayed telemetry buffer only (up to 6,000 samples)"
            if samples
            else "Mission and simulation"
        )
        mode, zero, aligned = self.mode, self.flight_zero, self.time_aligned
        pending = (
            self.recording_close_future if session is not None and not active and not self.reader else None
        )
        self.flight_busy = True
        self.submit(
            "flight_saved",
            lambda: save_flight(
                path,
                mission,
                reference,
                session=session,
                active=active,
                samples=samples,
                elapsed=elapsed,
                mode=mode,
                flight_zero=zero,
                time_aligned=aligned,
                position=position,
                scope=scope,
                demo=demo,
                pending_close=pending,
            ),
        )
        self.log("Saving flight · " + scope)

    def open_flight(self, path):
        from .flight import load_flight

        if self.flight_busy:
            raise ValueError("A flight file is already being opened or saved")
        if self.recorder:
            raise ValueError("Stop logging before opening another flight")
        if self.simulation:
            raise ValueError("Finish or cancel the simulation before opening another flight")
        self.flight_busy = True
        future = self.submit("flight_loaded", lambda: load_flight(path, self.data_dir / "flights"))
        future.flight_context = (self.generation, copy.deepcopy(self.mission))
        self.log("Opening flight file…")

    def apply_flight(self, flight):
        if flight.session:
            self.open_replay(flight.session)
        else:
            self.switch_mode("LIVE", force=True)
        self.mission = flight.mission
        self.reference = flight.reference
        if flight.session:
            self.seek(flight.metadata.get("position", 0))
        self.flight_zero = flight.metadata.get("flight_zero", self.flight_zero)
        self.time_aligned = flight.metadata.get("time_aligned", self.time_aligned)
        self.last_flight_path = flight.path
        self.log(f"Flight opened: {flight.path.name} · {flight.metadata['scope']}")
        self.changed.emit()

    def open_replay(self, path):
        reader = SessionReader(path)
        try:
            values = reader.manifest.get("mission", {})
            mission = Mission(
                **{k: v for k, v in values.items() if k in Mission.__dataclass_fields__}
            ).validate()
            mission.pointer_calibrated = False
            ref = reader.path / "reference.csv"
            reference = Trajectory.load(ref) if ref.exists() else None
        except Exception:
            reader.close()
            raise
        self.switch_mode("REPLAY", force=True)
        self.reader = reader
        self.last_session_path = reader.path
        self.mission = mission
        self.flight_zero = reader.manifest.get("flight_zero", 0)
        self.reference = reference
        self.seek(0)
        self.log(
            f"Replay loaded: {reader.count} samples; "
            + (
                "recording snapshot"
                if reader.manifest.get("snapshot")
                else "complete"
                if reader.manifest.get("complete")
                else "recovered/incomplete"
            )
        )

    def seek(self, elapsed):
        if not self.reader:
            return
        self.replay_time = max(0, min(elapsed, self.reader.duration))
        self.history.clear()
        self.track.clear()
        self.latest = None
        self.pointer_sent = None
        self.flight_zero = self.reader.manifest.get("flight_zero", 0)
        self.time_aligned = self.reader.manifest.get("time_aligned", True)
        for _, event in self.reader.events(self.replay_time):
            self.apply_replay_event(event)
        for _, sample in self.reader.between(max(-1, self.replay_time - 120), self.replay_time):
            sample.received = time.monotonic()
            self.accept(sample)
        self.replay_video()
        self.changed.emit()

    def apply_replay_event(self, event):
        if "flight_zero" in event["data"]:
            self.flight_zero = event["data"]["flight_zero"]
            self.time_aligned = True
        if event["name"] == "Pointer command sent":
            angles = event["data"].get("angles")
            self.pointer_sent = tuple(angles) if angles is not None else None
            self.pointer_last_command = event["data"].get("command", "azimuth/elevation")
            self.pointer_status = "Replay · " + self.pointer_last_command

    def acknowledge_alerts(self):
        for key, alert in self.alerts.items():
            if not alert["acknowledged"]:
                alert["acknowledged"] = True
                self.log("Alert acknowledged", {"key": key, "message": alert["message"]})

    def update_alerts(self, now):
        if self.mode != "LIVE":
            self.alerts.clear()
            self.alert_since.clear()
            return
        stale = not self.latest or now - self.latest.received > self.mission.freshness
        battery = self.latest.battery if self.latest else None
        threshold = self.mission.low_battery + (0.3 if "battery" in self.alerts else 0)
        conditions = {
            "telemetry": (self.ground_connected and self.polling and stale, "Telemetry stale / absent", 0),
            "battery": (not stale and battery is not None and battery < threshold, "Low battery", 1),
            "recording": (bool(self.recorder and self.recorder.error), "Recording fault / loss", 0),
        }
        for stream, state in self.video_streams.items():
            worker = state.worker
            conditions[f"video_{stream}"] = (
                bool(worker and (worker.error or (worker.last_frame and now - worker.last_frame > 2))),
                f"{self.video_labels[stream]} video ended / stale",
                1,
            )
        for key, (active, message, dwell) in conditions.items():
            if active:
                since = self.alert_since.setdefault(key, now)
                if now - since >= dwell and key not in self.alerts:
                    self.alerts[key] = {"message": message, "acknowledged": False}
                    self.log("Alert active: " + message, {"key": key})
            else:
                self.alert_since.pop(key, None)
                if self.alerts.pop(key, None):
                    self.log("Alert cleared: " + message, {"key": key})

    def replay_video(self, stream=None, force=True):
        channels = self._selected_video_channels(stream)
        if not self.reader:
            return
        import csv

        # A replay reader is a fixed session snapshot. Cache its media inventory;
        # segment transitions must not rescan the entire event log every UI tick.
        if self._video_replay_cache is None or self._video_replay_cache[0] is not self.reader:
            events = [
                (t, e)
                for t, e in self.reader.events(self.reader.duration)
                if e["name"] == "Video recording started"
            ]
            self._video_replay_cache = (self.reader, events, {})
        _, starts, indexes = self._video_replay_cache
        events = [(t, e) for t, e in starts if t <= self.replay_time - self.mission.video_offset]
        for channel in channels:
            state = self.video_streams[channel]
            if force:
                state.replay_paused = False
            elif state.replay_paused:
                continue
            eligible = [(t, e) for t, e in events if e["data"].get("stream", "digital") == channel]
            target = None
            if eligible:
                start, event = eligible[-1]
                directory = self.reader.path / Path(event["data"].get("directory", "video")).name
                listing = directory / "segments.csv"
                cursor = self.replay_time - start - self.mission.video_offset
                if directory not in indexes:
                    segments = []
                    if listing.exists():
                        with listing.open(newline="") as handle:
                            for name, begin, end in csv.reader(handle):
                                path = directory / Path(name).name
                                if path.is_file():
                                    segments.append((str(path), float(begin), float(end)))
                    indexes[directory] = segments
                for path, begin, end in indexes[directory]:
                    if begin <= cursor < end:
                        target = (path, cursor - begin)
                        break
            if target:
                key = (target[0], self.replay_speed, self.replay_playing)
                if force or state.replay_key != key:
                    self.start_video("file", *target, stream=channel)
                    state.replay_key = key
            elif state.config or state.replay_key:
                self.stop_video(channel)
                self.video_reset.emit(channel)

    def start_video(self, kind, source="", seek=0, *, stream="digital"):
        if stream is None:
            raise ValueError("Choose an active video channel")
        self._selected_video_channels(stream)
        state = self.video_streams[stream]
        if kind == "camera":
            if source is None or source == "":
                raise ValueError("Choose a USB camera")
            source = str(source)
            for other, other_state in self.video_streams.items():
                if other != stream and source in other_state.reserved_cameras:
                    raise ValueError(f"This USB camera is already assigned to {self.video_labels[other]}")
            state.reserved_cameras.add(source)
        state.generation += 1
        token = state.generation
        state.worker = None
        state.config = (kind, source)
        state.error = ""
        state.replay_key = None
        self.video_reset.emit(stream)
        # Every start owns a distinct directory, including rapid reconnects before
        # the previous asynchronous open has had time to create its directory.
        directory = self.recorder.path / f"video_{stream}_{uuid.uuid4().hex}" if self.recorder else None
        if directory:
            self.log("Video recording started", {"directory": directory.name, "kind": kind, "stream": stream})
        speed = self.replay_speed if self.mode == "REPLAY" else 1.0
        single_frame = self.mode == "REPLAY" and not self.replay_playing

        def open_video():
            if state.backend:
                state.backend.stop()
                state.backend = None
            if token != state.generation:
                return stream, token, None
            state.backend = VideoWorker(kind, source, directory, seek, speed, single_frame)
            return stream, token, state.backend

        future = state.lifecycle_future = state.executor.submit(open_video)
        future.video_context = (stream, token)
        self.futures.append(("video_open", future))
        return future

    def stop_video(self, stream=None, *, pause_replay=False):
        futures = []
        for channel in self._selected_video_channels(stream):
            state = self.video_streams[channel]
            state.generation += 1
            token = state.generation
            state.worker = None
            state.config = None
            state.error = ""
            state.replay_key = None
            state.replay_paused = pause_replay and self.mode == "REPLAY"

            def close_video(state=state, channel=channel, token=token):
                if state.backend:
                    state.backend.stop()
                    state.backend = None
                return channel, token

            future = state.lifecycle_future = state.executor.submit(close_video)
            self.futures.append(("video_closed", future))
            futures.append(future)
        return futures

    def run_simulation(self):
        if self.simulation:
            raise ValueError("A simulation is already running")
        mission = copy.deepcopy(self.mission).validate()
        if not mission.site_configured:
            raise ValueError("Configure the launch site before simulation")
        job = SimulationJob(mission, self.data_dir / "simulation" / uuid.uuid4().hex, self.settings)
        self.simulation = job
        self.submit("simulation", job.run)
        self.log("OpenRocket nominal simulation started")

    def save_settings(self, settings):
        settings.save(self.settings_path)
        self.settings = settings
        self.settings_error = ""

    def tick(self):
        now = time.monotonic()
        dt, self.last_tick = min(now - self.last_tick, 0.3), now
        if self.virtual_pointer:
            self.virtual_pointer.advance(dt)
        for _ in range(2000):
            try:
                generation, role, kind, data = self.messages.get_nowait()
            except queue.Empty:
                break
            worker = self.workers.get(role)
            if not worker or worker.generation != generation or self.mode != "LIVE":
                continue
            if role == "telemetry" and kind == "sample" and self.polling and self.states[role] == "Connected":
                self.accept(data)
            elif role == "telemetry" and kind == "stats":
                self.stats = data
            elif kind == "connected":
                self.states[role] = "Connected"
                if role == "pointer":
                    self.pointer_status = "Virtual pointer connected" if self.virtual_pointer else "Connected"
                    if self.virtual_pointer:
                        self.pointer_sent = self.virtual_pointer.target
                self.log(f"{role.title()} connected")
            elif kind in {"error", "disconnected"}:
                self.disconnect(role)
                self.log(f"{role.title()}: {data}")
                if role == "telemetry":
                    self.frozen_ground = None
                    self.polling = False
                    self.last_live_received = None
                if (role in {"telemetry", "pointer"} and self.tracking) or (role == "pointer" and self.pointer_pending):
                    self.hold(f"{role} unavailable")
                if role == "pointer":
                    self.pointer_sent = self.pointer_pending = None
                    self.mission.pointer_calibrated = False
                    self.pointer_status = "Disconnected"
            elif kind == "sent" and role == self.command_role:
                command = self.rocket_commands.pop(data, None)
                if command:
                    self.log("Rocket command sent", command)
            elif kind == "expired" and role == self.command_role:
                command = self.rocket_commands.pop(data, None)
                if command:
                    self.log("Rocket command expired; not sent", command)
            elif kind == "sent" and role == "pointer" and data in self.dispatched_commands:
                self.pointer_sent = self.dispatched_commands.pop(data)
                if self.virtual_pointer:
                    self.pointer_sent = self.virtual_pointer.target
                self.pointer_last_command = self.command_names.pop(data, "azimuth/elevation")
                if self.pointer_pending and data == self.pointer_pending[0]:
                    self.pointer_pending = None
                    self.pointer_status = (
                        "VIRTUAL · " if self.virtual_pointer else "Sent "
                    ) + self.pointer_last_command
                self.log(
                    "Pointer command sent",
                    {
                        "id": data,
                        "angles": self.pointer_sent,
                        "command": self.pointer_last_command,
                        "source": "VIRTUAL" if self.virtual_pointer else "LIVE",
                    },
                )
            elif kind == "expired" and role == "pointer":
                self.dispatched_commands.pop(data, None)
                self.command_names.pop(data, None)
                self.hold("Queued command expired")
            elif kind == "rx":
                self.log(f"{role.title()} RX: " + data[:140])
        if self.mode == "DEMO":
            self.advance_demo(dt, now)
        elif self.mode == "REPLAY" and self.reader and self.replay_playing:
            begin = self.replay_time
            self.replay_time = min(self.reader.duration, begin + dt * self.replay_speed)
            for _, event in self.reader.events(self.replay_time, begin):
                self.apply_replay_event(event)
                self.event.emit("REPLAY · " + event["name"])
            for _, sample in self.reader.between(begin, self.replay_time):
                sample.received = now
                self.accept(sample)
            if self.replay_time >= self.reader.duration:
                self.replay_playing = False
                self.stop_video()
            elif now - self.last_replay_video >= 0.2:
                self.last_replay_video = now
                self.replay_video(force=False)
        self.advance_virtual_flight(dt, now)
        if self.tracking and now - self.last_tracking >= 0.2:
            self.last_tracking = now
            try:
                target = self.tracking_target()
                if not self.pointer_pending:
                    self.point(*target)
            except (ValueError, queue.Full) as exc:
                self.hold(str(exc))
        for stream, state in self.video_streams.items():
            if state.worker:
                frame = None
                while not state.worker.frames.empty():
                    try:
                        frame = state.worker.frames.get_nowait()
                    except queue.Empty:
                        break
                if frame:
                    self.video_frame.emit(stream, frame)
                    if stream == "digital":
                        self.frame.emit(frame)
        for name, future in self.futures[:]:
            if future.done():
                self.futures.remove((name, future))
                try:
                    result = future.result()
                    if name in {"weather", "reference", "simulation"} and future.source_mode != self.mode:
                        raise ValueError(
                            "Completed result belongs to the previous source mode; load it explicitly if needed"
                        )
                    if name == "simulation" and self.simulation:
                        fields = (
                            "latitude",
                            "longitude",
                            "altitude",
                            "altitude_msl",
                            "model",
                            "motor_files",
                            "wind",
                            "rail_length",
                            "rail_tilt",
                            "rail_heading",
                            "seed",
                            "simulation_index",
                        )
                        if any(
                            getattr(self.simulation.mission, k) != getattr(self.mission, k) for k in fields
                        ):
                            raise ValueError(
                                "Simulation inputs changed while running; result remains in its job folder"
                            )
                    if name == "video_open":
                        stream, token, worker = result
                        state = self.video_streams[stream]
                        if token == state.generation:
                            state.worker = worker
                            state.reserved_cameras = (
                                {state.config[1]} if state.config and state.config[0] == "camera" else set()
                            )
                    elif name == "video_closed":
                        stream, token = result
                        state = self.video_streams[stream]
                        if token == state.generation:
                            state.reserved_cameras.clear()
                    if name == "flight_loaded":
                        if self.recorder or future.flight_context != (self.generation, self.mission):
                            raise ValueError(
                                "Flight settings or source changed while opening; open the file again"
                            )
                        self.apply_flight(result)
                    elif name == "flight_saved":
                        self.last_flight_path = Path(result["path"])
                        self.log(f"Flight saved: {result['path']} · {result['samples']} samples")
                    if name in {"flight_loaded", "flight_saved"}:
                        self.flight_busy = False
                    self.task_done.emit(name, result)
                except Exception as exc:
                    if name == "video_open":
                        stream, token = future.video_context
                        state = self.video_streams[stream]
                        if token == state.generation:
                            state.config = None
                            state.reserved_cameras.clear()
                            state.error = str(exc)
                    if name in {"flight_loaded", "flight_saved"}:
                        self.flight_busy = False
                    self.log(f"{name}: {exc}")
                    self.task_done.emit(name, exc)
                if name == "simulation":
                    self.simulation = None
        self.update_alerts(now)
        self.changed.emit()

    def shutdown(self):
        if getattr(self, "_shutdown", False):
            return
        self._shutdown = True
        self.timer.stop()
        for role in self.workers:
            worker = self.workers[role]
            self.workers[role] = None
            if worker:
                worker.stop()
        self.stop_video()
        for state in self.video_streams.values():
            state.executor.shutdown(wait=True, cancel_futures=False)
        if self.simulation:
            self.simulation.cancel()
        if self.recorder:
            self.recorder.close()
        if self.reader:
            self.reader.close()
        # A queued recorder close must finish even if the operator closes the app immediately.
        self.executor.shutdown(wait=True, cancel_futures=False)
