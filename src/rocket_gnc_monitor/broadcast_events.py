"""Bounded asynchronous production log; never blocks decoding or painting."""
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import threading
import time
import uuid


class ProductionLog:
    def __init__(self, directory):
        self.queue = queue.Queue(maxsize=2048)
        self.stopped = threading.Event()
        self.error = ""
        self.dropped = 0
        self.path = Path(directory) / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                       + "-" + uuid.uuid4().hex[:8] + ".jsonl")
        self.thread = threading.Thread(target=self._run, name="broadcast-log", daemon=True)
        self.thread.start()

    def write(self, action, **data):
        # Callers supply only presentation decisions, never OBS response objects.
        event = dict(action=action, monotonic=time.monotonic(),
                     utc=datetime.now(timezone.utc).isoformat(), **data)
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def _run(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as out:
                while not self.stopped.is_set() or not self.queue.empty():
                    try:
                        value = self.queue.get(timeout=.2)
                    except queue.Empty:
                        continue
                    out.write(json.dumps(value, allow_nan=False) + "\n")
                    out.flush()
        except (OSError, ValueError):
            self.error = "Production log could not be written"

    def close(self):
        self.stopped.set()
