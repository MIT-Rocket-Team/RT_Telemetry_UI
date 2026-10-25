"""One-loop-owned immutable state and bounded station event history."""
import copy
import time
from collections import deque

from .protocol import dumps


class EventJournal:
    def __init__(self, max_bytes=16 * 1024 * 1024, max_age=30.0, clock=time.monotonic):
        self.max_bytes, self.max_age, self.clock = max_bytes, max_age, clock
        self.entries = deque()
        self.head = 0
        self.bytes = 0

    def prune(self):
        now = self.clock()
        while self.entries and (self.bytes > self.max_bytes or now - self.entries[0][0] > self.max_age):
            _, _, size = self.entries.popleft()
            self.bytes -= size

    def append(self, event):
        event = copy.deepcopy(event)
        event["event_seq"] = str(self.head + 1)
        size = len(dumps(event).encode())
        self.head += 1
        self.entries.append((self.clock(), event, size))
        self.bytes += size
        self.prune()
        return copy.deepcopy(event)

    @property
    def first(self):
        self.prune()
        return int(self.entries[0][1]["event_seq"]) if self.entries else self.head + 1

    def retained(self, after, through=None):
        self.prune()
        through = self.head if through is None else through
        if after < self.first - 1 or after > self.head:
            return None
        return [copy.deepcopy(event) for _, event, _ in self.entries if after < int(event["event_seq"]) <= through]
