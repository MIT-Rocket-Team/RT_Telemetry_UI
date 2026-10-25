"""Loopback station protocol tests; no radio, serial, camera or external services."""
import asyncio
import base64
import copy
import hashlib
import time
import uuid

import aiohttp
import pytest

from rocket_gnc_monitor.network import NetworkProtocolError, NetworkRuntime
from rocket_gnc_monitor.network.journal import EventJournal
from rocket_gnc_monitor.network.protocol import (
    MAX_MESSAGE,
    dumps,
    endpoint,
    event_fingerprint,
    validate_info,
    validate_observation,
)
from rocket_gnc_monitor.station_profile import StationProfile

MISSION = "33333333-3333-4333-8333-333333333333"


def wait_for(predicate, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError("Timed out waiting for network condition")


@pytest.fixture
def runtime_factory(tmp_path):
    runtimes = []
    def make(station="base", mission=MISSION, **kwargs):
        service = NetworkRuntime(tmp_path / str(len(runtimes)), StationProfile(station=station), mission,
            settings={"network_port": 0, "network_bind_host": "127.0.0.1", "network_discovery": False}, **kwargs)
        runtimes.append(service)
        service.start()
        wait_for(lambda: service.state in {"LISTENING", "ERROR"})
        assert service.state == "LISTENING", service.error
        return service
    yield make
    for runtime in reversed(runtimes):
        runtime.stop()


def observation(runtime, count=1):
    raw = b"sample-wire-packet"
    return {
        "observation_id": f"{runtime.node_id}:{runtime.boot_id}:{count}",
        "receiver": {"station": runtime.station, "board_id": "downlink", "connection_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                     "rssi_dbm": -83.0, "rx_rssi_dbm": None, "decoder_gaps": "0", "rejected_packets": "0"},
        "vehicle": {"id": "balius", "basis": "operator_assignment", "target_epoch": None},
        "packet": {"protocol": "test", "protocol_version": "1", "decoder_version": "test-1", "type": "telemetry",
                   "sequence": str(count), "device_boot_id": None, "device_time_ms": None,
                   "raw_b64": base64.b64encode(raw).decode(), "raw_sha256": hashlib.sha256(raw).hexdigest()},
        "timing": {"received_mono_ms": runtime.mono_ms(), "received_time_utc": None, "clock_quality": "unknown", "clock_uncertainty_ms": None},
        "values": {"state": {"code": 1, "name": "PREFLIGHT"},
                   "position": {"lat_deg": None, "lon_deg": None, "alt_m": None, "alt_datum": "unknown", "fix": False},
                   "baro_altitude": {"value_m": 0.0, "datum": "agl_launch"}, "velocity_enu_m_s": None, "orientation": None,
                   "flight_status": {"motor_ignited": None, "motor_burning": None, "parachute_deployed": None, "evidence_source": None},
                   "native_schema": "test/1", "native": {}},
        "quality": {"validation": "accepted", "attribution_confidence": "operator_assignment", "warnings": []}}


async def request(runtime, path, method="GET", **kwargs):
    async with (aiohttp.ClientSession() as client,
                client.request(method, runtime.local_endpoint + "/api/v1/" + path, **kwargs) as response):
        return response.status, dict(response.headers), await response.json()


def test_node_identity_separate_from_wizard_choices(tmp_path):
    first = NetworkRuntime(tmp_path, StationProfile(), MISSION)
    second = NetworkRuntime(tmp_path, StationProfile(station="away3"), None)
    assert first.node_id == second.node_id
    assert first.boot_id != second.boot_id
    assert first.station == "launch" and second.station == "gs3"


def test_http_read_only_contract_and_no_commands(runtime_factory):
    service = runtime_factory("away1")
    status, headers, body = asyncio.run(request(service, "info"))
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert validate_info(body)["station"] == "gs1"
    assert body["capabilities"]["remote_commands"] is False
    assert asyncio.run(request(service, "commands", "POST", json={"command": "fire"}))[0] == 501
    assert asyncio.run(request(service, "info", headers={"Origin": "https://example.org"}))[0] == 403
    assert asyncio.run(request(service, "telemetry/latest"))[2]["observations"] == []
    assert asyncio.run(request(service, "video/sessions", "POST", json={}))[0] == 501


def test_live_two_nodes_archive_and_monotonic_age(runtime_factory):
    source, viewer = runtime_factory("away1"), runtime_factory()
    viewer.add_endpoint(source.local_endpoint)
    wait_for(lambda: any(peer["synchronized"] for peer in viewer.peers_snapshot()))
    payload = observation(source)
    assert source.publish_observation(payload)
    peers = wait_for(lambda: viewer.peers_snapshot()[0]["latest"])
    assert peers[0]["packet"]["raw_sha256"] == payload["packet"]["raw_sha256"]
    assert peers[0]["vehicle"]["id"] == "balius"
    first_age = peers[0]["sample_age_ms_lower_bound"]
    time.sleep(.05)
    assert viewer.peers_snapshot()[0]["latest"][0]["sample_age_ms_lower_bound"] > first_age
    assert viewer.peers_snapshot()[0]["rtt_metric"] == "application_round_trip"
    assert source._journal.head == 1
    assert source._archive.error == "" and viewer._archive.error == ""
    assert asyncio.run(request(source, "telemetry/latest"))[2]["observations"][0]["observation_id"] == payload["observation_id"]


def test_mission_mismatch_is_visible_without_data(runtime_factory):
    source = runtime_factory("away1")
    viewer = runtime_factory(mission=str(uuid.uuid4()))
    source.publish_observation(observation(source))
    viewer.add_endpoint(source.local_endpoint)
    peer = wait_for(lambda: next((p for p in viewer.peers_snapshot() if p["state"] == "MISMATCH"), None))
    assert peer["latest"] == [] and not peer["synchronized"]
    assert "mission" in peer["error"]


def test_no_mission_discovery_only_and_no_publication(runtime_factory):
    source = runtime_factory("away1", mission=None)
    assert not source.publish_observation(observation(source))
    assert asyncio.run(request(source, "info"))[2]["mission_id"] is None


def test_conflicting_slots_cannot_replace_each_other(runtime_factory):
    source1, source2, viewer = runtime_factory("away1"), runtime_factory("away1"), runtime_factory()
    viewer.add_endpoint(source1.local_endpoint)
    wait_for(lambda: any(p["synchronized"] for p in viewer.peers_snapshot()))
    viewer.add_endpoint(source2.local_endpoint)
    wait_for(lambda: len(viewer.peers_snapshot()) == 2 and all(p["conflict"] for p in viewer.peers_snapshot()))
    assert {p["info"]["node_id"] for p in viewer.peers_snapshot()} == {source1.node_id, source2.node_id}


def test_journal_prunes_and_rejects_expired_or_future_cursor():
    clock = [0.0]
    journal = EventJournal(max_age=30, clock=lambda: clock[0])
    for n in range(3):
        journal.append({"kind": "test", "payload": {"n": n}})
    assert journal.first == 1
    assert [e["event_seq"] for e in journal.retained(1)] == ["2", "3"]
    assert journal.retained(4) is None
    clock[0] = 31
    assert journal.first == 4 and journal.retained(0) is None
    assert journal.retained(3) == []


def test_replay_fingerprint_excludes_delivery_time():
    event = {"kind": "status.changed", "payload": {"event_created_mono_ms": "10"}, "event_seq": "1", "sent_mono_ms": "12", "sent_time_utc": None}
    replay = {**event, "sent_mono_ms": "123", "sent_time_utc": "2026-10-25T00:00:00Z"}
    assert event_fingerprint(event) == event_fingerprint(replay)
    replay["payload"] = {"event_created_mono_ms": "11"}
    assert event_fingerprint(event) != event_fingerprint(replay)


def test_raw_digest_unknown_orientation_and_limits(runtime_factory):
    payload = observation(runtime_factory("away1"))
    assert validate_observation(payload)["values"]["orientation"] is None
    payload["packet"]["raw_sha256"] = "0" * 64
    with pytest.raises(NetworkProtocolError):
        validate_observation(payload)
    with pytest.raises(NetworkProtocolError):
        dumps({"oversized": "x" * MAX_MESSAGE})
    with pytest.raises(NetworkProtocolError):
        endpoint("http://user:password@host/")
    assert endpoint("http://[::1]:8766/api/v1") == "http://[::1]:8766"


def test_ws_snapshot_barrier_then_resume_and_expired_reset(runtime_factory):
    source = runtime_factory("away2")
    source.publish_observation(observation(source))
    wait_for(lambda: source._journal.head == 1)
    client_id, client_boot = str(uuid.uuid4()), str(uuid.uuid4())
    async def exchange(resume):
        async with (aiohttp.ClientSession() as client,
                client.ws_connect(source.local_endpoint + "/api/v1/events", protocols=["rt-gs.v1"]) as ws):
            hello = await ws.receive_json()
            assert hello["kind"] == "hello"
            msg = source._envelope("subscribe", {"client_node_id": client_id, "client_boot_id": client_boot,
                "mission_id": MISSION, "accept_source_modes": ["live"], "resume": resume})
            msg.update(node_id=client_id, boot_id=client_boot)
            await ws.send_json(msg)
            result = []
            while True:
                event = await ws.receive_json(timeout=2)
                result.append(event)
                if event["kind"] == "sync.complete":
                    return result
    first = asyncio.run(exchange(None))
    assert [x["kind"] for x in first] == ["ready", "snapshot", "sync.complete"]
    assert first[1]["payload"]["through_event_seq"] == "1"
    replay = asyncio.run(exchange({"boot_id": source.boot_id, "after_event_seq": "0"}))
    assert [x["kind"] for x in replay] == ["ready", "telemetry.observation", "sync.complete"]
    source._journal.max_age = 0
    reset = asyncio.run(exchange({"boot_id": source.boot_id, "after_event_seq": "0"}))
    assert [x["kind"] for x in reset] == ["ready", "reset", "snapshot", "sync.complete"]
    assert reset[1]["payload"]["reason"] == "cursor_expired"


def test_status_snapshot_has_atomic_journal_watermark(runtime_factory):
    source = runtime_factory("away1")
    status = copy.deepcopy(source._status)
    status["application"]["state"] = "working"
    source.update_status(status)
    wait_for(lambda: source._journal.head == 1)
    result = asyncio.run(request(source, "status"))[2]
    assert result["as_of_event_seq"] == "1" and result["status"]["application"]["state"] == "working"
    next_status = copy.deepcopy(status)
    next_status["application"]["state"] = "waiting"
    source.update_status(next_status)
    early = asyncio.run(request(source, "status"))[2]
    assert (early["as_of_event_seq"], early["status"]["application"]["state"]) in {("1", "working"), ("2", "waiting")}


def test_ws_rejects_wrong_mission_and_subprotocol(runtime_factory):
    service = runtime_factory("away3")
    async def exchange():
        async with aiohttp.ClientSession() as client:
            with pytest.raises(aiohttp.WSServerHandshakeError) as err:
                await client.ws_connect(service.local_endpoint + "/api/v1/events")
            assert err.value.status == 400
            async with client.ws_connect(service.local_endpoint + "/api/v1/events", protocols=["rt-gs.v1"]) as ws:
                await ws.receive_json()
                node, boot = str(uuid.uuid4()), str(uuid.uuid4())
                value = service._envelope("subscribe", {"client_node_id": node, "client_boot_id": boot,
                    "mission_id": str(uuid.uuid4()), "accept_source_modes": ["live"], "resume": None})
                value.update(node_id=node, boot_id=boot)
                await ws.send_json(value)
                assert (await ws.receive_json())["kind"] == "error"
                assert (await ws.receive()).type == aiohttp.WSMsgType.CLOSE
                assert ws.close_code == 1008
    asyncio.run(exchange())


def test_http_request_size_and_error_bodies(runtime_factory):
    class Media:
        async def create_session(self, value, context):
            return {}
    service = runtime_factory("away1", media_session_backend=Media())
    assert asyncio.run(request(service, "video/sessions", "POST", data="x", headers={"Content-Type": "text/plain"}))[0] == 415
    assert asyncio.run(request(service, "video/sessions", "POST", data="{", headers={"Content-Type": "application/json"}))[0] == 400
    assert asyncio.run(request(service, "video/sessions", "POST", json={"body": "x" * 65536}))[0] == 413
    status, _, body = asyncio.run(request(service, "unknown"))
    assert status == 404 and body["error"]["code"] == "not_found"


def test_ui_event_drain_keeps_provenance(runtime_factory):
    source, viewer = runtime_factory("away4"), runtime_factory()
    viewer.add_endpoint(source.local_endpoint)
    wait_for(lambda: viewer.peers_snapshot() and viewer.peers_snapshot()[0]["synchronized"])
    viewer.drain_events()
    source.publish_observation(observation(source))
    wait_for(lambda: viewer.peers_snapshot()[0]["latest"])
    events = viewer.drain_events()
    received = next(event for event in events if event["kind"] == "telemetry.observation")
    assert received["node_id"] == source.node_id
    assert received["endpoint"] == source.local_endpoint
    assert received["payload"]["observation_id"].startswith(source.node_id)
    assert viewer.drain_events() == [] and viewer._ui_bytes == 0


def test_ingress_overflow_is_explicit_and_requires_fresh_boot(runtime_factory):
    service = runtime_factory("away1")
    service._pending = 8192
    assert not service.publish_observation(observation(service))
    wait_for(lambda: service._status["application"].get("network_ingress_dropped") == 1)
    assert "full" in service.error
    assert asyncio.run(request(service, "events"))[0] == 503
    assert service._archive.error == ""  # Original observation was archived before publication.


def test_slow_subscriber_queue_is_bounded():
    from rocket_gnc_monitor.network.runtime import _SendQueue
    class WS:
        closed = False
        code = None
        async def send_str(self, value):
            await asyncio.sleep(5)
        async def close(self, code, message):
            self.closed, self.code = True, code
    async def run():
        ws = WS()
        sender = _SendQueue(ws, max_bytes=100, max_age=.01)
        assert sender.offer({"first": "x" * 50})
        assert not sender.offer({"second": "x" * 50})
        await asyncio.sleep(.02)
        assert ws.closed and ws.code == 1013
        await sender.stop()
    asyncio.run(run())


def test_source_reboot_resets_resume_without_merging_history(runtime_factory):
    source, viewer = runtime_factory("away1"), runtime_factory()
    source.publish_observation(observation(source))
    viewer.add_endpoint(source.local_endpoint)
    wait_for(lambda: viewer.peers_snapshot() and viewer.peers_snapshot()[0]["latest"])
    old_boot, port, directory = source.boot_id, source.port, source.data_dir
    source.stop()
    restarted = NetworkRuntime(directory, StationProfile(station="away1"), MISSION,
        settings={"network_port": port, "network_bind_host": "127.0.0.1", "network_discovery": False})
    try:
        restarted.start()
        wait_for(lambda: restarted.state in {"LISTENING", "ERROR"})
        assert restarted.state == "LISTENING", restarted.error
        restarted.publish_observation(observation(restarted))
        peer = wait_for(lambda: next((p for p in viewer.peers_snapshot()
            if p["synchronized"] and p["info"]["boot_id"] != old_boot and p["latest"]), None), timeout=6)
        assert peer["gaps"] == 1
        assert peer["cursor"]["boot_id"] == restarted.boot_id
        assert peer["latest"][0]["observation_id"].startswith(f"{source.node_id}:{restarted.boot_id}:")
    finally:
        restarted.stop()


def test_configured_port_collision_is_visible_without_fallback(runtime_factory, tmp_path):
    source = runtime_factory("away1")
    contender = NetworkRuntime(tmp_path / "collision", StationProfile(station="away2"), MISSION,
        settings={"network_port": source.port, "network_bind_host": "127.0.0.1", "network_discovery": False})
    try:
        contender.start()
        wait_for(lambda: contender.state == "ERROR")
        assert contender.local_endpoint is None
        assert contender.port == source.port
        assert "failed" in contender.error
    finally:
        contender.stop()


def test_unknown_optional_events_preserve_cursor_and_all_receptions(runtime_factory):
    source, viewer = runtime_factory("away1"), runtime_factory()
    viewer.add_endpoint(source.local_endpoint)
    wait_for(lambda: viewer.peers_snapshot() and viewer.peers_snapshot()[0]["synchronized"])
    source._submit(source._event, "future.optional", {"extension": "ignored by this display"})
    source.publish_observation(observation(source, 1))
    source.publish_observation(observation(source, 2))
    peer = wait_for(lambda: next((p for p in viewer.peers_snapshot() if p.get("cursor", {}).get("after_event_seq") == "3"), None))
    assert peer["gaps"] == 0
    events = viewer.drain_events()
    observations = [event for event in events if event["kind"] == "telemetry.observation"]
    assert len(observations) == 2  # Identical payload bytes are still distinct reception records.
    assert observations[0]["payload"]["packet"]["raw_sha256"] == observations[1]["payload"]["packet"]["raw_sha256"]
    assert observations[0]["payload"]["observation_id"] != observations[1]["payload"]["observation_id"]


def test_oversized_journal_event_does_not_consume_a_sequence():
    journal = EventJournal()
    with pytest.raises(NetworkProtocolError):
        journal.append({"payload": {"large": "x" * MAX_MESSAGE}})
    assert journal.head == 0
    assert journal.append({"payload": {}})["event_seq"] == "1"
