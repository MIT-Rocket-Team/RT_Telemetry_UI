"""Real OS pseudo-serial transport, no attached hardware or motor motion."""

import os
import select
import time
import pytest
from rocket_gnc_monitor.devices import SerialWorker
from rocket_gnc_monitor.protocol import pointer_packet
from test_protocol import frame


@pytest.mark.skipif(os.name == "nt", reason="POSIX pseudo-terminal transport test")
def test_two_concurrent_serial_roles_and_exact_pointer_bytes():
    import pty

    pairs = [pty.openpty(), pty.openpty()]
    events = []
    raw = []
    workers = []
    try:
        for i, role in enumerate(("telemetry", "pointer")):
            worker = SerialWorker(
                role, os.ttyname(pairs[i][1]), i, lambda *e: events.append(e), lambda *r: raw.append(r)
            )
            workers.append(worker)
        deadline = time.monotonic() + 2
        while sum(e[2] == "connected" for e in events) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sum(e[2] == "connected" for e in events) == 2
        workers[0].set_polling(True)
        packet = frame(7)
        os.write(pairs[0][0], packet[:19])
        os.write(pairs[0][0], packet[19:])
        command = pointer_packet(90, 30)
        workers[1].send(command, "command")
        assert select.select([pairs[1][0]], [], [], 2)[0]
        assert os.read(pairs[1][0], 100) == command
        deadline = time.monotonic() + 2
        while not any(e[2] == "sample" for e in events) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert next(e[3] for e in events if e[2] == "sample").sequence == 7
        assert not select.select([pairs[0][0]], [], [], 0.1)[0]
        assert any(role == "pointer_tx" and data == command for role, data in raw)
    finally:
        for worker in workers:
            worker.stop()
        for pair in pairs:
            for fd in pair:
                os.close(fd)
