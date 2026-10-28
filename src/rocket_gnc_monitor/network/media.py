"""Shared H.264 encoders and explicitly leased, token-bound SRT sessions."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import asyncio
import copy
import hmac
import queue
import secrets
import subprocess
import threading
import time
import uuid

from ..media import WIDTH, HEIGHT, ffmpeg, popen_options
from .media_srt import SRTSocket


class MediaError(Exception):
    def __init__(self, status, code, message, retryable=False):
        super().__init__(message)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def channel_ids(profile):
    if hasattr(profile, "channel_identifiers"):
        return profile.channel_identifiers
    if profile.vehicle == "balius":
        return {"digital": "balius.digital", "analog": "balius.analog"}
    return {"digital": "sustainer.digital", "analog": "sustainer.analog", "analog2": "booster.analog"}


RENDITIONS = {
    "preview": dict(width=320, height=180, fps=10, target_bitrate=250000, max_bitrate=300000),
    "main": dict(width=960, height=540, fps=30, target_bitrate=3000000, max_bitrate=4000000),
}


def _replace_pending(target, value):
    try:
        target.put_nowait(value)
    except queue.Full:
        try:
            target.get_nowait()
        except queue.Empty:
            pass
        try:
            target.put_nowait(value)
        except queue.Full:
            pass


def _stop_process(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


class Encoder:
    """One encoder per rendition. Each viewer has its own bounded output queue."""

    def __init__(self, channel, rendition):
        self.channel, self.rendition = channel, rendition
        self.frames = queue.Queue(maxsize=2)
        self.sinks = set()
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.error = ""
        self.logs = deque(maxlen=12)
        self.process = None
        self.unused_since = None
        self.last_submitted = None
        self.submitted = deque(maxlen=256)
        self.output_times = deque(maxlen=120)
        self.timing = None
        self._last_pts = None
        self._pts_wrap = 0
        self.first_capture = None
        self.thread = threading.Thread(target=self.run, name=f"encode-{rendition}", daemon=True)
        self.thread.start()

    def arguments(self):
        spec = RENDITIONS[self.rendition]
        return [ffmpeg(), "-hide_banner", "-loglevel", "error",
                "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{WIDTH}x{HEIGHT}",
                "-framerate", str(spec["fps"]), "-use_wallclock_as_timestamps", "1", "-i", "pipe:0",
                "-an", "-vf", f"scale={spec['width']}:{spec['height']}", "-fps_mode", "passthrough",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-profile:v", "baseline", "-level:v", "3.1", "-pix_fmt", "yuv420p",
                "-bf", "0", "-g", str(spec["fps"]), "-keyint_min", str(spec["fps"]),
                "-force_key_frames", "expr:gte(t,n_forced*1)",
                "-sc_threshold", "0", "-x264-params", "repeat-headers=1:aud=1",
                "-b:v", str(spec["target_bitrate"]), "-maxrate", str(spec["max_bitrate"]),
                "-bufsize", str(spec["max_bitrate"]), "-threads", "1",
                "-output_ts_offset", str(max(0.0, (self.first_capture or self.channel.timeline_origin)
                                               - self.channel.timeline_origin)),
                "-f", "mpegts", "-mpegts_flags", "resend_headers", "-pat_period", "0.1",
                "-pcr_period", "20", "-muxdelay", "0", "-flush_packets", "1", "pipe:1"]

    def add(self, session):
        with self.lock:
            self.sinks.add(session)
            self.unused_since = None

    def remove(self, session):
        with self.lock:
            self.sinks.discard(session)
            if not self.sinks:
                self.unused_since = time.monotonic()

    def feed(self, frame, stamp):
        interval = 1.0 / RENDITIONS[self.rendition]["fps"]
        if (not self.stopped.is_set() and
                (self.last_submitted is None or stamp - self.last_submitted >= interval * .95)):
            self.last_submitted = stamp
            _replace_pending(self.frames, (frame, stamp, self.channel.capture_counter))

    def run(self):
        try:
            first = self.frames.get(timeout=5)
            self.first_capture = first[1]
            self.process = subprocess.Popen(self.arguments(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, bufsize=0, **popen_options())
            reader = threading.Thread(target=self.read_packets, name="mpegts-fanout", daemon=True)
            logger = threading.Thread(target=self.read_logs, daemon=True)
            reader.start()
            logger.start()
            while not self.stopped.is_set():
                try:
                    frame, stamp, counter = first if first is not None else self.frames.get(timeout=.2)
                    first = None
                except queue.Empty:
                    if self.process.poll() is not None:
                        raise RuntimeError("Encoder exited: " + " · ".join(self.logs))
                    continue
                with self.lock:
                    self.submitted.append((stamp, counter))
                data = memoryview(frame)
                while data:
                    written = self.process.stdin.write(data)
                    if not written:
                        raise OSError("Encoder input pipe closed")
                    data = data[written:]
            self.process.stdin.close()
        except Exception as exc:
            if not self.stopped.is_set():
                self.error = str(exc)
        finally:
            self.stopped.set()
            _stop_process(self.process)

    def read_logs(self):
        try:
            for line in iter(self.process.stderr.readline, b""):
                self.logs.append(line.decode(errors="replace")[-500:].strip())
        except (ValueError, OSError):
            pass

    def _observe_pts(self, packets):
        # One PES start per encoded frame: no B-frames or temporal duplication.
        # Associate actual muxed PTS with the exact input frame submitted to x264.
        for offset in range(0, len(packets), 188):
            packet = packets[offset:offset + 188]
            if packet[0] != 0x47 or not packet[1] & 0x40:
                continue
            control = (packet[3] >> 4) & 3
            if not control & 1:
                continue
            start = 5 + packet[4] if control & 2 else 4
            pes = packet[start:]
            if len(pes) < 14 or pes[:3] != b"\0\0\1" or pes[3] & 0xf0 != 0xe0 or not pes[7] & 0x80:
                continue
            pts = (((pes[9] >> 1) & 7) << 30) | (pes[10] << 22) | ((pes[11] >> 1) << 15) | (pes[12] << 7) | (pes[13] >> 1)
            if self._last_pts is not None and pts < self._last_pts - (1 << 32):
                self._pts_wrap += 1 << 33
            self._last_pts = pts
            with self.lock:
                matched = self.submitted.popleft() if self.submitted else None
            if matched is None:
                self.timing = None
                continue
            stamp, counter = matched
            self.output_times.append(time.monotonic())
            self.timing = dict(media_epoch=self.channel.epoch, pts_90k=str(pts + self._pts_wrap),
                               capture_frame_id=str(counter),
                               capture_mono_ms=str(max(0, round((stamp - self.channel.clock_origin) * 1000))),
                               capture_time_utc=datetime.fromtimestamp(time.time() - time.monotonic() + stamp,
                                                                     timezone.utc).isoformat(),
                               clock_uncertainty_ms=None, capture_delay_uncertainty_ms=None,
                               capture_timing_basis="host_frame_delivery_after_decode")

    def read_packets(self):
        try:
            pending = bytearray()
            while not self.stopped.is_set():
                data = self.process.stdout.read(1316)
                if not data:
                    break
                pending.extend(data)
                while len(pending) >= 188:
                    length = min(len(pending) // 188 * 188, 1316)
                    packet = bytes(pending[:length])
                    del pending[:length]
                    self._observe_pts(packet)
                    with self.lock:
                        sinks = list(self.sinks)
                    for sink in sinks:
                        sink.enqueue(packet)
        except (OSError, ValueError):
            pass

    def stop(self):
        self.stopped.set()
        _stop_process(self.process)
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=2)


@dataclass
class Channel:
    local_key: str
    channel_id: str
    stream_id: str
    epoch: str = field(default_factory=lambda: str(uuid.uuid4()))
    source: object = None
    encoders: dict = field(default_factory=dict)
    last_frame: float | None = None
    listener: object = None
    source_mode: str = "live"
    frame_times: deque = field(default_factory=lambda: deque(maxlen=120))
    capture_counter: int = 0
    timeline_origin: float = field(default_factory=time.monotonic)
    clock_origin: float = field(default_factory=time.monotonic)


class Session:
    def __init__(self, listener, port, channel, rendition, token, session_id, request, host, boot):
        self.listener, self.port = listener, port
        self.channel, self.rendition = channel, rendition
        self.token, self.session_id, self.request = token, session_id, request
        self.expiry = time.monotonic() + 30
        self.stopped = threading.Event()
        self.queue = queue.Queue(maxsize=256)
        self.connection = None
        self.error = ""
        self.ended = None
        encoding = copy.deepcopy(RENDITIONS[rendition])
        encoding["fps_limit"], encoding["fps"] = encoding["fps"], None
        self.response = dict(session_id=session_id, session_token=token, server_boot_id=boot,
                             media_epoch=channel.epoch, stream_id=channel.stream_id, rendition=rendition,
                             lease_ms=30000, encoding=encoding,
                             transport=dict(host=host, port=port, mode="caller", transport="srt",
                                            srt_streamid=f"rt1:{session_id}:{token}", latency_ms=120,
                                            container="mpegts", codec="h264"))
        self.thread = threading.Thread(target=self.run, name="srt-viewer", daemon=True)

    def enqueue(self, data):
        if self.stopped.is_set() or self.connection is None:
            return
        try:
            self.queue.put_nowait((time.monotonic(), data))
        except queue.Full:
            self.error = "Viewer fell behind the bounded media queue"
            self.close()

    def run(self):
        try:
            self.connection = self.listener.accept()
            while not self.stopped.is_set():
                try:
                    stamp, data = self.queue.get(timeout=.2)
                except queue.Empty:
                    continue
                if time.monotonic() - stamp > 2:
                    raise RuntimeError("Media send queue exceeded two seconds")
                self.connection.send(data)
        except Exception as exc:
            if not self.stopped.is_set():
                self.error = str(exc)
        finally:
            self.close()

    def close(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        self.ended = time.monotonic()
        self.listener.close()
        if self.connection:
            self.connection.close()
        encoder = self.channel.encoders.get(self.rendition)
        if encoder:
            encoder.remove(self)


class MediaService:
    """HTTP session backend. Supply existing VideoWorkers through set_source().

    The service never opens a camera. It subscribes to the one local capture owner.
    notify receives the current manifest after a material change.
    """

    def __init__(self, profile, *, port_start=55000, port_end=55031, bind_host="0.0.0.0", notify=None,
                 allowed_source_modes=("live",)):
        self.profile, self.bind_host, self.notify = profile, bind_host, notify
        self.allowed_source_modes = set(allowed_source_modes)
        if not self.allowed_source_modes <= {"live", "replay", "simulation"}:
            raise ValueError("Invalid media source mode")
        station = "launch" if profile.station == "base" else "gs" + profile.station[-1]
        mapping = channel_ids(profile)
        self.channels = {key: Channel(key, mapping[key], f"{station}.{mapping[key]}")
                         for key in profile.local_channels} if profile.role == "video" else {}
        self.station = station
        self.ports = tuple(range(port_start, port_end + 1))
        self.sessions, self.requests = {}, {}
        self._availability = None
        self._last_timing_publish = 0.0
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.sweeper = threading.Thread(target=self._sweep, name="media-leases", daemon=True)
        self.sweeper.start()

    def _changed(self):
        if self.notify:
            self.notify(self.streams())

    def set_context(self, identity, clock_origin):
        self.identity = dict(identity)
        with self.lock:
            for channel in self.channels.values():
                channel.clock_origin = clock_origin
                if channel.source and hasattr(channel.source, "set_recording_context"):
                    channel.source.set_recording_context(dict(identity, stream_id=channel.stream_id,
                                                              media_epoch=channel.epoch))

    def set_source(self, key, worker):
        with self.lock:
            channel = self.channels.get(key)
            if channel is None or channel.source is worker:
                return
            if channel.source is not None and channel.listener:
                channel.source.remove_frame_listener(channel.listener)
            for session in list(self.sessions.values()):
                if session.channel is channel:
                    session.close()
            for encoder in channel.encoders.values():
                encoder.stop()
            channel.encoders.clear()
            channel.source, channel.last_frame = worker, None
            channel.frame_times.clear()
            channel.capture_counter = 0
            channel.timeline_origin = time.monotonic()
            channel.source_mode = getattr(worker, "source_mode", None) or (
                "live" if worker is None or worker.kind == "camera" else "replay")
            channel.epoch = str(uuid.uuid4())

            def frame_arrived(frame, stamp):
                channel.last_frame = stamp
                channel.frame_times.append(stamp)
                channel.capture_counter += 1
                for encoder in tuple(channel.encoders.values()):
                    encoder.feed(frame, stamp)

            channel.listener = frame_arrived
            if worker is not None:
                worker.add_frame_listener(frame_arrived)
                if hasattr(worker, "set_recording_context"):
                    worker.set_recording_context(dict(getattr(self, "identity", {}),
                                                       stream_id=channel.stream_id, media_epoch=channel.epoch))
        self._changed()

    def streams(self):
        with self.lock:
            entries = []
            now = time.monotonic()
            for channel in self.channels.values():
                connected = channel.source is not None and not channel.source.error
                available = connected and channel.last_frame is not None and now - channel.last_frame < 5
                stamps = tuple(channel.frame_times)
                source_fps = ((len(stamps) - 1) / (stamps[-1] - stamps[0])
                              if len(stamps) > 1 and stamps[-1] > stamps[0] else None)
                entries.append(dict(stream_id=channel.stream_id, channel_id=channel.channel_id,
                                    station=self.station, vehicle=channel.channel_id.split(".")[0],
                                    radio_type=getattr(self.profile, "channel_kinds", {}).get(channel.local_key, channel.channel_id.split(".")[-1]), source_mode=channel.source_mode,
                                    capture_state="connected" if connected else "disconnected", rf_lock=None,
                                    source_fps=source_fps, source_resolution=None, decoded_resolution=[WIDTH, HEIGHT],
                                    capture_timing_basis="host_frame_delivery_after_decode",
                                    capture_delay_uncertainty_ms=None,
                                    media_epoch=channel.epoch,
                                    renditions=[self._rendition(channel, name, spec, available)
                                                for name, spec in RENDITIONS.items()]))
            return entries

    @staticmethod
    def _rendition(channel, name, spec, available):
        encoder = channel.encoders.get(name)
        stamps = tuple(encoder.output_times) if encoder else ()
        measured = ((len(stamps) - 1) / (stamps[-1] - stamps[0])
                    if len(stamps) > 1 and stamps[-1] > stamps[0] else None)
        result = dict(name=name, codec="h264", container="mpegts", transport="srt", **copy.deepcopy(spec))
        result["fps_limit"], result["fps"] = result["fps"], measured
        result.update(available=bool(available and not (encoder and encoder.error)),
                      reason=encoder.error if encoder and encoder.error else "" if available else "No recent source frame",
                      timing=copy.deepcopy(encoder.timing) if encoder else None)
        return result

    def input_status(self):
        with self.lock:
            now = time.monotonic()
            entries = []
            for channel in self.channels.values():
                source = channel.source
                error = source.error if source else ""
                stamps = tuple(channel.frame_times)
                fps = ((len(stamps) - 1) / (stamps[-1] - stamps[0])
                       if len(stamps) > 1 and stamps[-1] > stamps[0] else None)
                entries.append(dict(channel_id=channel.channel_id, stream_id=channel.stream_id,
                                    source_mode=channel.source_mode, media_epoch=channel.epoch,
                                    availability="error" if error else "connected" if source else "disconnected",
                                    changed_mono_ms=str(max(0, round((channel.timeline_origin-channel.clock_origin)*1000))),
                                    reason=error or ("" if source else "Camera not connected"),
                                    last_source_frame_age_ms=None if channel.last_frame is None else
                                        max(0, round((now-channel.last_frame)*1000)),
                                    source_fps=fps, source_resolution=None, decoded_resolution=[WIDTH, HEIGHT],
                                    pipeline_error=error or None, rf_lock=None, rf_quality=None,
                                    recording_error=getattr(source, "recording_error", None)))
            return entries

    async def create_session(self, request, context):
        return await asyncio.to_thread(self._create, request, context)

    def _create(self, request, context):
        required = {"request_id", "client_node_id", "client_boot_id", "mission_id", "expected_server_boot_id",
                    "stream_id", "rendition", "expected_media_epoch"}
        if not isinstance(request, dict) or not required.issubset(request):
            raise MediaError(400, "invalid_request", "Missing required media session fields")
        for key in ("request_id", "client_node_id", "client_boot_id", "mission_id", "expected_server_boot_id",
                    "expected_media_epoch"):
            try:
                uuid.UUID(request[key])
            except (ValueError, TypeError, AttributeError):
                raise MediaError(400, "invalid_request", f"{key} must be a UUID") from None
        if request["expected_server_boot_id"] != context["boot_id"]:
            raise MediaError(409, "boot_changed", "Rediscover this publisher after its restart")
        if request["mission_id"] != context["mission_id"]:
            raise MediaError(409, "mission_mismatch", "Media publisher belongs to another mission")
        with self.lock:
            previous = self.requests.get(request["request_id"])
            if previous:
                if previous.request != request:
                    raise MediaError(409, "request_conflict", "Request ID was already used with different fields")
                if previous.stopped.is_set():
                    raise MediaError(410, "session_expired", "The original allocated session ended")
                return copy.deepcopy(previous.response)
            channel = next((c for c in self.channels.values() if c.stream_id == request["stream_id"]), None)
            if channel is None or request["rendition"] not in RENDITIONS:
                raise MediaError(404, "not_found", "Unknown stream or rendition")
            if request["expected_media_epoch"] != channel.epoch:
                raise MediaError(409, "media_epoch_changed", "Refresh the stream manifest")
            if (channel.source_mode not in self.allowed_source_modes
                    or context.get("source_mode", "live") != channel.source_mode):
                raise MediaError(409, "source_mode_rejected", "This source mode is not enabled for network publication")
            if (channel.source is None or channel.source.error or channel.last_frame is None
                    or time.monotonic() - channel.last_frame > 5):
                raise MediaError(503, "capture_unavailable", "Camera has no recent source frame", True)
            active = [s for s in self.sessions.values() if not s.stopped.is_set()]
            if len(active) >= 4 or sum(s.rendition == request["rendition"] for s in active) >= 2:
                raise MediaError(429, "media_capacity", "Two main and two preview sessions are supported", True)
            session_id, token = str(uuid.uuid4()), secrets.token_hex(16)
            listener = None
            error = "No available media port"
            for port in self.ports:
                if any(s.port == port for s in active):
                    continue
                try:
                    listener = SRTSocket(sender=True)
                    listener.listen(self.bind_host, port, f"rt1:{session_id}:{token}")
                    break
                except OSError as exc:
                    error = str(exc)
                    if listener:
                        listener.close()
                    listener = None
            if listener is None:
                raise MediaError(503, "media_transport_unavailable", error, True)
            host = context.get("host", "")
            if not host or host in {"0.0.0.0", "::"}:
                listener.close()
                raise MediaError(503, "endpoint_unavailable", "No reachable media interface selected")
            session = Session(listener, port, channel, request["rendition"], token, session_id,
                              copy.deepcopy(request), host, context["boot_id"])
            encoder = channel.encoders.get(request["rendition"])
            if encoder is None or encoder.stopped.is_set():
                encoder = channel.encoders[request["rendition"]] = Encoder(channel, request["rendition"])
            encoder.add(session)
            self.sessions[session_id] = session
            self.requests[request["request_id"]] = session
            session.thread.start()
            return copy.deepcopy(session.response)

    def _authorized(self, session_id, context):
        session = self.sessions.get(session_id)
        if session is None:
            raise MediaError(404, "not_found", "Unknown media session")
        if not hmac.compare_digest(str(context.get("session_token", "")), session.token):
            raise MediaError(403, "invalid_token", "Invalid media session token")
        return session

    async def renew_session(self, session_id, request, context):
        with self.lock:
            if request != {"lease_ms": 30000}:
                raise MediaError(400, "invalid_lease", "Protocol 1.0 requires lease_ms=30000")
            session = self._authorized(session_id, context)
            if session.stopped.is_set():
                raise MediaError(410, "session_expired", "Session has ended")
            session.expiry = time.monotonic() + 30
            return {"lease_ms": 30000}

    async def delete_session(self, session_id, context):
        with self.lock:
            session = self._authorized(session_id, context)
            session.close()
        return None

    def _sweep(self):
        while not self.stopped.wait(.5):
            now = time.monotonic()
            with self.lock:
                for session in list(self.sessions.values()):
                    if now >= session.expiry:
                        session.close()
                    if session.ended and now - session.ended > 60:
                        self.sessions.pop(session.session_id, None)
                        self.requests.pop(session.request["request_id"], None)
                for channel in self.channels.values():
                    for name, encoder in list(channel.encoders.items()):
                        if channel.source and hasattr(channel.source, "record_network_timing"):
                            channel.source.record_network_timing(name, encoder.timing)
                        if encoder.unused_since is not None and now - encoder.unused_since >= 2:
                            encoder.stop()
                            channel.encoders.pop(name, None)
                availability = tuple((c.local_key, c.source is not None and not c.source.error,
                                      c.last_frame is not None and now - c.last_frame < 5)
                                     for c in self.channels.values())
                changed = availability != self._availability
                self._availability = availability
            if changed or now - self._last_timing_publish >= 1:
                self._last_timing_publish = now
                self._changed()

    def close(self):
        self.stopped.set()
        for key in self.channels:
            self.set_source(key, None)
        self.sweeper.join(timeout=2)
