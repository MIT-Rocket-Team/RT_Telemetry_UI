import struct
import pytest
from rocket_gnc_monitor.protocol import ZephyrusDecoder, pointer_packet, validate_route
from rocket_gnc_monitor.domain import Mission


def frame(sequence=1, tick=1000, latitude=42.0, altitude=123.25):
    payload = bytearray(128)
    payload[31] = 3
    payload[63] = 2
    struct.pack_into("<ii", payload, 32, int(latitude * 1e7), int(-71 * 1e7))
    struct.pack_into("<f", payload, 40, altitude)
    struct.pack_into("<f", payload, 59, altitude)
    struct.pack_into("<fff", payload, 64, 1, 2, 3)
    struct.pack_into("<IH", payload, 80, tick, sequence)
    struct.pack_into("<hhh", payload, 87, 4000, 4010, 4020)
    payload[127] = sum(payload[:127]) % 256
    trailer = struct.pack("<bBiiI", -20, 3, int(42e7), int(-71e7), 200)
    return b"\xab\xab" + payload + trailer


def test_stream_splits_noise_corruption_and_recovery():
    decoder = ZephyrusDecoder()
    corrupted = bytearray(frame(2))
    corrupted[12] ^= 1
    stream = b"garbage\xab" + frame() + corrupted + frame(3, 2000)
    results = []
    for byte in stream:
        results.extend(decoder.feed(bytes([byte])))
    assert [s.sequence for s in results] == [1, 3]
    assert results[0].altitude == 123.25
    assert results[0].battery == pytest.approx(12.03)
    assert results[0].rssi == -119
    assert results[0].phase == "Flight"
    assert results[0].details["receiver_height_wire"] == 200
    assert decoder.rejected >= 1 and decoder.gaps == 1
    assert len(decoder.buffer) < 144


def test_sync_inside_valid_payload_not_a_frame_boundary():
    data = bytearray(frame())
    data[18:20] = b"\xab\xab"
    data[129] = sum(data[2:129]) % 256
    decoder = ZephyrusDecoder()
    assert len(decoder.feed(data + frame(2))) == 2


def test_rollover_then_reboot_starts_new_time_epoch():
    decoder = ZephyrusDecoder()
    values = decoder.feed(frame(65535, 2**32 - 1) + frame(0, 25) + frame(1, 10))
    assert values[1].t > values[0].t
    assert values[2].t == 0.010
    assert values[2].details["boot_index"] == 1
    assert decoder.gaps == 0


def test_invalid_coordinates_and_nan_do_not_look_valid():
    value = ZephyrusDecoder().feed(frame(latitude=100, altitude=float("nan")))[0]
    assert value.gps_fix == 0 and value.latitude is None
    assert value.altitude is None


def test_pointer_wire_compatibility():
    assert pointer_packet(90, 45) == bytes.fromhex("aa000000b442000034426c")
    for opcode in range(1, 6):
        assert pointer_packet(opcode=opcode) == bytes([0xAA, opcode]) + bytes(8) + bytes([opcode])
    for az, el in [(float("nan"), 0), (float("inf"), 0), (0, float("inf"))]:
        with pytest.raises(ValueError):
            pointer_packet(az, el)


def test_shortest_path_cannot_bypass_cable_envelope():
    mission = Mission(pointer_az_min=10, pointer_az_max=350)
    with pytest.raises(ValueError, match="cable envelope"):
        validate_route((15, 0), (345, 30), mission)
    with pytest.raises(ValueError, match="reference"):
        validate_route(None, (30, 30), mission)
    validate_route((100, 0), (200, 30), mission)
    mission.pointer_full_rotation = True
    validate_route(None, (345, 30), mission)
