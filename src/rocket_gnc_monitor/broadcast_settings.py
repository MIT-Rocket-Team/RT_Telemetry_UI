"""Portable preferences. No active mission, source identity, T0 or credentials."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .broadcast_model import EVENTS
from .broadcast_timing import Calibration, TimingConfig


@dataclass
class BroadcastSettings:
    title: str = "MIT ROCKET TEAM"
    timing: TimingConfig = field(default_factory=TimingConfig)
    events: tuple = EVENTS
    preset: str = "1080p30"

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text())
            if data.get("schema") != 1:
                return cls()
            timing = data["timing"]
            timing["paths"] = {k: Calibration(**v) for k, v in timing.get("paths", {}).items()}
            timing = TimingConfig(**timing)
            timing.validate()
            labels = tuple(data.get("events", EVENTS))
            if not 1 <= len(labels) <= 6 or any(
                not isinstance(x, str) or not x.strip() or len(x) > 12 for x in labels
            ):
                return cls()
            title = data.get("title", "MIT ROCKET TEAM")
            if not isinstance(title, str) or len(title) > 60:
                return cls()
            # Persisted measurements are a reference only. Requalify this run.
            return cls(
                title,
                TimingConfig(timing.delay, False, timing.paths, timing.budget),
                labels,
                data.get("preset") if data.get("preset") in {"1080p30", "720p30"} else "1080p30",
            )
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return cls()

    def save(self, path):
        self.timing.validate()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema": 1,
            "title": self.title[:60],
            "timing": asdict(self.timing),
            "events": list(self.events),
            "preset": self.preset,
        }
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".broadcast-")
        try:
            with os.fdopen(fd, "w") as out:
                json.dump(data, out, indent=2, allow_nan=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
