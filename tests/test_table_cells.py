"""Live refreshes retain Qt item ownership and clear stale source colors."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QTableWidget

from rocket_gnc_monitor.protocol import ZephyrusDecoder
from rocket_gnc_monitor.station_workspace import StationWindow
from rocket_gnc_monitor.table_cells import clear_cells, set_cell
from test_protocol import frame


def test_cell_updates_preserve_owned_items_and_clear_foreground(qtbot):
    table = QTableWidget(1, 2)
    qtbot.addWidget(table)
    checkbox = QCheckBox()
    table.setCellWidget(0, 1, checkbox)
    item = set_cell(table, 0, 0, "4.2", "#00ff00")
    assert item.foreground().color() == QColor("#00ff00")
    assert set_cell(table, 0, 0, "3.2", "#ff0000") is item
    assert item.text() == "3.2" and item.foreground().color() == QColor("#ff0000")
    assert set_cell(table, 0, 0, "—") is item
    assert item.foreground().style() == Qt.BrushStyle.NoBrush
    set_cell(table, 0, 0, "FAIL", "#ff0000")
    clear_cells(table)
    assert table.item(0, 0) is item and item.text() == ""
    assert item.foreground().style() == Qt.BrushStyle.NoBrush
    assert table.cellWidget(0, 1) is checkbox


def test_station_refresh_retains_all_readout_items_and_updates_values(qtbot, tmp_path):
    window = StationWindow(tmp_path, station="base", auto_place=False)
    qtbot.addWidget(window)
    window.controller.timer.stop()

    def refresh(sample):
        window.controller.latest = sample
        window.last_ui = window.last_table = 0
        window.refresh()

    try:
        refresh(ZephyrusDecoder().feed(frame())[0])
        panel = window.rocket_panel
        tables = [panel.telemetry, panel.gps, panel.servos, panel.cells,
                  panel.power, panel.protections, panel.pyros, window.actuators]
        items = [(table, r, c, table.item(r, c)) for table in tables
                 for r in range(table.rowCount()) for c in range(table.columnCount())
                 if table.item(r, c) is not None]
        rail_controls = list(panel.rail_requests)
        for index in range(20):
            refresh(ZephyrusDecoder().feed(frame(index + 2, altitude=250.5 + index))[0])
            assert all(table.item(r, c) is item for table, r, c, item in items)
        assert panel.telemetry.item(3, 1).text() == "269.500"
        assert panel.cells.item(0, 1).foreground().style() != Qt.BrushStyle.NoBrush
        refresh(None)
        for table, row, column, item in items:
            if table is not window.actuators:
                assert table.item(row, column) is item
        assert panel.telemetry.item(3, 1).text() == "—"
        assert panel.cells.item(0, 1).text() == ""
        assert panel.cells.item(0, 1).foreground().style() == Qt.BrushStyle.NoBrush
        assert list(panel.rail_requests) == rail_controls
        assert not any(window.controller.workers.values())
    finally:
        window.close()
