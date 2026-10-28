import base64
import struct

import pytest

from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.mission_station import MissionStation
from rocket_gnc_monitor.tracking_policy import tracking_target


def frame(sequence=0, ticks=1000, altitude=500):
    payload = bytearray(128)
    payload[31] = 3
    struct.pack_into('<ii', payload, 32, 427041678, -771902950)
    struct.pack_into('<f', payload, 40, altitude)
    struct.pack_into('<f', payload, 59, altitude)
    struct.pack_into('<IH', payload, 80, ticks, sequence)
    payload[127] = sum(payload[:127]) & 255
    return b'\xab\xab' + payload + bytes(14)


def station(**kwargs):
    return MissionStation(dict(id='gs1', origin=[42.70278, -77.17606, 250], pointerEnabled=True,
                               **kwargs))


def delivery(data=None, tx=0, identity='packet-0'):
    return {'data': base64.b64encode(frame() if data is None else data).decode(),
                'txTimeUs': str(tx), 'packetId': identity,
                'heightReference': {'convention': 'relative-msl', 'baselineMslM': 250, 'geoidSeparationM': -33}}


def test_shared_legacy_geometry_is_identical_for_live_and_mission():
    s = station()
    result = s.advance(0, [delivery()], [{'kind': 'connect'}, {'kind': 'start-tracking'}])
    assert result['tracking'] and len(result['writes']) == 1
    az, el = struct.unpack_from('<ff', base64.b64decode(result['writes'][0]['data']), 2)
    sample = Sample.from_dict(result['samples'][0])
    sample.source = 'LIVE'
    target = tracking_target(sample, now=0, freshness=2, origin=(42.70278, -77.17606, 999))
    assert (az, el) == pytest.approx(target, abs=1e-5)


def test_stale_hold_requires_restart_and_pause_does_not_age():
    s = station()
    s.advance(0, [delivery()], [{'kind': 'connect'}, {'kind': 'start-tracking'}])
    assert s.advance(0)['tracking']  # Arbitrary host-time pause has no effect.
    assert not s.advance(2_200_000)['tracking']
    result = s.advance(2_400_000, [delivery(frame(1, 3400), tx=2_400_000)])
    assert not result['tracking'] and not result['writes']
    assert s.advance(2_400_000, actions=[{'kind': 'start-tracking'}])['tracking']


def test_reordering_does_not_reboot_refresh_or_replace_target():
    s = station()
    s.advance(200_000, [delivery(frame(2, 1200), tx=200_000)])
    prior = s.policy.latest
    r = s.advance(500_000, [delivery(frame(1, 1100), tx=100_000)])
    assert r['samples'][0]['details']['outOfOrder']
    assert s.decoder.boot == 0 and s.decoder.last_tick == 1200
    assert s.policy.latest is prior and prior.received == .2
    s.advance(600_000, [delivery(frame(3, 1600), tx=600_000)])
    assert s.decoder.boot == 0 and s.policy.latest.sequence == 3


def test_fragment_corruption_and_unknown_height():
    s = station(policy='geodetic.v1')
    raw = frame()
    assert not s.advance(0, [delivery(raw[:40])])['samples']
    assert len(s.advance(0, [delivery(raw[40:])])['samples']) == 1
    bad = bytearray(raw)
    bad[50] ^= 1
    r = s.advance(100_000, [delivery(bad, tx=100_000)])
    assert r['rejected'] == 1 and not r['samples']
    d = delivery(tx=200_000)
    d['heightReference'] = {'convention': 'unknown'}
    r = s.advance(200_000, [d], [{'kind': 'connect'}, {'kind': 'start-tracking'}])
    assert r['actions'][1]['outcome'] == 'rejected'
    assert 'unknown' in r['actions'][1]['reason']


def test_zero_emits_exact_firmware_bytes_and_observer_cannot_command():
    s = station()
    r = s.advance(0, actions=[{'kind': 'connect'}, {'kind': 'zero'}])
    assert base64.b64decode(r['writes'][0]['data']) == bytes.fromhex('aa05000000000000000005')
    assert r['writes'][0]['expiresUs'] == '500000'
    observer = station(owner='observer')
    r = observer.advance(0, [delivery()], [{'kind': 'connect'}, {'kind': 'start-tracking'}, {'kind': 'zero'}])
    assert not r['writes'] and r['actions'][1]['outcome'] == 'rejected'


def test_worker_import_does_not_load_qt_or_devices():
    import subprocess
    import sys
    check = subprocess.run([sys.executable, '-c',
        ("import rocket_gnc_monitor.mission_worker, sys; "
        "assert not any(n.startswith(('PySide6', 'serial', 'numpy')) for n in sys.modules)")],
        capture_output=True, text=True, check=False)
    assert check.returncode == 0, check.stderr


def test_connection_epoch_clears_previous_vehicle_and_partial_frame():
    s = station()
    s.advance(0, [delivery()], [{'kind': 'connect'}, {'kind': 'start-tracking'}])
    s.advance(100_000, [delivery(frame()[:10], tx=100_000)])
    r = s.advance(100_000, actions=[{'kind': 'disconnect'}, {'kind': 'connect'}, {'kind': 'start-tracking'}])
    assert not s.decoder.buffer and s.policy.latest is None
    assert r['connectionEpoch'] == 1 and r['actions'][-1]['outcome'] == 'rejected'
    r = s.advance(200_000, [delivery(frame(0, 0), tx=200_000)])
    assert r['samples'][0]['details']['boot_index'] == 0
    assert not r['tracking']
