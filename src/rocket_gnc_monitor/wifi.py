"""Machine-local WLAN selection and read-only operating-system Wi-Fi status.

The chosen SSID is an operator preference, not proof of a connection. Joining a
network remains in the native Wi-Fi settings so the monitor never handles Wi-Fi
passwords. No station transport or LTU link-health protocol is implied here.
"""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile


@dataclass(frozen=True)
class WifiSelection:
    ssid: str = ""
    schema_version: int = 1

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported Wi-Fi selection version")
        if not isinstance(self.ssid, str) or any(ord(char) < 32 or ord(char) == 127 for char in self.ssid):
            raise ValueError("Wi-Fi name must be text without control characters")
        if len(self.ssid.encode("utf-8")) > 32:
            raise ValueError("Wi-Fi name must be at most 32 UTF-8 bytes")
        return self

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(values, dict):
                raise ValueError("Expected a JSON object")
            return cls(**values).validate()
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"Could not load Wi-Fi selection: {exc}") from exc

    def save(self, path):
        self.validate()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(asdict(self), stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class WifiSnapshot:
    current_ssids: tuple[str, ...] = ()
    saved_ssids: tuple[str, ...] = ()
    detail: str = "Wi-Fi status has not been checked."


def _run(arguments):
    options = dict(capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=4)
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(arguments, **options)
    if result.returncode:
        raise OSError("Operating-system Wi-Fi query was unavailable")
    return result.stdout


def _mac_adapter(text):
    for block in re.split(r"\n\s*\n", text):
        if re.search(r"^Hardware Port: (?:Wi-Fi|AirPort)\s*$", block, re.MULTILINE):
            match = re.search(r"^Device: (\S+)\s*$", block, re.MULTILINE)
            if match:
                return match.group(1)
    return None


def _unique(values):
    return tuple(dict.fromkeys(value for value in values if value))


def read_wifi_status(system=None):
    """Read current SSIDs and macOS saved names, without scanning or joining.

    Newer operating systems can redact SSIDs unless permissions are granted.
    Missing or unrecognized output is therefore unknown, not disconnected.
    Windows' localized netsh output may require manual entry/native settings.
    """
    system = system or platform.system()
    try:
        if system == "Darwin":
            command = "/usr/sbin/networksetup"
            adapter = _mac_adapter(_run([command, "-listallhardwareports"]))
            if not adapter:
                return WifiSnapshot(detail="macOS did not report a Wi-Fi adapter. Enter the SSID manually.")
            current, saved, notes = (), (), []
            try:
                output = _run([command, "-getairportnetwork", adapter])
                match = re.search(r"^Current (?:Wi-Fi|AirPort) Network: (.+)$", output, re.MULTILINE)
                current = (match.group(1),) if match else ()
                if current == ("<redacted>",):
                    current = ()
                    notes.append("macOS returned a redacted Wi-Fi name. Verify the network in Wi-Fi settings.")
                elif not current:
                    notes.append("macOS did not report a connected SSID. Check Wi-Fi settings and permissions.")
            except (OSError, subprocess.SubprocessError):
                notes.append("The current Wi-Fi name is unavailable. Check Wi-Fi settings and permissions.")
            try:
                output = _run([command, "-listpreferredwirelessnetworks", adapter])
                # Only tab-indented entries under the expected heading are saved SSIDs.
                # These are remembered networks, not a scan of networks in range.
                if output.startswith("Preferred networks on "):
                    saved = _unique(line[1:] for line in output.splitlines()[1:] if line.startswith("\t"))
                else:
                    notes.append("Saved Wi-Fi names are unavailable; enter the SSID manually.")
            except (OSError, subprocess.SubprocessError):
                notes.append("Saved Wi-Fi names are unavailable; enter the SSID manually.")
            return WifiSnapshot(current, saved, " ".join(notes))
        if system == "Windows":
            output = _run(["netsh", "wlan", "show", "interfaces"])
            current = _unique(match.group(1) for match in re.finditer(
                r"^\s*SSID\s*: (.+)$", output, re.MULTILINE
            ))
            detail = "Enter the away station's Wi-Fi name or use the current network."
            if not current:
                detail = "Windows did not report a connected SSID. Check Wi-Fi settings and permissions."
            return WifiSnapshot(current, (), detail)
        return WifiSnapshot(detail="Wi-Fi discovery is unavailable on this platform. Enter the SSID manually.")
    except (OSError, subprocess.SubprocessError):
        return WifiSnapshot(detail="Wi-Fi status is unavailable. Enter the SSID or check system Wi-Fi settings.")


def wifi_settings_url(system=None):
    return {
        "Darwin": "x-apple.systempreferences:com.apple.wifi-settings-extension",
        "Windows": "ms-settings:network-wifi",
    }.get(system or platform.system(), "")
