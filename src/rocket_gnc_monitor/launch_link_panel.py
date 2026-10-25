"""Launch-station route selection, independent of the future radio transport."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QGridLayout, QVBoxLayout, QWidget

from .site_presets import URRG_STATION_MGRS
from .widgets import ComboBox


def text_label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class LaunchLinkPanel(QWidget):
    def __init__(self, controller, profile, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.profile = profile
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(text_label("Launch computer → Ethernet / PoE adapter\n4 × LTU-XR → Away stations 1–4"))
        self.site = text_label()
        layout.addWidget(self.site)
        layout.addWidget(text_label("Antenna pointer to use for communication"))
        self.selector = ComboBox()
        self.selector.setAccessibleName("Away antenna pointer for launch-station communication")
        self.selector.addItem("Choose an away-station pointer…", None)
        for number in range(1, 5):
            self.selector.addItem(f"Away station {number} · antenna pointer", f"away{number}")
        layout.addWidget(self.selector)
        paths = QGridLayout()
        paths.setHorizontalSpacing(8)
        paths.setVerticalSpacing(4)
        self.path_status = {}
        for number in range(1, 5):
            key = f"away{number}"
            stage = ("Booster" if number == 4 else "Sustainer") if profile.vehicle == "iris" else "Balius"
            paths.addWidget(text_label(f"LTU-XR {number} ↔ Away {number} · {stage}"), number - 1, 0)
            self.path_status[key] = text_label("Link status unavailable")
            paths.addWidget(self.path_status[key], number - 1, 1)
        layout.addLayout(paths)
        self.status = text_label()
        layout.addWidget(self.status)
        self.uplink = text_label("Uplink authority: Launch station only · local telemetry board\nUplink switch protocol pending · transmission unavailable")
        layout.addWidget(self.uplink)
        layout.addWidget(text_label("Selecting a pointer chooses the intended route. It does not connect a link, move an antenna or transmit a command."))
        layout.addStretch()
        self.selector.currentIndexChanged.connect(self.select_pointer)
        controller.changed.connect(self.refresh)
        self.refresh()

    def select_pointer(self):
        self.controller.select_away_station(self.selector.currentData())
        self.refresh()

    def refresh(self):
        selected = self.controller.selected_away_station
        previous = self.selector.blockSignals(True)
        self.selector.setCurrentIndex(max(0, self.selector.findData(selected)))
        self.selector.blockSignals(previous)
        self.status.setText(self.controller.remote_pointer_status)
        for key, label in self.path_status.items():
            label.setText("Selected route · unavailable" if key == selected else "Link status unavailable")
            label.setStyleSheet("color: #79d4c8;" if key == selected else "")
        self.site.setText(
            f"URRG launch station: {URRG_STATION_MGRS['base']}\nStation reference only · no local antenna pointer"
            if self.controller.mission.launch_site_name == "URRG" else
            "One local telemetry board · no local antenna pointer"
        )
