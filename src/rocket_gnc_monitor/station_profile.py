"""Validated startup choices for a station computer and its launch site."""

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from tempfile import NamedTemporaryFile


STATIONS = ("base", "away1", "away2", "away3", "away4")
ROLES = ("telemetry", "video")
VEHICLES = ("balius", "iris")
LAUNCH_SITES = ("custom", "urrg")
DISPLAY_MODES = ("standard", "launch_v2")
PROFILE_FILENAME = "station-profile.json"
STARTUP_CHOICES_FILENAME = "startup-choices.json"


@dataclass(frozen=True)
class StationProfile:
    station: str = "base"
    role: str = "telemetry"
    vehicle: str = "balius"
    launch_site: str = "custom"
    display_mode: str = "standard"

    def __post_init__(self):
        # Keep saved profiles and existing integrations compatible with the old ID.
        if self.station == "launch":
            object.__setattr__(self, "station", "base")
        for name, choices in (("station", STATIONS), ("role", ROLES), ("vehicle", VEHICLES),
                              ("launch_site", LAUNCH_SITES), ("display_mode", DISPLAY_MODES)):
            value = getattr(self, name)
            if not isinstance(value, str) or value not in choices:
                raise ValueError(f"Unknown {name}: {value!r}; choose {', '.join(choices)}")
        if self.station != "base":
            object.__setattr__(self, "display_mode", "standard")

    @property
    def layout(self):
        if self.role == "video":
            return "video"
        if self.station == "base":
            return "launch_v2" if self.display_mode == "launch_v2" else "base"
        return "away"

    @property
    def channels(self):
        return ("digital", "analog", "analog2") if self.vehicle == "iris" else ("digital", "analog")

    @property
    def local_channels(self):
        if self.station == "base":
            return ("digital",) if self.role == "video" else self.channels
        return self.away_channels[self.station]

    @property
    def away_channels(self):
        return {
            station: ("digital", "analog2" if self.vehicle == "iris" and station == "away4" else "analog")
            for station in STATIONS if station != "base"
        }

    @property
    def telemetry_targets(self):
        if self.vehicle == "balius":
            return ("Balius",)
        if self.station == "base":
            return ("Sustainer", "Booster")
        return ("Booster",) if self.station == "away4" else ("Sustainer",)

    @property
    def station_label(self):
        if self.station == "base":
            return "Base station" if self.display_mode == "launch_v2" else "Launch station"
        return f"Away station {self.station[-1]}"

    @property
    def vehicle_label(self):
        return self.vehicle.title()

    @property
    def channel_labels(self):
        if self.vehicle == "iris":
            return {"digital": "Sustainer Digital", "analog": "Sustainer Analog", "analog2": "Booster Analog"}
        return {"digital": "Digital", "analog": "Analog"}

    def to_dict(self):
        value = asdict(self)
        if self.display_mode == "standard":
            value.pop("display_mode")
        return value

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - {"station", "role", "vehicle", "launch_site", "display_mode"}:
            raise ValueError("A station profile must contain station, role, vehicle, launch-site and display choices")
        return cls(**value)

    @classmethod
    def load(cls, data_dir):
        return load_profile(data_dir)

    def save(self, data_dir):
        save_profile(self, data_dir)


def profile_for_layout(layout, profile=None):
    """Translate workspace choices without changing the vehicle or launch site."""
    profile = profile or StationProfile()
    values = {"launch": ("base", "telemetry"), "base": ("base", "telemetry"),
              "launch-v2": ("base", "telemetry"), "launch_v2": ("base", "telemetry"),
              "away": ("away1", "telemetry"), "video": ("away1", "video")}
    if layout not in values:
        raise ValueError(f"Unknown station layout: {layout!r}")
    station, role = values[layout]
    display_mode = "launch_v2" if layout in {"launch-v2", "launch_v2"} else "standard"
    return replace(profile, station=station, role=role, display_mode=display_mode)


def load_profile(data_dir):
    """Remember valid defaults; a missing or damaged preference never blocks setup."""
    directory = Path(data_dir)
    try:
        return StationProfile.from_dict(json.loads((directory / PROFILE_FILENAME).read_text(encoding="utf-8")))
    except FileNotFoundError:
        # Migrate the earlier workspace preference on the first wizard launch.
        try:
            old = json.loads((directory / "station-layout.json").read_text(encoding="utf-8"))
            return profile_for_layout(old["station"])
        except (OSError, ValueError, TypeError, KeyError):
            return StationProfile()
    except (OSError, ValueError, TypeError):
        return StationProfile()


def save_profile(profile, data_dir):
    """Legacy profile persistence; app startup uses explicit opt-in choices below."""
    _write_profile(profile, data_dir, PROFILE_FILENAME)


def load_startup_choices(data_dir):
    """Only choices explicitly remembered by the operator become startup defaults."""
    try:
        value = json.loads((Path(data_dir) / STARTUP_CHOICES_FILENAME).read_text(encoding="utf-8"))
        return StationProfile.from_dict(value), True
    except (OSError, ValueError, TypeError):
        return StationProfile(), False


def save_startup_choices(profile, data_dir, remember):
    """Save an opt-in profile, or forget it without touching legacy preference files."""
    if remember:
        _write_profile(profile, data_dir, STARTUP_CHOICES_FILENAME)
    else:
        forget_startup_choices(data_dir)


def forget_startup_choices(data_dir):
    """Forget only opt-in wizard choices; retain the current mission and app settings."""
    (Path(data_dir) / STARTUP_CHOICES_FILENAME).unlink(missing_ok=True)


def _write_profile(profile, data_dir, filename):
    if not isinstance(profile, StationProfile):
        raise TypeError("Expected a StationProfile")
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix=".station-profile-", delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps(profile.to_dict(), indent=2) + "\n")
        temporary.replace(directory / filename)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
