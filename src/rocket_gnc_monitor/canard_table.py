"""Named control-surface feedback and individual-command availability."""

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .domain import finite
from .table_cells import set_cell

COMMAND_UNAVAILABLE = "Individual servo commands are unavailable: firmware protocol not defined."


class CanardTable(QWidget):
    """Do not reinterpret the four unnamed drive channels as new surfaces.

    Named measured angles use the existing Sample.actuators contract. The
    current wire protocol offers only a grouped roll-angle command, without a
    channel identifier; it cannot implement any of these per-surface buttons.
    """

    def __init__(self, controller, guard, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.guard = guard
        self.channels = ()
        self.commands = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Servo", "Angle°", "Command"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(27)
        self.table.verticalHeader().setMinimumSectionSize(27)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setAccessibleName("Canards and tabs measured angles and commands")
        layout.addWidget(self.table, 1)
        self.note = QLabel("Individual commands unavailable · firmware protocol pending")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        self.note.setToolTip(COMMAND_UNAVAILABLE)
        layout.addWidget(self.note)
        self.refresh()

    def _set_channels(self, channels):
        saved = {name: controls[0].text() for name, controls in self.commands.items()}
        self.commands = {}
        self.channels = channels
        self.table.setRowCount(len(channels))
        for row, name in enumerate(channels):
            set_cell(self.table, row, 0, name)
            command = QWidget()
            controls = QHBoxLayout(command)
            controls.setContentsMargins(3, 1, 3, 1)
            controls.setSpacing(4)
            angle = QLineEdit(saved.get(name, ""))
            angle.setPlaceholderText("Angle °")
            angle.setMinimumWidth(46)
            angle.setAccessibleName(f"{name} command angle")
            button = QPushButton("Send")
            button.setMinimumWidth(42)
            button.setAccessibleName(f"Send {name} angle")
            button.clicked.connect(lambda checked=False, channel=name: self.send_command(channel))
            controls.addWidget(angle, 1)
            controls.addWidget(button)
            self.table.setCellWidget(row, 2, command)
            self.commands[name] = (angle, button)

    def send_command(self, channel):
        """Reject even a programmatic click until an actual protocol exists."""

        def unavailable():
            reason = self.controller.command_block_reason
            if reason:
                raise ValueError(reason)
            raise ValueError(COMMAND_UNAVAILABLE)

        self.guard(unavailable)

    def refresh(self):
        channels = tuple(f"Canard{i + 1}" for i in range(self.controller.mission.canard_count))
        channels += tuple(f"Tab{i + 1}" for i in range(4))
        if channels != self.channels:
            self._set_channels(channels)
        sample = getattr(self.controller, "display_latest", self.controller.latest)
        feedback = sample.actuators if sample is not None else {}
        blocked = self.controller.command_block_reason
        reason = f"{blocked}. {COMMAND_UNAVAILABLE}" if blocked else COMMAND_UNAVAILABLE
        for row, name in enumerate(channels):
            # The initial domain contract used a space ("Canard 1"). Accept
            # both spellings without assigning unrelated legacy PWM channels.
            spaced = name.replace("Canard", "Canard ").replace("Tab", "Tab ")
            values = feedback.get(name, feedback.get(spaced, {}))
            measured = values.get("measured") if isinstance(values, dict) else None
            item = set_cell(self.table, row, 1, f"{measured:.2f}" if finite(measured) else "—")
            item.setToolTip("Measured angle (°)" if finite(measured) else "Named angle feedback unavailable")
            for control in self.commands[name]:
                control.setEnabled(False)
                control.setToolTip(reason)
        self.table.setMinimumHeight(self.table.horizontalHeader().height() + len(channels) * 27 + 4)
