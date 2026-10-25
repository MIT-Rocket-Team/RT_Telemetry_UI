"""Embedded HTTP/WS discovery and monitoring, isolated from the Qt/UI thread.

All journal, peer and snapshot mutations belong to one asyncio loop. Public
methods copy data and enqueue bounded work; serial ingestion never waits on a
network client. Network observations never invoke the hardware controller.
"""
from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import logging
import queue
import random
import socket
import threading
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf

from .. import __version__
from ..recording import ObservationArchive
from .configuration import ConfigurationLedger, validate_configuration_request
from .journal import EventJournal
from .protocol import (
    EVENT_KINDS,
    MAX_MESSAGE,
    MAX_REQUEST,
    PROTOCOL,
    SERVICE_TYPE,
    SUBPROTOCOL,
    NetworkProtocolError,
    counter,
    dumps,
    endpoint,
    event_fingerprint,
    loads,
    obj,
    require,
    uuid_text,
    validate_envelope,
    validate_info,
    validate_observation,
    validate_status,
)

LOG = logging.getLogger(__name__)


def _copy(value):
    return json.loads(dumps(value))


def _utc():
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _setting(settings, name, default):
    return settings.get(name, default) if isinstance(settings, dict) else getattr(settings, name, default)


def _node_id(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "station-node-id"
    if path.exists():
        return uuid_text(path.read_text().strip())
    value = str(uuid.uuid4())
    try:
        with path.open("x") as handle:
            handle.write(value + "\n")
    except FileExistsError:
        return uuid_text(path.read_text().strip())
    return value


class _SendQueue:
    def __init__(self, ws, max_bytes=2 * 1024 * 1024, max_age=2.0):
        self.ws, self.max_bytes, self.max_age = ws, max_bytes, max_age
        self.queue = asyncio.Queue()
        self.bytes = 0
        self.closed = False
        self.writer = asyncio.create_task(self._write())

    def offer(self, message):
        if self.closed:
            return False
        raw = dumps(message)
        size = len(raw.encode())
        oldest = self.queue._queue[0][0] if self.queue.qsize() else time.monotonic()
        if self.bytes + size > self.max_bytes or time.monotonic() - oldest > self.max_age:
            self.closed = True
            asyncio.create_task(self.ws.close(code=1013, message=b"Subscriber queue overflow; resume required"))
            return False
        self.bytes += size
        self.queue.put_nowait((time.monotonic(), size, raw))
        return True

    async def _write(self):
        try:
            while True:
                queued, size, raw = await self.queue.get()
                if time.monotonic() - queued > self.max_age:
                    await self.ws.close(code=1013, message=b"Subscriber too slow; resume required")
                    break
                await asyncio.wait_for(self.ws.send_str(raw), self.max_age)
                self.bytes -= size
        except (TimeoutError, ConnectionError, RuntimeError):
            await self.ws.close(code=1013, message=b"Subscriber too slow; resume required")
        finally:
            self.closed = True

    async def stop(self):
        self.closed = True
        self.writer.cancel()
        await asyncio.gather(self.writer, return_exceptions=True)


class NetworkRuntime:
    """Thread-safe public facade for one station service process identity.

    Call stop and create a new runtime when station, role, mission or mode changes.
    Passing mission_id=None permits discovery but does not publish mission data.
    """
    def __init__(self, data_dir, profile, mission_id, settings=None, media_session_backend=None):
        self.data_dir, self.profile = Path(data_dir), profile
        self.node_id = _node_id(self.data_dir)
        self.boot_id = str(uuid.uuid4())
        self.mission_id = uuid_text(mission_id, nullable=True)
        self.boot_monotonic = time.monotonic()
        self._configuration = ConfigurationLedger(self.data_dir, self.boot_id)
        self.station = "launch" if profile.station in {"base", "launch"} else "gs" + profile.station[-1]
        self.role = "telem" if profile.role in {"telem", "telemetry"} else "video"
        self.vehicle_mode = profile.vehicle
        self.source_mode = _setting(settings, "network_source_mode", "live")
        require(self.source_mode in {"live", "replay", "simulation"}, "Invalid network source mode")
        self.accept_source_modes = _setting(settings, "network_accept_source_modes", ["live"])
        require(isinstance(self.accept_source_modes, list) and set(self.accept_source_modes) <= {"live", "replay", "simulation"}, "Invalid accepted source modes")
        self.bind_host = _setting(settings, "network_bind_host", "0.0.0.0")
        self.port = _setting(settings, "network_port", 8766)
        require(type(self.port) is int and 0 <= self.port <= 65535, "Invalid network service port")
        self.discovery_enabled = bool(_setting(settings, "network_discovery", True))
        self.media_session_backend = media_session_backend
        self.state, self.error, self.local_endpoint = "OFF", "", None
        self._lock = threading.RLock()
        self._public_peers, self._public_info = [], {}
        self._thread = self._loop = self._stop_event = None
        self._ready = threading.Event()
        self._pending = 0
        self._pending_bytes = 0
        self._ui_bytes = 0
        self._ingress_failed = False
        self.ingress_dropped = 0
        self.ui_event_drops = 0
        self._ui_events = queue.Queue(maxsize=8192)
        self._ui_gap_reported = 0
        self._peer_tasks, self._peers = {}, {}
        self._endpoints = set()
        self._hints = {}
        self._subscribers = set()
        self._active_ws = 0
        self._archive = None
        self._journal = EventJournal()
        self._latest, self._streams = {}, []
        self._status = {
            "application": {"state": "running", "source_mode": self.source_mode},
            "telemetry_board": None, "pointer": None, "video_inputs": None,
            "network": {"ltu_signal_dbm": None},
            "clock": {"quality": "unknown", "uncertainty_ms": None},
        }
        self._pending_status = None
        self._last_status_event = 0.0
        self._status_handle = None
        self._zc = self._browser = self._service_info = None
        self._session = None
        self._public_info = self._info()

    def mono_ms(self):
        return str(max(0, int((time.monotonic() - self.boot_monotonic) * 1000)))

    def _common(self):
        return {"protocol": PROTOCOL, "node_id": self.node_id, "boot_id": self.boot_id,
                "mission_id": self.mission_id, "generated_mono_ms": self.mono_ms()}

    def _info(self):
        return {**self._common(), "station": self.station, "role": self.role,
            "vehicle_mode": self.vehicle_mode, "source_mode": self.source_mode, "app_version": __version__,
            "supported_event_kinds": list(EVENT_KINDS),
            "clock": {"quality": "unknown", "uncertainty_ms": None},
            "capabilities": {"telemetry": self.role == "telem", "pointer_status": self.role == "telem" and self.station != "launch",
                             "video": self.media_session_backend is not None, "remote_commands": False,
                             "uplink_enable": False, "target_switch": False,
                             "station_configuration": self.station != "launch" and self.source_mode == "live"},
            "limits": {"message_bytes": MAX_MESSAGE, "request_bytes": MAX_REQUEST,
                       "journal_bytes": self._journal.max_bytes, "journal_ms": int(self._journal.max_age * 1000),
                       "heartbeat_ms": 1000, "heartbeat_stale_ms": 3000, "heartbeat_offline_ms": 10000,
                       "client_queue_bytes": 2097152, "client_queue_ms": 2000, "rf_stale_ms": 2000, "rf_lost_ms": 10000}}

    def info(self):
        with self._lock:
            value = copy.deepcopy(self._public_info)
        value["generated_mono_ms"] = self.mono_ms()
        return value

    def peers_snapshot(self):
        now = time.monotonic()
        with self._lock:
            result = copy.deepcopy(self._public_peers)
        for peer in result:
            received = peer.pop("_heartbeat_received", None)
            peer["heartbeat_age_s"] = None if received is None else max(0, now - received)
            if peer["state"] in {"ONLINE", "STALE", "OFFLINE"}:
                age = peer["heartbeat_age_s"]
                peer["state"] = "OFFLINE" if age is None or age >= 10 else "STALE" if age >= 3 else "ONLINE"
            for observation in peer.get("latest", []):
                arrived = observation.pop("_received_local_mono", None)
                age = observation.get("source_age_at_send_ms")
                observation["sample_age_ms_lower_bound"] = None if arrived is None or age is None else age + max(0, now-arrived)*1000
        return result

    def set_configuration(self, snapshot):
        """Publish GUI-owned station/location settings without actuating anything."""
        self._configuration.set_snapshot(snapshot)

    def drain_configuration_requests(self):
        return self._configuration.drain()

    def complete_configuration_request(self, request_id, result=None, error=None):
        # Durable before returning: GUI may immediately restart the service after renumbering.
        record = self._configuration.complete(request_id, result=result, error=error)
        if self._archive:
            self._archive.event("configuration.completed", record)
        return record

    def drain_events(self, limit=2000):
        """Drain UI copies; full originals already live in the independent archive."""
        result = []
        if self.ui_event_drops != self._ui_gap_reported:
            result.append({"kind": "ui.gap", "payload": {"dropped": self.ui_event_drops - self._ui_gap_reported}})
            self._ui_gap_reported = self.ui_event_drops
        for _ in range(max(0, min(int(limit), 8192))):
            try:
                size, item = self._ui_events.get_nowait()
                with self._lock:
                    self._ui_bytes -= size
                result.append(item)
            except queue.Empty:
                break
        return result

    @property
    def archive_status(self):
        archive = self._archive
        return {"path": str(archive.path) if archive else None, "error": archive.error if archive else "",
                "dropped": archive.dropped if archive else 0}

    def _ui_event(self, event, address):
        item = copy.deepcopy(event)
        item.update(endpoint=address, received_local_mono=time.monotonic())
        size = len(dumps(item).encode())
        try:
            with self._lock:
                if self._ui_bytes + size > 16 * 1024 * 1024:
                    raise queue.Full
                self._ui_events.put_nowait((size, item))
                self._ui_bytes += size
        except queue.Full:
            self.ui_event_drops += 1

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if self.state != "OFF":
            raise RuntimeError("Create a new runtime to restart with a new boot identity")
        self.state = "STARTING"
        self._archive = ObservationArchive(self.data_dir / "network-observations", metadata=self.info(), on_error=self._archive_error)
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="station-network")
        self._thread.start()

    def _archive_error(self, message):
        self.error = message

    def stop(self):
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        if self._archive:
            self._archive.close()
        if not self._thread or not self._thread.is_alive():
            self.state = "STOPPED"

    def _submit(self, callback, *args):
        size = len(dumps(args).encode())
        with self._lock:
            if self._loop is None or self.state not in {"LISTENING", "STARTING"} or self._ingress_failed:
                return False
            if self._pending >= 8192 or self._pending_bytes + size > 16 * 1024 * 1024:
                self._ingress_failed = True
                self.ingress_dropped += 1
                self.error = "Network ingestion queue full; observations missing from network journal"
                if self._archive:
                    self._archive.event("ingress.gap", {"dropped": self.ingress_dropped})
                self._loop.call_soon_threadsafe(self._abort_ingress)
                return False
            self._pending += 1
            self._pending_bytes += size
        def run():
            with self._lock:
                self._pending -= 1
                self._pending_bytes -= size
            try:
                callback(*args)
            except Exception as exc:
                self.error = f"Network publication rejected: {exc}"
                LOG.exception("Network publication rejected")
        self._loop.call_soon_threadsafe(run)
        return True

    def _abort_ingress(self):
        # Stop accepting subscriptions until a fresh service boot makes the gap explicit.
        self._status = copy.deepcopy(self._status)
        self._status["application"].update(state="error", reason=self.error, network_ingress_dropped=self.ingress_dropped)
        self._event("status.changed", self._status)
        for subscriber in tuple(self._subscribers):
            asyncio.create_task(subscriber.ws.close(code=1013, message=b"Source ingestion overflow; restart service to resume"))

    def publish_observation(self, value):
        value = _copy(validate_observation(value))
        require(value["observation_id"].startswith(f"{self.node_id}:{self.boot_id}:"), "Observation belongs to another service boot")
        require(value["receiver"]["station"] == self.station, "Observation belongs to another station")
        if self._archive:
            self._archive.event("source.observation", self._envelope("telemetry.observation", value))
        if self.mission_id is None or self.role != "telem":
            return False
        return self._submit(self._observation, value)

    def update_status(self, value):
        return self._submit(self._replace_status, _copy(validate_status(value)))

    def update_streams(self, value):
        require(isinstance(value, list), "Stream manifest must be an array")
        return self._submit(self._replace_streams, _copy({"streams": value})["streams"])

    def add_endpoint(self, value):
        value = endpoint(value)
        require(len(self._endpoints) < 128 or value in self._endpoints, "Too many manual peer endpoints")
        self._endpoints.add(value)
        if self._loop:
            self._loop.call_soon_threadsafe(self._connect_peer, value, None, True)
        return value

    def _envelope(self, kind, payload, seq=None):
        return {"v": 1, "kind": kind, "node_id": self.node_id, "boot_id": self.boot_id,
            "mission_id": self.mission_id, "source_mode": self.source_mode, "event_seq": seq,
            "sent_mono_ms": self.mono_ms(), "sent_time_utc": _utc(), "payload": payload}

    def _event(self, kind, payload):
        # Original event time is immutable payload metadata; envelope time is delivery time.
        payload = copy.deepcopy(payload)
        payload.setdefault("event_created_mono_ms", self.mono_ms())
        payload.setdefault("event_created_time_utc", _utc())
        event = self._journal.append(self._envelope(kind, payload))
        for subscriber in tuple(self._subscribers):
            subscriber.offer(event)
        return event

    def _observation(self, value):
        event = self._event("telemetry.observation", value)
        self._latest[value["vehicle"]["id"]] = {"event_seq": event["event_seq"], **value}

    def _replace_status(self, value):
        if value == self._status:
            return
        self._pending_status = value
        # Snapshot state changes are journaled at most twice/second, together at event publication.
        if self._status_handle is None:
            delay = max(0, .5 - (time.monotonic() - self._last_status_event))
            self._status_handle = self._loop.call_later(delay, self._emit_status)

    def _emit_status(self):
        self._status_handle = None
        self._last_status_event = time.monotonic()
        if self._pending_status is not None:
            self._status, self._pending_status = self._pending_status, None
        self._event("status.changed", self._status)

    def _replace_streams(self, value):
        if value != self._streams:
            self._streams = value
            self._event("streams.changed", {"streams": value})

    def _snapshot(self):
        # No await occurs while capturing state and its matching event watermark.
        return {"through_event_seq": str(self._journal.head), "status": copy.deepcopy(self._status),
                "latest": copy.deepcopy(list(self._latest.values())), "streams": copy.deepcopy(self._streams)}

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.state, self.error = "ERROR", f"Station service failed: {exc}"
            LOG.exception("Station service failed")
        finally:
            self._loop = None
            self._ready.set()

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._session = ClientSession(timeout=ClientTimeout(total=5), trust_env=False)
        runner = web.AppRunner(self._application(), access_log=None)
        try:
            await runner.setup()
            site = web.TCPSite(runner, self.bind_host, self.port)
            await site.start()
            self.port = site._server.sockets[0].getsockname()[1]
            host = "::1" if ":" in self.bind_host else "127.0.0.1" if self.bind_host == "0.0.0.0" else self.bind_host
            self.local_endpoint = f"http://[{host}]:{self.port}" if ":" in host else f"http://{host}:{self.port}"
            self.state = "LISTENING"
            self._public_info = self._info()
            self._ready.set()
            if self.discovery_enabled:
                try:
                    await self._start_discovery()
                except Exception as exc:  # noqa: BLE001 - manual endpoints remain available after platform discovery errors
                    self.error = f"Discovery unavailable; enter peer IP manually: {exc}"
            for address in tuple(self._endpoints):
                self._connect_peer(address)
            await self._stop_event.wait()
        finally:
            if self._status_handle:
                self._status_handle.cancel()
            for task in self._peer_tasks.values():
                task.cancel()
            await asyncio.gather(*self._peer_tasks.values(), return_exceptions=True)
            for subscriber in tuple(self._subscribers):
                await subscriber.ws.close(code=1000, message=b"Station service stopped")
            if self._browser:
                self._browser.cancel()
            if self._zc:
                if self._service_info:
                    await asyncio.to_thread(self._zc.unregister_service, self._service_info)
                await asyncio.to_thread(self._zc.close)
            await runner.cleanup()
            await self._session.close()
            if self.state != "ERROR":
                self.state = "STOPPED"

    def _application(self):
        @web.middleware
        async def errors(request, handler):
            counted_ws = False
            try:
                if request.path == "/api/v1/events":
                    if self._active_ws >= 16:
                        raise NetworkProtocolError(429, "capacity", "Station subscriber capacity reached", True)
                    self._active_ws += 1
                    counted_ws = True
                origin = request.headers.get("Origin")
                # Native clients have no Origin. Browser access is same-origin only.
                if origin and origin.rstrip("/") != f"{request.scheme}://{request.host}":
                    raise NetworkProtocolError(403, "origin_denied", "Browser origin is not permitted")
                return await handler(request)
            except NetworkProtocolError as exc:
                return web.json_response(exc.body(), status=exc.status, headers={"Cache-Control": "no-store", **({"Retry-After": "1"} if exc.status == 429 else {})})
            except web.HTTPRequestEntityTooLarge:
                return web.json_response({"error": {"code": "request_too_large", "message": "Request exceeds 64 KiB", "retryable": False}}, status=413)
            except web.HTTPException as exc:
                return web.json_response({"error": {"code": "method_not_allowed" if exc.status == 405 else "not_found" if exc.status == 404 else "http_error", "message": exc.reason, "retryable": False}}, status=exc.status)
            except Exception as exc:
                if all(hasattr(exc, key) for key in ("status", "code", "message", "retryable")):
                    return web.json_response({"error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable}}, status=exc.status, headers={"Retry-After": "1"} if exc.status == 429 else {})
                LOG.exception("Station request failed")
                return web.json_response({"error": {"code": "internal_error", "message": "Station service could not handle this request", "retryable": True}}, status=503)
            finally:
                if counted_ws:
                    self._active_ws -= 1
        app = web.Application(client_max_size=MAX_REQUEST, middlewares=[errors])
        app.router.add_get("/api/v1/info", self._get_info)
        for name in ("status", "telemetry/latest", "streams"):
            app.router.add_get("/api/v1/" + name, self._get_snapshot)
        app.router.add_get("/api/v1/events", self._ws)
        app.router.add_post("/api/v1/commands", self._commands)
        app.router.add_get("/api/v1/configuration", self._get_configuration)
        app.router.add_patch("/api/v1/configuration", self._patch_configuration)
        app.router.add_post("/api/v1/video/sessions", self._media)
        app.router.add_patch("/api/v1/video/sessions/{id}", self._media)
        app.router.add_delete("/api/v1/video/sessions/{id}", self._media)
        return app

    async def _get_configuration(self, request):
        request_id = request.query.get("request_id")
        payload = self._configuration.get(request_id) if request_id else {"configuration": self._configuration.get_snapshot()}
        return web.Response(text=dumps({**self._common(), **payload}), content_type="application/json", headers={"Cache-Control": "no-store"})

    async def _patch_configuration(self, request):
        value = validate_configuration_request(await self._request_json(request))
        if self.station == "launch" or self.source_mode != "live":
            raise NetworkProtocolError(409, "configuration_unavailable", "Only live Away stations accept station/location configuration")
        if self.mission_id is None or value["mission_id"] != self.mission_id:
            raise NetworkProtocolError(409, "mission_mismatch", "Station configuration requires the same assigned mission")
        if value["expected_node_id"] != self.node_id:
            raise NetworkProtocolError(409, "identity_mismatch", "Configuration request targets another installation")
        # UUIDs identify peers on this trusted LAN; they are not cryptographic authentication.
        known = [peer for peer in self._peers.values() if peer.get("info") and not peer.get("conflict")
                 and peer["state"] in {"ONLINE", "STALE", "SYNCHRONIZING"}
                 and time.monotonic() - peer.get("_last_seen", 0) < 10]
        base_peer = any(info["node_id"] == value["client_node_id"] and info["boot_id"] == value["client_boot_id"]
                        and info["station"] == "launch" and info["role"] in {"telem", "video"}
                        and info["mission_id"] == self.mission_id and info["source_mode"] == "live"
                        and info["vehicle_mode"] == self.vehicle_mode
                        for info in (peer["info"] for peer in known))
        if not base_peer:
            raise NetworkProtocolError(409, "base_peer_required", "Discover or connect this Away station to the Base computer in the same mission first")
        # Completed retry is valid after a renumber reboot; it never applies again.
        previous = self._configuration.existing(value)
        if previous is not None:
            return web.Response(text=dumps({**self._common(), **previous}), content_type="application/json", status=202 if previous["state"] == "queued" else 200, headers={"Cache-Control": "no-store"})
        if value["expected_boot_id"] != self.boot_id:
            raise NetworkProtocolError(409, "stale_boot", "Station service restarted; refresh its identity before configuring")
        result = await asyncio.to_thread(self._configuration.enqueue, value)
        if self._archive:
            self._archive.event("configuration.queued", {"request": value, "result": result})
        return web.Response(text=dumps({**self._common(), **result}), content_type="application/json", status=202, headers={"Cache-Control": "no-store"})

    async def _get_info(self, request):
        return web.Response(text=dumps(self._info()), content_type="application/json", headers={"Cache-Control": "no-store"})

    async def _get_snapshot(self, request):
        value = self._snapshot()
        payload = {**self._common(), "as_of_event_seq": value["through_event_seq"]}
        if request.path.endswith("/status"):
            payload["status"] = value["status"]
        elif request.path.endswith("/streams"):
            payload["streams"] = value["streams"]
        else:
            payload["observations"] = value["latest"]
        return web.Response(text=dumps(payload), content_type="application/json", headers={"Cache-Control": "no-store"})

    async def _commands(self, request):
        raise NetworkProtocolError(501, "unsupported", "Remote hardware commands are not supported")

    async def _request_json(self, request):
        if request.content_type != "application/json":
            raise NetworkProtocolError(415, "content_type", "Use application/json")
        return loads(await request.read(), MAX_REQUEST)

    async def _media(self, request):
        backend = self.media_session_backend
        if backend is None:
            raise NetworkProtocolError(501, "unsupported", "This service has no media session provider")
        token = request.headers.get("Authorization", "")
        context = {"node_id": self.node_id, "boot_id": self.boot_id, "mission_id": self.mission_id,
                   "source_mode": self.source_mode, "host": urlsplit(f"http://{request.host}").hostname,
                   "request_host": request.host, "session_token": token[7:] if token.startswith("Bearer ") else None}
        if request.method == "POST":
            result = await backend.create_session(await self._request_json(request), context)
            status = 201
        elif request.method == "PATCH":
            result = await backend.renew_session(request.match_info["id"], await self._request_json(request), context)
            status = 200
        else:
            result = await backend.delete_session(request.match_info["id"], context)
            status = 200
        return web.Response(text=dumps(result or {}), content_type="application/json", status=status, headers={"Cache-Control": "no-store"})

    async def _ws(self, request):
        if self._ingress_failed:
            raise NetworkProtocolError(503, "ingestion_gap", "Source ingestion overflow; restart station network service", True)
        if len(self._subscribers) >= 16:
            raise NetworkProtocolError(429, "capacity", "Station subscriber capacity reached", True)
        if SUBPROTOCOL not in [v.strip() for v in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]:
            raise NetworkProtocolError(400, "subprotocol", "WebSocket requires rt-gs.v1")
        ws = web.WebSocketResponse(protocols=[SUBPROTOCOL], max_msg_size=MAX_MESSAGE, compress=False, heartbeat=None)
        await ws.prepare(request)
        out = _SendQueue(ws)
        heartbeats = None
        pending_beats = {}
        rtt_ms = None
        last_ack = time.monotonic()
        try:
            out.offer(self._envelope("hello", {**self._info(), "retained_first_event_seq": str(self._journal.first),
                "head_event_seq": str(self._journal.head), "heartbeat_ms": 1000}))
            message = await asyncio.wait_for(ws.receive(), 5)
            require(message.type == WSMsgType.TEXT, "Expected subscribe text message")
            request_value = validate_envelope(loads(message.data))
            require(request_value["kind"] == "subscribe" and request_value["event_seq"] is None, "Expected subscribe control message")
            subscribe = obj(request_value["payload"], ("client_node_id", "client_boot_id", "mission_id", "accept_source_modes", "resume"))
            require(subscribe["client_node_id"] == request_value["node_id"] and subscribe["client_boot_id"] == request_value["boot_id"], "Client identity mismatch")
            require(self.mission_id is not None and subscribe["mission_id"] == self.mission_id == request_value["mission_id"], "Mission identity mismatch")
            require(isinstance(subscribe["accept_source_modes"], list) and self.source_mode in subscribe["accept_source_modes"], "Source mode is not accepted")
            client_identity = (request_value["node_id"], request_value["boot_id"])
            resume = subscribe["resume"]
            barrier = self._journal.head
            history, reason = None, None
            if resume is not None:
                obj(resume, ("boot_id", "after_event_seq")); uuid_text(resume["boot_id"])
                after = counter(resume["after_event_seq"])
                if resume["boot_id"] != self.boot_id:
                    reason = "new_boot"
                else:
                    require(after <= barrier, "Resume cursor is ahead of source journal")
                    history = self._journal.retained(after, barrier)
                    if history is None:
                        reason = "cursor_expired"
            if history is not None and sum(len(dumps(event).encode()) for event in history) > 1024 * 1024:
                history, reason = None, "overflow"
            mode = "resume" if history is not None else "reset" if reason else "snapshot"
            out.offer(self._envelope("ready", {"sync_mode": mode, "through_event_seq": str(barrier),
                "retained_first_event_seq": str(self._journal.first), "head_event_seq": str(barrier)}))
            if reason:
                out.offer(self._envelope("reset", {"reason": reason, "old_cursor": resume,
                    "retained_first_event_seq": str(self._journal.first), "through_event_seq": str(barrier)}))
            if history is None:
                out.offer(self._envelope("snapshot", self._snapshot()))
            else:
                for event in history:
                    event["sent_mono_ms"], event["sent_time_utc"] = self.mono_ms(), _utc()
                    out.offer(event)
            out.offer(self._envelope("sync.complete", {"through_event_seq": str(barrier)}))
            # Atomic barrier + live registration: no await in the whole synchronization transaction.
            self._subscribers.add(out)
            async def beat():
                sequence = 0
                while not ws.closed:
                    sequence += 1
                    now = time.monotonic()
                    if now - last_ack >= 10:
                        await ws.close(code=1001, message=b"Client heartbeat acknowledgement timed out")
                        return
                    pending_beats[str(sequence)] = now
                    for key in list(pending_beats):
                        if now - pending_beats[key] > 10:
                            del pending_beats[key]
                    ages = {key: max(0, int(self.mono_ms()) - int(value["timing"]["received_mono_ms"])) for key, value in self._latest.items()}
                    out.offer(self._envelope("heartbeat", {"counter": str(sequence), "source_mono_ms": self.mono_ms(),
                        "head_event_seq": str(self._journal.head), "boot_id": self.boot_id,
                        "source_ages_ms": ages, "rtt_ms": rtt_ms}))
                    await asyncio.sleep(1)
            heartbeats = asyncio.create_task(beat())
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    value = validate_envelope(loads(message.data))
                    require((value["node_id"], value["boot_id"]) == client_identity and value["mission_id"] == self.mission_id, "Client identity changed")
                    require(value["kind"] == "heartbeat.ack" and value["event_seq"] is None, "Unsupported client control message")
                    count = str(counter(value["payload"].get("counter")))
                    started = pending_beats.pop(count, None)
                    if started is not None:
                        last_ack = time.monotonic()
                        rtt_ms = max(0, (time.monotonic() - started) * 1000)
                elif message.type == WSMsgType.BINARY:
                    raise NetworkProtocolError(422, "binary_message", "Binary WebSocket messages are unsupported")
                elif message.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                    break
        except (NetworkProtocolError, TimeoutError) as exc:
            if not ws.closed:
                payload = exc.body()["error"] if isinstance(exc, NetworkProtocolError) else {"code": "timeout", "message": "Subscription timed out", "retryable": True}
                await ws.send_str(dumps(self._envelope("error", payload)))
                await ws.close(code=1009 if isinstance(exc, NetworkProtocolError) and exc.status == 413 else 1008, message=b"Invalid subscription or message")
        finally:
            self._subscribers.discard(out)
            if heartbeats:
                heartbeats.cancel()
                await asyncio.gather(heartbeats, return_exceptions=True)
            await out.stop()
        return ws

    async def _start_discovery(self):
        addresses = set()
        if self.bind_host not in {"0.0.0.0", "::"}:
            addresses.add(self.bind_host)
        else:
            try:
                for family, _, _, _, addr in await asyncio.to_thread(socket.getaddrinfo, socket.gethostname(), None):
                    if family in {socket.AF_INET, socket.AF_INET6} and not ipaddress.ip_address(addr[0].split("%")[0]).is_loopback:
                        addresses.add(addr[0].split("%")[0])
            except OSError:
                pass
            # Route lookup chooses an interface; UDP connect does not transmit a packet.
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    probe.connect(("192.0.2.1", 9))
                    addresses.add(probe.getsockname()[0])
            except OSError:
                pass
        if not addresses:
            raise OSError("No LAN address available")
        self._zc = Zeroconf()
        alias = f"rt-{'base' if self.station == 'launch' else self.station}-{self.role}"
        properties = {"p": "1", "node": self.node_id, "station": self.station, "role": self.role, "api": "/api/v1"}
        self._service_info = ServiceInfo(SERVICE_TYPE, f"{alias}.{SERVICE_TYPE}",
            addresses=[ipaddress.ip_address(address).packed for address in addresses], port=self.port,
            properties=properties, server=f"{alias}-{self.node_id[:8]}.local.")
        await asyncio.to_thread(self._zc.register_service, self._service_info, allow_name_change=True)
        owner = self
        class Listener:
            def add_service(self, zc, service_type, name):
                if owner._loop:
                    owner._loop.call_soon_threadsafe(lambda: asyncio.create_task(owner._resolve_service(zc, service_type, name)))
            update_service = add_service
            def remove_service(self, zc, service_type, name):
                # Cache expiry is not an application heartbeat or proof of disconnection.
                pass
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, Listener())

    async def _resolve_service(self, zc, service_type, name):
        info = await asyncio.to_thread(zc.get_service_info, service_type, name, 2000)
        if info is None:
            return
        hint = {key.decode(): value.decode() for key, value in info.properties.items() if isinstance(key, bytes) and isinstance(value, bytes)}
        if hint.get("node") == self.node_id and info.name == self._service_info.name:
            return
        for address in info.parsed_scoped_addresses():
            host = f"[{address.replace('%', '%25')}]" if ":" in address else address
            self._connect_peer(f"http://{host}:{info.port}", hint)

    def _connect_peer(self, address, hint=None, force=False):
        if address == self.local_endpoint:
            return
        if len(self._peer_tasks) >= 128 and address not in self._peer_tasks:
            self.error = "Discovery endpoint limit reached (128); review local network"
            return
        if hint is not None:
            changed = self._hints.get(address) != hint
            self._hints[address] = hint
            force = force or changed
        existing = self._peer_tasks.get(address)
        if existing and not existing.done():
            if not force:
                return
            existing.cancel()
        self._peer_tasks[address] = asyncio.create_task(self._peer_loop(address))

    def _refresh_public(self):
        now = time.monotonic()
        active = [peer for peer in self._peers.values() if peer.get("info") and peer["state"] in {"ONLINE", "STALE", "CONNECTING", "SYNCHRONIZING", "CONFLICT"} and now-peer.get("_last_seen", 0) < 10]
        for peer in active:
            info = peer["info"]
            slot_conflict = (info["station"], info["role"]) == (self.station, self.role) and (info["node_id"], info["boot_id"]) != (self.node_id, self.boot_id)
            identity_conflict = info["node_id"] == self.node_id and info["boot_id"] != self.boot_id
            others = [p for p in active if p is not peer]
            conflict = slot_conflict or identity_conflict or any(
                (p["info"]["station"], p["info"]["role"]) == (info["station"], info["role"]) and p["info"]["node_id"] != info["node_id"]
                or p["info"]["node_id"] == info["node_id"] and any(p["info"][field] != info[field] for field in ("boot_id", "station", "role", "mission_id", "vehicle_mode", "source_mode")) for p in others)
            peer["conflict"] = bool(conflict)
            if conflict:
                peer["state"] = "CONFLICT"
                peer["error"] = "Conflicting station slot or node identity; choose a unique station/installation"
        with self._lock:
            # Merge multiple addresses only when node, boot and full station identity agree.
            merged = {}
            for peer in self._peers.values():
                info = peer.get("info")
                key = (info["node_id"], info["boot_id"], info["station"], info["role"], info["mission_id"]) if info else peer["endpoint"]
                if key not in merged or peer.get("_last_seen", 0) > merged[key].get("_last_seen", 0):
                    merged[key] = copy.deepcopy(peer)
            self._public_peers = list(merged.values())

    async def _get_peer_info(self, address):
        async with self._session.get(address + "/api/v1/info") as response:
            if response.status != 200:
                raise OSError(f"HTTP {response.status} from station identity endpoint")
            raw = await response.content.read(MAX_MESSAGE + 1)
            return validate_info(loads(raw))

    async def _peer_loop(self, address):
        peer = self._peers.setdefault(address, {"endpoint": address, "state": "CONNECTING", "error": "", "info": None,
            "status": None, "latest": [], "streams": [], "rtt_ms": None, "rtt_metric": "application_round_trip",
            "_heartbeat_received": None, "_last_seen": time.monotonic(), "cursor": None,
            "conflict": False, "synchronized": False, "gaps": 0})
        failures = 0
        digests = OrderedDict()
        while not self._stop_event.is_set():
            healthy_start = None
            try:
                peer.update(state="CONNECTING", error="", synchronized=False)
                self._refresh_public()
                info = await self._get_peer_info(address)
                peer["info"], peer["_last_seen"] = info, time.monotonic()
                hint = self._hints.get(address)
                if hint and any(hint.get(key) != str(info[field]) for key, field in (("node", "node_id"), ("station", "station"), ("role", "role"))):
                    raise NetworkProtocolError(409, "identity_hint_mismatch", "Discovery hints disagree with HTTP identity")
                if hint and (hint.get("p") != "1" or hint.get("api") != "/api/v1"):
                    raise NetworkProtocolError(409, "incompatible", "Discovery advertises another protocol version")
                self._refresh_public()
                if peer["conflict"]:
                    return
                if info["node_id"] == self.node_id and info["boot_id"] == self.boot_id:
                    self._peers.pop(address, None)
                    self._refresh_public()
                    return
                if self.mission_id is None or info["mission_id"] != self.mission_id or info["vehicle_mode"] != self.vehicle_mode:
                    raise NetworkProtocolError(409, "mission_mismatch", "Join the same mission UUID and vehicle mode to receive data")
                if info["source_mode"] not in self.accept_source_modes:
                    raise NetworkProtocolError(409, "mode_mismatch", "Peer source mode is not enabled")
                healthy_start = time.monotonic()
                await self._consume_peer(address, peer, info, digests)
                if time.monotonic() - healthy_start >= 30:
                    failures = 0
                raise OSError("Peer closed the event connection")
            except asyncio.CancelledError:
                return
            except NetworkProtocolError as exc:
                if exc.code == "sequence_gap":
                    peer["gaps"] += 1
                    self._archive.event("sequence.gap", {"endpoint": address, "cursor": peer["cursor"], "reason": exc.message})
                    peer.update(state="DISCONNECTED", error=exc.message, synchronized=False)
                    self._refresh_public()
                    await asyncio.sleep(.5)
                    continue
                peer.update(state="INCOMPATIBLE" if exc.code == "incompatible" else "CONFLICT" if exc.code == "identity_hint_mismatch" else "MISMATCH" if exc.code in {"mission_mismatch", "mode_mismatch"} else "ERROR", error=exc.message, synchronized=False)
                self._refresh_public()
                return  # Configuration or discovery evidence must change before retry.
            except Exception as exc:  # noqa: BLE001 - keep the peer retry worker alive across transport failures
                if healthy_start is not None and time.monotonic() - healthy_start >= 30:
                    failures = 0
                peer.update(state="DISCONNECTED", error=str(exc)[:240], synchronized=False)
                self._refresh_public()
                if self._archive:
                    self._archive.event("peer.disconnected", {"endpoint": address, "cursor": peer["cursor"], "reason": str(exc)[:240]})
                delay = min(.5 * (2 ** min(failures, 4)), 8) * random.uniform(.8, 1.2)
                failures += 1
                try:
                    await asyncio.wait_for(self._stop_event.wait(), delay)
                except TimeoutError:
                    pass

    async def _consume_peer(self, address, peer, info, digests):
        async with self._session.ws_connect(address + "/api/v1/events", protocols=[SUBPROTOCOL], compress=0,
                max_msg_size=MAX_MESSAGE, autoping=True) as ws:
            require(ws.protocol == SUBPROTOCOL, "Peer did not select the station WebSocket protocol")
            first = await asyncio.wait_for(ws.receive(), 1)
            require(first.type == WSMsgType.TEXT, "Peer did not send hello")
            hello = validate_envelope(loads(first.data))
            self._check_origin(hello, info)
            require(hello["kind"] == "hello", "Peer did not send hello")
            validate_info(hello["payload"])
            require(all(hello["payload"][field] == info[field] for field in ("node_id", "boot_id", "mission_id", "station", "role", "vehicle_mode", "source_mode")), "Hello identity changed")
            await ws.send_str(dumps(self._envelope("subscribe", {"client_node_id": self.node_id, "client_boot_id": self.boot_id,
                "mission_id": self.mission_id, "accept_source_modes": self.accept_source_modes, "resume": peer["cursor"]})))
            peer.update(state="SYNCHRONIZING", synchronized=False)
            barrier = None
            deadline = time.monotonic() + 5
            while not ws.closed:
                timeout = max(.01, deadline-time.monotonic()) if not peer["synchronized"] else 10
                message = await asyncio.wait_for(ws.receive(), timeout)
                if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    return
                require(message.type == WSMsgType.TEXT, "Peer sent unsupported binary data")
                event = validate_envelope(loads(message.data))
                self._check_origin(event, info)
                kind, payload = event["kind"], event["payload"]
                require(kind not in EVENT_KINDS or event["event_seq"] is not None, "Retained event requires a sequence")
                peer["_last_seen"] = time.monotonic()
                if kind == "heartbeat":
                    counter(payload.get("counter"))
                    peer["_heartbeat_received"] = time.monotonic()
                    peer["rtt_ms"] = payload.get("rtt_ms")
                    if peer["synchronized"]:
                        peer["state"] = "ONLINE"
                    await ws.send_str(dumps(self._envelope("heartbeat.ack", {"counter": payload["counter"]})))
                elif kind == "ready":
                    require(barrier is None, "Duplicate ready")
                    barrier = counter(payload.get("through_event_seq"))
                elif kind == "reset":
                    peer["gaps"] += 1
                    peer["cursor"] = None
                    digests.clear()
                    self._archive.event("reset", event)
                    self._ui_event(event, address)
                elif kind == "snapshot":
                    require(barrier is not None and counter(payload.get("through_event_seq")) == barrier, "Snapshot barrier mismatch")
                    validate_status(payload.get("status"))
                    require(isinstance(payload.get("latest"), list) and isinstance(payload.get("streams"), list), "Invalid snapshot contents")
                    latest = []
                    for observation in payload["latest"]:
                        validate_observation(observation)
                        latest.append(self._received_observation(observation, event))
                    peer.update(status=payload["status"], latest=latest, streams=payload["streams"])
                    peer["cursor"] = {"boot_id": info["boot_id"], "after_event_seq": str(barrier)}
                    self._archive.event("snapshot", event)
                    self._ui_event(event, address)
                elif kind == "sync.complete":
                    require(barrier is not None and counter(payload.get("through_event_seq")) == barrier, "Synchronization barrier mismatch")
                    require(peer["cursor"] is not None and counter(peer["cursor"]["after_event_seq"]) == barrier, "Synchronization completed before all events")
                    peer.update(synchronized=True, state="ONLINE", error="")
                    peer["_heartbeat_received"] = time.monotonic()
                elif event["event_seq"] is not None:
                    sequence = counter(event["event_seq"])
                    digest = event_fingerprint(event)
                    key = (event["boot_id"], sequence)
                    if key in digests:
                        require(digests[key] == digest, "Repeated event has different immutable content")
                        continue
                    cursor = peer["cursor"]
                    expected = counter(cursor["after_event_seq"]) + 1 if cursor and cursor["boot_id"] == info["boot_id"] else 1
                    if sequence != expected:
                        raise NetworkProtocolError(409, "sequence_gap", "Event sequence gap; resynchronization required", True)
                    if kind == "telemetry.observation":
                        validate_observation(payload)
                        require(payload["receiver"]["station"] == info["station"], "Receiver station differs from publisher")
                        received = self._received_observation(payload, event)
                        peer["latest"] = [p for p in peer["latest"] if p["vehicle"]["id"] != payload["vehicle"]["id"]] + [received]
                    elif kind == "status.changed":
                        peer["status"] = validate_status(payload)
                    elif kind == "streams.changed":
                        require(isinstance(payload.get("streams"), list), "Invalid stream manifest")
                        peer["streams"] = payload["streams"]
                    else:
                        LOG.info("Ignoring optional station event %s while retaining cursor", kind)
                    self._archive.event(kind, event)
                    self._ui_event(event, address)
                    digests[key] = digest
                    if len(digests) > 16384:
                        digests.popitem(last=False)
                    peer["cursor"] = {"boot_id": info["boot_id"], "after_event_seq": str(sequence)}
                elif kind == "error":
                    raise NetworkProtocolError(409, payload.get("code", "peer_error"), payload.get("message", "Peer rejected subscription"))
                self._refresh_public()
                if peer["conflict"]:
                    await ws.close(code=1008, message=b"Logical station conflict")
                    return

    def _check_origin(self, event, info):
        require(event["node_id"] == info["node_id"] and event["boot_id"] == info["boot_id"]
                and event["mission_id"] == self.mission_id and event["source_mode"] == info["source_mode"], "Peer event identity changed")

    def _received_observation(self, observation, event):
        require(observation["observation_id"].startswith(f"{event['node_id']}:{event['boot_id']}:"), "Observation origin does not match publisher")
        result = copy.deepcopy(observation)
        age = counter(event["sent_mono_ms"]) - counter(observation["timing"]["received_mono_ms"])
        require(age >= 0, "Source observation time is after send time")
        result.update(source_age_at_send_ms=age, _received_local_mono=time.monotonic())
        return result
