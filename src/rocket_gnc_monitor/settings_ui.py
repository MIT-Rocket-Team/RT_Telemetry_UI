"""Global preferences editor; saves through the owning window only on acceptance."""

from pathlib import Path
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from .settings import AppSettings, runtime_root
from .widgets import ComboBox as QComboBox


class SettingsDialog(QDialog):
    def __init__(self, settings, data_dir, apply_settings, parent=None):
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.apply_settings = apply_settings
        self.setWindowTitle("Settings · Rocket GNC Monitor")
        self.setMinimumWidth(650)
        self.resize(780, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        heading = QLabel("Settings")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        description = QLabel("Preferences for this computer · saved between launches")
        description.setObjectName("muted")
        layout.addWidget(description)
        form = QFormLayout()
        form.setVerticalSpacing(16)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.engine = QLineEdit()
        self.engine.setAccessibleName("OpenRocket JAR path")
        self.engine.setPlaceholderText("Use bundled OpenRocket")
        form.addRow("OpenRocket JAR", self.path_row(self.engine, self.browse_engine, "Use bundled"))
        self.engine_status = QLabel()
        self.engine_status.setWordWrap(True)
        self.engine_status.setTextFormat(Qt.TextFormat.PlainText)
        self.engine_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.engine_status.setObjectName("muted")
        form.addRow("", self.engine_status)
        engine_help = QLabel(
            "Choose a complete OpenRocket-MIT JAR compatible with the simulation bridge. "
            "Java is included with the app. Engine changes apply to the next simulation."
        )
        engine_help.setWordWrap(True)
        form.addRow("", engine_help)
        self.timeout = QSpinBox()
        self.timeout.setRange(10, 1800)
        self.timeout.setSuffix(" seconds")
        form.addRow("Simulation time limit", self.timeout)
        self.sessions = QLineEdit()
        self.sessions.setAccessibleName("Recording folder")
        self.sessions.setPlaceholderText(str(AppSettings().sessions_path(data_dir)))
        form.addRow("Recording folder", self.path_row(self.sessions, self.browse_sessions, "Use default"))
        recording_help = QLabel(
            "Applies to new logging sessions. Active recordings keep their current folder."
        )
        recording_help.setWordWrap(True)
        recording_help.setObjectName("muted")
        form.addRow("", recording_help)
        self.theme = QComboBox()
        self.theme.addItems(["Dark", "Daylight"])
        form.addRow("Appearance", self.theme)
        layout.addLayout(form)
        layout.addStretch()
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setAccessibleName("Settings error")
        self.error.hide()
        layout.addWidget(self.error)
        location = QLabel("Saved in: " + str(self.data_dir / "settings.json"))
        location.setWordWrap(True)
        location.setTextFormat(Qt.TextFormat.PlainText)
        location.setObjectName("muted")
        layout.addWidget(location)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            lambda: self.populate(AppSettings())
        )
        layout.addWidget(self.buttons)
        self.engine.textChanged.connect(self.describe_engine)
        self.populate(settings)

    def path_row(self, field, browse, reset_text):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field, 1)
        button = QPushButton("Browse…")
        button.clicked.connect(browse)
        layout.addWidget(button)
        reset = QPushButton(reset_text)
        reset.clicked.connect(field.clear)
        layout.addWidget(reset)
        return row

    def populate(self, settings):
        self.engine.setText(settings.openrocket_jar)
        self.sessions.setText(settings.sessions_directory)
        self.timeout.setValue(settings.simulation_timeout)
        self.theme.setCurrentIndex(int(settings.daylight))
        self.error.hide()
        self.describe_engine()

    def describe_engine(self):
        path = self.engine.text().strip()
        if path:
            self.engine_status.setText(
                "Custom engine selected" if Path(path).is_file() else "Custom JAR not found"
            )
            return
        root = runtime_root()
        title = "Bundled engine" if (root / "openrocket.jar").is_file() else "Bundled engine missing"
        try:
            metadata = json.loads((root / "engine.json").read_text(encoding="utf-8"))
            tag = metadata.get("source", {}).get("release_tag", "")
            if tag:
                title += " · " + tag
        except (OSError, ValueError, AttributeError):
            pass
        self.engine_status.setText(title + "\n" + str(root / "openrocket.jar"))

    def browse_engine(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose OpenRocket-MIT JAR", self.engine.text(), "Java archive (*.jar)"
        )
        if path:
            self.engine.setText(path)

    def browse_sessions(self):
        path = QFileDialog.getExistingDirectory(
            self, "Choose recording folder", self.sessions.text() or str(self.data_dir)
        )
        if path:
            self.sessions.setText(path)

    def accept(self):
        def absolute(text):
            return str(Path(text.strip()).expanduser().resolve()) if text.strip() else ""

        try:
            settings = AppSettings(
                openrocket_jar=absolute(self.engine.text()),
                sessions_directory=absolute(self.sessions.text()),
                daylight=bool(self.theme.currentIndex()),
                simulation_timeout=self.timeout.value(),
            ).validate(paths=True)
            self.apply_settings(settings)
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        super().accept()
