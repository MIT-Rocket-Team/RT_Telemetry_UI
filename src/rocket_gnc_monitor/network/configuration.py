"""Bounded, durable station/location configuration requests, never hardware commands.

The HTTP worker queues validated requests. The GUI owns applying station and
mission choices. Completed tickets survive the service restart used to renumber
an Away station, so a lost acknowledgement cannot apply a request twice.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from .protocol import NetworkProtocolError, dumps, number, obj, require, uuid_text


def validate_configuration_request(value):
    obj(value, ("request_id", "client_node_id", "client_boot_id", "expected_node_id", "expected_boot_id", "mission_id"))
    allowed = {"request_id", "client_node_id", "client_boot_id", "expected_node_id", "expected_boot_id", "mission_id", "station", "location"}
    require(set(value) <= allowed, "Unsupported station configuration field")
    for key in allowed - {"station", "location"}:
        uuid_text(value[key])
    require("station" in value or "location" in value, "Choose a station number or location to configure")
    if "station" in value:
        require(value["station"] in {"gs1", "gs2", "gs3", "gs4"}, "Only Away stations GS1 through GS4 can be assigned")
    if "location" in value:
        location = obj(value["location"], ("lat_deg", "lon_deg", "alt_m", "alt_datum", "label"))
        require(set(location) <= {"lat_deg", "lon_deg", "alt_m", "alt_datum", "label", "coordinate_input"}, "Unsupported location field")
        number(location["lat_deg"], nullable=False)
        number(location["lon_deg"], nullable=False)
        number(location["alt_m"], nullable=False)
        require(-90 <= location["lat_deg"] <= 90 and -180 <= location["lon_deg"] <= 180, "Invalid station latitude or longitude")
        require(-12000 <= location["alt_m"] <= 100000, "Station altitude is outside supported limits")
        require(location["alt_datum"] == "wgs84_ellipsoid", "Station location altitude must use WGS84 ellipsoid metres")
        require(isinstance(location["label"], str) and len(location["label"]) <= 160 and all(ord(c) >= 32 for c in location["label"]), "Invalid station location label")
        if "coordinate_input" in location:
            source = obj(location["coordinate_input"], ("format", "code"))
            require(set(source) <= {"format", "code"} and source["format"] in {"latlon", "mgrs", "pluscode"}, "Invalid original coordinate format")
            require(isinstance(source["code"], str) and len(source["code"]) <= 160, "Invalid original coordinate text")
    dumps(value)
    return copy.deepcopy(value)


class ConfigurationLedger:
    MAX_ENTRIES = 128
    MAX_AGE = 3600

    def __init__(self, data_dir, boot_id):
        self.path = Path(data_dir) / "station-configuration-requests.json"
        self.boot_id = boot_id
        self.lock = threading.RLock()
        self.records = {}
        self.pending = []
        self.snapshot = {"station": None, "location": None}
        if self.path.exists():
            try:
                if self.path.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError("Configuration ledger exceeds its size limit")
                data = json.loads(self.path.read_text())
                if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("records"), dict):
                    raise ValueError("Unsupported configuration ledger")
                self.records = data["records"]
                self._prune()
                for record in self.records.values():
                    if record["state"] == "queued":
                        record.update(state="failed", error="Station service restarted before the request was applied", result=None)
                self._save()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"Station configuration ledger cannot be read safely: {exc}") from exc

    def _prune(self):
        now = time.time()
        self.records = {key: value for key, value in self.records.items()
                        if isinstance(value, dict) and now - value.get("created_utc", 0) <= self.MAX_AGE}
        # Do not silently remove queued requests to make room for new requests.
        if len(self.records) > self.MAX_ENTRIES:
            raise ValueError("Configuration ledger has too many requests")

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
                temp_path = Path(handle.name)
                json.dump({"version": 1, "records": self.records}, handle, allow_nan=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _digest(request):
        return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    @staticmethod
    def _public(record):
        return {key: copy.deepcopy(record[key]) for key in ("request_id", "state", "result", "error")}

    def existing(self, request):
        with self.lock:
            self._prune()
            record = self.records.get(request["request_id"])
            if record is None:
                return None
            if record["digest"] != self._digest(request):
                raise NetworkProtocolError(409, "idempotency_conflict", "Request ID was already used with different configuration")
            return self._public(record)

    def enqueue(self, request):
        with self.lock:
            previous = self.existing(request)
            if previous:
                return previous
            if len(self.records) >= self.MAX_ENTRIES:
                raise NetworkProtocolError(429, "configuration_capacity", "Station configuration request capacity reached", True)
            record = {"request_id": request["request_id"], "digest": self._digest(request), "created_utc": time.time(),
                      "state": "queued", "result": None, "error": None, "request": copy.deepcopy(request)}
            self.records[request["request_id"]] = record
            try:
                self._save()
            except OSError:
                del self.records[request["request_id"]]
                raise
            self.pending.append(request["request_id"])
            return self._public(record)

    def get(self, request_id):
        uuid_text(request_id)
        with self.lock:
            self._prune()
            record = self.records.get(request_id)
            if record is None:
                raise NetworkProtocolError(404, "configuration_request_not_found", "Configuration request is unknown or expired")
            return self._public(record)

    def drain(self):
        with self.lock:
            identities, self.pending = self.pending, []
            return [copy.deepcopy(self.records[key]["request"]) for key in identities
                    if key in self.records and self.records[key]["state"] == "queued"]

    def complete(self, request_id, result=None, error=None):
        uuid_text(request_id)
        if error is not None:
            require(isinstance(error, str) and len(error) <= 2000, "Configuration error must be bounded text")
        if result is not None:
            obj(result)
            require(len(dumps(result).encode()) <= 65536, "Configuration result exceeds 64 KiB")
        with self.lock:
            record = self.records.get(request_id)
            if record is None:
                raise ValueError("Configuration request was not queued")
            if record["state"] != "queued":
                return self._public(record)
            previous = copy.deepcopy(record)
            record.update(state="failed" if error else "applied", result=copy.deepcopy(result), error=error)
            try:
                self._save()
            except OSError:
                self.records[request_id] = previous
                raise
            return self._public(record)

    def set_snapshot(self, value):
        obj(value, ("station", "location"))
        dumps(value)
        with self.lock:
            self.snapshot = copy.deepcopy(value)

    def get_snapshot(self):
        with self.lock:
            return copy.deepcopy(self.snapshot)
