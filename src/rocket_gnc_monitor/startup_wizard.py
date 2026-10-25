"""Choose the launch-day computer assignment before opening any instruments."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QLabel, QRadioButton, QVBoxLayout, QWizard, QWizardPage

from .station_profile import StationProfile
from .site_presets import URRG_LAUNCH_MGRS, URRG_STATION_MGRS
from .widgets import STYLE


SETUP_STYLE = """
QWizard { background: #0b111a; }
QWizardPage { background: #0b111a; }
QRadioButton#setupChoice { background: #121d2a; border: 1px solid #2a3b4f;
    border-radius: 8px; padding: 13px 16px; spacing: 14px; font-size: 14px; }
QRadioButton#setupChoice:hover { border-color: #79d4c8; background: #1a293a; }
QRadioButton#setupChoice:checked { border-color: #79d4c8; background: #183a40; }
QRadioButton#setupChoice::indicator { width: 16px; height: 16px; border-radius: 8px;
    border: 1px solid #617287; background: #0b111a; }
QRadioButton#setupChoice::indicator:checked { background: #79d4c8; border-color: #79d4c8; }
QLabel#setupSummary { background: #121d2a; border: 1px solid #2a3b4f;
    border-radius: 8px; padding: 16px; color: #b9d3dd; }
"""


class StartupWizard(QWizard):
    def __init__(self, profile=None, parent=None, *, remember_choices=False):
        super().__init__(parent)
        profile = profile or StationProfile()
        self.setWindowTitle("Station setup · Rocket GNC Monitor")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage)
        self.setOption(QWizard.WizardOption.NoCancelButton, False)
        self.setOption(QWizard.WizardOption.NoCancelButtonOnLastPage, False)
        self.setButtonText(QWizard.WizardButton.NextButton, "Continue")
        self.setButtonText(QWizard.WizardButton.BackButton, "Back")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Open station")
        self.button(QWizard.WizardButton.FinishButton).setObjectName("primary")
        self.resize(790, 660)
        self.setMinimumSize(680, 610)
        self.setStyleSheet(STYLE + SETUP_STYLE)
        self.choices = {}
        self.groups = {}
        self.add_choices(
            "station", "01 / STATION", "Where is this computer?",
            "Assign this computer to the launch station or one of the four away stations.",
            [("base", "Launch station", "One telemetry board · four LTU-XR links · no local pointer")]
            + [(f"away{i}", f"Away station {i}", "Local Wi-Fi · downlink board and antenna pointer") for i in range(1, 5)],
            profile.station,
        )
        self.add_choices(
            "role", "02 / ROLE", "What will this computer display?",
            "Keep each station focused on its assigned task.",
            [
                ("telemetry", "Telemetry", "Rocket telemetry and station communication controls"),
                ("video", "Video", "Camera reception, recording and playback in one dedicated window"),
            ],
            profile.role,
        )
        self.add_choices(
            "vehicle", "03 / VEHICLE", "Which rocket is flying?",
            "Video and telemetry receivers are assigned for the station you selected.",
            [
                ("balius", "Balius", "Two video channels · Digital and Analog"),
                ("iris", "Iris", "Sustainer Digital + Sustainer Analog + Booster Analog"),
            ],
            profile.vehicle,
        )
        launch_page = self.add_choices(
            "launch_site", "04 / LAUNCH SITE", "Are you launching from URRG?",
            "Set the launch pad and this station's location for the mission.",
            [
                ("urrg", "URRG", "Use the URRG launch pad and the preset for your station number"),
                ("custom", "Another launch site", "Set launch and station locations in Mission profile"),
            ],
            profile.launch_site,
        )
        self.summary = QLabel()
        self.summary.setObjectName("setupSummary")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        launch_page.layout().insertWidget(launch_page.layout().count() - 1, self.summary)
        self.remember_checkbox = QCheckBox("Remember these choices next time")
        self.remember_checkbox.setChecked(remember_choices)
        self.remember_checkbox.setToolTip("Remember this station, role, rocket and launch-site choice on this computer.")
        launch_page.layout().insertWidget(launch_page.layout().count() - 1, self.remember_checkbox)
        self.currentIdChanged.connect(self.update_summary)
        for group in self.groups.values():
            group.buttonToggled.connect(lambda *_: self.update_summary())
        self.update_summary()

    def add_choices(self, key, step, title, description, choices, selected):
        page = QWizardPage()
        page.setTitle(title)
        page.setSubTitle(description)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(10)
        eyebrow = QLabel(step)
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        group = self.groups[key] = QButtonGroup(page)
        self.choices[key] = {}
        for value, name, detail in choices:
            control = QRadioButton(f"{name}\n{detail}")
            control.setObjectName("setupChoice")
            control.setAccessibleName(name)
            control.setCursor(Qt.CursorShape.PointingHandCursor)
            control.setChecked(value == selected)
            group.addButton(control)
            self.choices[key][value] = control
            layout.addWidget(control)
        layout.addStretch()
        self.addPage(page)
        return page

    @property
    def remember_choices(self):
        return self.remember_checkbox.isChecked()

    @property
    def profile(self):
        return StationProfile(**{
            key: next(value for value, control in choices.items() if control.isChecked())
            for key, choices in self.choices.items()
        })

    def update_summary(self, *_):
        if not hasattr(self, "summary"):
            return
        profile = self.profile
        text = f"{profile.station_label} · {profile.role.title()} · {profile.vehicle_label}"
        if profile.role == "video":
            channels = ", ".join(profile.channel_labels[channel] for channel in profile.local_channels)
            text += f"\nLocal USB inputs: {channels}."
            if profile.station == "base":
                text += " Away-station feeds: connection pending."
        else:
            text += ("\nTwo displays · Ethernet / PoE to four LTU-XR links."
                     if profile.station == "base" else
                     "\nOne display · Wi-Fi, downlink board and antenna control.")
            if profile.station == "base":
                text += "\nOne telemetry board: downlink reception and polling."
                text += "\nUplink enable: firmware pending; DEMO simulation only."
                text += "\nChoose an away antenna pointer; remote control pending."
            targets = ", ".join(target.title() for target in profile.telemetry_targets)
            text += f"\nTelemetry assignment: {targets}."
            if profile.station == "base" and profile.vehicle == "iris":
                text += " One shared board target."
        text += f"\nMission: {profile.vehicle_label} Launch."
        if profile.launch_site == "urrg":
            site_label = "Launch station reference" if profile.station == "base" else profile.station_label
            text += (f"\nURRG launch pad: {URRG_LAUNCH_MGRS}"
                     f"\n{site_label}: {URRG_STATION_MGRS[profile.station]}")
            text += ("\nSet launch elevation in Mission profile; no local antenna pointer."
                     if profile.station == "base" else
                     "\nSet launch and antenna elevations separately in Mission profile.")
        else:
            text += ("\nSet the launch location in Mission profile; choose an away antenna in the workspace."
                     if profile.station == "base" else
                     "\nSet launch and antenna locations in Mission profile before tracking.")
        self.summary.setText(text)
