"""Local OBS WebSocket 5 control. Credentials live only in the connection worker.

No stream key is requested, read or logged. Commands are never replayed after a
connection failure. A disconnected UI cannot claim that OBS stopped sending.
"""
import asyncio
import base64
import hashlib
import queue
import threading
import time
import uuid

import aiohttp
from PySide6.QtCore import QObject, Signal

COMMANDS = {'StartRecord', 'StopRecord', 'StartStream', 'StopStream'}


def authentication(password, salt, challenge):
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


async def identify(ws, password):
    hello = await ws.receive_json(timeout=3)
    if hello.get('op') != 0:
        raise ValueError('OBS handshake missing')
    auth = hello['d'].get('authentication')
    if not auth:
        raise ValueError('Enable authentication in OBS WebSocket Server Settings')
    data = dict(rpcVersion=1, eventSubscriptions=0,
                authentication=authentication(password, auth['salt'], auth['challenge']))
    await ws.send_json(dict(op=1, d=data))
    if (await ws.receive_json(timeout=3)).get('op') != 2:
        raise ValueError('OBS authentication failed')


async def request(ws, kind):
    identity = uuid.uuid4().hex
    await ws.send_json(dict(op=6, d=dict(requestType=kind, requestId=identity)))
    while True:
        reply = await ws.receive_json(timeout=3)
        if reply.get('op') != 7 or reply.get('d', {}).get('requestId') != identity:
            continue
        data = reply['d']
        if not data['requestStatus']['result']:
            # Server comments/URLs can contain private data; keep errors structural.
            raise ValueError(f"{kind} rejected by OBS (code {data['requestStatus']['code']})")
        return data.get('responseData', {})


class OBSBridge(QObject):
    changed = Signal()
    _update = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = dict(connected=False, message='OBS not connected — output state unknown')
        self.commands = queue.Queue(maxsize=8)
        self.stopped = threading.Event()
        self.thread = None
        self.generation = 0
        self._update.connect(self._accept)

    def _accept(self, generation, state):
        if generation == self.generation:
            self.state = state
            self.changed.emit()

    def fresh(self):
        return bool(self.state.get('connected') and time.monotonic() - self.state.get('updated', 0) < 4)

    def connect_local(self, port, password):
        if not 1024 <= port <= 65535:
            raise ValueError('Choose a local OBS port from 1024 to 65535')
        if not password:
            raise ValueError('Enter the OBS WebSocket password (not the YouTube stream key)')
        self.close()
        self.generation += 1
        self.stopped = stopped = threading.Event()
        self.commands = commands = queue.Queue(maxsize=8)
        self.state = dict(connected=False, message='Connecting to local OBS…')
        self.changed.emit()
        self.thread = threading.Thread(target=self._run, args=(self.generation, stopped, commands, port, password),
                                       name='broadcast-obs', daemon=True)
        self.thread.start()

    def command(self, kind):
        if kind not in COMMANDS:
            raise ValueError('Unsupported OBS operation')
        if not self.fresh():
            raise ValueError('Connect to OBS and wait for current status first')
        try:
            self.commands.put_nowait(kind)
        except queue.Full:
            raise ValueError('OBS is busy; wait for its status update') from None

    def _run(self, generation, stopped, commands, port, password):
        try:
            asyncio.run(self._connection(generation, stopped, commands, port, password))
        except (OSError, ValueError, KeyError, TypeError, asyncio.TimeoutError, aiohttp.ClientError):
            self._update.emit(generation, dict(connected=False,
                message='OBS disconnected or authentication failed — output state unknown. Check OBS and reconnect.'))
        finally:
            while not commands.empty():
                try:
                    commands.get_nowait()
                except queue.Empty:
                    break

    async def _connection(self, generation, stopped, commands, port, password):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
            async with session.ws_connect(f'http://127.0.0.1:{port}', max_msg_size=256*1024) as ws:
                await identify(ws, password)
                password = None
                previous_bytes, previous_time = None, None
                while not stopped.is_set():
                    message = 'Connected to OBS'
                    try:
                        kind = commands.get_nowait()
                    except queue.Empty:
                        kind = None
                    if kind:
                        try:
                            await request(ws, kind)
                            message = kind + ' acknowledged'
                        except ValueError as exc:
                            message = str(exc)
                    stream = await request(ws, 'GetStreamStatus')
                    recording = await request(ws, 'GetRecordStatus')
                    stats = await request(ws, 'GetStats')
                    scene = await request(ws, 'GetCurrentProgramScene')
                    now, size = time.monotonic(), stream.get('outputBytes', 0)
                    bitrate = max(0, (size-previous_bytes)*8/(now-previous_time)/1000) if previous_time else 0
                    previous_bytes, previous_time = size, now
                    self._update.emit(generation, dict(connected=True, updated=now, message=message,
                        streaming=bool(stream.get('outputActive')), reconnecting=bool(stream.get('outputReconnecting')),
                        recording=bool(recording.get('outputActive')), recording_paused=bool(recording.get('outputPaused')),
                        duration_ms=stream.get('outputDuration', 0), bitrate_kbps=bitrate,
                        dropped_frames=stream.get('outputSkippedFrames', 0), total_frames=stream.get('outputTotalFrames', 0),
                        congestion=stream.get('outputCongestion', 0), fps=stats.get('activeFps', 0),
                        render_lag=stats.get('renderSkippedFrames', 0), encode_lag=stats.get('outputSkippedFrames', 0),
                        scene=scene.get('currentProgramSceneName', ''), recording_path=recording.get('outputPath', '')))
                    for _ in range(10):
                        if stopped.is_set() or not commands.empty():
                            break
                        await asyncio.sleep(.1)

    def close(self):
        self.stopped.set()
        self.generation += 1
        self.state = dict(connected=False, message='OBS disconnected — output state unknown')
        self.changed.emit()
