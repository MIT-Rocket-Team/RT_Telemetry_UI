"""Launch telemetry-board uplink placeholder pending a firmware contract."""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class LaunchUplinkPanel(QFrame):
    """Expose the intended switch without claiming an unknown hardware state."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("card")
        self.setAccessibleName("Launch telemetry board uplink switch placeholder")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 5, 9, 5)
        layout.setSpacing(9)
        information = QVBoxLayout()
        information.setSpacing(1)
        heading = QLabel("LAUNCH UPLINK")
        heading.setObjectName("eyebrow")
        information.addWidget(heading)
        self.status = QLabel()
        self.status.setAccessibleName("Launch uplink switch status")
        information.addWidget(self.status)
        self.placeholder = QLabel("Placeholder · firmware command not defined")
        self.placeholder.setObjectName("muted")
        information.addWidget(self.placeholder)
        layout.addLayout(information)
        self.toggle = QPushButton("Unavailable")
        self.toggle.setMinimumWidth(100)
        self.toggle.setAccessibleName("Launch uplink switch unavailable")
        layout.addWidget(self.toggle)
        self.refresh()

    def refresh(self):
        self.toggle.setEnabled(False)
        self.status.setText("Hardware uplink state: UNKNOWN")
        self.toggle.setToolTip("Placeholder · firmware command not defined")
