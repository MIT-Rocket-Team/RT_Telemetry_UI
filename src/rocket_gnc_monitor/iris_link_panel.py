"""Iris target placeholders; only DEMO selection is implemented."""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .station_profile import STATIONS
from .widgets import ComboBox


class IrisLinkPanel(QFrame):
    """Keep board-target intentions separate from unknown hardware state.

    This panel deliberately has no transport dependency. A simulated selection
    changes its own state and writes a demo event; it never changes the recorded
    flight data, the controller's telemetry source, or a physical board.
    """

    def __init__(self, controller, station="base", parent=None):
        super().__init__(parent)
        if station not in STATIONS:
            raise ValueError(f"Unknown station: {station!r}")
        self.controller = controller
        self.station = station
        self.is_launch = station == "base"
        self.selectors, self.buttons, self.status = {}, {}, {}
        self.simulated_targets = {}
        self._last_mode = controller.mode
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
            selector.currentIndexChanged.connect(self.refresh)
            controls.addWidget(selector)
            action = self.buttons[board] = QPushButton("Simulate switch")
            action.setAccessibleName(f"Simulate {title.lower()} switch")
            action.clicked.connect(lambda checked=False, selected=board: self.simulate(selected))
            controls.addWidget(action)
            column.addLayout(controls)
            state = self.status[board] = QLabel()
            state.setObjectName("muted")
            state.setWordWrap(True)
            state.setAccessibleName(f"{title.title()} switch status")
            column.addWidget(state)
            self.simulated_targets[board] = None
            layout.addWidget(group, 1)
        self.refresh()

    def simulate(self, board):
        if board not in self.selectors:
            raise ValueError(f"No {board!r} selector at this station")
        if self.controller.mode != "DEMO":
            self.refresh()
            return
        target = self.selectors[board].currentData()
        self.simulated_targets[board] = target
        name = "telemetry board" if board == "telemetry" else "downlink receiver"
        self.controller.log(
            f"Iris {name} switch simulated",
            dict(board=board, target=target, source="DEMO", placeholder=True),
        )
        self.refresh()

    def refresh(self, *_):
        mode = self.controller.mode
        if self._last_mode == "DEMO" and mode != "DEMO":
            for board in self.simulated_targets:
                self.simulated_targets[board] = None
        self._last_mode = mode
        for board, action in self.buttons.items():
            action.setEnabled(mode == "DEMO")
            action.setToolTip(
                "Simulate this target selection; flight data remains unchanged"
                if mode == "DEMO" else "Placeholder · firmware command not defined"
            )
            if mode == "DEMO":
                target = self.simulated_targets[board]
                state = f"Simulated target: {target.title()}" if target else "No switch simulated"
                self.status[board].setText(f"DEMO · Placeholder · {state} · Flight data unchanged")
            else:
                self.status[board].setText("Hardware target: unknown · Placeholder · firmware command not defined")
