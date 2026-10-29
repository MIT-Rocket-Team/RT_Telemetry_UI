"""Bundle OpenRocket's headless rt-gs publisher; excludes Qt and user application code."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('destination', type=Path)
parser.add_argument('--check', action='store_true')
parser.add_argument('--committed-media', action='store_true', help='Use reviewed HEAD media source when unrelated local media work is present')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
package = root / 'src/rocket_gnc_monitor'
files = ['__init__.py', 'domain.py', 'protocol.py', 'balius_states.py', 'recording.py',
         'station_profile.py', 'rocket_protocol.py', 'media.py', 'mission_network.py', 'mission_recording.py']
files += [str(p.relative_to(package)) for p in (package / 'network').glob('*.py') if p.name != 'media_receiver.py']
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for name in sorted(files):
        # An explicit qualification override keeps unrelated local media edits out of a bundle.
        data = subprocess.check_output(['git', 'show', 'HEAD:src/rocket_gnc_monitor/media.py'], cwd=root) if name == 'media.py' and args.committed_media else (package / name).read_bytes()
        info = zipfile.ZipInfo('rocket_gnc_monitor/' + name, (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, data)
data = buffer.getvalue()
manifest = dict(format='openrocket.mission-network', version=1,
                sha256=hashlib.sha256(data).hexdigest(), files=sorted(files), python='>=3.12,<3.15')
args.destination.mkdir(parents=True, exist_ok=True)
if args.check:
    assert (args.destination / 'station-network.zip').read_bytes() == data, 'Network bundle differs'
else:
    (args.destination / 'station-network.zip').write_bytes(data)
    (args.destination / 'network.json').write_text(json.dumps(manifest, indent=2) + '\n')
print('Network bundle verified' if args.check else 'Network bundle written')
