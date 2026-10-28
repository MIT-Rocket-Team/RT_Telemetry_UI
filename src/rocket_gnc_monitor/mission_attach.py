"""Explicit local rendezvous for an already-open application; never commandeers LIVE."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import TCPServer

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPlainTextEdit, QVBoxLayout

from .mission_runtime import capabilities, require_supported_qt


class _LoopbackServer(HTTPServer):
    def server_bind(self):
        # HTTPServer performs a reverse hostname lookup even for 127.0.0.1.
        # This endpoint has a fixed numeric address and must also start offline.
        TCPServer.server_bind(self)
        self.server_name = 'localhost'
        self.server_port = self.server_address[1]


class MissionLobby(QDialog):
    requested = Signal(object)

    def __init__(self, parent=None, ticket=None):
        require_supported_qt()
        super().__init__(parent)
        self.setWindowTitle('OpenRocket Mission connection')
        self.setMinimumWidth(560)
        self.active = None
        self.pending = False
        self.failure = ""
        self.boot_id = str(uuid.uuid4())
        self.credential = secrets.token_hex(32)
        self.guard = threading.Lock()
        layout = QVBoxLayout(self)
        self.status = QLabel('Ready for a Mission Simulation connection.\nSelect this connection file for one station in OpenRocket.')
        layout.addWidget(self.status)
        self.path_display = QPlainTextEdit()
        self.path_display.setReadOnly(True)
        self.path_display.setMaximumHeight(80)
        layout.addWidget(self.path_display)
        lobby = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, status, data):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def authorized(self):
                return (self.headers.get('Host') == f'127.0.0.1:{lobby.server.server_port}'
                        and not self.headers.get('Origin')
                        and hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + lobby.credential))

            def do_GET(self):
                if not self.authorized() or self.path != '/api/v1/info':
                    return self.respond(403, {'error': 'Private Mission connection required'})
                self.respond(200, {'protocol': 'rt-mission-attach.v1', 'bootId': lobby.boot_id,
                                  'source_mode': 'simulation', 'connectionError': lobby.failure, 'ready': not lobby.pending and lobby.active is None,
                                  **capabilities()})

            def do_POST(self):
                if not self.authorized() or self.path != '/mission-api/v1/attach':
                    return self.respond(403, {'error': 'Private Mission connection required'})
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 1 <= size <= 1_048_576:
                        raise ValueError('Invalid manifest size')
                    manifest = json.loads(self.rfile.read(size))
                    if manifest['host'] != '127.0.0.1' or manifest['protocol'] != 'rt-mission.v1':
                        raise ValueError('Unsupported Mission connection')
                    with lobby.guard:
                        if lobby.pending or lobby.active is not None:
                            return self.respond(409, {'error': 'This connection is already assigned'})
                        lobby.pending = True
                        lobby.failure = ""
                    lobby.requested.emit(manifest)
                    self.respond(202, {'accepted': True, 'bootId': lobby.boot_id})
                except (ValueError, KeyError, TypeError):
                    self.respond(400, {'error': 'Invalid Mission manifest'})

        self.server = _LoopbackServer(('127.0.0.1', 0), Handler)
        self.server.timeout = 1
        self.thread = threading.Thread(target=self.server.serve_forever, name='mission-attachment', daemon=True)
        self.thread.start()
        if ticket is None:
            descriptor, filename = tempfile.mkstemp(prefix='openrocket-', suffix='.mission-connection.json')
            os.close(descriptor)
            ticket = filename
        self.ticket = Path(ticket)
        self.ticket.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.ticket, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump({'protocol': 'rt-mission-attach.v1', 'bootId': self.boot_id, 'credential': self.credential,
                       'endpoint': f'http://127.0.0.1:{self.server.server_port}'}, output)
        self.path_display.setPlainText(str(self.ticket))
        self.requested.connect(self.attach)

    @Slot(object)
    def attach(self, manifest):
        from .mission_application import launch
        try:
            self.active = launch(manifest, on_finished=self.finished_session)
            self.status.setText('Mission Simulation connected. This application owns a separate simulation workspace.')
        except Exception as exc:  # noqa: BLE001 -- report startup errors in the waiting application
            self.failure = str(exc)
            self.status.setText('Connection failed: ' + self.failure)
        finally:
            self.pending = False

    def finished_session(self, failure):
        self.active = None
        self.status.setText(('Previous mission failed: ' + failure + '\n' if failure else '') + 'Ready for another Mission Simulation connection.')

    def closeEvent(self, event):
        if self.active or self.pending:
            self.status.setText('Finish or cancel the connected mission before closing this connection.')
            event.ignore()
            return
        self.server.shutdown()
        self.server.server_close()
        self.ticket.unlink(missing_ok=True)
        super().closeEvent(event)


def main(ticket):
    app = QApplication.instance() or QApplication([])
    lobby = MissionLobby(ticket=ticket)
    lobby.show()
    return app.exec()
