"""Receive-only APRS and opt-in, read-only station snapshots over a local network.

No class in this module can access the controller or issue a hardware command.
Workers exchange bounded immutable JSON/position values with the GUI. LAN peers
exchange one newline-delimited versioned snapshot every 0.5 seconds; the server
accepts one peer at a time. Both ends must be explicitly started each session.

Protocol references:
https://www.ka9q.net/papers/kiss.html
https://www.aprs.org/doc/APRS101.PDF (chapters 3, 6 and 8)
https://github.com/wb2osz/direwolf/blob/master/conf/generic.conf
"""

from __future__ import annotations

import json
import math
import re
import select
import socket
import threading
import time
from dataclasses import asdict, dataclass

MAX_KISS_FRAME = 4096
MAX_SNAPSHOT = 16_384
LAN_PROTOCOL = "rocket-gnc-station"


@dataclass(frozen=True)
class APRSPosition:
    callsign: str
    latitude: float
    longitude: float
    altitude_m: float | None
    comment: str
    received: float


class KissDecoder:
    """Incremental KISS decoder; discard malformed/oversized frames until FEND."""

    def __init__(self):
        self.buffer = bytearray()
        self.in_frame = False
        self.escaped = False
        self.invalid = False

    def feed(self, data: bytes) -> list[bytes]:
        frames = []
        for value in data:
            if value == 0xC0:
                # Low nibble is the KISS command; high nibble is the radio port.
                if (
                    self.in_frame
                    and self.buffer
                    and not self.invalid
                    and not self.escaped
                    and self.buffer[0] & 0x0F == 0
                ):
                    frames.append(bytes(self.buffer[1:]))
                self.buffer.clear()
                self.in_frame, self.escaped, self.invalid = True, False, False
            elif self.in_frame and not self.invalid:
                if self.escaped:
                    if value not in (0xDC, 0xDD):
                        self.invalid = True
                    else:
                        self.buffer.append(0xC0 if value == 0xDC else 0xDB)
                    self.escaped = False
                elif value == 0xDB:
                    self.escaped = True
                else:
                    self.buffer.append(value)
                if len(self.buffer) > MAX_KISS_FRAME:
                    self.buffer.clear()
                    self.invalid = True
        return frames


def parse_aprs_position(frame: bytes, *, now: float | None = None) -> APRSPosition | None:
    """Read AX.25 UI uncompressed !, =, /, @ APRS positions, never transmit.

    Compressed positions, Mic-E, objects, NMEA and ambiguous-space positions are
    deliberately rejected rather than guessed. Altitude is APRS /A= feet converted
    to metres; its datum is not inferred or used for antenna motion.
    """
    if len(frame) > MAX_KISS_FRAME or len(frame) < 16:
        return None
    addresses = []
    offset = 0
    for _ in range(10):
        if offset + 7 > len(frame):
            return None
        address = frame[offset : offset + 7]
        if any(value & 1 for value in address[:6]):
            return None
        call = "".join(chr(value >> 1) for value in address[:6]).rstrip()
        if not re.fullmatch(r"[A-Z0-9]{1,6}", call):
            return None
        ssid = (address[6] >> 1) & 0x0F
        addresses.append(call + (f"-{ssid}" if ssid else ""))
        offset += 7
        if address[6] & 1:
            break
    else:
        return None
    if len(addresses) < 2 or frame[offset : offset + 2] != b"\x03\xf0":
        return None
    try:
        payload = frame[offset + 2 :].decode("ascii")
    except UnicodeDecodeError:
        return None
    if not payload or payload[0] not in "!=/@":
        return None
    if payload[0] in "/@":
        if not re.match(r"^[0-9]{6}[zh/]", payload[1:]):
            return None
        position = payload[8:]
    else:
        position = payload[1:]
    match = re.match(
        r"^(\d{2})(\d{2}\.\d{2})([NS])([/\\0-9A-Z])(\d{3})(\d{2}\.\d{2})([EW])([!-~])(.*)$", position
    )
    if not match:
        return None
    lat_deg, lat_min, lat_dir, _, lon_deg, lon_min, lon_dir, _, comment = match.groups()
    lat_min, lon_min = float(lat_min), float(lon_min)
    lat = int(lat_deg) + lat_min / 60
    lon = int(lon_deg) + lon_min / 60
    if lat_min >= 60 or lon_min >= 60 or lat > 90 or lon > 180:
        return None
    lat *= -1 if lat_dir == "S" else 1
    lon *= -1 if lon_dir == "W" else 1
    altitude = re.search(r"/A=(-\d{5}|\d{6})(?!\d)", comment)
    return APRSPosition(
        addresses[1],
        lat,
        lon,
        int(altitude[1]) * 0.3048 if altitude else None,
        comment[:160],
        time.monotonic() if now is None else now,
    )


def _endpoint(host: str, port: int, *, ephemeral=False):
    if not isinstance(host, str) or not host.strip() or len(host) > 253 or any(c.isspace() for c in host):
        raise ValueError("Enter an IP address or host name")
    if type(port) is not int or not (0 if ephemeral else 1) <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    return host.strip(), port


class _SocketWorker:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._socket = None
        self._listener = None
        self.state = "Off"
        self.error = ""

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def _state(self, state, error=""):
        with self._lock:
            self.state, self.error = state, error

    def _launch(self, function):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=function, daemon=True, name=type(self).__name__)
        self._thread.start()

    def stop(self):
        self._stop.set()
        for connection in (self._socket, self._listener):
            if connection:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.4)
        self._state("Off")

    def _connect(self, host, port):
        # DNS/connect work remains off the Qt thread. Keep the worker identity
        # alive through failure; callers cannot start a replacement until it exits.
        connection = socket.create_connection((host, port), timeout=2.0)
        self._socket = connection
        connection.settimeout(0.2)
        if self._stop.is_set():
            connection.close()
            raise OSError("Connection cancelled")
        return connection


class APRSReceiver(_SocketWorker):
    """Connect to an existing Dire Wolf KISS TCP server; receive only.

    No audio decoder is bundled. An empty filter shows the latest supported
    position from any callsign; a filter matches the complete callsign including
    SSID. ``snapshot()`` returns state, counters, latest fix and error.
    """

    def __init__(self):
        super().__init__()
        self.latest = None
        self.frames = 0
        self.positions = 0
        self.unsupported = 0

    def start(self, host="127.0.0.1", port=8001, callsign=""):
        host, port = _endpoint(host, port)
        callsign = callsign.strip().upper()
        if callsign and not re.fullmatch(r"[A-Z0-9]{1,6}(?:-(?:[0-9]|1[0-5]))?", callsign):
            raise ValueError("Use a complete APRS callsign, optionally with SSID 0–15")
        if self.active:
            raise ValueError("Disconnect APRS before changing its connection")
        with self._lock:
            self.latest = None
            self.frames = self.positions = self.unsupported = 0
        self._state("Connecting")
        self._launch(lambda: self._run(host, port, callsign))

    def snapshot(self):
        with self._lock:
            return {
                "state": self.state,
                "error": self.error,
                "latest": self.latest,
                "frames": self.frames,
                "positions": self.positions,
                "unsupported": self.unsupported,
            }

    def _run(self, host, port, callsign):
        try:
            with self._connect(host, port) as connection:
                self._state("Connected")
                decoder = KissDecoder()
                while not self._stop.is_set():
                    try:
                        data = connection.recv(8192)
                    except TimeoutError:
                        continue
                    if not data:
                        raise OSError("Dire Wolf closed the connection")
                    for frame in decoder.feed(data):
                        position = parse_aprs_position(frame)
                        with self._lock:
                            self.frames += 1
                            if position is None:
                                self.unsupported += 1
                            elif not callsign or position.callsign == callsign:
                                self.latest = position
                                self.positions += 1
        except OSError as exc:
            if not self._stop.is_set():
                self._state("Disconnected", str(exc)[:160])
        finally:
            if self._stop.is_set():
                self._state("Off")


def _number(value, *, optional=True):
    if value is None and optional:
        return None
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("Station values must be finite numbers")
    return value


def _text(value, limit=80):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid station text")
    return value


def validate_snapshot(data):
    """Whitelist the display-only wire schema, rejecting commands/unknown fields."""
    allowed = {
        "protocol",
        "version",
        "name",
        "mode",
        "mission",
        "utc",
        "sample_age_s",
        "telemetry",
        "pointer",
        "links",
        "aprs",
    }
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("Unknown station message fields")
    if data.get("protocol") != LAN_PROTOCOL or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("Unsupported station snapshot version")
    result = {"protocol": LAN_PROTOCOL, "version": 1}
    for key in ("name", "mode", "mission"):
        result[key] = _text(data.get(key, ""))
    result["utc"] = _number(data.get("utc"), optional=False)
    result["sample_age_s"] = _number(data.get("sample_age_s"))
    if result["sample_age_s"] is not None and result["sample_age_s"] < 0:
        raise ValueError("Sample age cannot be negative")
    telemetry = data.get("telemetry")
    numeric = {
        "t",
        "sequence",
        "altitude",
        "velocity",
        "latitude",
        "longitude",
        "gps_altitude",
        "gps_fix",
        "battery",
        "rssi",
    }
    if telemetry is None:
        result["telemetry"] = None
    elif not isinstance(telemetry, dict) or set(telemetry) - (numeric | {"phase"}):
        raise ValueError("Invalid station telemetry")
    else:
        result["telemetry"] = {
            key: _number(value) if key in numeric else _text(value) for key, value in telemetry.items()
        }
        for key, limit in (("latitude", 90), ("longitude", 180)):
            value = result["telemetry"].get(key)
            if value is not None and not -limit <= value <= limit:
                raise ValueError("Invalid peer location")
    pointer = data.get("pointer", {})
    if not isinstance(pointer, dict) or set(pointer) - {
        "connected",
        "tracking",
        "virtual",
        "azimuth",
        "elevation",
    }:
        raise ValueError("Invalid pointer summary")
    for key, value in pointer.items():
        if key in {"connected", "tracking", "virtual"}:
            if type(value) is not bool:
                raise ValueError("Invalid pointer flag")
        else:
            _number(value)
    result["pointer"] = dict(pointer)
    links = data.get("links", {})
    if not isinstance(links, dict) or set(links) - {"telemetry", "pointer", "rocket"}:
        raise ValueError("Invalid link summary")
    result["links"] = {key: _text(value) for key, value in links.items()}
    aprs = data.get("aprs")
    if aprs is not None:
        if not isinstance(aprs, dict) or set(aprs) != {
            "callsign",
            "latitude",
            "longitude",
            "altitude_m",
            "age_s",
        }:
            raise ValueError("Invalid APRS summary")
        aprs = dict(aprs)
        aprs["callsign"] = _text(aprs["callsign"], 10)
        for key in ("latitude", "longitude", "age_s"):
            aprs[key] = _number(aprs[key], optional=False)
        aprs["altitude_m"] = _number(aprs["altitude_m"])
        if not -90 <= aprs["latitude"] <= 90 or not -180 <= aprs["longitude"] <= 180 or aprs["age_s"] < 0:
            raise ValueError("Invalid APRS location")
    result["aprs"] = aprs
    return result


def encode_snapshot(data):
    raw = json.dumps(validate_snapshot(data), separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    if len(raw) > MAX_SNAPSHOT:
        raise ValueError("Station snapshot is too large")
    return raw


class SnapshotDecoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        messages = []
        for part in data.splitlines(keepends=True):
            self.buffer.extend(part)
            if len(self.buffer) > MAX_SNAPSHOT:
                self.buffer.clear()
                raise ValueError("Peer exceeded the station message size limit")
            if self.buffer.endswith(b"\n"):
                try:
                    messages.append(validate_snapshot(json.loads(self.buffer)))
                except (ValueError, TypeError, RecursionError) as exc:
                    raise ValueError("Peer sent an invalid station snapshot") from exc
                finally:
                    self.buffer.clear()
        return messages


class StationLink(_SocketWorker):
    """Explicit local-network read-only snapshot exchange; never remote control.

    ``publish`` must be called by the owner on its GUI thread. ``listen`` accepts
    one peer, returning to listening after disconnect; ``connect`` stops on loss.
    ``snapshot`` returns a copy of the received peer message and monotonic age.
    No discovery, relay, authentication or encryption is provided: use on the
    team's trusted local network. No received field is passed to a controller.
    """

    def __init__(self):
        super().__init__()
        self._outbound = None
        self._remote = None
        self._received = None
        self.received_count = 0
        self.bound_port = None
        self.peer_address = ""

    def publish(self, data):
        raw = encode_snapshot(data)
        with self._lock:
            self._outbound = raw

    def snapshot(self):
        with self._lock:
            return {
                "state": self.state,
                "error": self.error,
                "remote": json.loads(json.dumps(self._remote)) if self._remote else None,
                "age_s": None if self._received is None else time.monotonic() - self._received,
                "received_count": self.received_count,
                "port": self.bound_port,
                "peer": self.peer_address,
            }

    def _prepare(self):
        if self.active:
            raise ValueError("Stop the station link before changing its connection")
        with self._lock:
            self._remote = self._received = None
            self.received_count = 0
            self.bound_port = None
            self.peer_address = ""

    def connect(self, host, port=8765):
        host, port = _endpoint(host, port)
        self._prepare()
        self._state("Connecting")
        self._launch(lambda: self._run_connect(host, port))

    def listen(self, host="0.0.0.0", port=8765):
        host, port = _endpoint(host, port, ephemeral=True)
        self._prepare()
        self._state("Starting")
        self._launch(lambda: self._run_listen(host, port))

    def _run_connect(self, host, port):
        try:
            with self._connect(host, port) as connection:
                self._exchange(connection, f"{host}:{port}")
        except (OSError, ValueError) as exc:
            if not self._stop.is_set():
                self._state("Disconnected", str(exc)[:160])
        finally:
            if self._stop.is_set():
                self._state("Off")

    def _run_listen(self, host, port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                self._listener = listener
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((host, port))
                listener.listen(1)
                listener.settimeout(0.2)
                with self._lock:
                    self.bound_port = listener.getsockname()[1]
                self._state("Listening")
                while not self._stop.is_set():
                    try:
                        connection, address = listener.accept()
                    except TimeoutError:
                        continue
                    self._socket = connection
                    connection.settimeout(0.2)
                    try:
                        with connection:
                            self._exchange(connection, f"{address[0]}:{address[1]}")
                    except (OSError, ValueError) as exc:
                        if not self._stop.is_set():
                            self._state("Listening", str(exc)[:160])
        except OSError as exc:
            if not self._stop.is_set():
                self._state("Disconnected", str(exc)[:160])
        finally:
            if self._stop.is_set():
                self._state("Off")

    def _exchange(self, connection, address):
        with self._lock:
            self.peer_address = address
            # A new connection cannot inherit a previous peer's displayed fix.
            self._remote = self._received = None
        self._state("Connected")
        decoder = SnapshotDecoder()
        last_sent, last_received = 0.0, time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_sent >= 0.5:
                with self._lock:
                    outgoing = self._outbound
                if outgoing:
                    connection.sendall(outgoing)
                last_sent = now
            readable, _, _ = select.select([connection], [], [], 0.05)
            if readable:
                data = connection.recv(8192)
                if not data:
                    raise OSError("Peer disconnected")
                for message in decoder.feed(data):
                    last_received = time.monotonic()
                    with self._lock:
                        self._remote, self._received = message, last_received
                        self.received_count += 1
            if now - last_received > 10:
                raise OSError("No valid station snapshot received for 10 seconds")


def controller_snapshot(controller, name, aprs: APRSPosition | None = None):
    """Copy only local display values on the Qt thread; never include commands."""
    sample = controller.latest
    fields = (
        "t",
        "sequence",
        "altitude",
        "velocity",
        "latitude",
        "longitude",
        "gps_altitude",
        "gps_fix",
        "battery",
        "rssi",
        "phase",
    )
    telemetry = {field: getattr(sample, field) for field in fields} if sample else None
    # Legacy packets can contain unavailable NaNs; encode those as missing.
    if telemetry:
        telemetry = {
            k: None if isinstance(v, float) and not math.isfinite(v) else v for k, v in telemetry.items()
        }
    angles = controller.virtual_pointer.pose if controller.virtual_pointer else controller.pointer_sent
    links = {key: str(controller.states.get(key, "Remote transport pending"))[:80] for key in ("telemetry", "pointer")}
    links["rocket"] = str(controller.rocket_link_state()[0])[:80]
    return validate_snapshot(
        {
            "protocol": LAN_PROTOCOL,
            "version": 1,
            "name": name[:80],
            "mode": controller.mode,
            "mission": controller.mission.name[:80],
            "utc": time.time(),
            "sample_age_s": max(0, time.monotonic() - sample.received) if sample else None,
            "telemetry": telemetry,
            "links": links,
            "pointer": {
                "connected": controller.pointer_connected,
                "tracking": controller.tracking,
                "virtual": bool(controller.virtual_pointer),
                "azimuth": float(angles[0]) if angles else None,
                "elevation": float(angles[1]) if angles else None,
            },
            "aprs": {
                **{key: value for key, value in asdict(aprs).items() if key not in {"comment", "received"}},
                "age_s": max(0, time.monotonic() - aprs.received),
            }
            if aprs
            else None,
        }
    )
