"""Operator controls are separate from the clean, fixed-size Program surface."""

import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .broadcast_model import SCENES, BroadcastSelection
from .broadcast_timing import Calibration, TimingConfig
from .obs_bridge import OBSBridge


class FrameCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame = QImage()
        self.setMinimumSize(320, 180)

    def present(self, frame):
        self.frame = frame
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("black"))
        if not self.frame.isNull():
            factor = min(self.width() / 1920, self.height() / 1080)
            target = QRectF(
                (self.width() - 1920 * factor) / 2,
                (self.height() - 1080 * factor) / 2,
                1920 * factor,
                1080 * factor,
            )
            painter.drawImage(target, self.frame)


class ProgramWindow(FrameCanvas):
    def __init__(self, controller):
        super().__init__(controller.owner)
        self.controller = controller
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowTitle("Rocket Broadcast — Program")
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.reset_size()
        controller.program_frame.connect(self.present)

    def reset_size(self):
        ratio = self.devicePixelRatioF()
        self.setFixedSize(round(1920 / ratio), round(1080 / ratio))

    def closeEvent(self, event):
        answer = QMessageBox.question(
            self,
            "Stop Program?",
            "Close the clean Program window? OBS streaming and recording will continue until you stop them separately.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.stop_program()
            event.accept()
        else:
            event.ignore()


class BroadcastPanel(QDialog):
    def __init__(self, controller):
        super().__init__(controller.owner)
        self.c = controller
        self.obs = OBSBridge(self)
        self.setWindowTitle("Broadcast · Base Video")
        self.resize(1060, 900)
        self.setModal(False)
        self.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #284358; } "
            "QTabBar::tab { background: #172b3b; color: #dce7ef; padding: 9px 16px; } "
            "QTabBar::tab:selected { background: #284358; color: #57d7c8; }"
        )
        root = QVBoxLayout(self)
        self.overview = QLabel()
        self.overview.setWordWrap(True)
        root.addWidget(self.overview)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self.build_production()
        self.build_timing()
        self.build_clock()
        self.build_obs()
        self.message = QLabel("Start with a slate. Preview a camera, then Take when ready.")
        self.message.setWordWrap(True)
        root.addWidget(self.message)
        controller.changed.connect(self.refresh)
        self.obs.changed.connect(self.refresh)
        self._catalog = None
        self.refresh()

    def page(self, name):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.tabs.addTab(widget, name)
        return layout

    def action(self, text, callback, layout):
        button = QPushButton(text)
        button.clicked.connect(lambda: self.run(callback))
        layout.addWidget(button)
        return button

    def run(self, callback):
        try:
            callback()
            self.message.setText("Updated")
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc))
        self.refresh()

    def combo(self, items):
        result = QComboBox()
        for text, value in items:
            result.addItem(text, value)
        return result

    def build_production(self):
        layout = self.page("Produce")
        self.canvas = FrameCanvas()
        self.canvas.setMinimumHeight(300)
        self.c.preview_frame.connect(self.canvas.present)
        layout.addWidget(self.canvas, 1)
        self.preview_status = QLabel()
        self.preview_status.setWordWrap(True)
        layout.addWidget(self.preview_status)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        self.scene = self.combo([(s, s) for s in SCENES])
        self.main = self.combo([("Choose a camera", None)])
        self.inset = self.combo([("Off", None), ("Rocket orientation", "orientation")])
        self.title = QLineEdit(self.c.settings.title)
        self.title.setMaxLength(60)
        self.caption = QLineEdit()
        self.caption.setMaxLength(96)
        self.crop = [QDoubleSpinBox() for _ in range(4)]
        crop_widget = QWidget()
        crop_row = QHBoxLayout(crop_widget)
        crop_row.setContentsMargins(0, 0, 0, 0)
        for field, name in zip(self.crop, ("L", "T", "R", "B")):
            field.setRange(0, 45)
            field.setSuffix(" %")
            field.setAccessibleName("Main camera crop " + name)
            crop_row.addWidget(QLabel(name))
            crop_row.addWidget(field)
        self.main.currentIndexChanged.connect(lambda: [f.setValue(0) for f in self.crop])
        self.telemetry = self.combo(
            [("Best connection (each second)", "best")]
            + [(f"Away station {n}", f"gs{n}") for n in range(1, 5)]
        )
        vehicles = (
            ["balius"] if self.c.owner.profile.vehicle == "balius" else ["unknown", "sustainer", "booster"]
        )
        self.vehicle = self.combo([(x.title(), x) for x in vehicles])
        for name, widget in [
            ("Scene", self.scene),
            ("Main camera", self.main),
            ("Inset", self.inset),
            ("Mission title", self.title),
            ("Caption", self.caption),
            ("Main crop (Preview before Take)", crop_widget),
            ("Telemetry receiver", self.telemetry),
            ("Qualified vehicle", self.vehicle),
        ]:
            form.addRow(name, widget)
        self.telemetry.currentIndexChanged.connect(self.receiver_changed)
        self.vehicle.currentIndexChanged.connect(self.vehicle_changed)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        self.action("Prepare Preview", self.prepare, buttons)
        self.take_button = self.action("Take → Program", self.c.take, buttons)
        self.action("Open Program", self.c.open_program, buttons)
        self.action("Stop Program", self.c.stop_program, buttons)
        self.action("Emergency slate", self.c.emergency_slate, buttons)
        self.health = QLabel()
        self.health.setWordWrap(True)
        layout.addWidget(self.health)
        note = QLabel(
            "Units follow Settings → Display units (Imperial by default). Camera choice is independent of telemetry receiver. Closing this panel leaves Program running."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

    def prepare(self):
        self.c.stage(self.draft_selection())

    def draft_selection(self):
        return BroadcastSelection(
            self.scene.currentData(),
            self.main.currentData(),
            self.inset.currentData(),
            self.title.text().strip() or "MIT ROCKET TEAM",
            self.caption.text().strip(),
            tuple(f.value() / 100 for f in self.crop),
        )

    def receiver_changed(self):
        self.c.session.telemetry_choice = self.telemetry.currentData()
        self.c.session.last_evaluation = float("-inf")
        self.c.log.write("telemetry.policy", choice=self.telemetry.currentData())

    def vehicle_changed(self):
        self.c.session.set_scope(replace(self.c.session.scope, vehicle=self.vehicle.currentData()))
        self.c.datum_override, self.c.vertical_contract = "unknown", False
        self.c.log.write("vehicle.changed", vehicle=self.vehicle.currentData())
        self.obs_ready.setChecked(False)

    def build_timing(self):
        layout = self.page("Timing and telemetry")
        note = QLabel(
            "All output uses one delayed timeline. Measure each path with a visible flash/time marker and matching telemetry event. Unknown fields stay blank. Changing timing returns Program to a slate and clears the event clock; take the camera again after buffering."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        layout.addLayout(form)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0.034, 10)
        self.delay.setDecimals(3)
        self.delay.setSuffix(" s")
        self.delay.setValue(self.c.session.timing.delay)
        self.calibrated = QCheckBox("Measurements qualified for this run")
        self.audio_qualified = QCheckBox("OBS audio offsets rechecked for this delay")
        self.budget = QSpinBox()
        self.budget.setRange(64, 256)
        self.budget.setValue(256)
        self.budget.setSuffix(" MiB")
        form.addRow("Common delay", self.delay)
        form.addRow("Broadcast working-memory budget", self.budget)
        form.addRow(self.calibrated)
        form.addRow(self.audio_qualified)
        self.paths = QTableWidget(0, 6)
        self.paths.setHorizontalHeaderLabels(
            ["Path", "Delay s", "Jitter s", "Method / evidence", "Date", "Residual ms"]
        )
        self.paths.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.paths)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        self.action("Load current paths", self.load_paths, buttons)
        self.action("Apply timing → slate", self.apply_timing, buttons)
        form = QFormLayout()
        layout.addLayout(form)
        self.datum = self.combo(
            [
                ("Unknown — suppress altitude", "unknown"),
                ("Above launch", "above_launch"),
                ("Mean sea level", "msl"),
                ("WGS84 ellipsoid", "ellipsoid"),
            ]
        )
        self.vertical = QCheckBox("Native scalar velocity is qualified as signed vertical m/s")
        self.basis = QLineEdit()
        self.basis.setPlaceholderText("Source contract / adapter version / measurement evidence")
        self.basis.setMaxLength(300)
        form.addRow("Unknown barometric datum override", self.datum)
        form.addRow(self.vertical)
        form.addRow("Qualification evidence", self.basis)
        self.action(
            "Apply field qualification for this scope",
            lambda: self.c.set_qualification(
                self.datum.currentData(), self.vertical.isChecked(), self.basis.text()
            ),
            layout,
        )
        self.events = QLineEdit(", ".join(self.c.settings.events))
        form.addRow("Timeline labels (up to six, 12 characters each)", self.events)
        self.action("Save timeline labels", self.save_events, layout)

    def load_paths(self):
        names = sorted(set(self.c.session.sources) | {"telemetry:gs" + str(n) for n in range(1, 5)})
        self.paths.setRowCount(len(names))
        for row, name in enumerate(names):
            path = self.c.settings.timing.paths.get(name, Calibration())
            values = [
                name,
                path.delay,
                path.jitter,
                path.method,
                path.measured_at,
                path.residual_ms if path.residual_ms is not None else "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.paths.setItem(row, col, item)
        self.paths.resizeColumnsToContents()

    def apply_timing(self):
        if self.calibrated.isChecked() and not self.audio_qualified.isChecked():
            raise ValueError("Recheck OBS audio offsets before qualifying timing")
        paths = {}
        for row in range(self.paths.rowCount()):
            values = [self.paths.item(row, col).text().strip() for col in range(6)]
            if not values[3] and not values[4] and not values[5]:
                continue
            paths[values[0]] = Calibration(
                float(values[1]), float(values[2]), values[3], values[4], float(values[5])
            )
        if self.calibrated.isChecked() and not paths:
            raise ValueError("Enter measured path timings first")
        self.c.set_timing(
            TimingConfig(
                self.delay.value(), self.calibrated.isChecked(), paths, self.budget.value() * 1024 * 1024
            )
        )
        self.obs_ready.setChecked(False)

    def save_events(self):
        labels = tuple(x.strip().upper() for x in self.events.text().split(","))
        if (
            not 1 <= len(labels) <= 6
            or len(set(labels)) != len(labels)
            or any(not x or len(x) > 12 for x in labels)
        ):
            raise ValueError("Use one to six unique labels of up to 12 characters")
        self.c.settings.events = labels
        self.c.settings.save(self.c.settings_path)

    def build_clock(self):
        layout = self.page("Clock and events")
        label = QLabel(
            "Observed now means the real event happened now; it reaches Program after the common delay. On Program means the event is visible in the delayed frame now. Planned zero always waits for an actual liftoff mark."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        self.clock_status = QLabel()
        layout.addWidget(self.clock_status)
        self.countdown = QSpinBox()
        self.countdown.setRange(0, 86400)
        self.countdown.setValue(60)
        self.countdown.setSuffix(" seconds until planned T0")
        layout.addWidget(self.countdown)
        self.scheduled_time = QLineEdit()
        self.scheduled_time.setPlaceholderText(
            "Scheduled launch: 2026-11-04T14:30:00-05:00 (UTC offset required)"
        )
        layout.addWidget(self.scheduled_time)
        self.action(
            "Use scheduled launch time", lambda: self.c.schedule_utc(self.scheduled_time.text()), layout
        )
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        self.action(
            "Plan countdown", lambda: self.c.clock_action("countdown", self.countdown.value()), buttons
        )
        self.action("Hold", lambda: self.c.clock_action("hold"), buttons)
        self.action("Resume countdown", lambda: self.c.clock_action("resume"), buttons)
        self.event = self.combo([(s, s) for s in dict.fromkeys(("LIFTOFF", *self.c.settings.events))])
        layout.addWidget(self.event)
        self.correction = QCheckBox("Correct an existing event time (logged)")
        layout.addWidget(self.correction)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        self.action(
            "Mark event observed now",
            lambda: self.c.clock_action(
                "observed_now", event=self.event.currentText(), correction=self.correction.isChecked()
            ),
            buttons,
        )
        self.action(
            "Mark event on Program",
            lambda: self.c.clock_action(
                "on_program", event=self.event.currentText(), correction=self.correction.isChecked()
            ),
            buttons,
        )
        self.ledger = QLabel()
        layout.addWidget(self.ledger)
        layout.addStretch()

    def build_obs(self):
        layout = self.page("OBS / YouTube")
        note = QLabel(
            "OBS runs on this computer. In OBS: Tools → WebSocket Server Settings, enable the server and authentication. Use that password below. OBS needs no account; the YouTube stream key is entered only in OBS. Capture the window “Rocket Broadcast — Program”, with cursor and window decorations excluded."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        layout.addLayout(form)
        self.obs_port = QSpinBox()
        self.obs_port.setRange(1024, 65535)
        self.obs_port.setValue(4455)
        self.obs_password = QLineEdit()
        self.obs_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.obs_password.setPlaceholderText("OBS WebSocket password — kept only for this connection")
        form.addRow("Local OBS port", self.obs_port)
        form.addRow("OBS password", self.obs_password)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        self.action("Connect to OBS", self.connect_obs, buttons)
        self.action("Disconnect controls", self.obs.close, buttons)
        self.action("Reset Program to 1920×1080 pixels", self.reset_program_size, buttons)
        self.action("Export OBS presets…", self.export_obs_presets, buttons)
        self.obs_status = QLabel()
        self.obs_status.setWordWrap(True)
        layout.addWidget(self.obs_status)
        self.obs_ready = QCheckBox(
            "I verified the OBS Program capture, output resolution, audio and destination for this run"
        )
        layout.addWidget(self.obs_ready)
        self.obs_ready.stateChanged.connect(self.refresh)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        self.record_start = self.action(
            "Start local recording", lambda: self.obs_command("StartRecord"), buttons
        )
        self.record_stop = self.action("Stop recording", lambda: self.obs_command("StopRecord"), buttons)
        self.stream_start = self.action("Start YouTube broadcast…", self.start_stream, buttons)
        self.stream_stop = self.action("Stop broadcast…", self.stop_stream, buttons)
        label = QLabel(
            "YouTube visibility is unverified here. Check the private/unlisted return in YouTube Studio and on another device. Stopping Program or closing this panel does not stop OBS. If control disconnects, use OBS directly."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch()

    def connect_obs(self):
        self.obs.connect_local(self.obs_port.value(), self.obs_password.text())
        self.obs_password.clear()

    def export_obs_presets(self):
        from .obs_presets import prepare

        directory = QFileDialog.getExistingDirectory(self, "Export OBS presets to a new subfolder")
        if directory:
            target = Path(directory) / ("Rocket-OBS-" + time.strftime("%Y%m%d-%H%M%S"))
            prepare(
                target,
                self.c.owner.resource_root / "resources/broadcast",
                Path.home() / "Movies/Rocket Broadcast",
            )
            QMessageBox.information(
                self,
                "OBS presets exported",
                f"Presets saved to {target}. Import the profile and scene collection in OBS, then map the Program window and local audio. No credentials are included.",
            )

    def reset_program_size(self):
        if not self.c.program_window:
            self.c.open_program()
        self.c.program_window.reset_size()
        self.obs_ready.setChecked(False)

    def obs_command(self, command):
        self.obs.command(command)
        self.c.log.write("obs.command", command=command)

    def start_stream(self):
        if not (self.c.active and self.obs_ready.isChecked()):
            raise ValueError("Open Program and verify OBS capture, audio and destination first")
        answer = QMessageBox.question(
            self,
            "Start YouTube broadcast?",
            "OBS will send to the destination configured in OBS. Confirm the account, visibility and stream key in OBS / YouTube Studio before continuing.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.obs_command("StartStream")

    def stop_stream(self):
        if (
            QMessageBox.question(
                self,
                "Stop broadcast?",
                "Stop sending from OBS? Local recording will continue.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.obs_command("StopStream")

    def refresh(self):
        if not hasattr(self, "obs_status"):
            return
        c, s = self.c, self.c.session
        now = time.monotonic()
        ready, reason = s.readiness(s.preview, now)
        prepared = self.draft_selection() == s.preview
        crop_notice = " · MAIN CAMERA CROPPED" if any(s.preview.main_crop) else ""
        self.preview_status.setText(
            "PREVIEW · "
            + (reason + crop_notice if prepared else "Changes not prepared — click Prepare Preview")
        )
        self.take_button.setEnabled(ready and prepared)
        program = "PROGRAM ERROR" if c.error else "PROGRAM OPEN" if c.active else "PROGRAM STOPPED"
        sending = (
            ("OBS SENDING" if self.obs.state.get("streaming") else "OBS NOT SENDING")
            if self.obs.fresh()
            else "OBS STATE UNKNOWN"
        )
        self.overview.setText(f"{program}  •  {sending}  •  YouTube visibility unverified")
        receiver = next(
            (
                (p.get("info") or {}).get("station", "").upper()
                for p in s.peers
                if (p.get("info") or {}).get("node_id") == s.node
            ),
            "REPLAY" if s.node == "replay" else "unavailable",
        )
        self.health.setText(
            f"Render {c.render_ms:.1f} ms · Frames {c.rendered:,} · Missed slots {c.skipped:,} · "
            f"Buffer {s.frames.bytes / 1048576:.1f} MiB · Broadcast frame drops {s.frames.dropped:,} · "
            f"Delay {s.timing.delay:.3f} s · Telemetry {receiver}"
            + (f" · {c.log.error}" if c.log.error else "")
        )
        catalog = tuple((key, value.label) for key, value in sorted(s.sources.items()))
        if catalog != self._catalog:
            self._catalog = catalog
            for combo, initial in [
                (self.main, [("Choose a camera", None)]),
                (self.inset, [("Off", None), ("Rocket orientation", "orientation")]),
            ]:
                selected = combo.currentData()
                selected_label = combo.currentText().removesuffix(" · unavailable")
                combo.blockSignals(True)
                combo.clear()
                for label, value in initial + [(label, key) for key, label in catalog]:
                    combo.addItem(label, value)
                index = combo.findData(selected)
                if index < 0:
                    # A transient catalog loss must not discard the operator's
                    # prepared shot or crop. Readiness still requires live frames.
                    combo.addItem(selected_label + " · unavailable", selected)
                    index = combo.count() - 1
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
        target = s.pause_time if s.paused and s.pause_time is not None else now - s.timing.delay
        elapsed, state, basis = s.clock.at(target)
        from .broadcast_renderer import mission_clock

        self.clock_status.setText(mission_clock(elapsed, state) + " · " + basis)
        self.ledger.setText(
            "\n".join(f"{name}: {evidence}" for name, (_, evidence, _) in s.events.events.items())
        )
        event_choices = tuple(dict.fromkeys(("LIFTOFF", *c.settings.events)))
        if tuple(self.event.itemText(i) for i in range(self.event.count())) != event_choices:
            self.event.clear()
            self.event.addItems(event_choices)
        status = self.obs.state
        fresh = self.obs.fresh()
        obs_health = (
            fresh,
            status.get("streaming") if fresh else None,
            status.get("recording") if fresh else None,
            status.get("recording_paused") if fresh else None,
            status.get("reconnecting") if fresh else None,
        )
        if obs_health != getattr(self, "_last_obs_health", None):
            self._last_obs_health = obs_health
            c.log.write(
                "obs.status",
                known=obs_health[0],
                streaming=obs_health[1],
                recording=obs_health[2],
                recording_paused=obs_health[3],
                reconnecting=obs_health[4],
            )
        self.obs_status.setText(
            status.get("message", "Unknown")
            + (
                f"\nScene: {status.get('scene')} · {'RECONNECTING' if status.get('reconnecting') else 'Sending' if status.get('streaming') else 'Stream stopped'}"
                f"\n{status.get('bitrate_kbps', 0):,.0f} kb/s · {status.get('fps', 0):.1f} fps · Dropped {status.get('dropped_frames', 0)}"
                f" · Render lag {status.get('render_lag', 0)} · Encode lag {status.get('encode_lag', 0)}"
                f"\nRecording: {'PAUSED' if status.get('recording_paused') else 'ACTIVE' if status.get('recording') else 'STOPPED'}"
                f"\n{status.get('recording_path', '')}"
                if fresh
                else "\nOutput state unknown; check OBS directly."
            )
        )
        verified = c.active and not c.error and self.obs_ready.isChecked()
        if c.program_window:
            window = c.program_window
            ratio = window.devicePixelRatioF()
            if (round(window.width() * ratio), round(window.height() * ratio)) != (1920, 1080):
                verified = False
                self.obs_ready.blockSignals(True)
                self.obs_ready.setChecked(False)
                self.obs_ready.blockSignals(False)
                self.message.setText(
                    "Program display scale changed: reset output size and recheck OBS capture"
                )
        self.stream_start.setEnabled(fresh and verified and not status.get("streaming"))
        self.record_start.setEnabled(fresh and verified and not status.get("recording"))
        self.stream_stop.setEnabled(fresh and bool(status.get("streaming")))
        self.record_stop.setEnabled(fresh and bool(status.get("recording")))

    def closeEvent(self, event):
        self.c.preview_active = False
        self.c.sync_leases()
        event.accept()
