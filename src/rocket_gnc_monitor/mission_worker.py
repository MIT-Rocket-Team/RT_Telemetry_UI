"""Bundled headless station worker. Usage: python -m rocket_gnc_monitor.mission_worker manifest.json."""
import hmac
import json
import socket
import sys
from pathlib import Path

from .mission_contracts.wire import encode_frame, read_frame
from .mission_station import MissionStation

CAPABILITIES = ["legacy-zephyrus.v1", "declarative-codec.v1", "legacy-ui.v1", "geodetic.v1",
                "mission-clock.v1", "reorder-safe.v1", "pointer-bytes.v1"]


def run(manifest, *, before_advance=None, after_advance=None, configured=None, control=None):
    if manifest["host"] != "127.0.0.1":
        raise ValueError("Mission worker requires a loopback host")
    with socket.create_connection((manifest["host"], manifest["port"]), timeout=10) as connection:
        # The Java owner applies a per-request watchdog. A deliberately paused
        # mission may remain idle indefinitely without advancing station time.
        connection.settimeout(None)
        with connection.makefile("rwb", buffering=0) as channel:
            stations, sequence, last_input = {}, 0, -1
            authenticated = False
            while True:
                message = read_frame(channel)
                if message is None:
                    return
                if any(message[key] != manifest[key] for key in
                       ("sessionId", "missionId", "runId", "memberId", "participantId", "connectionId")):
                    raise ValueError("Mission identity mismatch")
                current = int(message["sequence"])
                if current <= last_input:
                    raise ValueError("Mission sequence must increase")
                last_input = current
                kind, body = message["type"], message["body"]
                try:
                    if not authenticated and kind != "hello":
                        raise ValueError("Mission handshake required")
                    if kind == "hello" and not authenticated:
                        if not hmac.compare_digest(body.get("credential", ""), manifest["credential"]):
                            raise ValueError("Mission credential mismatch")
                        authenticated = True
                        reply, result = "capabilities", {"capabilities": CAPABILITIES, "workerVersion": 1}
                    elif kind == "configure":
                        if len(body["stations"]) > 5:
                            raise ValueError("At most five stations per member")
                        stations = {s["id"]: MissionStation(s) for s in body["stations"]}
                        reply, result = "ready", {"stations": list(stations), **(configured(stations) if configured else {})}
                    elif kind == "advance":
                        batches = body["stations"]
                        if before_advance:
                            before_advance(batches)
                        if len(batches) > 5 or sum(len(b.get("deliveries", [])) + len(b.get("actions", []))
                                                  for b in batches) > 256:
                            raise ValueError("Advance batch exceeds bounded queue capacity")
                        time_us = int(message["missionTimeUs"])
                        result = {"acknowledgedSequence": message["sequence"], "stations": [
                            stations[b["id"]].advance(time_us, b.get("deliveries", []), b.get("actions", []),
                                                     evaluate=b.get("evaluate", True))
                            for b in batches]}
                        if after_advance:
                            after_advance(time_us, batches, result, stations)
                        reply = "advanced"
                    elif kind == "action" and control:
                        reply, result = "result", control(body["kind"], body)
                    elif kind in ("pause", "resume"):
                        reply, result = "result", {"paused": kind == "pause"}
                    elif kind == "close":
                        reply, result = "closed", {}
                    else:
                        raise ValueError("Unsupported worker message")
                except (ValueError, KeyError, TypeError) as exc:
                    reply, result = "error", {"message": str(exc)}
                response = {**message, "type": reply, "body": result,
                            "messageId": "reply-" + str(sequence), "sequence": str(sequence)}
                connection.sendall(encode_frame(response))
                sequence += 1
                if kind == "close" or reply == "error":
                    return


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Expected a private mission manifest")
    path = Path(sys.argv[1])
    if path.stat().st_size > 1_048_576:
        raise ValueError("Manifest exceeds 1 MiB")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    run(manifest)


if __name__ == "__main__":
    main()
