from collections import deque
from dataclasses import replace
import pytest
from PySide6.QtCore import Qt

from rocket_gnc_monitor.domain import Sample
from rocket_gnc_monitor.station_profile import StationProfile
from rocket_gnc_monitor.station_workspace import StationWindow


@pytest.mark.parametrize('remote', [False, True])
def test_clear_button_hides_history_then_plots_new_samples_without_erasing_data(qtbot, tmp_path, remote):
    window = StationWindow(tmp_path, profile=StationProfile(), auto_place=False)
    qtbot.addWidget(window)
    c = window.controller
    c.timer.stop()
    first = Sample(t=1, sequence=1, source='NETWORK' if remote else 'LIVE',
                   altitude=100, rates=[360, 180, 90], attitude=[1, 2, 3])
    history = deque([first], maxlen=3)
    if remote:
        c.remote_display_key = 'gs3-node'
        c.remote_display_history, c.remote_display_sample = history, first
    else:
        c.history, c.latest = history, first
    original = first.to_dict()
    try:
        window.refresh_plots(force=True)
        assert len(window.altitude_curve.yData) == 1
        qtbot.mouseClick(window.clear_plots_button, Qt.MouseButton.LeftButton)
        curves = [window.altitude_curve, *window.rate_curves, *window.angle_curves]
        assert all(curve.yData is None or len(curve.yData) == 0 for curve in curves)
        assert list(history) == [first] and c.display_latest is first
        assert first.to_dict() == original
        window.apply_settings(replace(c.settings, display_units='metric'))
        assert all(curve.yData is None or len(curve.yData) == 0 for curve in curves)
        second = replace(first, t=2, sequence=2, altitude=200)
        history.append(second)
        if remote:
            c.remote_display_sample = second
        else:
            c.latest = second
        window.refresh_plots(force=True)
        assert window.altitude_curve.yData.tolist() == [200]
        assert window.rate_curves[0].yData.tolist() == [1]
        assert window.angle_curves[2].yData.tolist() == [3]
        assert len(history) == 2 and history[0] is first
        # Once the cleared sample rolls out, the retained new buffer still plots.
        history.extend([replace(second, sequence=3), replace(second, sequence=4)])
        window.refresh_plots(force=True)
        assert window.altitude_curve.yData.tolist() == [200, 200, 200]
    finally:
        window.close()
