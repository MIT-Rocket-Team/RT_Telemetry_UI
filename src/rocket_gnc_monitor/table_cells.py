"""Update table-owned items in place during continuous telemetry refreshes."""

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidgetItem


class _TableItem(QTableWidgetItem):
    """Avoid stale plain-item wrappers when Qt destroys and rebuilds tables.

    A Python subclass prevents the PySide 6.11 wrapper-reuse crash observed
    while replacing station windows and constructing subsequent QObjects.
    The table still owns the item and its lifetime.
    """


def set_cell(table, row, column, text, foreground=None):
    # Replacing live items repeatedly can crash PySide's wrapper destructor on
    # Intel macOS. Keep the table's existing item and change only its content.
    item = table.item(row, column)
    if item is None:
        item = _TableItem()
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
