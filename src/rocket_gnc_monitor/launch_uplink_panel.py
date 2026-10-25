"""Launch telemetry-board uplink placeholder with DEMO-only switching."""

from PySide6.QtCore import QSignalBlocker
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
        self.toggle = QPushButton()
        self.toggle.setCheckable(True)
        self.toggle.setMinimumWidth(100)
        self.toggle.setAccessibleName("Simulate launch uplink on or off")
        self.toggle.clicked.connect(self._toggle)
        layout.addWidget(self.toggle)
        self.refresh()

    def _toggle(self, enabled):
        # Recheck the mode here; a queued click must not become a live command.
        if self.controller.mode == "DEMO":
            self.controller.set_simulated_uplink(bool(enabled))
        self.refresh()

    def refresh(self):
        demo = self.controller.mode == "DEMO"
        enabled = demo and getattr(self.controller, "simulated_uplink_enabled", False) is True
        with QSignalBlocker(self.toggle):
            self.toggle.setChecked(enabled)
        self.toggle.setEnabled(demo)
        if demo:
            state = "ON" if enabled else "OFF"
            self.toggle.setText(f"Uplink {state}")
            self.status.setText(f"DEMO · Simulated uplink {state}")
            self.toggle.setToolTip("Simulate the launch board uplink switch; no hardware command is sent")
        else:
            self.toggle.setText("Unavailable")
            self.status.setText("Hardware uplink state: UNKNOWN")
            self.toggle.setToolTip("Placeholder · firmware command not defined")
