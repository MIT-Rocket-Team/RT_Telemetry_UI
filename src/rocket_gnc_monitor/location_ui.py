"""Launch and antenna location entry with offline coordinate conversion."""

import math
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QStackedWidget,
    QDoubleSpinBox,
    QLineEdit,
    QLabel,
    QSizePolicy,
)
from openlocationcode import openlocationcode as olc
from .location import (
    Location, coordinates, decode_mgrs, decode_plus_code, encode_mgrs, encode_plus_code, pointer_from_launch,
)
from .domain import to_enu
from .site_presets import URRG_LAUNCH_MGRS, URRG_STATION_MGRS
from .widgets import ComboBox as QComboBox


class LaunchLocation(QWidget):
    preset_selected = Signal()

    def __init__(self, mission, parent=None):
        super().__init__(parent)
        self._location = coordinates(mission.latitude, mission.longitude)
        self._editing = False
        self._filling = True
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.preset = QComboBox()
        self.preset.addItem("Custom launch site", "")
        self.preset.addItem(f"URRG · {URRG_LAUNCH_MGRS}", "URRG")
        self.preset.setAccessibleName("Launch site preset")
        self.preset.setCurrentIndex(max(0, self.preset.findData(mission.launch_site_name)))
        root.addWidget(self.preset)
        note = QLabel("Site presets set coordinates only. Set launch elevations separately below.")
        note.setWordWrap(True)
        root.addWidget(note)
        self.format = QComboBox()
        for title, key in [("Latitude / longitude", "latlon"), ("MGRS", "mgrs"), ("Plus Code", "pluscode")]:
            self.format.addItem(title, key)
        self.format.setAccessibleName("Launch location coordinate format")
        root.addWidget(self.format)
        self.pages = QStackedWidget()
        root.addWidget(self.pages)
        page = QWidget()
        form = QFormLayout(page)
        self.latitude = self.number(-90, 90, "Launch latitude in degrees")
        self.longitude = self.number(-180, 180, "Launch longitude in degrees")
        form.addRow("Latitude (°)", self.latitude)
        form.addRow("Longitude (°)", self.longitude)
        self.pages.addWidget(page)
        page = QWidget()
        form = QFormLayout(page)
        self.mgrs = QLineEdit()
        self.mgrs.setPlaceholderText("e.g. 19T CG 27554 91778")
        self.mgrs.setAccessibleName("Launch MGRS coordinate")
        self.mgrs.setMaxLength(80)
        form.addRow("MGRS", self.mgrs)
        self.pages.addWidget(page)
        page = QWidget()
        form = QFormLayout(page)
        self.plus_code = QLineEdit()
        self.plus_code.setPlaceholderText("e.g. 87JC9W64+4C")
        self.plus_code.setAccessibleName("Launch Plus Code")
        self.plus_code.setMaxLength(80)
        form.addRow("Plus Code", self.plus_code)
        self.reference = QWidget()
        ref_form = QFormLayout(self.reference)
        ref_form.setContentsMargins(0, 0, 0, 0)
        self.reference_lat = QLineEdit(str(mission.latitude) if mission.site_configured else "")
        self.reference_lon = QLineEdit(str(mission.longitude) if mission.site_configured else "")
        self.reference_lat.setPlaceholderText("Nearby latitude, decimal degrees")
        self.reference_lon.setPlaceholderText("Nearby longitude, decimal degrees")
        ref_form.addRow("Nearby latitude (°)", self.reference_lat)
        ref_form.addRow("Nearby longitude (°)", self.reference_lon)
        form.addRow(self.reference)
        help_text = QLabel("Full codes work directly. A short code needs a nearby reference location.")
        help_text.setWordWrap(True)
        form.addRow(help_text)
        self.pages.addWidget(page)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setAccessibleName("Resolved launch latitude and longitude")
        root.addWidget(self.preview)
        for field in (self.latitude, self.longitude):
            field.valueChanged.connect(self.edited)
        for field in (self.mgrs, self.plus_code, self.reference_lat, self.reference_lon):
            field.textChanged.connect(self.edited)
        self.format.currentIndexChanged.connect(self.change_format)
        self.format.setCurrentIndex(max(0, self.format.findData(mission.launch_location_format)))
        self.change_format()
        if mission.launch_location_code and self.format.currentData() != "latlon":
            # Canonical saved latitude/longitude is authoritative; code is entry provenance.
            field = self.mgrs if self.format.currentData() == "mgrs" else self.plus_code
            self._filling = True
            field.setText(mission.launch_location_code)
            self._location = Location(mission.latitude, mission.longitude, mission.launch_location_code)
            self._filling = False
        self.update_preview()
        self.preset.currentIndexChanged.connect(self.choose_preset)

    def choose_preset(self):
        if self.preset.currentData() != "URRG":
            return
        self._location = decode_mgrs(URRG_LAUNCH_MGRS)
        self.format.setCurrentIndex(self.format.findData("mgrs"))
        self.change_format()
        self._filling = True
        self.mgrs.setText(URRG_LAUNCH_MGRS)
        self._location = decode_mgrs(URRG_LAUNCH_MGRS)
        self._editing = False
        self._filling = False
        self.update_preview()
        self.preset_selected.emit()

    def number(self, low, high, name):
        field = QDoubleSpinBox()
        field.setDecimals(7)
        field.setRange(low, high)
        field.setAccessibleName(name)
        return field

    def change_format(self):
        self._filling = True
        self.pages.setCurrentIndex(self.format.currentIndex())
        lat, lon = self._location.latitude, self._location.longitude
        self.latitude.setValue(lat)
        self.longitude.setValue(lon)
        kind = self.format.currentData()
        code = (
            encode_mgrs(lat, lon)
            if kind == "mgrs"
            else encode_plus_code(lat, lon)
            if kind == "pluscode"
            else ""
        )
        if kind == "mgrs":
            if self.preset.currentData() == "URRG":
                code = URRG_LAUNCH_MGRS
            self.mgrs.setText(code)
        elif kind == "pluscode":
            self.plus_code.setText(code)
        self._location = Location(lat, lon, code)
        self._editing = False
        self._filling = False
        self.update_preview()

    def edited(self):
        if not self._filling:
            self.preset.setCurrentIndex(0)
            self._editing = True
            self.update_preview()

    def value(self):
        if not self._editing:
            return self._location
        kind = self.format.currentData()
        if kind == "latlon":
            return coordinates(self.latitude.value(), self.longitude.value())
        if kind == "mgrs":
            return decode_mgrs(self.mgrs.text())
        reference = None
        if olc.isShort(self.plus_code.text().strip().upper()):
            try:
                reference = (float(self.reference_lat.text()), float(self.reference_lon.text()))
            except ValueError as exc:
                raise ValueError(
                    "Enter a nearby reference latitude and longitude for this short Plus Code."
                ) from exc
        return decode_plus_code(self.plus_code.text(), reference)

    def update_preview(self):
        self.reference.setVisible(olc.isShort(self.plus_code.text().strip().upper()))
        try:
            value = self.value()
        except ValueError as exc:
            self.preview.setText(str(exc))
            return
        self._location = value
        self.preview.setText(f"Launch site: {value.latitude:.7f}°, {value.longitude:.7f}°\n{value.note}")


class LocationPages(QStackedWidget):
    def sizeHint(self):
        return self.currentWidget().sizeHint()

    def minimumSizeHint(self):
        return self.currentWidget().minimumSizeHint()


class PointerLocation(QWidget):
    """Antenna entry; relative offsets follow the current mission launch origin."""

    preset_selected = Signal()

    def __init__(self, mission, launch_origin, parent=None, *, station=None):
        super().__init__(parent)
        self.launch_origin = launch_origin
        self._location = coordinates(mission.pointer_latitude, mission.pointer_longitude)
        self._kind = mission.pointer_location_format
        self._has_location = mission.pointer_site_configured
        self._editing = False
        self._filling = True
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.preset = QComboBox()
        self.preset.setAccessibleName("Antenna station location preset")
        self.preset.addItem("Custom station location", "")
        for key, code in URRG_STATION_MGRS.items():
            name = "Launch station" if key == "base" else f"Away {key[-1]}"
            current = " · this station" if key == station else ""
            self.preset.addItem(f"URRG {name} · {code}{current}", f"URRG:{key}")
        self.preset.setCurrentIndex(max(0, self.preset.findData(mission.pointer_site_name)))
        root.addWidget(self.preset)
        self.preset_note = QLabel("URRG station presets set coordinates only; set antenna altitude below.")
        self.preset_note.setWordWrap(True)
        root.addWidget(self.preset_note)
        self.set_urrg_available(mission.launch_site_name == "URRG")
        self.format = QComboBox()
        for title, key in [("Latitude / longitude", "latlon"), ("MGRS", "mgrs"), ("Relative to launch", "relative")]:
            self.format.addItem(title, key)
        self.format.setAccessibleName("Antenna location coordinate format")
        self.format.setCurrentIndex(self.format.findData(self._kind))
        root.addWidget(self.format)
        self.pages = LocationPages()
        self.pages.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        root.addWidget(self.pages)
        page = QWidget()
        form = QFormLayout(page)
        self.latitude = self.number(-90, 90, 7, mission.pointer_latitude)
        self.longitude = self.number(-180, 180, 7, mission.pointer_longitude)
        form.addRow("Latitude (°)", self.latitude)
        form.addRow("Longitude (°)", self.longitude)
        self.pages.addWidget(page)
        page = QWidget()
        form = QFormLayout(page)
        self.mgrs = QLineEdit(mission.pointer_location_code or encode_mgrs(
            mission.pointer_latitude, mission.pointer_longitude
        ))
        self.mgrs.setMaxLength(80)
        self.mgrs.setPlaceholderText("e.g. 18T UN 20615 30290")
        self.mgrs.setAccessibleName("Antenna MGRS coordinate")
        form.addRow("MGRS", self.mgrs)
        self.pages.addWidget(page)
        page = QWidget()
        form = QFormLayout(page)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.heading = self.number(0, 360, 3, mission.pointer_launch_heading)
        self.distance = self.number(0, 100_000, 2, mission.pointer_launch_distance)
        self.height_difference = self.number(-10000, 10000, 2, mission.pointer_height_difference)
        form.addRow("Heading: antenna → launch pad (° true)", self.heading)
        form.addRow("Horizontal distance to launch pad (m)", self.distance)
        form.addRow("Antenna altitude − launch altitude (m)", self.height_difference)
        note = QLabel("0° points north, 90° east. Positive height means the antenna is above the launch site. The rocket position here is its launch-pad position.")
        note.setWordWrap(True)
        form.addRow(note)
        self.pages.addWidget(page)
        self.absolute_height = QWidget()
        form = QFormLayout(self.absolute_height)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.altitude = self.number(-10000, 20000, 2, mission.pointer_altitude)
        form.addRow("Antenna altitude, WGS84 ellipsoid (m)", self.altitude)
        root.addWidget(self.absolute_height)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setAccessibleName("Resolved antenna coordinates and altitude")
        root.addWidget(self.preview)
        for field in (self.latitude, self.longitude):
            field.valueChanged.connect(self.edited)
        self.mgrs.textChanged.connect(self.edited)
        for field in (self.altitude, self.heading, self.distance, self.height_difference):
            field.valueChanged.connect(self.update_preview)
        self.format.currentIndexChanged.connect(self.change_format)
        if mission.pointer_location_code and self._kind == "mgrs":
            self._location = Location(mission.pointer_latitude, mission.pointer_longitude, mission.pointer_location_code)
        self._filling = False
        self.show_page()
        self.update_preview()
        self.preset.currentIndexChanged.connect(self.choose_preset)

    def set_urrg_available(self, available):
        self.preset.setEnabled(available)
        self.preset_note.setText(
            "URRG station presets set coordinates only; set antenna altitude below."
            if available else "Select URRG as the launch site to use the station presets."
        )
        if not available:
            # Removing a preset label must never relocate the antenna.
            self.preset.setCurrentIndex(0)

    def choose_preset(self):
        key = self.preset.currentData()
        if self._filling or not key or not self.preset.isEnabled():
            return
        try:
            _, altitude = self.value()
        except ValueError:
            altitude = self.altitude.value()
        point = decode_mgrs(URRG_STATION_MGRS[key.removeprefix("URRG:")])
        self._filling = True
        self._kind = "mgrs"
        blocked = self.format.blockSignals(True)
        self.format.setCurrentIndex(self.format.findData(self._kind))
        self.format.blockSignals(blocked)
        self.latitude.setValue(point.latitude)
        self.longitude.setValue(point.longitude)
        self.mgrs.setText(point.code)
        self.altitude.setValue(altitude)
        self._location = point
        self._editing = False
        self._has_location = True
        self._filling = False
        self.show_page()
        self.update_preview()
        self.preset_selected.emit()

    @staticmethod
    def number(low, high, decimals, value):
        field = QDoubleSpinBox()
        field.setDecimals(decimals)
        field.setRange(low, high)
        field.setValue(value)
        return field

    def value(self):
        if self._kind == "relative":
            return pointer_from_launch(
                self.launch_origin(), self.heading.value(), self.distance.value(), self.height_difference.value()
            )
        point = self._location
        if self._editing:
            point = (decode_mgrs(self.mgrs.text()) if self._kind == "mgrs"
                     else coordinates(self.latitude.value(), self.longitude.value()))
        return point, self.altitude.value()

    def edited(self):
        if not self._filling:
            self.preset.setCurrentIndex(0)
            self._editing = True
            self._has_location = True
            self.update_preview()

    def show_page(self):
        self.pages.setCurrentIndex(self.format.currentIndex())
        self.pages.updateGeometry()
        self.absolute_height.setVisible(self._kind != "relative")

    def change_format(self):
        try:
            point, altitude = self.value()
        except ValueError:
            point, altitude = self._location, self.altitude.value()
        self._filling = True
        self._kind = self.format.currentData()
        if self._kind == "relative":
            self.preset.setCurrentIndex(0)
        self.latitude.setValue(point.latitude)
        self.longitude.setValue(point.longitude)
        self.altitude.setValue(altitude)
        code = encode_mgrs(point.latitude, point.longitude) if self._kind == "mgrs" else ""
        if self._kind == "mgrs" and self.preset.currentData():
            # Preserve the supplied grid reference across display-format changes.
            # Re-encoding a point on a grid boundary can round into its neighbor.
            code = URRG_STATION_MGRS[self.preset.currentData().removeprefix("URRG:")]
        self.mgrs.setText(code)
        if self._kind == "relative" and self._has_location:
            try:
                launch = self.launch_origin()
            except ValueError:
                pass  # Show the missing-origin explanation until the launch site is set.
            else:
                east, north, _ = to_enu(*launch, (point.latitude, point.longitude, altitude))
                if math.hypot(east, north) <= 100_000 and abs(altitude - launch[2]) <= 10000:
                    self.heading.setValue(math.degrees(math.atan2(east, north)) % 360)
                    self.distance.setValue(math.hypot(east, north))
                    self.height_difference.setValue(altitude - launch[2])
        self._location = Location(point.latitude, point.longitude, code)
        self._editing = False
        self._filling = False
        self.show_page()
        self.update_preview()

    def update_preview(self):
        if self._filling:
            return
        try:
            point, altitude = self.value()
        except ValueError as exc:
            self.preview.setText(str(exc))
            return
        self._location = point
        self.preview.setText(
            f"Antenna: {point.latitude:.7f}°, {point.longitude:.7f}°\n"
            f"{altitude:.2f} m ellipsoid · {point.note}"
        )

    def apply(self, mission):
        point, altitude = self.value()
        mission.pointer_latitude, mission.pointer_longitude = point.latitude, point.longitude
        mission.pointer_altitude = altitude
        mission.pointer_location_format = self._kind
        mission.pointer_location_code = point.code
        mission.pointer_site_name = self.preset.currentData()
        mission.pointer_launch_heading = self.heading.value()
        mission.pointer_launch_distance = self.distance.value()
        mission.pointer_height_difference = self.height_difference.value()
