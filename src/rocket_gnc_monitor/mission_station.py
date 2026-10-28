"""Qt-free station used by the bundled worker and Mission-connected application.

The only target observations admitted here come from decoded receiver bytes. Metadata
carries packet identity, acquisition times and declared height references, never truth.
"""
import base64
import copy

from .domain import Sample
from .mission_contracts.codec import TelemetryCodec
from .protocol import TelemetryDecoder, pointer_packet
from .tracking_policy import TrackingPolicy


class CodecDecoder:
    def __init__(self, definition):
        self.codec = TelemetryCodec(definition)
        self.buffer = bytearray()
        self.accepted = self.rejected = self.discarded = 0

    def feed(self, chunk, *, received, utc):
        self.buffer.extend(chunk)
        codec, output = self.codec, []
        while len(self.buffer) >= codec.frame_bytes:
            if codec.sync:
                at = self.buffer.find(codec.sync)
                if at < 0:
                    keep = len(codec.sync) - 1
                    self.discarded += len(self.buffer) - keep
                    self.buffer[:] = self.buffer[-keep:] if keep else b""
                    break
                self.discarded += at
                del self.buffer[:at]
                if len(self.buffer) < codec.frame_bytes:
                    break
            try:
                decoded = codec.decode(bytes(self.buffer[len(codec.sync):len(codec.sync) + codec.length]))
            except ValueError:
                self.rejected += 1
                del self.buffer[0 if codec.sync else slice(0, codec.frame_bytes)]
                continue
            del self.buffer[:codec.frame_bytes]
            values = {field["source"]: decoded[field["name"]] for field in codec.definition["fields"]}
            output.append(Sample(
                t=(values.get("uptime_ms") or 0) / 1000,
                sequence=int(values.get("packet_sequence") or 0), source="SIMULATION",
                received=received, utc=utc, altitude=values.get("barometer.height_m"),
                velocity=values.get("velocity.up_ms"), latitude=values.get("gps.latitude_deg"),
                longitude=values.get("gps.longitude_deg"), gps_altitude=values.get("gps.height_m"),
                gps_fix=int(values.get("gps.fix") or 0),
                details={"codec": codec.definition["id"], "availability": list(values)},
            ))
            self.accepted += 1
        return output


class MissionStation:
    def __init__(self, config):
        self.config = copy.deepcopy(config)
        self.id = config["id"]
        self.decoder = CodecDecoder(config["codec"]) if config.get("codec") else TelemetryDecoder()
        self.policy = TrackingPolicy(origin=tuple(config["origin"]),
                                     policy=config.get("policy", "legacy-ui.v1"),
                                     freshness=config.get("freshnessS", 2.0))
        self.pointer_enabled = config.get("pointerEnabled", False)
        self.owner = config.get("owner", "headless-policy")
        self.newest_tx_us = -(2**63)
        self.last_time_us = -1_000_000
        self.epoch = 0

    def receive(self, delivery, time_us):
        raw = base64.b64decode(delivery["data"], validate=True)
        if len(raw) > 65536:
            raise ValueError("Receiver chunk exceeds 64 KiB")
        tx_us = int(delivery["txTimeUs"])
        old = tx_us <= self.newest_tx_us
        # The normal decoder retains its real-device reboot heuristic. A simulated
        # connection epoch explicitly identifies reboots; reordered frames must
        # not mutate that heuristic or refresh the newest observation.
        decoder_state = {key: getattr(self.decoder, key) for key in (
            "last_sequence", "last_tick", "wrap_offset", "boot", "gaps"
        ) if hasattr(self.decoder, key)}
        samples = self.decoder.feed(raw, received=time_us / 1e6, utc=0.0)
        if old:
            for key, value in decoder_state.items():
                setattr(self.decoder, key, value)
        results = []
        for sample in samples:
            sample.source = "SIMULATION"
            sample.details.update({"mission": True, "packetId": delivery["packetId"],
                                   "txTimeUs": str(tx_us), "outOfOrder": old,
                                   "connectionEpoch": self.epoch,
                                   "heightReference": delivery.get("heightReference", {}),
                                   "acquisitionTimeUs": delivery.get("acquisitionTimeUs", str(tx_us)),
                                   "unavailable": delivery.get("unavailable", [])})
            if old and "boot" in decoder_state:
                sample.details["boot_index"] = decoder_state["boot"]
            # Explicit optional-field masks prevent zero-filled absent fields from
            # appearing to be valid measurements in a declarative custom codec.
            if isinstance(self.decoder, CodecDecoder):
                missing = set(delivery.get("missingFields", []))
                fields = self.decoder.codec.definition["fields"]
                attributes = {"gps.latitude_deg": "latitude", "gps.longitude_deg": "longitude",
                              "gps.height_m": "gps_altitude", "barometer.height_m": "altitude",
                              "velocity.up_ms": "velocity", "gps.fix": "gps_fix"}
                for field in fields:
                    if field["name"] in missing and field["source"] in attributes:
                        setattr(sample, attributes[field["source"]], 0 if field["source"] == "gps.fix" else None)
            if not old:
                self.policy.latest = sample
                self.policy.height_reference = delivery.get("heightReference")
                self.newest_tx_us = tx_us
            results.append(sample.to_dict())
        return results

    def advance(self, time_us, deliveries=(), actions=(), *, evaluate=True):
        if time_us < self.last_time_us:
            raise ValueError("Station clock cannot move backwards")
        if len(deliveries) + len(actions) > 256:
            raise ValueError("Station batch exceeds the 256-message bound")
        self.last_time_us = time_us
        samples, writes, outcomes = [], [], []
        before_rejected = self.decoder.rejected
        for delivery in deliveries:
            samples.extend(self.receive(delivery, time_us))
        for action in actions:
            kind = action["kind"]
            try:
                if kind == "connect":
                    self.policy.connected = self.pointer_enabled
                    self.policy.polling = True
                elif kind == "disconnect":
                    self.policy.connected = self.policy.polling = False
                    self.policy.hold("Disconnected")
                    self.decoder.buffer.clear()
                    for key, value in (("last_sequence", None), ("last_tick", None), ("wrap_offset", 0), ("boot", 0)):
                        if hasattr(self.decoder, key):
                            setattr(self.decoder, key, value)
                    self.policy.latest = None
                    self.policy.height_reference = None
                    self.newest_tx_us = -(2**63)
                    self.epoch += 1
                elif kind == "start-tracking":
                    if self.owner == "observer":
                        raise ValueError("Observers cannot command the pointer")
                    self.policy.start(time_us)
                elif kind == "hold":
                    self.policy.hold("Operator hold")
                elif kind in ("zero", "point"):
                    if not self.pointer_enabled or not self.policy.connected or self.owner == "observer":
                        raise ValueError("This station does not own a connected pointer")
                    self.policy.hold("Manual command")
                    packet = pointer_packet(opcode=5) if kind == "zero" else pointer_packet(
                        action["azimuthDeg"], action["elevationDeg"])
                    writes.append(self.write(packet, time_us))
                else:
                    raise ValueError("Unsupported station action")
                outcomes.append({"id": action.get("id", kind), "outcome": "applied"})
            except (ValueError, KeyError) as exc:
                outcomes.append({"id": action.get("id", kind), "outcome": "rejected", "reason": str(exc)})
        target = self.policy.advance(time_us) if evaluate else None
        if target is not None and self.owner != "observer":
            writes.append(self.write(pointer_packet(*target), time_us))
        return {"id": self.id, "samples": samples, "writes": writes, "actions": outcomes,
                "tracking": self.policy.tracking, "reason": self.policy.reason,
                "rejected": self.decoder.rejected - before_rejected,
                "acceptedTotal": self.decoder.accepted, "connectionEpoch": self.epoch}

    def write(self, packet, time_us):
        return {"siteId": self.id, "role": "pointer", "data": base64.b64encode(packet).decode("ascii"),
                "createdUs": str(time_us), "expiresUs": str(time_us + TrackingPolicy.EXPIRY_US),
                "connectionEpoch": self.epoch}
