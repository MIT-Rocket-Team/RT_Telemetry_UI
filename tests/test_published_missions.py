import uuid

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from rocket_gnc_monitor.network import NetworkRuntime
from rocket_gnc_monitor.network.missions import make_invite, read_invite
from rocket_gnc_monitor.network.protocol import validate_info
from rocket_gnc_monitor.network_ui import StationNetworkDialog
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow


def station(qtbot, directory, site, *, discovery=False):
    config = dict(network_bind_host="127.0.0.1", network_port=0, network_discovery=discovery,
                  mission_id=None, mission_simulation=True, peers=[])
    w = StationWindow(directory, profile=StationProfile(station=site), auto_place=False, network_config=config)
    qtbot.addWidget(w)
    b = w.station_network
    qtbot.waitUntil(lambda: b.runtime.state == "LISTENING")
    d = StationNetworkDialog(b, w)
    qtbot.addWidget(d)
    d.show()
    return w, b, d


def test_create_find_by_invite_join_and_leave(qtbot, tmp_path):
    bw, base, bd = station(qtbot, tmp_path / "base", "base")
    aw, away, ad = station(qtbot, tmp_path / "away", "away1")
    try:
        assert not ad.create.isVisible()
        with pytest.raises(ValueError, match="Base"):
            away.publish_mission("Pad test")
        bd.title.setText("Balius Pad Test")
        qtbot.mouseClick(bd.create, Qt.MouseButton.LeftButton)
        key = (base.runtime.mission_id, base.runtime.node_id)
        qtbot.waitUntil(lambda: base.runtime.state == "LISTENING")
        bd.refresh()
        qtbot.mouseClick(bd.copy, Qt.MouseButton.LeftButton)
        invite = QApplication.clipboard().text()
        mission, publisher, addresses = read_invite(invite)
        assert (mission, publisher) == key
        assert addresses == [base.runtime.local_endpoint]
        # The URL alone must never join a different session at a recycled address.
        ad.invite.setText(make_invite(str(uuid.uuid4()), publisher, addresses))
        ad.find_invite()
        qtbot.waitUntil(lambda: key in away.published_missions())
        ad.refresh()
        assert not ad.join.isEnabled()
        ad.invite.setText(invite)
        ad.find_invite()
        qtbot.waitUntil(lambda: ad.join.isEnabled())
        assert "Balius Pad Test" in ad.nearby.currentText()
        qtbot.mouseClick(ad.join, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: any(p["state"] == "ONLINE" for p in base.peers), timeout=10000)
        qtbot.waitUntil(lambda: any(p["state"] == "ONLINE" for p in away.peers), timeout=10000)
        ad.refresh()
        assert "Connected to Base" in ad.membership.text()
        assert away.runtime.mission_id == mission
        assert not away.runtime.info().get("published_mission_title")
        bd.leave_mission()
        qtbot.waitUntil(lambda: key not in away.published_missions(), timeout=7000)
        ad.refresh()
        assert "Waiting for Base" in ad.membership.text()
        bd.reopen_mission()
        assert base.runtime.mission_id == mission
        ad.leave_mission()
        assert away.runtime.mission_id is None
    finally:
        ad.close()
        bd.close()
        aw.close()
        bw.close()


def test_named_base_only_and_validation(tmp_path):
    mid = str(uuid.uuid4())
    publisher = NetworkRuntime(tmp_path, StationProfile(), mid, {})
    info = publisher.info() | {"published_mission_title": "Test"}
    assert validate_info(info) is info
    for change in ({"station": "gs1"}, {"mission_id": None}, {"published_mission_title": " "},
                   {"published_mission_title": "x"*65}, {"source_mode": "simulation"}):
        with pytest.raises(ValueError):
            validate_info(info | change)
    with pytest.raises(ValueError):
        read_invite("rtmission://join/invalid?base=no&address=bad")


def test_discover_published_mission_without_an_address(qtbot, tmp_path, monkeypatch):
    from zeroconf import IPVersion, Zeroconf

    # Exercise the actual multicast registration, browser, HTTP identity and join
    # on an isolated loopback interface without advertising to operating stations.
    monkeypatch.setattr("rocket_gnc_monitor.network.runtime.Zeroconf",
                        lambda: Zeroconf(interfaces=["127.0.0.1"], ip_version=IPVersion.V4Only))
    aw, away, ad = station(qtbot, tmp_path / "away", "away2", discovery=True)
    bw, base, bd = station(qtbot, tmp_path / "base", "base", discovery=True)
    try:
        bd.title.setText("Bench rehearsal")
        bd.create.click()
        qtbot.waitUntil(lambda: ad.nearby.currentText().startswith("Bench rehearsal"), timeout=20000)
        assert not away.config["peers"]
        ad.join.click()
        qtbot.waitUntil(lambda: any(p["state"] == "ONLINE" for p in base.peers), timeout=15000)
        qtbot.waitUntil(lambda: any(p["state"] == "ONLINE" for p in away.peers), timeout=15000)
        assert away.config["mission_id"] == base.config["mission_id"]
    finally:
        ad.close()
        bd.close()
        aw.close()
        bw.close()
