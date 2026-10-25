"""Away-station Wi-Fi selection, separate from future station data transport."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .wifi import WifiSelection, WifiSnapshot, read_wifi_status, wifi_settings_url
from .widgets import ComboBox


class WifiPanel(QFrame):
    def __init__(self, data_dir, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setAccessibleName("Away station Wi-Fi network")
        self.selection_path = Path(data_dir) / "station-wifi.json"
        self.selection = WifiSelection()
        self.snapshot = WifiSnapshot()
        self.error = ""
        try:
            self.selection = WifiSelection.load(self.selection_path)
        except ValueError as exc:
            self.error = str(exc)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wifi-status")
        self._future = None
        self._closed = False
        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._finish_refresh)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)
        heading = QLabel("AWAY STATION WI-FI")
        heading.setObjectName("eyebrow")
        layout.addWidget(heading)
        row = QHBoxLayout()
        row.setSpacing(7)
        self.ssid = ComboBox()
        self.ssid.setEditable(True)
        self.ssid.setInsertPolicy(ComboBox.InsertPolicy.NoInsert)
        self.ssid.setMinimumWidth(160)
        self.ssid.lineEdit().setPlaceholderText("Saved network or enter SSID")
        self.ssid.setAccessibleName("Desired away station Wi-Fi SSID")
        self.ssid.setToolTip("Saved names are not a scan of networks in range. Blank clears the selection.")
        self.ssid.setEditText(self.selection.ssid)
        row.addWidget(self.ssid, 1)
        self.select_button = QPushButton("Select Wi-Fi")
        self.select_button.clicked.connect(self.select_network)
        row.addWidget(self.select_button)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Read the current Wi-Fi name and saved networks from the operating system")
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)
        self.settings_button = QPushButton("Open Wi-Fi settings")
        self.settings_button.clicked.connect(self.open_settings)
        self.settings_button.setEnabled(bool(wifi_settings_url()))
        row.addWidget(self.settings_button)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        self.status.setAccessibleName("Selected and operating-system Wi-Fi status")
        layout.addWidget(self.status)
        note = QLabel("Join the selected WLAN in system Wi-Fi settings. Station data transfer is not configured yet.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        self._render_status()
        self.refresh()

    def select_network(self):
        try:
            selection = WifiSelection(self.ssid.currentText()).validate()
            selection.save(self.selection_path)
            self.selection = selection
            self.error = ""
        except (ValueError, OSError) as exc:
            self.error = str(exc)
        self._render_status()

    def open_settings(self):
        url = wifi_settings_url()
        if not url or not QDesktopServices.openUrl(QUrl(url)):
            self.error = "Could not open Wi-Fi settings. Open them from the operating system."
            self._render_status()

    def refresh(self):
        if self._closed or self._future is not None:
            return
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Checking…")
        self._future = self._executor.submit(read_wifi_status)
        self._poll.start()

    def _finish_refresh(self):
        if self._closed or self._future is None or not self._future.done():
            return
        self._poll.stop()
        try:
            self.snapshot = self._future.result()
        except Exception:
            self.snapshot = WifiSnapshot(detail="Wi-Fi status is unavailable. Check system Wi-Fi settings.")
        self._future = None
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Refresh")
        draft = self.ssid.currentText()
        self.ssid.blockSignals(True)
        self.ssid.clear()
        self.ssid.addItem("")
        names = dict.fromkeys((self.selection.ssid, *self.snapshot.current_ssids, *self.snapshot.saved_ssids))
        for name in names:
            if name:
                self.ssid.addItem(name)
        self.ssid.setEditText(draft)
        self.ssid.blockSignals(False)
        self._render_status()

    def _render_status(self):
        desired = self.selection.ssid or "None"
        actual = ", ".join(self.snapshot.current_ssids) or "Not reported"
        matching = self.selection.ssid and self.selection.ssid in self.snapshot.current_ssids
        state = " · Matches selection" if matching else ""
        details = [f"Selected WLAN: {desired} · OS Wi-Fi: {actual}{state}"]
        if self.snapshot.detail:
            details.append(self.snapshot.detail)
        if self.error:
            details.append(self.error)
        self.status.setText("\n".join(details))

    def cleanup(self):
        if self._closed:
            return
        self._closed = True
        self._poll.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)
