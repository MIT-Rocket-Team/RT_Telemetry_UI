"""Iris target placeholders pending a firmware command contract."""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .station_profile import STATIONS
from .widgets import ComboBox


class IrisLinkPanel(QFrame):
    """Keep board-target intentions separate from unknown hardware state.

    This panel has no transport dependency. Selection is unavailable until the
    firmware defines a target-switch command and acknowledgement.
    """

    def __init__(self, controller, station="base", parent=None):
        super().__init__(parent)
        if station not in STATIONS:
            raise ValueError(f"Unknown station: {station!r}")
        self.controller = controller
        self.station = station
        self.is_launch = station == "base"
        self.selectors, self.buttons, self.status = {}, {}, {}
        self.setObjectName("card")
        self.setAccessibleName("Iris board target placeholder")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(18)
        boards = ([("telemetry", "TELEMETRY BOARD TARGET")] if self.is_launch else
                  [("downlink", "DOWNLINK RECEIVER")])
        for board, title in boards:
            group = QWidget()
            group.setObjectName("transparent")
            column = QVBoxLayout(group)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)
            controls = QHBoxLayout()
            controls.setSpacing(7)
            heading = QLabel(title)
            heading.setObjectName("eyebrow")
            controls.addWidget(heading)
            controls.addStretch()
            selector = self.selectors[board] = ComboBox()
            selector.setAccessibleName(f"{title.title()} desired target")
            selector.setToolTip("Desired target only; the physical board target is unknown")
            for target in ("sustainer", "booster"):
                selector.addItem(target.title(), target)
            selector.setCurrentIndex(1 if station == "away4" else 0)
            selector.setEnabled(False)
            controls.addWidget(selector)
            action = self.buttons[board] = QPushButton("Unavailable")
            action.setAccessibleName(f"{title.title()} switch unavailable")
            controls.addWidget(action)
            column.addLayout(controls)
            state = self.status[board] = QLabel()
            state.setObjectName("muted")
            state.setWordWrap(True)
            state.setAccessibleName(f"{title.title()} switch status")
            column.addWidget(state)
            layout.addWidget(group, 1)
        self.refresh()

    def refresh(self, *_):
        for board, action in self.buttons.items():
            action.setEnabled(False)
            action.setToolTip("Placeholder · firmware command not defined")
            self.status[board].setText("Hardware target: unknown · Placeholder · firmware command not defined")
