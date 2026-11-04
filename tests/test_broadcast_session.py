from dataclasses import replace
from rocket_gnc_monitor.broadcast_model import BroadcastScope, BroadcastSelection, CameraKey, CameraSource, BroadcastFrame, EVENTS
from rocket_gnc_monitor.broadcast_session import BroadcastSession
from rocket_gnc_monitor.broadcast_timing import TimingConfig
import pytest


def populated():
    session = BroadcastSession(BroadcastScope('mission'))
    key = CameraKey('mission','balius','live','node','boot','gs1','camera','epoch')
    source = CameraSource('camera:away1:digital','GS1 DIGITAL',key,'away1','digital')
    session.sources[source.logical] = source
    for i in range(40):
        session.frames.offer(BroadcastFrame(key,bytes(6),10+i/30,2,1))
    return session, source


def test_take_is_atomic_and_failure_preserves_program():
    s,source = populated()
    selection = BroadcastSelection('Flight',source.logical)
    s.stage(selection)
    assert s.program.scene == 'Standby'
    s.request_take(11.3)
    assert s.program.scene == 'Standby'
    assert s.output_boundary(11.3)
    assert s.program == selection
    s.stage(replace(selection,main='missing'))
    with pytest.raises(ValueError):
        s.request_take(11.4)
    assert s.program == selection


def test_frozen_video_shows_delay_then_slate_and_requires_warm_recovery():
    s,source = populated()
    selection = BroadcastSelection('Flight',source.logical)
    assert s.snapshot(selection,11.3,EVENTS).main.arrival <= 10.8
    assert s.snapshot(selection,11.9,EVENTS).view.camera_notice == 'VIDEO DELAYED'
    assert s.snapshot(selection,13.5,EVENTS).main is None
    s.frames.offer(BroadcastFrame(source.key,bytes(6),14,2,1))
    assert s.snapshot(selection,14,EVENTS).view.camera_notice == 'CAMERA RECOVERING'
    for i in range(1,40):
        s.frames.offer(BroadcastFrame(source.key,bytes(6),14+i/30,2,1))
    assert s.snapshot(selection,15.3,EVENTS).main is not None


def test_scope_reset_and_timing_change_require_new_take():
    s,source = populated()
    s.program = BroadcastSelection('Flight',source.logical)
    s.clock.revise(10,'elapsed',anchor=10)
    s.events.mark('LIFTOFF',10,'operator')
    s.apply_timing(TimingConfig(.7))
    assert s.program.scene == 'Standby' and s.frames.bytes == 0
    assert s.clock.at(20)[0] is None
    s.set_scope(replace(s.scope,vehicle='booster'))
    assert not s.sources and not s.events.events


def test_new_gs_without_calibrated_history_stays_unavailable():
    from test_broadcast_core import event
    from rocket_gnc_monitor.broadcast_model import BroadcastTelemetry
    s,_ = populated()
    item = BroadcastTelemetry.from_event(event(arrival=10),s.scope)
    s.accept(item)
    s.node = item.node
    s.timing = TimingConfig(.5,True,{})
    snap = s.snapshot(BroadcastSelection(),11,EVENTS)
    assert snap.view.telemetry_status == 'unavailable'


def test_only_one_new_camera_can_warm_at_a_time():
    s,_ = populated()
    with pytest.raises(ValueError,match='one new camera'):
        s.stage(BroadcastSelection('Flight','a','b'))
