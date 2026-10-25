"""Update table-owned items in place during continuous telemetry refreshes."""

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidgetItem


def set_cell(table, row, column, text, foreground=None):
    # Replacing live items repeatedly can crash PySide's wrapper destructor on
    # Intel macOS. Keep the table's existing item and change only its content.
    item = table.item(row, column)
    if item is None:
        item = QTableWidgetItem()
        table.setItem(row, column, item)
    if item.text() != text:
        item.setText(text)
    brush = QBrush(QColor(foreground)) if foreground is not None else QBrush()
    if item.foreground() != brush:
        item.setForeground(brush)
    return item


def clear_cells(table):
    """Clear source-specific values and colors without destroying cell items."""
    for row in range(table.rowCount()):
        for column in range(table.columnCount()):
            if table.item(row, column) is not None:
                set_cell(table, row, column, "")
