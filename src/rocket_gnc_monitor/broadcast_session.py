"""Presentation-only production state, independent of Qt, OBS and hardware commands."""
from dataclasses import replace

from .broadcast_model import BroadcastScope, BroadcastSelection, BroadcastSnapshot, FrameView, SCENES
from .broadcast_timing import BroadcastClock, EventLedger, FrameHistory, TelemetryHistory, TimingConfig
from .network_bridge import best_telemetry_node
from .domain import finite


class BroadcastSession:
    def __init__(self, scope=None, timing=None, freshness=1.0):
        self.scope = scope or BroadcastScope(None)
        self.timing = timing or TimingConfig()
        self.freshness = freshness
        self.sources, self.peers = {}, []
        self.program = self.preview = BroadcastSelection()
        self.take_pending = False
        self.previous_snapshots = {}
        self.telemetry_choice = 'best'
        self.node = self.last_node = None
        self.last_evaluation = float('-inf')
        self.paused = False
        self.pause_time = None
        self.reset_histories()

    def reset_histories(self):
        self.previous_snapshots = {}
        self.frames = FrameHistory(self.timing.budget, max(1.15, self.timing.delay + .2))
        self.telemetry = TelemetryHistory()
        self.clock, self.events = BroadcastClock(), EventLedger()
        self.lost_cameras = set()
        self.node = self.last_node = None
        self.last_evaluation = float('-inf')
        self.take_pending = False

    def set_scope(self, scope):
        if scope == self.scope:
            return False
        self.scope = scope
        self.reset_histories()
        self.program = self.preview = BroadcastSelection(title=self.program.title)
        self.sources = {}
        self.paused, self.pause_time = False, None
        return True

    def apply_timing(self, timing):
        timing.validate(self.required_cameras())
        self.timing = timing
        self.reset_histories()
        self.program = BroadcastSelection(title=self.program.title)

    def required_cameras(self, preview=None):
        return set(self.program.cameras) | set((preview or self.preview).cameras)

    def stage(self, selection):
        if selection.scene not in SCENES:
            raise ValueError('Unknown scene')
        if len(selection.main_crop) != 4 or not all(finite(x) and 0 <= x <= .45 for x in selection.main_crop):
            raise ValueError('Camera crop must have four margins between 0 and 45 percent')
        if selection.scene == 'Replay' and self.scope.mode != 'replay':
            raise ValueError('Select REPLAY in the Base Video data source first')
        if len(set(selection.cameras) - set(self.program.cameras)) > 1:
            raise ValueError('Warm one new camera at a time: take the main camera before adding a second-camera inset')
        self.timing.validate(self.required_cameras(selection))
        self.preview = selection
        self.take_pending = False

    def evaluate_receiver(self, now):
        if self.scope.mode == 'replay' and self.telemetry.histories.get('replay'):
            self.node = self.last_node = 'replay'
            return
        if now - self.last_evaluation < 1:
            return
        self.last_evaluation = now
        peers = [p for p in self.peers if (p.get('info') or {}).get('mission_id') == self.scope.mission
                 and (p.get('info') or {}).get('source_mode') == self.scope.mode
                 and (p.get('info') or {}).get('vehicle_mode') == self.scope.family]
        if self.telemetry_choice == 'best':
            self.node = best_telemetry_node(peers, self.freshness * 1000, self.node, self.scope.vehicle)
        else:
            self.node = next((p['info']['node_id'] for p in peers
                              if p['info'].get('station') == self.telemetry_choice
                              and p.get('synchronized') and not p.get('conflict')
                              and p.get('state') == 'ONLINE'), None)
        if self.node:
            self.last_node = self.node

    def accept(self, record):
        if record is None:
            return False
        return self.telemetry.offer(record)

    def camera(self, logical, now, target):
        source = self.sources.get(logical)
        if not source or not source.available:
            self.lost_cameras.add(logical)
            return None, 'CAMERA UNAVAILABLE'
        latest = self.frames.latest(source.key)
        delay = self.timing.path_delay(logical)
        if delay is None:
            return None, 'CAMERA TIMING UNCALIBRATED'
        if latest is None or now - latest.arrival >= 2:
            self.lost_cameras.add(logical)
            return None, source.reason or 'CAMERA UNAVAILABLE'
        if logical in self.lost_cameras:
            if not self.frames.continuous(source.key, now):
                return None, 'CAMERA RECOVERING'
            self.lost_cameras.discard(logical)
        frame = self.frames.at(source.key, target, delay)
        if frame is None:
            return None, 'CAMERA BUFFERING'
        age = max(0, target - (frame.arrival - delay))
        if age >= 2:
            self.lost_cameras.add(logical)
            return None, 'CAMERA UNAVAILABLE'
        return frame, 'VIDEO DELAYED' if max(now-latest.arrival, age) >= .5 else ''

    def readiness(self, selection, now):
        if selection.scene in {'Standby', 'End card'}:
            return True, 'Slate ready'
        if not selection.main:
            return False, 'Choose a main camera'
        target = self.pause_time if self.paused and self.pause_time is not None else now - self.timing.delay
        for logical in selection.cameras:
            frame, notice = self.camera(logical, now, target)
            source = self.sources.get(logical)
            if frame is None or notice or not self.frames.continuous(source.key, now):
                return False, notice or 'Warming camera history (one second)'
        return True, 'Ready to take'

    def request_take(self, now):
        ready, reason = self.readiness(self.preview, now)
        if not ready:
            raise ValueError(reason)
        self.take_pending = True

    def output_boundary(self, now):
        if self.take_pending:
            ready, _ = self.readiness(self.preview, now)
            self.take_pending = False
            if ready:
                self.program = self.preview
                return True
        return False

    def snapshot(self, selection, now, event_labels):
        target = self.pause_time if self.paused and self.pause_time is not None else now - self.timing.delay
        if self.paused and selection in self.previous_snapshots:
            previous = self.previous_snapshots[selection]
            notice = previous.view.camera_notice
            main, inset = previous.main, previous.inset
            for logical in selection.cameras:
                source = self.sources.get(logical)
                latest = self.frames.latest(source.key) if source else None
                pinned = previous.main if logical == selection.main else previous.inset
                if (not source or (pinned and source.key != pinned.key)
                        or (self.scope.mode != 'replay' and (not latest or now-latest.arrival >= 2))):
                    notice = 'TRANSPORT LOST · PAUSED'
                    main = inset = None
            return replace(previous, output_time=now, main=main, inset=inset,
                           view=replace(previous.view, paused=True, camera_notice=notice))
        main = inset = pose = None
        notice = ''
        if selection.scene not in {'Standby', 'End card'}:
            main, notice = self.camera(selection.main, now, target)
            if selection.inset and selection.inset != 'orientation':
                inset, inset_notice = self.camera(selection.inset, now, target)
                if inset_notice:
                    notice = notice or 'INSET ' + inset_notice
        node = self.node or self.last_node
        history = self.telemetry.histories.get(node, ())
        station = history[-1].station if history else next(((p.get('info') or {}).get('station','')
                  for p in self.peers if (p.get('info') or {}).get('node_id') == node), '')
        delay = self.timing.path_delay('telemetry:' + station)
        record = self.telemetry.at(node, target, delay)
        # A conflicting identity is never retained as last-known telemetry.
        if any(p.get('conflict') for p in self.peers if (p.get('info') or {}).get('node_id') == node):
            record = None
        age = None
        status = 'unavailable'
        if record and record.age_at_arrival is not None:
            age = record.age_at_arrival + max(0, target - (record.arrival - delay))
            status = 'fresh' if age <= self.freshness and self.node else 'stale'
            if age > max(5, self.freshness):
                status = 'unavailable'
        if status == 'fresh' and record:
            if record.state_schema == 'zephyrus-v1' and record.state in {'APOGEE', 'MAIN'}:
                self.events.mark(record.state, record.arrival - delay, 'received state; event time uncertain',
                                 uncertain=True)
            if selection.inset == 'orientation':
                pose = record.pose
        completed, uncertain = self.events.at(target)
        elapsed, clock_state, _ = self.clock.at(target)
        if self.scope.mode == 'replay' and record and record.mission_elapsed is not None:
            elapsed, clock_state = record.mission_elapsed, 'elapsed'
        source = self.sources.get(selection.main)
        missing = []
        if delay is None:
            missing.append('SELECTED GS TIMING UNQUALIFIED')
        if history and self.scope.mode != 'replay':
            latest = history[-1]
            current_age = (latest.age_at_arrival or 0) + max(0,now-latest.arrival)
            if current_age > self.freshness:
                missing.append('CURRENT TELEMETRY LINK STALE')
        if record and record.altitude is None:
            missing.append('ALTITUDE DATUM UNQUALIFIED')
        if record and record.vertical_speed is None:
            missing.append('VERTICAL SPEED UNQUALIFIED')
        if selection.inset == 'orientation' and (pose is None or not pose.attitude_known):
            notice = notice or 'ORIENTATION UNAVAILABLE'
        view = FrameView(mission=selection.title, mode={'live':'LIVE SOURCE','simulation':'SIMULATION','replay':'REPLAY'}[self.scope.mode],
                         camera_label=source.label if source else 'NO CAMERA SELECTED',
                         telemetry_source=station.upper() or 'UNAVAILABLE',
                         altitude_m=record.altitude if record else None, altitude_datum=record.datum if record else 'unknown',
                         vertical_speed_m_s=record.vertical_speed if record else None,
                         vertical_speed_valid=bool(record and record.vertical_speed is not None),
                         flight_state=record.state if record else 'UNKNOWN', mission_elapsed_s=elapsed,
                         clock_state=clock_state, telemetry_status=status, telemetry_age_s=age,
                         completed_events=completed, uncertain_events=uncertain, event_labels=event_labels,
                         camera_notice=notice, telemetry_notice=' · '.join(missing),
                         timing_label='SYNC CALIBRATED' if self.timing.calibrated else 'SYNC UNCALIBRATED',
                         scene=selection.scene, caption=selection.caption, paused=self.paused,
                         camera_crop=selection.main_crop)
        ready, reason = self.readiness(selection, now)
        snapshot = BroadcastSnapshot(now, target, view, main, inset, pose, ready, reason)
        if not self.paused:
            # At most Program and Preview retain pinned images, included in the budget.
            self.previous_snapshots = {k:v for k,v in self.previous_snapshots.items()
                                       if k in (self.program, self.preview)}
            self.previous_snapshots[selection] = snapshot
        return snapshot
