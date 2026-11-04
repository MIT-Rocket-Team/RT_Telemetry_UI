import asyncio
import base64
import hashlib
import threading
import time

from aiohttp import web
import pytest

from rocket_gnc_monitor.obs_bridge import OBSBridge, authentication


class FakeOBS:
    def __init__(self):
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.calls = []
        self.recording = self.streaming = False
        self.thread = threading.Thread(target=lambda: asyncio.run(self.run()), daemon=True)
        self.thread.start()
        assert self.ready.wait(3)

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json(
            {"op": 0, "d": {"rpcVersion": 1, "authentication": {"salt": "salt", "challenge": "challenge"}}}
        )
        reply = await ws.receive_json()
        if reply["d"]["authentication"] != authentication("fixture", "salt", "challenge"):
            await ws.close(code=4009)
            return ws
        await ws.send_json({"op": 2, "d": {"negotiatedRpcVersion": 1}})
        async for msg in ws:
            data = msg.json()["d"]
            kind = data["requestType"]
            self.calls.append(kind)
            if kind == "StartRecord":
                self.recording = True
            if kind == "StopRecord":
                self.recording = False
            if kind == "StartStream":
                self.streaming = True
            if kind == "StopStream":
                self.streaming = False
            body = {
                "outputActive": self.recording if "Record" in kind else self.streaming,
                "outputBytes": 1000,
                "currentProgramSceneName": "Rocket Program",
                "activeFps": 30,
            }
            await ws.send_json(
                {
                    "op": 7,
                    "d": {
                        "requestId": data["requestId"],
                        "requestStatus": {"result": True, "code": 100},
                        "responseData": body,
                    },
                }
            )
        return ws

    async def run(self):
        app = web.Application()
        app.router.add_get("/", self.handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.ready.set()
        while not self.stopped.is_set():
            await asyncio.sleep(0.05)
        await runner.cleanup()


@pytest.fixture
def obs():
    value = FakeOBS()
    yield value
    value.stopped.set()


def test_authentication_matches_independent_digest():
    secret = base64.b64encode(hashlib.sha256(b"fixturesalt").digest())
    expected = base64.b64encode(hashlib.sha256(secret + b"challenge").digest()).decode()
    assert authentication("fixture", "salt", "challenge") == expected


def test_local_obs_control_status_and_no_automatic_start(qtbot, obs):
    bridge = OBSBridge()
    try:
        bridge.connect_local(obs.port, "fixture")
        qtbot.waitUntil(bridge.fresh, timeout=5000)
        assert not any(x.startswith("Start") for x in obs.calls)
        bridge.command("StartRecord")
        qtbot.waitUntil(lambda: bridge.state.get("recording") is True, timeout=5000)
        assert not bridge.state["streaming"]
        bridge.command("StopRecord")
        qtbot.waitUntil(lambda: bridge.state.get("recording") is False, timeout=5000)
        bridge.close()
        assert not bridge.fresh()
        with pytest.raises(ValueError, match="Connect"):
            bridge.command("StartStream")
        bridge.connect_local(obs.port, "fixture")
        qtbot.waitUntil(bridge.fresh, timeout=5000)
        assert obs.calls.count("StartRecord") == 1 and "StartStream" not in obs.calls
    finally:
        bridge.close()


def test_failed_auth_cannot_control_or_claim_stopped(qtbot, obs):
    bridge = OBSBridge()
    try:
        bridge.connect_local(obs.port, "wrong")
        qtbot.waitUntil(lambda: "failed" in bridge.state.get("message", ""), timeout=5000)
        assert not bridge.fresh() and "streaming" not in bridge.state
    finally:
        bridge.close()
