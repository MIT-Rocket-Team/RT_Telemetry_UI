import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid

import pytest

from rocket_gnc_monitor.mission_contracts.wire import encode_frame, read_frame
from test_mission_station import delivery


def test_real_workspace_uses_software_receiver_and_closes_native_session(tmp_path):
    identity = {key: str(uuid.uuid4()) for key in (
        'sessionId', 'missionId', 'runId', 'memberId', 'participantId', 'connectionId')}
    station = dict(id='gs1', origin=[42.70278, -77.17606, 250], pointerEnabled=True,
                   owner='interactive-ui', policy='legacy-ui.v1')
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        server.settimeout(20)
        manifest = dict(identity, host='127.0.0.1', port=server.getsockname()[1], credential='test-secret',
                        station=station, role='telem', dataDirectory=str(tmp_path / 'data'),
                        network=dict(network_bind_host='127.0.0.1', network_port=0, network_discovery=False,
                                     peers=[], mission_id=identity['missionId'], mission_simulation=True,
                                     network_accept_source_modes=['simulation']))
        path = tmp_path / 'private.json'
        path.write_text(json.dumps(manifest))
        log = (tmp_path / 'process.log').open('w+')
        process = subprocess.Popen([sys.executable, '-m', 'rocket_gnc_monitor', '--mission-manifest', str(path)],
                                   env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, stdout=log, stderr=log)
        try:
            connection, _ = server.accept()
            connection.settimeout(15)
            with connection, connection.makefile('rwb', buffering=0) as channel:
                sequence = 0

                def request(kind, body, time_us=0):
                    nonlocal sequence
                    message = dict(identity, protocol='rt-mission.v1', type=kind, body=body,
                                   sequence=str(sequence), missionTimeUs=str(time_us), messageId=str(sequence))
                    sequence += 1
                    connection.sendall(encode_frame(message))
                    reply = read_frame(channel)
                    assert reply['type'] != 'error', reply
                    return reply['body']

                request('hello', {'credential': 'test-secret'})
                ready = request('configure', {'stations': [station]})
                assert ready['identity']['source_mode'] == 'simulation'
                assert ready['endpoint'].startswith('http://127.0.0.1:')
                request('action', {'kind': 'network-peers', 'endpoints': []})
                result = request('advance', {'stations': [dict(id='gs1', deliveries=[delivery()],
                    actions=[{'kind': 'connect'}, {'kind': 'start-tracking'}])]})
                assert result['stations'][0]['tracking']
                assert len(base64.b64decode(result['stations'][0]['writes'][0]['data'])) == 11
                request('advance', {'stations': [dict(id='gs1', deliveries=[], actions=[])]})
                request('close', {})
            assert process.wait(timeout=15) == 0
            sessions = list((tmp_path / 'data' / 'sessions').glob('*/manifest.json'))
            assert len(sessions) == 1
            session = json.loads(sessions[0].read_text())
            assert session['mode'] == 'SIMULATION' and session['complete']
            mappings = (tmp_path / 'data' / 'mission-clock.jsonl').read_text().splitlines()
            assert [json.loads(line)['missionTimeUs'] for line in mappings] == ['0', '0']
        except Exception as exc:
            log.flush()
            pytest.fail(str(exc) + "\n" + Path(log.name).read_text())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            log.close()
