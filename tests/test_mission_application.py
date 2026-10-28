import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
from test_mission_station import delivery

from rocket_gnc_monitor.mission_contracts.wire import encode_frame, read_frame


@pytest.mark.parametrize('attach', [False, True])
def test_real_workspace_uses_software_receiver_and_closes_native_session(tmp_path, attach):
    identity = {key: str(uuid.uuid4()) for key in (
        'sessionId', 'missionId', 'runId', 'memberId', 'participantId', 'connectionId')}
    station = {'id': 'gs1', 'origin': [42.70278, -77.17606, 250], 'pointerEnabled': True,
                   'owner': 'interactive-ui', 'policy': 'legacy-ui.v1'}
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        server.settimeout(20)
        manifest = dict(identity, protocol='rt-mission.v1', host='127.0.0.1', port=server.getsockname()[1], credential='test-secret',
                        station=station, role='telem', dataDirectory=str(tmp_path / 'data'),
                        network={'network_bind_host': '127.0.0.1', 'network_port': 0, 'network_discovery': False,
                                     'peers': [], 'mission_id': identity['missionId'], 'mission_simulation': True,
                                     'network_accept_source_modes': ['simulation']})
        path = tmp_path / 'private.json'
        path.write_text(json.dumps(manifest))
        log = (tmp_path / 'process.log').open('w+')
        ticket = tmp_path / 'attachment.json'
        process = subprocess.Popen([sys.executable, '-m', 'rocket_gnc_monitor',
                                    '--mission-listen' if attach else '--mission-manifest', str(ticket if attach else path)],
                                   env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, stdout=log, stderr=log)
        try:
            if attach:
                deadline = time.monotonic() + 15
                while not ticket.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                config = json.loads(ticket.read_text())
                headers = {'Authorization': 'Bearer ' + config['credential']}
                with urllib.request.urlopen(urllib.request.Request(config['endpoint'] + '/api/v1/info', headers=headers)) as response:
                    info = json.load(response)
                    assert info['ready'] and info['source_mode'] == 'simulation'
                request_attach = urllib.request.Request(config['endpoint'] + '/mission-api/v1/attach',
                    data=json.dumps(manifest).encode(), headers={**headers, 'Content-Type': 'application/json'})
                with urllib.request.urlopen(request_attach) as response:
                    assert response.status == 202
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
                result = request('advance', {'stations': [{'id': 'gs1', 'deliveries': [delivery()],
                    'actions': [{'kind': 'connect'}, {'kind': 'start-tracking'}]}]})
                assert result['stations'][0]['tracking']
                assert len(base64.b64decode(result['stations'][0]['writes'][0]['data'])) == 11
                request('advance', {'stations': [{'id': 'gs1', 'deliveries': [], 'actions': []}]})
                request('pause', {})
                request('action', {'kind': 'ownership', 'siteId': 'gs1', 'owner': 'observer', 'epoch': '1'})
                request('resume', {})
                result = request('advance', {'stations': [{'id': 'gs1', 'deliveries': [], 'actions': [{'kind': 'zero'}]}]})
                assert not result['stations'][0]['writes'] and not result['stations'][0]['tracking']
                request('close', {})
            if attach:
                assert process.poll() is None  # Closing a participant does not kill the existing application.
            else:
                assert process.wait(timeout=15) == 0
            sessions = list((tmp_path / 'data' / 'sessions').glob('*/manifest.json'))
            assert len(sessions) == 1
            session = json.loads(sessions[0].read_text())
            assert session['mode'] == 'SIMULATION' and session['complete']
            mappings = (tmp_path / 'data' / 'mission-clock.jsonl').read_text().splitlines()
            assert [json.loads(line)['missionTimeUs'] for line in mappings] == ['0', '0', '0']
        except Exception as exc:  # noqa: BLE001 -- include child diagnostics on any failed assertion
            log.flush()
            if attach and ticket.exists():
                config = json.loads(ticket.read_text())
                with urllib.request.urlopen(urllib.request.Request(config['endpoint'] + '/api/v1/info', headers={'Authorization': 'Bearer ' + config['credential']})) as response:
                    print(json.load(response))
            pytest.fail(str(exc) + "\n" + Path(log.name).read_text())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            log.close()
