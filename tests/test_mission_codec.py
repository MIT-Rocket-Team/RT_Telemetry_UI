import copy
import struct

import pytest

from rocket_gnc_monitor.mission_contracts.codec import TelemetryCodec
from rocket_gnc_monitor.mission_contracts.contracts import resource


def test_independent_layout_vector_and_unavailable_provenance():
    v = resource("codec-vectors.json")
    codec = TelemetryCodec(v["codec"])
    payload, missing = codec.encode(v["values"])
    assert payload.hex() == v["payloadHex"]
    assert missing == frozenset(v["unavailable"])
    assert codec.decode(payload) == pytest.approx(v["decoded"], abs=1e-12)
    assert len(codec.frame(payload, bytes(14))) == codec.frame_bytes == 30
    damaged = bytearray(payload)
    damaged[0] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        codec.decode(damaged)
    with pytest.raises(ValueError):
        codec.frame(payload, bytes(0))


@pytest.mark.parametrize("check", resource("codec-vectors.json")["crcChecks"])
def test_published_crc_check_strings(check):
    definition = resource("codec-vectors.json")["codec"]
    definition.update(payloadBytes=11, fields=[])
    definition["checksum"] = {**check, "algorithm": "crc16", "byteOffset": 9, "start": 0, "endExclusive": 9}
    codec = TelemetryCodec(definition)
    payload = b"123456789" + struct.pack("<H", check["expected"])
    assert codec.decode(payload) == {}
    with pytest.raises(ValueError):
        codec.decode(payload[:-1] + bytes([payload[-1] ^ 1]))


@pytest.mark.parametrize(
    "path,value",
    [
        (("fields", 0, "byteOffset"), 13),
        (("fields", 1, "byteOffset"), 1),
        (("fields", 0, "source"), "truth.altitude"),
        (("fields", 0, "scale"), 0),
        (("checksum", "endExclusive"), 14),
        (("payloadBytes",), 4097),
        (("fields", 0, "type"), "python"),
    ],
)
def test_invalid_layout_is_rejected(path, value):
    definition = resource("codec-vectors.json")["codec"]
    current = definition
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    with pytest.raises(ValueError):
        TelemetryCodec(definition)


def test_missing_and_overflow_fields_are_not_invented_or_wrapped():
    v = resource("codec-vectors.json")
    codec = TelemetryCodec(v["codec"])
    for values in ({}, {**v["values"], "gps.latitude_deg": 1e9}, {**v["values"], "gps.height_m": 1e100}):
        with pytest.raises(ValueError):
            codec.encode(values)
    definition = copy.deepcopy(v["codec"])
    other = TelemetryCodec(definition)
    definition["payloadBytes"] = 1
    assert other.encode(v["values"])[0].hex() == v["payloadHex"]
