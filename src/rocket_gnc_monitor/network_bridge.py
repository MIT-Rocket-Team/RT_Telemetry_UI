"""Qt boundary for station networking; hardware remains owned by Controller."""
from collections import deque
from dataclasses import replace
import copy
import json
from pathlib import Path
import threading
import time
import uuid
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, QTimer, Signal

from .network import NetworkRuntime
from .network.observation import observation_from_sample, observation_to_sample
from .network.protocol import endpoint


def network_configuration(data_dir, **overrides):
    config = dict(network_bind_host="0.0.0.0", network_port=8766,
                  network_discovery=True, peers=[], mission_id=None)
    try:
        saved = json.loads((Path(data_dir) / "station-network.json").read_text())
        config.update({key: saved[key] for key in config if key != "mission_id" and key in saved})
    except (OSError, ValueError, TypeError):
        pass
    config.update({key: value for key, value in overrides.items() if value is not None})
    return validate_configuration(config)


def validate_configuration(config):
    result = dict(config)
    mission = result.get("mission_id")
    result["mission_id"] = str(uuid.UUID(mission.strip())) if mission else None
    port = result.get("network_port", 8766)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Network port must be between 1 and 65535")
    host = result.get("network_bind_host", "0.0.0.0").strip()
    if not host or "/" in host or " " in host:
        raise ValueError("Enter a local bind address, such as 0.0.0.0")
    result["network_bind_host"] = host
    result["peers"] = list(dict.fromkeys(endpoint(value) for value in result.get("peers", [])))
    return result


class StationNetworkBridge(QObject):
    changed = Signal()
    configuration_finished = Signal(str, object)

    def __init__(self, window, config):
        super().__init__(window)
        self.window, self.controller = window, window.controller
        self.config = validate_configuration(config)
        self.runtime = self.media = self.viewer = None
        self.error = ""
        self.peers = []
        self._counter = 0
        self._lock = threading.Lock()
        self._history, self._last_observations = {}, {}
        self._workers, self._hardware = {}, {}
        self._last_status = 0
        self._mode = None
        self._restart_pending = False
        self._location_provenance = None
        self._request_targets = {}
        self.station_assignments = {}
        try:
            saved = json.loads((self.controller.data_dir / "station-assignments.json").read_text())
            if isinstance(saved, dict):
                self.station_assignments = saved
        except (OSError, ValueError):
            pass
        self.configuration_finished.connect(self._configuration_sent)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.start()
        self.timer.start()

    def start(self):
        from .network.media import MediaService

        self.error = ""
        self._mode = self.controller.mode.lower()
        settings = {**self.config, "network_source_mode": self._mode}
        if self.config.get("accept_replay"):
            settings["network_accept_source_modes"] = ["live", "replay"]
        if self.window.profile.role == "video":
            try:
                self.media = MediaService(self.window.profile, bind_host=self.config["network_bind_host"])
            except Exception as exc:
                self.error = f"Network video unavailable: {exc}"
        self.runtime = NetworkRuntime(self.controller.data_dir, self.window.profile,
                                      self.config["mission_id"], settings,
                                      media_session_backend=self.media)
        if self.media:
            self.media.notify = self.runtime.update_streams
            self.media.set_context(self.runtime.info(), self.runtime.boot_monotonic)
        self._counter = 0
        self._workers.clear()
        self._hardware.clear()
        self._restore_station_location()
        self.runtime.set_configuration(self.configuration_snapshot())
        for address in self.config["peers"]:
            self.runtime.add_endpoint(address)
        self.runtime.start()
        self.controller.network_observation_sink = self.observation
        existing = self.controller.workers.get("telemetry")
        if existing is not None and hasattr(existing, "on_observation"):
            def accepted(sample, packet, context):
                if (self.controller.workers.get("telemetry") is existing
                        and self.controller.mode == "LIVE" and self.controller.polling
                        and self.controller.network_observation_sink is not None):
                    self.controller.network_observation_sink(sample, packet, context)
            existing.on_observation = accepted
        if self.window.profile.role == "video" and self.window.profile.station == "base":
            from .network.media_receiver import VideoWallNetwork
            self.viewer = VideoWallNetwork(self.runtime, self.window.video_wall)

    def stop(self):
        self.controller.network_observation_sink = None
        if self.viewer:
            self.viewer.close()
            self.viewer = None
        if self.runtime:
            self.runtime.stop()
        if self.media:
            self.media.close()
            self.media = None

    def close(self):
        self.timer.stop()
        self.stop()

    def apply(self, config):
        config = validate_configuration(config)
        # Deliberately never restore a previous mission merely by restarting.
        path = self.controller.data_dir / "station-network.json"
        persisted = {key: config[key] for key in ("network_bind_host", "network_port", "network_discovery", "peers")}
        path.write_text(json.dumps(persisted, indent=2) + "\n")
        self.stop()
        self.config = config
        self._history.clear()
        self._last_observations.clear()
        self.select_source(None)
        self.start()
        self.poll()

    def observation(self, sample, raw, context):
        # Serial worker callback: no widget access or UI queue on this path.
        with self._lock:
            runtime = self.runtime
            if runtime is None or runtime.source_mode != "live":
                return
            self._counter += 1
            payload = observation_from_sample(
                sample, raw=raw, station=runtime.station, node_id=runtime.node_id,
                boot_id=runtime.boot_id, connection_id=context["connection_id"],
                observation_counter=self._counter, boot_monotonic=runtime.boot_monotonic,
                vehicle_mode=runtime.vehicle_mode, decoder_gaps=context.get("decoder_gaps", 0),
                rejected_packets=context.get("rejected_packets"),
            )
            runtime.publish_observation(payload)

    def select_source(self, node_id):
        c = self.controller
        c.remote_display_key = node_id or None
        c.remote_display_sample = None
        c.remote_display_history = deque(maxlen=6000)
        if node_id in self._history:
            c.remote_display_history = self._history[node_id]
            c.remote_display_sample = c.remote_display_history[-1] if c.remote_display_history else None
        self.changed.emit()

    def _hardware_status(self, role):
        if role not in self.controller.states:
            state, reason = "unsupported", "No local device for this role"
        else:
            reason = self.controller.states[role]
            state = "connected" if reason == "Connected" else "disconnected"
        old = self._hardware.get(role)
        if old is None or (old["availability"], old["reason"]) != (state, reason):
            self._hardware[role] = dict(availability=state, reason=reason,
                                        changed_mono_ms=self.runtime.mono_ms())
        return self._hardware[role]

    def poll(self):
        if self._mode != self.controller.mode.lower():
            self.stop()
            self.start()
        runtime, c = self.runtime, self.controller
        self.peers = runtime.peers_snapshot()
        now = time.monotonic()
        # A bounded UI copy queue is separate from the complete disk archive.
        # Consume every delivered observation for plot history, then snapshots
        # fill initial/reset state. Sequence comparison prevents rollback.
        eligible = {(p.get("info") or {}).get("node_id"): p for p in self.peers
                    if p.get("synchronized") and not p.get("conflict") and p["state"] in {"ONLINE", "STALE"}}
        for event in runtime.drain_events(limit=2000):
            peer = eligible.get(event.get("node_id"))
            if event.get("kind") == "telemetry.observation" and peer and event["boot_id"] == peer["info"]["boot_id"]:
                payload = event["payload"]
                age = max(0, int(event["sent_mono_ms"]) - int(payload["timing"]["received_mono_ms"]))
                age += max(0, now - event["received_local_mono"]) * 1000
                self._accept_remote(peer, payload, now, age)
        if now - self._last_status >= .5:
            self._last_status = now
            runtime.update_status(dict(
                application=dict(state="running", source_mode=runtime.source_mode),
                telemetry_board=self._hardware_status("telemetry") if self.window.profile.role == "telemetry" else None,
                pointer=self._hardware_status("pointer") if self.window.profile.role == "telemetry" and self.window.profile.station != "base" else None,
                video_inputs=self.media.input_status() if self.media else None,
                network=dict(ltu_signal_dbm=None, metric="application_round_trip"),
                clock=dict(quality="unknown", uncertainty_ms=None),
            ))
        if self.media:
            for key, state in c.video_streams.items():
                if self._workers.get(key) is not state.worker:
                    self.media.set_source(key, state.worker)
                    self._workers[key] = state.worker
        for peer in self.peers:
            if not peer.get("synchronized") or peer.get("conflict") or peer["state"] not in {"ONLINE", "STALE"}:
                continue
            for payload in peer.get("latest", []):
                age = payload.get("sample_age_ms_lower_bound")
                if age is not None:
                    self._accept_remote(peer, payload, now, age)
        panel = getattr(self.window, "away_connections", None)
        if panel:
            self._update_connections(panel)
        self._apply_configuration_requests()
        self.changed.emit()

    def configuration_snapshot(self):
        mission = self.controller.mission
        location = None
        if mission.pointer_site_configured and self.window.profile.station != "base":
            location = dict(lat_deg=mission.pointer_latitude, lon_deg=mission.pointer_longitude,
                alt_m=mission.pointer_altitude, alt_datum="wgs84_ellipsoid", label=mission.pointer_site_name,
                coordinate_input=dict(format=mission.pointer_location_format, code=mission.pointer_location_code))
            original = self._location_provenance
            if original and all(original.get(key) == location[key] for key in ("lat_deg", "lon_deg", "alt_m")):
                location = copy.deepcopy(original)
        return dict(station=self.runtime.station, location=location)

    def _restore_station_location(self):
        try:
            saved = json.loads((self.controller.data_dir / "station-configuration.json").read_text())
            if (self.runtime.mission_id and saved.get("mission_id") == self.runtime.mission_id
                    and saved.get("station") == self.runtime.station and saved.get("location")):
                mission = copy.deepcopy(self.controller.mission)
                self._set_location(mission, saved["location"])
                self.controller.mission = mission.validate()
                self._location_provenance = saved["location"]
        except (OSError, ValueError, KeyError, TypeError):
            pass

    @staticmethod
    def _set_location(mission, location):
        mission.pointer_latitude = location["lat_deg"]
        mission.pointer_longitude = location["lon_deg"]
        mission.pointer_altitude = location["alt_m"]
        mission.pointer_site_configured = True
        coordinates = location.get("coordinate_input") or {}
        kind = coordinates.get("format", "latlon")
        mission.pointer_location_format = "latlon" if kind == "pluscode" else kind
        mission.pointer_location_code = "" if kind == "pluscode" else coordinates.get("code", "")
        mission.pointer_site_name = location.get("label", "Remote station location")

    def _apply_configuration_requests(self):
        for request in self.runtime.drain_configuration_requests():
            try:
                if self._restart_pending:
                    raise ValueError("Station is restarting after an assignment; retry after reconnection")
                station = request.get("station", self.runtime.station)
                location = request.get("location")
                new_mission = copy.deepcopy(self.controller.mission)
                if location is not None:
                    self._set_location(new_mission, location)
                new_mission.validate()
                changed_station = station != self.runtime.station
                configuration = dict(station=station, location=location or self.configuration_snapshot()["location"],
                                     mission_id=self.runtime.mission_id, node_id=self.runtime.node_id)
                from .domain import write_json
                write_json(self.controller.data_dir / "station-configuration.json", configuration)
                if self.controller.tracking:
                    self.controller.hold("Station location updated remotely; review location before resuming tracking")
                self.controller.mission = new_mission
                if location is not None:
                    self._location_provenance = copy.deepcopy(location)
                self.controller.changed.emit()
                self.runtime.set_configuration(configuration)
                self.runtime.complete_configuration_request(request["request_id"], result={
                    "configuration": configuration, "restart_required": changed_station})
                self.controller.log(f"Station configuration received from Base: {station}")
                if changed_station:
                    self._restart_pending = True
                    profile = replace(self.window.profile, station="away" + station[-1])
                    QTimer.singleShot(500, lambda p=profile, m=new_mission: self._restart_station(p, m))
            except (ValueError, OSError, KeyError) as exc:
                self.error = f"Station configuration error: {exc}"
                try:
                    self.runtime.complete_configuration_request(request["request_id"], error=str(exc))
                except (OSError, ValueError) as persist_error:
                    self.error = f"Could not persist station configuration acknowledgement: {persist_error}; inspect the applied location"

    def _restart_station(self, profile, mission):
        from PySide6.QtWidgets import QApplication
        from .station_workspace import StationWindow
        app = QApplication.instance()
        previous = app.quitOnLastWindowClosed()
        app.setQuitOnLastWindowClosed(False)
        data, config = self.controller.data_dir, dict(self.config)
        self.window.close()
        try:
            window = StationWindow(data, profile=profile, network_config=config)
            window.controller.mission = mission
            window.controller.changed.emit()
            app.station_window = window  # Hold the new top-level Python owner.
            window.show()
        finally:
            app.setQuitOnLastWindowClosed(previous)

    def request_remote_configuration(self, peer, station, location):
        info, target = self.runtime.info(), peer.get("info") or {}
        if info["station"] != "launch" or not info["mission_id"]:
            raise ValueError("Join a mission as Base Station before configuring away devices")
        if target.get("mission_id") != info["mission_id"]:
            raise ValueError("The target device must join the same mission first")
        request_id = str(uuid.uuid4())
        request = dict(request_id=request_id, client_node_id=info["node_id"], client_boot_id=info["boot_id"],
            expected_node_id=target["node_id"], expected_boot_id=target["boot_id"], mission_id=info["mission_id"],
            station=station)
        if location is not None:
            request["location"] = location
        self._request_targets[request_id] = (target["node_id"], station)
        thread = threading.Thread(target=self._send_configuration, args=(peer["endpoint"], request),
                                  name="station-configuration", daemon=True)
        thread.start()
        return request_id

    def _send_configuration(self, address, body):
        request_id = body["request_id"]
        def send(method, suffix="", payload=None):
            request = urllib.request.Request(address.rstrip("/") + "/api/v1/configuration" + suffix,
                data=json.dumps(payload, allow_nan=False).encode() if payload is not None else None,
                headers={"Content-Type": "application/json", "Accept": "application/json"}, method=method)
            with urllib.request.urlopen(request, timeout=3) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError("Configuration response exceeded its size limit")
                response = json.loads(raw)
                if (response.get("node_id") != body["expected_node_id"]
                        or response.get("mission_id") != body["mission_id"]
                        or response.get("request_id") != request_id):
                    raise ValueError("Configuration acknowledgement identity does not match the selected device/mission/request")
                return response
        result = None
        try:
            # Retries retain the same id/body, including after an uncertain response.
            for attempt in range(3):
                try:
                    result = send("PATCH", payload=body)
                    break
                except urllib.error.HTTPError:
                    raise
                except OSError:
                    if attempt == 2:
                        raise
                    time.sleep(.5)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if result.get("state") in {"applied", "failed"}:
                    break
                time.sleep(.3)
                try:
                    result = send("GET", "?request_id=" + request_id)
                except OSError:
                    continue  # Renumbering restarts this same endpoint/installation.
            if result.get("state") != "applied":
                raise ValueError(result.get("error") or "No applied acknowledgement received; inspect target before retrying")
            applied = result.get("result") or result
            self.configuration_finished.emit(request_id, {"ok": True, **applied})
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    details = json.loads(exc.read(65536))
                    message = details.get("error", {})
                    message = message.get("message", str(exc)) if isinstance(message, dict) else str(message)
                except (ValueError, OSError):
                    message = str(exc)
            else:
                message = str(exc)
            self.configuration_finished.emit(request_id, {"ok": False, "error": message})

    def _configuration_sent(self, request_id, result):
        target = self._request_targets.pop(request_id, None)
        if result.get("ok") and target:
            self.station_assignments[target[0]] = target[1]
            from .domain import write_json
            try:
                write_json(self.controller.data_dir / "station-assignments.json", self.station_assignments)
            except OSError as exc:
                self.error = f"Remote update applied, but local device mapping could not be saved: {exc}"
        self.changed.emit()

    def _accept_remote(self, peer, payload, now, age):
        info, identity = peer["info"], payload["observation_id"]
        node = info["node_id"]
        previous = self._last_observations.get(node, "")
        same_boot = previous and previous.split(":")[:2] == identity.split(":")[:2]
        if same_boot and int(previous.rsplit(":", 1)[1]) >= int(identity.rsplit(":", 1)[1]):
            return
        source = f"{info.get('station', '?').upper()} · {payload.get('vehicle', {}).get('id', 'unknown')} · read only"
        sample = observation_to_sample(payload, received_monotonic=now, source_age_ms=age, source_label=source)
        history = self._history.setdefault(node, deque(maxlen=6000))
        if previous and not same_boot:
            history.clear()
        history.append(sample)
        self._last_observations[node] = identity
        if self.controller.remote_display_key == node:
            self.controller.remote_display_history, self.controller.remote_display_sample = history, sample

    def _update_connections(self, panel):
        for index in range(1, 5):
            rows = [p for p in self.peers if (p.get("info") or {}).get("station") == f"gs{index}"]
            eligible = [p for p in rows if not p.get("conflict") and p.get("synchronized") and p["state"] in {"ONLINE", "STALE"}]
            telem = next((p for p in eligible if p["info"]["role"] == "telem"), None)
            online = bool(eligible)
            ping = min((p["rtt_ms"] for p in eligible if p.get("rtt_ms") is not None), default=None)
            ages = [o["sample_age_ms_lower_bound"] for o in (telem or {}).get("latest", []) if o.get("sample_age_ms_lower_bound") is not None]
            panel.health.observe_link(f"away{index}", online=online, ping_ms=ping)
            panel.health.observe_rocket(f"away{index}", sample_age_ms=min(ages) if ages else None)
            labels = []
            for role, name in (("telem", "T"), ("video", "V")):
                peers = [p for p in rows if (p.get("info") or {}).get("role") == role]
                labels.append(name + ": " + ("CONFLICT" if any(p.get("conflict") for p in peers) else peers[0]["state"] if peers else "OFFLINE"))
            panel.tiles[f"away{index}"].role_status = " / ".join(labels)
        panel.refresh()
        base_video = next((p for p in self.peers if (p.get("info") or {}).get("station") == "launch" and p["info"].get("role") == "video"), None)
        panel.status.setText(panel.status.text() + " · Base video: " + (base_video["state"] if base_video else "OFFLINE"))
        panel.status.setToolTip("LTU-XR bar uses application round-trip time across the station link, not RF signal strength. Rocket age is an unsynchronized-clock lower bound. App heartbeat: stale at 3 s, offline at 10 s. GS colors use 250 ms / 1 s rocket age thresholds.")

    def summary(self):
        mission = self.config["mission_id"]
        online = sum(p["state"] == "ONLINE" and not p.get("conflict") for p in self.peers)
        return self.error or self.runtime.error or self.runtime.archive_status.get("error") or (
            f"Network: {self.runtime.state} · {online} peers · " + (f"mission {mission[:8]}" if mission else "choose a mission")
        )
