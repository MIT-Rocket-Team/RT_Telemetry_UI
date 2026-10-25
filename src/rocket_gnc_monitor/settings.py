"""Machine-local preferences, separate from portable flight and mission data."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile


def runtime_root():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "vendor"
    return Path(__file__).resolve().parents[2] / "vendor"


def validate_engine_jar(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".jar":
        raise ValueError("Choose an existing OpenRocket-MIT JAR file, or use the bundled engine.")
    try:
        with zipfile.ZipFile(path) as archive:
            required = {
                "info/openrocket/core/document/OpenRocketDocument.class",
                "info/openrocket/core/startup/Application.class",
                "com/google/inject/Guice.class",
            }
            if not required.issubset(archive.namelist()):
                raise ValueError("Choose the complete OpenRocket-MIT JAR, including its dependencies.")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("The selected OpenRocket JAR cannot be read as a Java archive.") from exc
    return path


@dataclass(frozen=True)
class AppSettings:
    schema_version: int = 1
    openrocket_jar: str = ""
    sessions_directory: str = ""
    daylight: bool = False
    simulation_timeout: int = 120

    @property
    def engine_path(self):
        return Path(self.openrocket_jar) if self.openrocket_jar else runtime_root() / "openrocket.jar"

    def sessions_path(self, data_dir):
        return Path(self.sessions_directory) if self.sessions_directory else Path(data_dir) / "sessions"

    def validate(self, *, paths=False):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported settings version")
        if type(self.daylight) is not bool:
            raise ValueError("Daylight must be true or false")
        if type(self.simulation_timeout) is not int or not 10 <= self.simulation_timeout <= 1800:
            raise ValueError("Simulation time limit must be between 10 and 1800 seconds")
        for name in ("openrocket_jar", "sessions_directory"):
            value = getattr(self, name)
            if not isinstance(value, str) or "\0" in value:
                raise ValueError("Settings paths must be text")
            if value and not Path(value).is_absolute():
                raise ValueError("Settings paths must be absolute")
        if paths:
            if self.openrocket_jar:
                validate_engine_jar(self.openrocket_jar)
            if self.sessions_directory and not Path(self.sessions_directory).is_dir():
                raise ValueError("Choose an existing recording folder")
        return self

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Settings must contain a JSON object")
            return cls(**data).validate()
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"Could not load settings from {path}: {exc}") from exc

    def save(self, path):
        self.validate()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
                temporary = Path(f.name)
                json.dump(asdict(self), f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
