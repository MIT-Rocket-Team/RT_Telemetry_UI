import hashlib
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest

from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.flight import load_flight
from rocket_gnc_monitor.mission_results import build_index, export_flight, query


def bundle(root, path):
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    manifest = {'format': 'openrocket.mission-result', 'version': 1, 'status': 'complete',
                    'files': [{'path': k, 'length': len(v), 'sha256': hashlib.sha256(v).hexdigest()} for k, v in files.items()]}
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr('manifest.json', json.dumps(manifest))


def test_selected_station_export_opens_in_existing_flight_reader(tmp_path):
    root = tmp_path / 'stage'
    root.mkdir()
    (root / 'mission.json').write_text(json.dumps({'identity': {'title': 'Export test'}, 'sites': [{
        'id': 'launch-site', 'latitudeDeg': 42.7, 'longitudeDeg': -77.2, 'heightM': 250, 'ellipsoidalHeightM': 217}]}))
    folder = root / 'runs/member-0/stations/gs1/receiver/decoded'
    folder.mkdir(parents=True)
    sample = Sample(t=1, received=0, utc=0, sequence=1, source='SIMULATION', altitude=100).to_dict()
    sample['timeUs'] = '0'
    (folder / 'chunk-00000.jsonl').write_text(json.dumps(sample) + '\n')
    files = [{'path': p.relative_to(root).as_posix(), 'committedBytes': p.stat().st_size,
                  'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in root.rglob('*') if p.is_file()]
    (root / 'checkpoint.json').write_text(json.dumps({'files': files}))
    build_index(root, root / 'index.sqlite')
    archive = tmp_path / 'source.rktmission'
    bundle(root, archive)
    assert query(archive, {})['members'] == ['member-0']
    destination = tmp_path / 'station.rktflight'
    result = export_flight(archive, destination, {'member': 'member-0', 'station': 'gs1'})
    assert result['samples'] == 1
    loaded = load_flight(destination, tmp_path / 'cache')
    assert loaded.mission.name == 'Export test'
    assert loaded.session is not None
    with zipfile.ZipFile(destination) as saved:
        assert 'omissions.json' in saved.namelist()
        manifest = json.loads(saved.read('session/manifest.json'))
        assert manifest['mode'] == 'SIMULATION'


@pytest.mark.parametrize('mutation', ["time_us=50", "offset=1", "stream='runs/member-0/other'"])
def test_hash_valid_but_inconsistent_index_is_rejected(tmp_path, mutation):
    root = tmp_path / 'stage'
    folder = root / 'runs/member-0/events'
    folder.mkdir(parents=True)
    (root / 'mission.json').write_text('{}')
    (folder / 'chunk-00000.jsonl').write_text('{"timeUs":"0","kind":"tick"}\n')
    files = [{'path': p.relative_to(root).as_posix(), 'committedBytes': p.stat().st_size,
              'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in root.rglob('*') if p.is_file()]
    (root / 'checkpoint.json').write_text(json.dumps({'files': files}))
    build_index(root, root / 'index.sqlite')
    with closing(sqlite3.connect(root / 'index.sqlite')) as db:
        db.execute('UPDATE records SET ' + mutation)
        db.commit()
    archive = tmp_path / 'tampered.rktmission'
    bundle(root, archive)  # Recompute ZIP inventory: semantic verification must still reject it.
    stream = 'runs/member-0/other' if mutation.startswith('stream=') else 'runs/member-0/events'
    with pytest.raises(ValueError, match='Index|index'):
        query(archive, {'stream': stream})
