"""Base Video's single broadcast owner. Reuses its network and camera workers."""
from pathlib import Path
from datetime import datetime
import time
import uuid

from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtGui import QImage

from .broadcast_events import ProductionLog
from .broadcast_model import BroadcastFrame, BroadcastScope, BroadcastSelection, BroadcastTelemetry, CameraKey, CameraSource
from .broadcast_renderer import compose
from .broadcast_session import BroadcastSession
from .broadcast_settings import BroadcastSettings
from .broadcast_timing import next_deadline
from .display_units import DisplayUnits
from .domain import finite
from .rocket_pose import telemetry_pose


class BroadcastController(QObject):
    changed = Signal()
    program_frame = Signal(object)
    preview_frame = Signal(object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.settings_path = Path(owner.controller.data_dir) / 'broadcast.json'
        self.settings = BroadcastSettings.load(self.settings_path)
        self.session = BroadcastSession(scope=BroadcastScope(None, owner.profile.vehicle, 'balius' if owner.profile.vehicle == 'balius' else 'unknown'), timing=self.settings.timing, freshness=owner.controller.mission.freshness)
        self.session.program = self.session.preview = BroadcastSelection(title=self.settings.title)
        self.logo = QImage(str(owner.resource_root / 'resources/broadcast/mit-rocket-team.png'))
        self.log = ProductionLog(Path(owner.controller.data_dir) / 'broadcast-events')
        self.bridge = self.viewer = None
        self.local = {}
        self.active = self.preview_active = False
        self.panel = self.program_window = None
        self.last_program = self.last_preview = QImage()
        self.last_snapshot = None
        self.last_preview_time = self.last_status_time = 0
        self.last_metrics_time = 0
        self.start_time = time.monotonic()
        self.render_ms = self.rendered = self.skipped = 0
        self.error = ''
        self.datum_override, self.vertical_contract = 'unknown', False
        self.qualification_basis = ''
        self.epoch = uuid.uuid4().hex
        self.replay_last = None
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.tick)
        self.timer.start(0)

    def open_panel(self):
        from .broadcast_ui import BroadcastPanel
        if self.panel is None:
            self.panel = BroadcastPanel(self)
        self.panel.show()
        self.panel.raise_()
        self.preview_active = True

    def open_program(self):
        from .broadcast_ui import ProgramWindow
        if self.program_window is None:
            self.program_window = ProgramWindow(self)
        self.active = True
        self.program_window.show()
        self.program_window.raise_()
        self.log.write('program.open')
        self.changed.emit()

    def stop_program(self):
        self.active = False
        if self.program_window:
            self.program_window.hide()
        self.session.program = BroadcastSelection(title=self.settings.title)
        self.session.take_pending = False
        self.sync_leases()
        self.log.write('program.stop', obs_unchanged=True)
        self.changed.emit()

    def stage(self, selection):
        self.session.stage(selection)
        self.settings.title = selection.title
        self.settings.save(self.settings_path)
        self.preview_active = True
        self.sync_leases()
        self.log.write('preview.stage', scene=selection.scene, main=selection.main, inset=selection.inset,
                       title=selection.title, caption=selection.caption)

    def take(self):
        if not self.active:
            self.open_program()
        self.session.request_take(time.monotonic())

    def emergency_slate(self):
        self.session.preview = BroadcastSelection(title=self.settings.title, caption='PLEASE STAND BY')
        self.session.take_pending = True
        if not self.active:
            self.open_program()
        self.log.write('emergency.slate')

    def set_timing(self, timing):
        self.session.apply_timing(timing)
        self.settings.timing = timing
        self.settings.save(self.settings_path)
        self.log.write('timing.changed', delay=timing.delay, calibrated=timing.calibrated,
                       paths=list(timing.paths), audio_requalification_required=True)
        self.sync_leases()

    def set_qualification(self, datum, vertical, basis):
        if (datum != 'unknown' or vertical) and not basis.strip():
            raise ValueError('Document the source contract or measurement that qualifies these fields')
        self.datum_override, self.vertical_contract = datum, vertical
        self.qualification_basis = basis
        self.session.telemetry.clear()
        self.log.write('semantics.qualified', datum=datum, vertical=vertical, basis=basis)

    def clock_action(self, action, seconds=0, event=None, correction=False):
        now = time.monotonic()
        target = now - self.session.timing.delay
        clock = self.session.clock
        if action == 'countdown':
            clock.revise(target, 'countdown', anchor=now + seconds, basis='planned host countdown')
        elif action == 'hold':
            clock.hold(target)
        elif action == 'resume':
            clock.resume(target)
        else:
            stamp = now if action == 'observed_now' else target
            marked = self.session.events.mark(event, stamp, action, correction=correction)
            if event == 'LIFTOFF' and marked:
                clock.revise(stamp, 'elapsed', anchor=stamp, basis=action)
        self.log.write('clock.' + action, event=event, seconds=seconds, correction=correction)

    def schedule_utc(self, value):
        planned = datetime.fromisoformat(value)
        if planned.tzinfo is None or planned.utcoffset() is None:
            raise ValueError('Include a UTC offset, for example 2026-11-04T14:30:00-05:00')
        now = time.monotonic()
        self.session.clock.revise(now-self.session.timing.delay, 'countdown',
                                  anchor=now+planned.timestamp()-time.time(), basis=value)
        self.log.write('clock.scheduled', launch_time=value)

    def _accept(self, event):
        record = BroadcastTelemetry.from_event(event, self.session.scope, datum_override=self.datum_override,
                                                vertical_contract=self.vertical_contract)
        self.session.accept(record)

    def refresh_sources(self):
        bridge = self.owner.station_network
        if bridge is not self.bridge:
            if self.bridge:
                self.bridge.observation_received.disconnect(self._accept)
            self.bridge = bridge
            if bridge:
                bridge.observation_received.connect(self._accept)
        viewer = getattr(bridge, 'viewer', None)
        if viewer is not self.viewer:
            self.release_leases()
            self.viewer = viewer
        config = bridge.config if bridge else {}
        c = self.owner.controller
        mode = c.mode.lower() if c.mode in {'REPLAY', 'SIMULATION'} else config.get('receive_source_mode', 'live')
        # Local replay seek or replacement starts a new presentation epoch.
        if mode == 'replay':
            current = (id(c.reader), c.replay_time, time.monotonic())
            if self.replay_last:
                old_reader, old_cursor, old_now = self.replay_last
                expected = (current[2]-old_now) * getattr(c, 'replay_speed', 1)
                if old_reader != current[0] or current[1] < old_cursor or abs(current[1]-old_cursor-expected) > 1:
                    self.epoch = uuid.uuid4().hex
            self.replay_last = current
        else:
            self.replay_last = None
        scope = BroadcastScope(config.get('mission_id'), self.owner.profile.vehicle,
                               self.session.scope.vehicle, mode, self.epoch)
        if self.session.set_scope(scope):
            self.datum_override, self.vertical_contract, self.qualification_basis = 'unknown', False, ''
            self.log.write('scope.reset', mission=scope.mission, vehicle=scope.vehicle, mode=mode, epoch=self.epoch)
        self.session.peers = getattr(bridge, 'peers', [])
        paused = (mode == 'replay' and not c.replay_playing) or bool(getattr(c, 'mission_paused', False))
        applications = [(p.get('status') or {}).get('application') or {} for p in self.session.peers
                        if (p.get('info') or {}).get('mission_id') == scope.mission
                        and p.get('state') == 'ONLINE' and (p.get('info') or {}).get('source_mode') == mode]
        if mode == 'simulation' and applications:
            paused = all(a.get('state') == 'paused' for a in applications)
        if paused and not self.session.paused:
            self.session.pause_time = time.monotonic() - self.session.timing.delay
        self.session.paused = paused
        if mode == 'replay' and c.latest and scope.vehicle == 'balius':
            sample = c.latest
            record = BroadcastTelemetry(f'{self.epoch}:{sample.sequence}:{sample.t}', 'replay', self.epoch,
                self.epoch, 'replay', scope.vehicle, time.monotonic(), 0, 'recorded playback',
                sample.altitude if self.datum_override != 'unknown' else None, self.datum_override,
                sample.velocity if self.vertical_contract else None, sample.phase,
                sample.details.get('state_schema', 'provisional'), telemetry_pose(sample, fresh=True),
                sample.t-c.flight_zero if c.time_aligned and finite(sample.t) else None)
            self.session.accept(record)
        sources = {}
        if viewer and mode != 'replay':
            for (station, channel), entry in viewer.sources.items():
                if not entry:
                    continue
                peer, stream = entry
                info = peer['info']
                key = self.camera_key(info, stream)
                if key.vehicle != scope.vehicle or key.mode != scope.mode or key.mission != scope.mission:
                    continue
                logical = f'camera:{station}:{channel}'
                reason = viewer.consumer_errors.get('broadcast:' + logical, '')
                sources[logical] = CameraSource(logical, f'{station.upper()} · {channel}', key, station, channel,
                                               reason=reason)
        for channel, state in c.video_streams.items():
            worker = state.worker
            if worker and hasattr(worker, 'add_frame_listener'):
                key = CameraKey(scope.mission or 'local', scope.vehicle, mode, 'local', self.epoch, 'launch', channel,
                                str(getattr(worker, 'capture_epoch', id(worker))))
                logical = f'camera:local:{channel}'
                sources[logical] = CameraSource(logical, 'LOCAL GROUND VIEW · ' + channel.upper(), key, 'local', channel)
        self.session.sources = sources
        self.session.frames.retain([s.key for k, s in sources.items() if k in self.wanted()])
        self.sync_leases()

    @staticmethod
    def camera_key(info, stream):
        return CameraKey(info['mission_id'], stream.get('vehicle', 'unknown'), info['source_mode'],
                         info['node_id'], info['boot_id'], info['station'], stream['stream_id'], stream['media_epoch'])

    def wanted(self):
        return ((set(self.session.program.cameras) if self.active else set()) |
                (set(self.session.preview.cameras) if self.preview_active else set()))

    def sync_leases(self):
        wanted = self.wanted()
        if self.viewer:
            for consumer in list(self.viewer.consumers):
                if consumer.startswith('broadcast:') and consumer[10:] not in wanted:
                    self.viewer.unsubscribe(consumer)
            for logical in wanted:
                source = self.session.sources.get(logical)
                if source and source.station != 'local':
                    def receive(raw, stamp, info, stream, logical=logical):
                        source = self.session.sources.get(logical)
                        key = self.camera_key(info, stream)
                        if source and source.key == key:
                            self.session.frames.offer(BroadcastFrame(key, raw, stamp))
                    self.viewer.subscribe('broadcast:' + logical, (source.station, source.channel), receive)
        for logical, (worker, callback, key) in list(self.local.items()):
            source = self.session.sources.get(logical)
            if logical not in wanted or not source or key != source.key:
                worker.remove_frame_listener(callback)
                del self.local[logical]
        for logical in wanted:
            source = self.session.sources.get(logical)
            if source and source.station == 'local' and logical not in self.local:
                worker = self.owner.controller.video_streams[source.channel].worker
                def receive(raw, stamp, key=source.key):
                    self.session.frames.offer(BroadcastFrame(key, raw, stamp))
                worker.add_frame_listener(receive)
                self.local[logical] = worker, receive, source.key

    def release_leases(self):
        if self.viewer:
            for consumer in list(self.viewer.consumers):
                if consumer.startswith('broadcast:'):
                    self.viewer.unsubscribe(consumer)
        for worker, callback, _ in self.local.values():
            worker.remove_frame_listener(callback)
        self.local.clear()

    def render(self, snapshot):
        def image(frame):
            return QImage(frame.pixels, frame.width, frame.height, frame.width*3, QImage.Format.Format_RGB888) if frame else None
        return compose(image(snapshot.main), snapshot.view, DisplayUnits(self.owner.controller.settings.display_units),
                       self.logo, inset=image(snapshot.inset), pose=snapshot.pose)

    def tick(self):
        start = time.monotonic()
        if start - self.last_status_time >= .1:
            self.refresh_sources()
            old_node = self.session.node
            self.session.evaluate_receiver(start)
            if old_node != self.session.node:
                self.log.write('telemetry.selected', node=self.session.node, policy=self.session.telemetry_choice)
            self.last_status_time = start
            self.changed.emit()
        if self.active:
            if self.session.output_boundary(start):
                self.log.write('program.take', scene=self.session.program.scene, main=self.session.program.main,
                               inset=self.session.program.inset)
                self.sync_leases()
            self.last_snapshot = self.session.snapshot(self.session.program, start, self.settings.events)
            self.last_program = self.render(self.last_snapshot)
            self.program_frame.emit(self.last_program)
            self.rendered += 1
        if self.preview_active and start - self.last_preview_time >= .1:
            snapshot = self.session.snapshot(self.session.preview, start, self.settings.events)
            self.last_preview = self.render(snapshot)
            self.preview_frame.emit(self.last_preview)
            self.last_preview_time = start
        finished = time.monotonic()
        self.render_ms = (finished-start)*1000
        self.skipped += max(0, int((finished-start)*30))
        if finished-self.last_metrics_time >= 10 and (self.active or self.preview_active):
            self.log.write('metrics', rendered=self.rendered, missed_slots=self.skipped,
                           render_ms=self.render_ms, buffer_bytes=self.session.frames.bytes,
                           dropped=self.session.frames.dropped)
            self.last_metrics_time = finished
        deadline = next_deadline(self.start_time, finished)
        self.timer.start(max(1, round((deadline-finished)*1000)))

    def close(self):
        self.timer.stop()
        self.release_leases()
        self.session.frames.clear()
        self.session.previous_snapshots.clear()
        if self.program_window:
            self.program_window.hide()
        if self.panel:
            self.panel.obs.close()
            self.panel.hide()
        self.log.write('shutdown')
        self.log.close()
