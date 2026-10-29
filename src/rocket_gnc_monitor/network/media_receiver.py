"""Leased SRT receivers and the Qt video wall adapter.

HTTP/SRT/FFmpeg work stays in bounded workers. The Qt timer only copies the most
recent decoded frame and updates source labels after a replacement is decodable.
"""
from __future__ import annotations

from collections import deque
import json
import queue
import random
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QImage, QPixmap

from ..media import WIDTH, HEIGHT, ffmpeg, popen_options
from .media import _replace_pending, _stop_process, channel_ids
from .media_srt import SRTSocket, SRTError


def _http(endpoint, method, path, body=None, token=None):
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body, allow_nan=False).encode() if body is not None else None
    request = urllib.request.Request(endpoint.rstrip("/") + "/api/v1" + path,
                                     data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=3) as response:
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("Media response exceeded 64 KiB")
        return json.loads(raw) if raw else None


class MediaReceiver:
    def __init__(self, endpoint, identity, peer, stream, rendition):
        self.endpoint, self.identity, self.peer = endpoint, dict(identity), dict(peer)
        self.stream, self.rendition = dict(stream), rendition
        self.frames = queue.Queue(maxsize=2)
        self.stopped = threading.Event()
        self.process = self.connection = self.session = None
        self.received_frames, self.last_frame = 0, None
        self.error = "Connecting"
        self.logs = deque(maxlen=8)
        self.thread = threading.Thread(target=self.run, name="network-video", daemon=True)
        self.thread.start()

    def _allocate(self):
        request = dict(request_id=str(uuid.uuid4()), client_node_id=self.identity["node_id"],
                       client_boot_id=self.identity["boot_id"], mission_id=self.identity["mission_id"],
                       expected_server_boot_id=self.peer["boot_id"], stream_id=self.stream["stream_id"],
                       rendition=self.rendition, expected_media_epoch=self.stream["media_epoch"])
        # Retry an uncertain POST with exactly the same request ID/body. A new
        # attempt gets a new ID only after that lease's cleanup/expiry boundary.
        for attempt in range(3):
            try:
                result = _http(self.endpoint, "POST", "/video/sessions", request)
                if (result["server_boot_id"] != request["expected_server_boot_id"]
                        or result["media_epoch"] != request["expected_media_epoch"]
                        or result["stream_id"] != request["stream_id"]
                        or result["rendition"] != self.rendition):
                    raise ValueError("Media allocation identity differs from the requested source")
                return result
            except urllib.error.HTTPError:
                raise
            except (OSError, ValueError, KeyError):
                if attempt == 2:
                    raise
                if self.stopped.wait(.5 * (attempt + 1)):
                    raise RuntimeError("Media receiver stopped")

    def _receive_transport(self, connection, process, failed):
        try:
            while not self.stopped.is_set() and not failed.is_set():
                try:
                    data = connection.receive()
                except SRTError:
                    if connection.connected:
                        continue  # Bounded read timeout; frame watchdog owns staleness.
                    raise
                if not data:
                    raise OSError("Video transport closed")
                process.stdin.write(data)
        except (OSError, ValueError) as exc:
            if not self.stopped.is_set():
                self.error = str(exc)
        finally:
            failed.set()
            _stop_process(process)

    def _watch_session(self, session, connection, process, failed):
        started, renewed = time.monotonic(), time.monotonic()
        while not self.stopped.wait(.25) and not failed.is_set():
            now = time.monotonic()
            if now - (self.last_frame or started) > 5:
                self.error = "No decoded video frame for five seconds"
                break
            if now - renewed >= 10:
                try:
                    _http(self.endpoint, "PATCH", "/video/sessions/" + session["session_id"],
                          {"lease_ms": 30000}, session["session_token"])
                    renewed = now
                except (OSError, ValueError):
                    self.error = "Video session renewal failed"
                    break
        failed.set()
        connection.close()
        _stop_process(process)

    def run(self):
        attempts = 0
        while not self.stopped.is_set():
            failed = threading.Event()
            try:
                self.last_frame = None
                self.session = session = self._allocate()
                transport = session["transport"]
                self.connection = connection = SRTSocket()
                connection.connect(transport["host"], transport["port"], transport["srt_streamid"])
                arguments = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-threads", "1",
                             "-fflags", "+discardcorrupt", "-probesize", "32768", "-analyzeduration", "1000000",
                             "-f", "mpegts", "-i", "pipe:0", "-map", "0:v:0", "-an",
                             "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                                    f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2",
                             "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
                self.process = process = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                           stderr=subprocess.PIPE, bufsize=0, **popen_options())
                threading.Thread(target=self._receive_transport, args=(connection, process, failed), daemon=True).start()
                threading.Thread(target=self._watch_session, args=(session, connection, process, failed), daemon=True).start()
                threading.Thread(target=self._read_logs, args=(process,), daemon=True).start()
                frame_size = WIDTH * HEIGHT * 3
                while not self.stopped.is_set() and not failed.is_set():
                    frame = bytearray()
                    while len(frame) < frame_size:
                        data = process.stdout.read(frame_size - len(frame))
                        if not data:
                            break
                        frame.extend(data)
                    if len(frame) != frame_size:
                        break
                    self.last_frame = time.monotonic()
                    self.received_frames += 1
                    self.error = ""
                    _replace_pending(self.frames, (bytes(frame), self.last_frame))
                    attempts = 0
                if not self.stopped.is_set() and not self.error:
                    self.error = "Video connection ended"
            except Exception as exc:
                if not self.stopped.is_set():
                    # Stream IDs/tokens are never included in diagnostics.
                    self.error = f"{type(exc).__name__}: video source unavailable"
            finally:
                failed.set()
                if self.connection:
                    self.connection.close()
                _stop_process(self.process)
                if self.session:
                    try:
                        _http(self.endpoint, "DELETE", "/video/sessions/" + self.session["session_id"],
                              token=self.session["session_token"])
                    except (OSError, ValueError):
                        pass
                self.session = None
            delay = min(8, .5 * 2 ** min(attempts, 4)) * random.uniform(.8, 1.2)
            attempts += 1
            self.stopped.wait(delay)

    def _read_logs(self, process):
        try:
            for line in iter(process.stderr.readline, b""):
                self.logs.append(line.decode(errors="replace")[:500])
        except (ValueError, OSError):
            pass

    def stop(self):
        self.stopped.set()
        if self.connection:
            self.connection.close()
        _stop_process(self.process)
        # HTTP cleanup can finish on its daemon worker; never wait in the GUI.


class VideoWallNetwork(QObject):
    """Bind NetworkRuntime peers to a VideoWall; create/destroy on the Qt thread."""
    def __init__(self, runtime, wall, parent=None):
        super().__init__(parent or wall)
        self.runtime, self.wall = runtime, wall
        self.workers, self.descriptions = {}, {}
        self.pending = {}
        self.on_received = None
        self.mapping = {value: key for key, value in channel_ids(wall.profile).items()}
        wall.external_selection = True
        wall.source_requested.connect(self.request_source)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def request_source(self, station, channel):
        if station == "local":
            self.pending.pop(channel, None)
            return
        self.pending[channel] = (station, channel)
        self.wall.primary_status[channel].setText(f"Connecting to GS{station[-1]} main video…")

    def _start(self, key, peer, stream):
        worker = self.workers.get(key)
        signature = (peer["info"]["node_id"], peer["info"]["boot_id"], stream["media_epoch"], peer["endpoint"])
        if worker and self.descriptions[key] == signature:
            return worker
        if worker:
            worker.stop()
        worker = MediaReceiver(peer["endpoint"], self.runtime.info(), peer["info"], stream, key[2])
        self.workers[key], self.descriptions[key] = worker, signature
        return worker

    def refresh(self):
        peers = self.runtime.peers_snapshot()
        sources = {}
        for peer in peers:
            info = peer.get("info", {})
            station = info.get("station", "")
            if (info.get("role") != "video" or station not in {"gs1", "gs2", "gs3", "gs4"}
                    or info.get("mission_id") != self.runtime.mission_id
                    or info.get("source_mode") not in getattr(self.runtime, "accept_source_modes", ["live"])
                    or str(peer.get("state", "")).lower() not in {"online", "stale"}):
                continue
            local_station = "away" + station[-1]
            for stream in peer.get("streams", []):
                if not isinstance(stream, dict) or not isinstance(stream.get("channel_id"), str):
                    continue
                try:
                    if str(uuid.UUID(stream.get("media_epoch", ""))) != stream["media_epoch"]:
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
                if (stream.get("station") != station
                        or stream.get("stream_id") != station + "." + stream["channel_id"]
                        or not isinstance(stream.get("renditions"), list)):
                    continue
                if not all(isinstance(item, dict) and item.get("name") in ("main", "preview")
                           and item.get("codec") == "h264" and item.get("container") == "mpegts"
                           and item.get("transport") == "srt" and type(item.get("available")) is bool
                           for item in stream["renditions"]):
                    continue
                channel = self.mapping.get(stream.get("channel_id"))
                key = (local_station, channel)
                if key in self.wall.feeds and stream.get("source_mode") == info.get("source_mode"):
                    # Ambiguous publishers never silently replace an existing source.
                    if key in sources:
                        sources[key] = None
                    else:
                        sources[key] = (peer, stream)
        wanted = set()
        for key in self.wall.thumbnails:
            entry = sources.get(key)
            if not entry:
                self.wall.mark_remote_unavailable(*key, "Station video link unavailable or conflicting")
                continue
            peer, stream = entry
            channel = key[1]
            main_selected = self.wall.selected_sources[channel] == key
            main_requested = self.pending.get(channel) == key or main_selected
            main_key = (*key, "main")
            preview_key = (*key, "preview")
            main = self._start(main_key, peer, stream) if main_requested else self.workers.get(main_key)
            if main_requested:
                wanted.add(main_key)
            # Keep preview during promotion. Keep main during demotion until the
            # replacement preview has decoded, then release the redundant session.
            main_ready = main_requested and main and main.last_frame is not None and not main.error
            preview = None if main_ready else self._start(preview_key, peer, stream)
            if preview:
                wanted.add(preview_key)
            if not main_requested and main and (preview is None or preview.last_frame is None):
                wanted.add(main_key)
            receiver = main if main_ready else preview
            if receiver:
                latest = None
                try:
                    while True:
                        latest = receiver.frames.get_nowait()
                except queue.Empty:
                    pass
                if latest:
                    raw, stamp = latest
                    if self.on_received:
                        self.on_received(dict(streamId=stream["stream_id"], mediaEpoch=stream["media_epoch"],
                                              hostMonotonic=stamp, rendition=receiver.rendition,
                                              receivedFrames=receiver.received_frames))
                    frame = QPixmap.fromImage(QImage(raw, WIDTH, HEIGHT, WIDTH * 3,
                                                    QImage.Format.Format_RGB888).copy())
                    quality = "Main" if receiver is main else "Preview · reduced quality"
                    self.wall.update_remote_frame(*key, frame, quality, stamp,
                                                  epoch=stream["media_epoch"])
                    if receiver is main and self.pending.get(channel) == key:
                        self.wall.commit_remote(*key)
                        self.pending.pop(channel, None)
                if receiver.error and receiver.last_frame is None:
                    self.wall.mark_remote_unavailable(*key, receiver.error)
            if self.pending.get(channel) == key and main and main.error and main.error != "Connecting":
                self.wall.primary_status[channel].setText(
                    f"GS{key[0][-1]} main unavailable; previous source retained · {main.error}")
        for key in set(self.workers) - wanted:
            self.workers.pop(key).stop()
            self.descriptions.pop(key, None)

    def close(self):
        self.timer.stop()
        for worker in self.workers.values():
            worker.stop()
        self.workers.clear()
        self.wall.external_selection = False
