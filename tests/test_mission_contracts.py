"""Shared, stored contract vectors plus boundary failures; imports require no Qt runtime."""

import hashlib
import io
import math
import subprocess
import sys
from importlib.resources import files

import pytest

from rocket_gnc_monitor.location import decode_mgrs
from rocket_gnc_monitor.mission_contracts import (
    create_urrg,
    geodesy,
    load_draft,
    sampling,
    save_draft,
    validate_draft,
    wire,
)
from rocket_gnc_monitor.mission_contracts.contracts import MAX_BYTES, parse, resource, validate
from rocket_gnc_monitor.protocol import TelemetryDecoder, pointer_packet
from rocket_gnc_monitor.rocket_protocol import rocket_packet


@pytest.mark.parametrize("case", resource("configuration-cases.json")["cases"], ids=lambda c: c["name"])
def test_shared_configuration_cases(case):
    draft = resource("urrg-draft.json")
    for patch in case["patch"]:
        parts = patch["path"].split("/")[1:]
        parent = draft
        for key in parts[:-1]:
            parent = parent[int(key)] if isinstance(parent, list) else parent[key]
        key = parts[-1]
        if key == "-":
            parent.append(patch["value"])
        else:
            parent[int(key) if isinstance(parent, list) else key] = patch["value"]
    actual = sorted([i["path"], i["code"]] for i in validate_draft(draft))
    assert actual == sorted(case["expected"])


def test_drafts_are_independent_lossless_and_unresolved(tmp_path):
    draft, other = create_urrg(), create_urrg()
    assert draft["identity"]["missionId"] != other["identity"]["missionId"]
    draft["future"] = {"values": [123, "preserve", None]}
    path = tmp_path / "mission.json"
    save_draft(path, draft)
    assert load_draft(path) == draft
    assert len(list(tmp_path.iterdir())) == 1
    assert all(s["heightM"] is None for s in draft["sites"])
    draft["sites"][0]["latitudeDeg"] = 100
    with pytest.raises(ValueError):
        save_draft(path, draft)
    assert load_draft(path)["sites"][0]["latitudeDeg"] == other["sites"][0]["latitudeDeg"]


def test_urrg_matches_actual_telemetry_decoder():
    draft = create_urrg()
    for actual, vector in zip(draft["sites"], resource("urrg-sites.json")["sites"], strict=True):
        decoded = decode_mgrs(vector["mgrs"])
        assert actual["latitudeDeg"] == vector["latitudeDeg"]
        assert actual["longitudeDeg"] == vector["longitudeDeg"]
        # Native MGRS builds differ in their final floating-point bit. Keep
        # saved defaults exact; compare the independent conversion to 0.1 mm.
        assert actual["latitudeDeg"] == pytest.approx(decoded.latitude, abs=1e-9, rel=0)
        assert actual["longitudeDeg"] == pytest.approx(decoded.longitude, abs=1e-9, rel=0)
        assert actual["coordinateInput"]["value"] == vector["mgrs"]
        assert actual["coordinateInput"]["reference"] == "grid-point"
    assert len(draft["pointers"]) == 4


@pytest.mark.parametrize(
    "data",
    [
        b"[]",
        b"{} {}",
        b'{"x":1,"x":2}',
        b'{"x":1,"\\u0078":2}',
        b'{"x":NaN}',
        b'{"x":1e999}',
        b"\xff",
        b'{"x":"\\ud800"}',
        b'{"x":' + b"[" * 65 + b"0" + b"]" * 65 + b"}",
        b" " * (MAX_BYTES + 1),
    ],
    ids=['array', 'trailing', 'duplicate', 'escaped-duplicate', 'nan',
         'overflow', 'utf8', 'surrogate', 'depth', 'size'],
)
def test_strict_json(data):
    with pytest.raises(ValueError):
        parse(data)


def test_unsupported_schema_keywords_are_not_silently_ignored():
    with pytest.raises(ValueError, match="Unsupported schema"):
        validate({"$ref": "https://example.invalid/schema"}, {})


def test_oversized_or_invalid_draft_does_not_replace_existing_file(tmp_path):
    path = tmp_path / "mission.json"
    save_draft(path, create_urrg())
    previous = path.read_bytes()
    draft = create_urrg()
    draft["large"] = "x" * MAX_BYTES
    with pytest.raises(ValueError):
        save_draft(path, draft)
    assert path.read_bytes() == previous


def _args(fields):
    return (fields[1], int(fields[2]), fields[3], fields[4], fields[5], int(fields[6]), int(fields[7]))


def test_random_matches_stored_independent_vectors_and_is_order_independent():
    for vector in resource("sampler-vectors.json")["vectors"]:
        args = _args(vector["fields"])
        assert sampling.digest(*args).hex() == vector["sha256"]
        assert sampling.uniform(*args) == vector["uniform"]
        assert 0 < sampling.uniform(*args) < 1
    for vector in resource("sampler-vectors.json")["normalVectors"]:
        assert sampling.normal(*_args(vector["fields"])) == pytest.approx(vector["normal"], abs=1e-14)
    before = sampling.uniform("1", 0, "gs1", "loss", "1", 23, 0)
    for i in range(100):
        sampling.uniform("1", 0, "gs4", "loss", "1", i, 0)
    assert before == sampling.uniform("1", 0, "gs1", "loss", "1", 23, 0)
    for seed in ("-1", "01", "18446744073709551616"):
        with pytest.raises(ValueError):
            sampling.uniform(seed, 0, "gs1", "loss", "1", 0, 0)


def test_analytic_geodesy_and_explicit_height_reference():
    for v in resource("geodesy-vectors.json")["ecef"]:
        assert geodesy.ecef(v["lat"], v["lon"], v["h"]) == pytest.approx(v["xyz"], abs=1e-8)
    assert geodesy.ellipsoidal_height(23, -33.842) == pytest.approx(-10.842)
    with pytest.raises(ValueError):
        geodesy.ellipsoidal_height(23, math.nan)
    with pytest.raises(ValueError):
        geodesy.ecef(91, 0, 0)


def test_wire_matches_literal_bytes_and_fragmentation():
    vector = resource("ipc-vector.json")
    expected = bytes.fromhex(vector["frameHex"])
    assert wire.encode_frame(vector["message"]) == expected

    class Fragmented(io.BytesIO):
        def read(self, n=-1):
            return super().read(min(n, 1))

    stream = Fragmented(expected + expected)
    assert wire.read_frame(stream) == vector["message"]
    assert wire.read_frame(stream) == vector["message"]
    assert wire.read_frame(stream) is None
    for count in (1, 3, 4, len(expected) - 1):
        with pytest.raises(EOFError):
            wire.read_frame(io.BytesIO(expected[:count]))
    with pytest.raises(ValueError):
        wire.read_frame(io.BytesIO(b"\xff" * 4))


def test_wire_rejects_wrong_clock_type_overflow_and_noncanonical_base64():
    message = resource("ipc-vector.json")["message"]
    for value in (42, "9223372036854775808", "-9223372036854775809"):
        with pytest.raises(ValueError):
            wire.encode_frame({**message, "missionTimeUs": value})
    for value in ("@@", "YQ", "YR==", "Y Q==", "x" * 100_000):
        with pytest.raises(ValueError):
            wire.encode_frame({**message, "body": {"data": value}})
    assert wire.encode_frame({**message, "missionTimeUs": "-9223372036854775808"})


def test_legacy_frame_through_real_decoder_and_pointer_encoder():
    vector = resource("legacy-vectors.json")
    frame = bytes.fromhex(vector["frameHex"])
    expected = vector["expected"]
    sample = TelemetryDecoder().feed(frame)[0]
    assert sample.latitude == pytest.approx(expected["latitude"])
    assert sample.longitude == pytest.approx(expected["longitude"])
    assert sample.gps_altitude == expected["gpsAltitude"]
    assert sample.altitude == expected["barometricAltitude"]
    assert sample.velocity == expected["velocity"]
    assert sample.rssi == expected["receiverRssi"]
    assert sample.details["receiver_height_wire"] == expected["receiverHeightWire"]
    assert sample.phase == "Unknown state 255"
    assert pointer_packet(90, 45).hex() == vector["pointerHex"]
    assert rocket_packet("advance_state", 1).hex() == vector["uplinkHex"]
    damaged = bytearray(frame)
    damaged[52] ^= 1
    assert not TelemetryDecoder().feed(damaged)


def test_inventory_and_no_qt_dependency_on_contract_import():
    root = files("rocket_gnc_monitor.mission_contracts").joinpath("data", "v1")
    inventory = resource("manifest.json")["files"]
    assert {p.name for p in root.iterdir()} == set(inventory) | {"manifest.json"}
    for name, digest in inventory.items():
        assert hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest() == digest
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import rocket_gnc_monitor.mission_contracts; "
                "assert not any(x.startswith('PySide6') for x in sys.modules)"
            ),
        ],
        check=True,
    )
