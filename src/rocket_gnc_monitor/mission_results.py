"""Stdlib-only Mission result indexing and bounded selected-stream queries.

Run only through the bundled utility launcher. Imported files are data; SQLite is
opened read-only with trusted_schema disabled and an execution budget.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import struct
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

MAX_FILES = 100_000
MAX_BYTES = 64 * 1024**3
CHUNK = 16 * 1024**2


def safe_name(name):
    if not isinstance(name, str) or len(name) > 1024 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*', name):
        raise ValueError('Unsafe Mission entry path')
    for part in name.split('/'):
        if part.endswith('.') or part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
            raise ValueError('Nonportable Mission entry path')
    return name


def read_json(path, limit=32 * 1024**2):
    if path.stat().st_size > limit:
        raise ValueError('Oversized Mission metadata')
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path, length=None):
    result = hashlib.sha256()
    with path.open('rb') as source:
        remaining = path.stat().st_size if length is None else length
        while remaining:
            chunk = source.read(min(remaining, 1024**2))
            if not chunk:
                raise ValueError('Truncated committed file')
            remaining -= len(chunk)
            result.update(chunk)
    return result.hexdigest()


def build_index(root, destination):
    checkpoint = read_json(root / 'checkpoint.json')
    files = {safe_name(f['path']): f for f in checkpoint['files']}
    if len(files) != len(checkpoint['files']) or len(files) >= MAX_FILES:
        raise ValueError('Invalid checkpoint inventory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.working.sqlite')
    if temporary.exists() or destination.exists():
        raise ValueError('Index destination already exists')
    with closing(sqlite3.connect(temporary)) as db:
        db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA trusted_schema=OFF;
            CREATE TABLE files(path TEXT PRIMARY KEY, length INTEGER, sha256 TEXT);
            CREATE TABLE records(stream TEXT, time_us INTEGER, path TEXT, offset INTEGER, length INTEGER);
            CREATE INDEX record_window ON records(stream,time_us);
            CREATE TABLE streams(stream TEXT PRIMARY KEY, first_us INTEGER, last_us INTEGER, count INTEGER);
            PRAGMA user_version=1;''')
        raw_cache = {}
        for name, entry in sorted(files.items()):
            path = root / name
            if path.is_symlink() or not path.is_file() or digest(path, entry['committedBytes']) != entry['sha256']:
                raise ValueError('Committed file hash mismatch: ' + name)
            db.execute('INSERT INTO files VALUES(?,?,?)', (name, entry['committedBytes'], entry['sha256']))
            if not re.search(r'/(?:chunk|index)-[0-9]+\.(?:jsonl|csv)$', name):
                continue
            stream = str(PurePosixPath(name).parent)
            if PurePosixPath(name).name.startswith('index-'):
                stream += '/raw-index'
            offset = 0
            with path.open('rb') as source:
                while offset < entry['committedBytes']:
                    line = source.readline(1_048_577)
                    if not line.endswith(b'\n') or len(line) > 1_048_576 or offset + len(line) > entry['committedBytes']:
                        raise ValueError('Incomplete or oversized canonical record')
                    if name.endswith('.jsonl'):
                        value = json.loads(line)
                        time_us = int(value['timeUs'])
                        if 'file' in value and 'offset' in value:
                            referenced = files.get(safe_name(value['file']))
                            if referenced is None or value['offset'] < 0 or value['length'] < 0 or value['offset'] + value['length'] > referenced['committedBytes']:
                                raise ValueError('Raw index extends beyond committed bytes')
                            if referenced['committedBytes'] > CHUNK or value['length'] > 65536:
                                raise ValueError('Raw record exceeds canonical bounds')
                            if value['file'] not in raw_cache:
                                with (root / value['file']).open('rb') as raw:
                                    raw_cache = {value['file']: raw.read(referenced['committedBytes'])}
                            payload = raw_cache[value['file']][value['offset']:value['offset'] + value['length']]
                            if hashlib.sha256(payload).hexdigest() != value['sha256']:
                                raise ValueError('Raw record hash mismatch')
                    else:
                        time_us = int(line.split(b',', 1)[0])
                    if not -(2**63) <= time_us < 2**63:
                        raise ValueError('Mission time is outside signed 64-bit range')
                    db.execute('INSERT INTO records VALUES(?,?,?,?,?)', (stream, time_us, name, offset, len(line)))
                    offset += len(line)
            db.commit()
        db.execute('INSERT INTO streams SELECT stream,MIN(time_us),MAX(time_us),COUNT(*) FROM records GROUP BY stream')
        db.commit()
        # SQLite backup, rather than copying a live main database while WAL pages exist.
        with closing(sqlite3.connect(destination)) as output:
            db.backup(output)
            output.execute('PRAGMA journal_mode=DELETE')
    temporary.unlink()
    for suffix in ('-wal', '-shm'):
        temporary.with_name(temporary.name + suffix).unlink(missing_ok=True)
    with destination.open('r+b') as handle:
        os.fsync(handle.fileno())
    return {'indexedFiles': len(files), 'index': str(destination)}


def preflight(path):
    with path.open('rb') as source:
        source.seek(0, 2)
        size = source.tell()
        source.seek(max(0, size - 65557))
        tail = source.read()
        at = tail.rfind(b'PK\x05\x06')
        if at < 0 or at + 22 > len(tail):
            raise ValueError('Missing ZIP directory')
        _, disk, start_disk, disk_count, count, length, offset, comment = struct.unpack_from('<4s4H2IH', tail, at)
        if disk or start_disk or disk_count != count or at + 22 + comment != len(tail):
            raise ValueError('Invalid ZIP directory')
        if count == 65535 or length == 0xffffffff or offset == 0xffffffff:
            source.seek(size - len(tail) + at - 20)
            locator = source.read(20)
            signature, disk, record_at, disks = struct.unpack('<4sIQI', locator)
            if signature != b'PK\x06\x07' or disk or disks != 1 or record_at > size - 56:
                raise ValueError('Invalid ZIP64 locator')
            source.seek(record_at)
            record = source.read(56)
            if record[:4] != b'PK\x06\x06':
                raise ValueError('Invalid ZIP64 directory')
            count, length, offset = struct.unpack_from('<QQQ', record, 32)
        if not 0 < count <= MAX_FILES or not 0 < length <= 128 * 1024**2 or offset + length > size:
            raise ValueError('ZIP directory exceeds bounds')


class Archive:
    def __init__(self, path):
        preflight(path)
        self.zip = zipfile.ZipFile(path)
        entries = self.zip.infolist()
        seen = set()
        for entry in entries:
            name = safe_name(entry.filename)
            if name.casefold() in seen or entry.is_dir() or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG} or entry.flag_bits & 1:
                raise ValueError('Unsafe or duplicate ZIP entry')
            seen.add(name.casefold())
        if sum(e.file_size for e in entries) > MAX_BYTES:
            raise ValueError('Result exceeds 64 GiB')
        if self.zip.getinfo('manifest.json').file_size > 32 * 1024**2:
            raise ValueError('Oversized result inventory')
        self.manifest = json.loads(self.zip.read('manifest.json'))
        if self.manifest['format'] != 'openrocket.mission-result' or self.manifest['version'] != 1:
            raise ValueError('Unsupported result format')
        self.files = {safe_name(f['path']): f for f in self.manifest['files']}
        if len(self.files) != len(self.manifest['files']) or set(self.files) | {'manifest.json'} != {e.filename for e in entries}:
            raise ValueError('Result inventory mismatch')

    def read(self, name, limit=CHUNK):
        entry = self.files[name]
        if not 0 <= entry['length'] <= limit or self.zip.getinfo(name).file_size != entry['length']:
            raise ValueError('Entry exceeds read bound')
        with self.zip.open(name) as source:
            data = source.read(limit + 1)
        if len(data) != entry['length'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Result entry hash mismatch')
        return data

    def close(self):
        self.zip.close()


def query(path, request):
    archive = Archive(path)
    try:
        with tempfile.TemporaryDirectory(prefix='mission-query-') as directory:
            index = Path(directory) / 'index.sqlite'
            index.write_bytes(archive.read('index.sqlite', 512 * 1024**2))
            with closing(sqlite3.connect(index.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                db.execute('PRAGMA trusted_schema=OFF')
                db.set_progress_handler(lambda: 1, 5_000_000)
                if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                    raise ValueError('Unsupported result index')
                prefix = request.get('member', 'member-0')
                if not re.fullmatch(r'member-[0-9]{1,5}', prefix):
                    raise ValueError('Invalid member ID')
                streams = [{'stream': row[0], 'firstUs': str(row[1]), 'lastUs': str(row[2]), 'count': row[3]}
                           for row in db.execute('SELECT stream,first_us,last_us,count FROM streams WHERE stream LIKE ? ORDER BY stream LIMIT 1000', ('runs/' + prefix + '/%',))]
                if 'stream' not in request:
                    members = [row[0] for row in db.execute("SELECT DISTINCT substr(stream,6,instr(substr(stream,6),'/')-1) FROM streams WHERE stream LIKE 'runs/%' ORDER BY 1 LIMIT 10000")]
                    return {'streams': streams, 'status': archive.manifest['status'], 'members': members,
                            'sessions': [name.removesuffix('/manifest.json') for name in archive.files
                                         if name.startswith('runs/' + prefix + '/stations/') and '/sessions/' in name and name.endswith('/manifest.json')]}
                stream = safe_name(request['stream'])
                if not stream.startswith('runs/' + prefix + '/'):
                    raise ValueError('Result channel belongs to another member')
                start, end, limit = int(request.get('startUs', '-1000000')), int(request.get('endUs', '86400000000')), int(request.get('limit', 250))
                if not -2**63 <= start <= end < 2**63 or not 1 <= limit <= 500:
                    raise ValueError('Invalid result window')
                records, cache, used = [], {}, 0
                for time_us, name, offset, length in db.execute('SELECT time_us,path,offset,length FROM records WHERE stream=? AND time_us BETWEEN ? AND ? ORDER BY time_us,path,offset LIMIT ?', (stream, start, end, limit)):
                    if not re.search(r'/(?:chunk|index)-[0-9]+\.(?:jsonl|csv)$', name):
                        raise ValueError('Index references a noncanonical record file')
                    expected_stream = str(PurePosixPath(name).parent) + ('/raw-index' if PurePosixPath(name).name.startswith('index-') else '')
                    if expected_stream != stream:
                        raise ValueError('Index channel does not match the referenced file')
                    if name not in cache:
                        cache = {name: archive.read(name)}  # At most one canonical chunk resident.
                    raw = cache[name]
                    if offset < 0 or length < 1 or offset + length > len(raw):
                        raise ValueError('Result index points outside a chunk')
                    line = raw[offset:offset + length]
                    if (offset and raw[offset - 1:offset] != b'\n') or not line.endswith(b'\n') or b'\n' in line[:-1]:
                        raise ValueError('Index does not reference one complete record')
                    used += len(line)
                    if used > 512 * 1024:
                        break
                    value = json.loads(line) if name.endswith('.jsonl') else {'timeUs': str(time_us), 'columns': line.decode().rstrip().split(',')}
                    actual_time = int(value['timeUs']) if name.endswith('.jsonl') else int(value['columns'][0])
                    if actual_time != time_us:
                        raise ValueError('Index time does not match the canonical record')
                    records.append(value)
                return {'stream': stream, 'records': records, 'count': len(records), 'limit': limit}
    finally:
        archive.close()


def export_flight(path, destination, request):
    import zlib
    from dataclasses import asdict

    from .domain import Mission

    member, station = request['member'], request['station']
    if not re.fullmatch(r'member-[0-9]{1,5}', member) or station not in {'launch', 'gs1', 'gs2', 'gs3', 'gs4'}:
        raise ValueError('Select a member and station')
    archive = Archive(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.mission-flight-', dir=destination.parent) as directory:
            root = Path(directory)
            payload = root / 'payload'
            session = payload / 'session'
            session.mkdir(parents=True)
            mission = json.loads(archive.read('mission.json'))
            prefix = f'runs/{member}/stations/{station}/'
            native = request.get('session', '')
            if native:
                safe_name(native)
                if not native.startswith(prefix + 'sessions/') or native + '/manifest.json' not in archive.files:
                    raise ValueError('Native session is outside the selected station')
                for name in archive.files:
                    if name.startswith(native + '/'):
                        target = session / name[len(native) + 1:]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        expected = archive.files[name]
                        digest_value, length = hashlib.sha256(), 0
                        with archive.zip.open(name) as source, target.open('wb') as output:
                            while chunk := source.read(1024**2):
                                output.write(chunk)
                                digest_value.update(chunk)
                                length += len(chunk)
                                if length > expected['length']:
                                    raise ValueError('Session expands beyond declared length')
                        if length != expected['length'] or digest_value.hexdigest() != expected['sha256']:
                            raise ValueError('Native session integrity failure')
                session_manifest = read_json(session / 'manifest.json')
                configuration = session_manifest['mission']
                # Preserve actual host/UTC clocks and snapshot SQLite through its backup API.
                with closing(sqlite3.connect((session / 'session.sqlite').as_uri() + '?mode=ro', uri=True)) as source:
                    source.execute('PRAGMA trusted_schema=OFF')
                    with closing(sqlite3.connect(session / 'backup.sqlite')) as output:
                        source.backup(output)
                        output.execute('PRAGMA journal_mode=DELETE')
                os.replace(session / 'backup.sqlite', session / 'session.sqlite')
                for suffix in ('-wal', '-shm'):
                    (session / ('session.sqlite' + suffix)).unlink(missing_ok=True)
            else:
                configuration = asdict(Mission(name=mission['identity']['title']))
                launch = next(s for s in mission['sites'] if s['id'] == 'launch-site')
                configuration.update(latitude=launch['latitudeDeg'], longitude=launch['longitudeDeg'],
                                     altitude=launch.get('ellipsoidalHeightM', 0),
                                     altitude_msl=launch.get('heightM', 0), site_configured=True)
                with closing(sqlite3.connect(session / 'session.sqlite')) as db, (session / 'raw.bin').open('wb') as raw:
                    db.executescript('''CREATE TABLE samples(id INTEGER PRIMARY KEY,elapsed REAL,t REAL,data TEXT);
                        CREATE INDEX sample_time ON samples(elapsed);
                        CREATE TABLE events(id INTEGER PRIMARY KEY,elapsed REAL,data TEXT);
                        CREATE INDEX event_time ON events(elapsed);
                        CREATE TABLE raw_index(id INTEGER PRIMARY KEY,elapsed REAL,role TEXT,offset INTEGER,length INTEGER);''')
                    decoded = prefix + 'receiver/decoded/'
                    for name in sorted(archive.files):
                        if name.startswith(decoded) and name.endswith('.jsonl'):
                            for line in archive.read(name).splitlines():
                                sample = json.loads(line)
                                elapsed = (int(sample.pop('timeUs')) + 1_000_000) / 1e6
                                sample['received'], sample['utc'] = elapsed, 0.0
                                sample['details']['exportClock'] = 'synthetic session elapsed = mission time + 1 second; UTC unavailable'
                                db.execute('INSERT INTO samples(elapsed,t,data) VALUES(?,?,?)', (elapsed, sample['t'], json.dumps(sample)))
                    cache = {}
                    for group, role, code in [('receiver', 'telemetry_rx', 1), ('pointer/tx', 'pointer_tx', 3), ('uplink/tx', 'telemetry_tx', 4)]:
                        for name in sorted(archive.files):
                            if name.startswith(prefix + group + '/index-') and name.endswith('.jsonl'):
                                for line in archive.read(name).splitlines():
                                    entry = json.loads(line)
                                    if entry['file'] not in cache:
                                        cache = {entry['file']: archive.read(entry['file'])}
                                    data = cache[entry['file']][entry['offset']:entry['offset'] + entry['length']]
                                    if len(data) != entry['length'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                                        raise ValueError('Raw export index mismatch')
                                    elapsed = (int(entry['timeUs']) + 1_000_000) / 1e6
                                    header = struct.pack('<4sddBI', b'RGM1', elapsed, 0, code, len(data))
                                    offset = raw.tell()
                                    raw.write(header + data + struct.pack('<I', zlib.crc32(header + data)))
                                    db.execute('INSERT INTO raw_index(elapsed,role,offset,length) VALUES(?,?,?,?)', (elapsed, role, offset, len(data)))
                    db.commit()
                    if db.execute('SELECT COUNT(*) FROM samples').fetchone()[0] == 0 and not raw.tell():
                        raise ValueError('Selected station has no recorded session data')
                session_manifest = {'schema_version': 1, 'mode': 'SIMULATION', 'complete': archive.manifest['status'] == 'complete',
                                        'mission': configuration, 'flight_zero': 1.0, 'time_aligned': True, 'raw_format': 'RGM1',
                                        'raw_packets_available': True, 'clock_basis': 'synthetic elapsed from mission clock; UTC unavailable'}
            configuration.update(model='', motor_files=[], pointer_calibrated=False)
            session_manifest['mission'] = configuration
            (payload / 'mission.json').write_text(json.dumps(dict(configuration, schema_version=1)) + '\n', encoding='utf-8')
            (session / 'manifest.json').write_text(json.dumps(session_manifest) + '\n', encoding='utf-8')
            with closing(sqlite3.connect(session / 'session.sqlite')) as db:
                count, duration = db.execute('SELECT COUNT(*),COALESCE(MAX(elapsed),0) FROM samples').fetchone()
            omissions = {'source': 'openrocket.mission-result', 'member': member, 'station': station,
                             'nativeSession': native or None, 'omitted': ['Other members and stations', 'Truth branches and physical pointer pose',
                             'FC implementation and diagnostics', 'Rocket/motor assets and reusable Mission configuration',
                             'Logical video events and static slates; native encoded session video is retained when present']}
            (payload / 'omissions.json').write_text(json.dumps(omissions, indent=2) + '\n', encoding='utf-8')
            files = {file.relative_to(payload).as_posix(): {'size': file.stat().st_size, 'sha256': digest(file)}
                     for file in payload.rglob('*') if file.is_file()}
            metadata = {'format': 'rocket-gnc-flight', 'schema_version': 1, 'app_version': 'mission-export.v1',
                            'saved_utc': 0, 'scope': 'Selected Mission station session; see omissions.json', 'source_mode': 'SIMULATION',
                            'samples': count, 'session': 'session', 'position': 0, 'flight_zero': session_manifest.get('flight_zero', 1),
                            'time_aligned': bool(session_manifest.get('time_aligned', True)), 'files': files}
            (payload / 'flight.json').write_text(json.dumps(metadata) + '\n', encoding='utf-8')
            temporary = root / 'export.tmp'
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as output:
                for file in sorted(payload.rglob('*')):
                    if file.is_file():
                        output.write(file, file.relative_to(payload).as_posix())
            with temporary.open('r+b') as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            return {'saved': destination.name, 'samples': count, 'duration': duration, 'omissions': omissions}
    finally:
        archive.close()


def main():
    request = read_json(Path(sys.argv[1]), 1_048_576)
    if request['operation'] == 'index':
        result = build_index(Path(request['root']), Path(request['destination']))
    elif request['operation'] == 'export':
        result = export_flight(Path(request['archive']), Path(request['destination']), request['selection'])
    elif request['operation'] == 'query':
        result = query(Path(request['archive']), request.get('query', {}))
    else:
        raise ValueError('Unknown result operation')
    Path(sys.argv[2]).write_text(json.dumps(result, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
