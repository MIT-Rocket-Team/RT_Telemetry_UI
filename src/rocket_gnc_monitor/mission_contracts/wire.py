"""rt-mission.v1 framing only. Callers own transport/lifecycle; no devices are opened here."""

import base64
import binascii
import struct

from .contracts import MAX_BYTES, encode, parse, resource, validate

MAX_CHUNK_BYTES = 65_536


def validate_message(message):
    if issues := validate(resource("ipc.schema.json"), message):
        raise ValueError(issues)
    if not -(2**63) <= int(message["missionTimeUs"]) < 2**63 or not 0 <= int(message["sequence"]) < 2**63:
        raise ValueError("IPC integer out of range")
    if message["type"] in ("rx-bytes", "tx-bytes"):
        data = message["body"].get("data")
        if not isinstance(data, str) or len(data) > ((MAX_CHUNK_BYTES + 2) // 3) * 4:
            raise ValueError("Missing or oversized byte data")
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid base64") from exc
        if len(raw) > MAX_CHUNK_BYTES or base64.b64encode(raw).decode("ascii") != data:
            raise ValueError("Byte chunk must use bounded canonical base64")


def _exact(source, size):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = source.read(size - len(chunks))
        if not chunk:
            raise EOFError("Truncated mission frame")
        chunks.extend(chunk)
    return bytes(chunks)


def read_frame(source):
    first = source.read(1)
    if not first:
        return None
    size = struct.unpack(">I", first + _exact(source, 3))[0]
    if not 1 <= size <= MAX_BYTES:
        raise ValueError("Mission frame size out of bounds")
    message = parse(_exact(source, size))
    validate_message(message)
    return message


def encode_frame(message):
    validate_message(message)
    data = encode(message)
    return struct.pack(">I", len(data)) + data
