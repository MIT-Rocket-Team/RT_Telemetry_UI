"""Build the reviewed stdlib-only worker bundle for OpenRocket; no third-party packages."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('destination', type=Path)
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
package = root / 'src' / 'rocket_gnc_monitor'
files = ['__init__.py', 'domain.py', 'protocol.py', 'tracking_policy.py', 'mission_station.py', 'mission_worker.py']
files += [str(p.relative_to(package)) for p in (package / 'mission_contracts').rglob('*')
          if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py', '.json')]
import io

buffer = io.BytesIO()
with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
    for name in sorted(files):
        info = zipfile.ZipInfo('rocket_gnc_monitor/' + name, (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, (package / name).read_bytes())
data = buffer.getvalue()
manifest = {'format': 'openrocket.mission-worker', 'version': 1,
                'sha256': hashlib.sha256(data).hexdigest(), 'files': sorted(files), 'python': '>=3.12,<3.15'}
if args.check:
    if (args.destination / 'station-worker.zip').read_bytes() != data:
        raise SystemExit('Worker bundle differs from Telemetry UI source')
else:
    args.destination.mkdir(parents=True, exist_ok=True)
    (args.destination / 'station-worker.zip').write_bytes(data)
    (args.destination / 'worker.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
print('Mission worker bundle verified' if args.check else 'Mission worker bundle written')
