"""Create portable OBS profiles/scene collection, with no credentials or device IDs.

An optional local install adds named profiles without altering existing ones.
OBS must be closed before installation. The fixture collection uses only an
explicit local test movie; it does not request screen or microphone permission.
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from rocket_gnc_monitor.obs_presets import prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/obs-setup")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    recordings = Path.home() / "Movies/Rocket Broadcast"
    prepare(args.output, ROOT / "resources/broadcast", recordings, args.fixture)
    if args.install:
        if sys.platform != "darwin":
            raise SystemExit("Automatic profile installation is macOS-only")
        obs = Path.home() / "Library/Application Support/obs-studio/basic"
        for folder in ("profiles", "scenes"):
            for entry in (args.output / folder).iterdir():
                target = obs / folder / entry.name
                if target.exists():
                    raise SystemExit(f"Refusing to overwrite existing OBS preset: {target}")
        for folder in ("profiles", "scenes"):
            for entry in (args.output / folder).iterdir():
                target = obs / folder / entry.name
                target.parent.mkdir(parents=True, exist_ok=True)
                if entry.is_dir():
                    shutil.copytree(entry, target)
                else:
                    shutil.copy2(entry, target)
    print(args.output)


if __name__ == "__main__":
    main()
