import asyncio
import uuid

import pytest
from test_network_runtime import MISSION, request, wait_for

from rocket_gnc_monitor.network.configuration import ConfigurationLedger, validate_configuration_request
from rocket_gnc_monitor.network.protocol import NetworkProtocolError

pytest_plugins = ["test_network_runtime"]


def configuration_request(source, client):
    return {"request_id": str(uuid.uuid4()), "client_node_id": client.node_id, "client_boot_id": client.boot_id,
        "expected_node_id": source.node_id, "expected_boot_id": source.boot_id, "mission_id": MISSION,
        "station": "gs2", "location": {"lat_deg": 42.703, "lon_deg": -77.194, "alt_m": 287.5,
                                      "alt_datum": "wgs84_ellipsoid", "label": "URRG GS2",
                                      "coordinate_input": {"format": "mgrs", "code": "18TUN2291333237"}}}


def connect_both(source, base):
    source.add_endpoint(base.local_endpoint)
    base.add_endpoint(source.local_endpoint)
    wait_for(lambda: source.peers_snapshot() and source.peers_snapshot()[0]["synchronized"])
    wait_for(lambda: base.peers_snapshot() and base.peers_snapshot()[0]["synchronized"])


def test_configuration_requires_known_live_base_and_stays_separate_from_commands(runtime_factory):
    source, base = runtime_factory("away1"), runtime_factory()
    body = configuration_request(source, base)
    assert asyncio.run(request(source, "configuration", "PATCH", json=body))[0] == 409
    connect_both(source, base)
    status, _, reply = asyncio.run(request(source, "configuration", "PATCH", json=body))
    assert status == 202 and reply["state"] == "queued"
    assert source.drain_configuration_requests() == [body]
    assert source.drain_configuration_requests() == []
    source.set_configuration({"station": "gs2", "location": body["location"]})
    result = source.complete_configuration_request(body["request_id"], result={"station": "gs2", "location": body["location"]})
    assert result["state"] == "applied"
    assert asyncio.run(request(source, "configuration?request_id=" + body["request_id"]))[2]["state"] == "applied"
    assert asyncio.run(request(source, "configuration"))[2]["configuration"]["station"] == "gs2"
    assert asyncio.run(request(source, "configuration", "PATCH", json=body))[0] == 200
    assert source.drain_configuration_requests() == []
    assert asyncio.run(request(source, "commands", "POST", json=body))[0] == 501


def test_configuration_wrong_boot_wrong_mission_and_nonbase_are_rejected(runtime_factory):
    source, away, base = runtime_factory("away1"), runtime_factory("away2"), runtime_factory()
    source.add_endpoint(away.local_endpoint)
    wait_for(lambda: source.peers_snapshot() and source.peers_snapshot()[0]["synchronized"])
    body = configuration_request(source, away)
    assert asyncio.run(request(source, "configuration", "PATCH", json=body))[2]["error"]["code"] == "base_peer_required"
    connect_both(source, base)
    body = configuration_request(source, base)
    body["expected_boot_id"] = str(uuid.uuid4())
    assert asyncio.run(request(source, "configuration", "PATCH", json=body))[2]["error"]["code"] == "stale_boot"
    body["expected_boot_id"] = source.boot_id
    body["mission_id"] = str(uuid.uuid4())
    assert asyncio.run(request(source, "configuration", "PATCH", json=body))[2]["error"]["code"] == "mission_mismatch"


def test_configuration_idempotency_conflict_is_not_reapplied(runtime_factory):
    source, base = runtime_factory("away1"), runtime_factory()
    connect_both(source, base)
    body = configuration_request(source, base)
    assert asyncio.run(request(source, "configuration", "PATCH", json=body))[0] == 202
    assert asyncio.run(request(source, "configuration", "PATCH", json=body))[0] == 202
    body["station"] = "gs3"
    status, _, reply = asyncio.run(request(source, "configuration", "PATCH", json=body))
    assert status == 409 and reply["error"]["code"] == "idempotency_conflict"
    assert len(source.drain_configuration_requests()) == 1


def test_configuration_result_survives_boot_and_unapplied_request_fails_explicitly(tmp_path):
    class Node:
        node_id = str(uuid.uuid4())
        boot_id = str(uuid.uuid4())
    ledger = ConfigurationLedger(tmp_path, str(uuid.uuid4()))
    one = configuration_request(Node(), Node())
    two = configuration_request(Node(), Node())
    ledger.enqueue(one)
    ledger.enqueue(two)
    ledger.complete(one["request_id"], result={"station": "gs2"})
    restarted = ConfigurationLedger(tmp_path, str(uuid.uuid4()))
    assert restarted.get(one["request_id"])["state"] == "applied"
    assert restarted.get(two["request_id"])["state"] == "failed"
    assert "restarted" in restarted.get(two["request_id"])["error"]
    assert restarted.drain() == []


def test_configuration_validates_coordinates_and_bounds_ledger(runtime_factory, tmp_path):
    source, base = runtime_factory("away1"), runtime_factory()
    body = configuration_request(source, base)
    body["location"]["lat_deg"] = 100
    with pytest.raises(NetworkProtocolError):
        validate_configuration_request(body)
    body["location"]["lat_deg"] = 42
    body["location"]["alt_datum"] = "unknown"
    with pytest.raises(NetworkProtocolError):
        validate_configuration_request(body)
    body["location"]["alt_datum"] = "wgs84_ellipsoid"
    ledger = ConfigurationLedger(tmp_path / "bounded", source.boot_id)
    ledger.MAX_ENTRIES = 1
    ledger.enqueue(body)
    body["request_id"] = str(uuid.uuid4())
    with pytest.raises(NetworkProtocolError) as error:
        ledger.enqueue(body)
    assert error.value.status == 429
