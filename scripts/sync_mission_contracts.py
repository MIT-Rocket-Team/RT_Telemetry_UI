"""Vendor the reviewed OpenRocket contract bundle; --check detects divergence without writing."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="OpenRocket core/src/main/resources/mission/v1 directory")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "src/rocket_gnc_monitor/mission_contracts/data/v1"
    manifest = json.loads((args.source / "manifest.json").read_text())
    if manifest.get("format") != "openrocket.mission-contracts" or manifest.get("version") != 1:
        raise SystemExit("Unsupported contract bundle")
    for name, digest in manifest["files"].items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.json", name) or name == "manifest.json":
            raise SystemExit("Invalid inventory filename")
        if hashlib.sha256((args.source / name).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"Source hash mismatch: {name}")
    names = set(manifest["files"]) | {"manifest.json"}
    if {p.name for p in args.source.iterdir()} != names:
        raise SystemExit("Unlisted files in source contract directory")
    if args.check:
        if not target.exists() or {p.name for p in target.iterdir()} != names:
            raise SystemExit("Vendored inventory differs")
        for name in sorted(names):
            if (target / name).read_bytes() != (args.source / name).read_bytes():
                raise SystemExit(f"Contract differs: {name}")
        print(f"Verified {len(names)} identical shared contract files")
        return
    target.mkdir(parents=True, exist_ok=True)
    extra = {p.name for p in target.iterdir()} - names
    if extra:
        raise SystemExit(f"Unexpected destination files; reconcile explicitly: {sorted(extra)}")
    for name in sorted(names):
        shutil.copyfile(args.source / name, target / name)
    print(f"Vendored {len(names)} reviewed contract files")


if __name__ == "__main__":
    main()
