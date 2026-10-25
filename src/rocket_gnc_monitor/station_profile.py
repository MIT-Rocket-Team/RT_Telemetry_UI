"""Validated startup choices for a station computer and its launch site."""

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from tempfile import NamedTemporaryFile


STATIONS = ("base", "away1", "away2", "away3", "away4")
ROLES = ("telemetry", "video")
VEHICLES = ("balius", "iris")
LAUNCH_SITES = ("custom", "urrg")
PROFILE_FILENAME = "station-profile.json"
STARTUP_CHOICES_FILENAME = "startup-choices.json"


@dataclass(frozen=True)
class StationProfile:
    station: str = "base"
    role: str = "telemetry"
    vehicle: str = "balius"
    launch_site: str = "custom"

    def __post_init__(self):
        # Keep saved profiles and existing integrations compatible with the old ID.
        if self.station == "launch":
            object.__setattr__(self, "station", "base")
        for name, choices in (("station", STATIONS), ("role", ROLES), ("vehicle", VEHICLES),
                              ("launch_site", LAUNCH_SITES)):
            value = getattr(self, name)
            if not isinstance(value, str) or value not in choices:
                raise ValueError(f"Unknown {name}: {value!r}; choose {', '.join(choices)}")

    @property
    def layout(self):
        return "video" if self.role == "video" else "base" if self.station == "base" else "away"

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
        return "Launch station" if self.station == "base" else f"Away station {self.station[-1]}"

    @property
    def vehicle_label(self):
        return self.vehicle.title()

    @property
    def channel_labels(self):
        if self.vehicle == "iris":
            return {"digital": "Sustainer Digital", "analog": "Sustainer Analog", "analog2": "Booster Analog"}
        return {"digital": "Digital", "analog": "Analog"}

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - {"station", "role", "vehicle", "launch_site"}:
            raise ValueError("A station profile must contain station, role, vehicle and launch-site choices")
        return cls(**value)

    @classmethod
    def load(cls, data_dir):
        return load_profile(data_dir)

    def save(self, data_dir):
        save_profile(self, data_dir)


def profile_for_layout(layout, profile=None):
    """Translate the original three workspace flags without changing the vehicle."""
    profile = profile or StationProfile()
    values = {"launch": ("base", "telemetry"), "base": ("base", "telemetry"),
              "away": ("away1", "telemetry"), "video": ("away1", "video")}
    if layout not in values:
        raise ValueError(f"Unknown station layout: {layout!r}")
    station, role = values[layout]
    return replace(profile, station=station, role=role)


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
