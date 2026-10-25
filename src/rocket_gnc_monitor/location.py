"""Offline WGS84 launch-site entry using MGRS and Google Open Location Codes."""

from dataclasses import dataclass
import math
import re
import mgrs
import numpy as np
from openlocationcode import openlocationcode as olc
from .domain import finite, to_enu


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float
    code: str = ""
    note: str = "WGS84 latitude / longitude"


def coordinates(latitude, longitude):
    if not finite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between −90 and 90 degrees.")
    if not finite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between −180 and 180 degrees.")
    return Location(float(latitude), float(longitude))


def decode_mgrs(text):
    code = re.sub(r"\s+", "", text).upper()
    match = re.fullmatch(r"(?:\d{1,2}[C-HJ-NP-X]|[ABYZ])[A-HJ-NP-Z]{2}(\d{0,10})", code)
    if not match or len(match[1]) % 2:
        raise ValueError("Enter an MGRS grid reference with equally many easting and northing digits.")
    try:
        lat, lon = mgrs.MGRS().toLatLon(code)
    except mgrs.core.MGRSError as exc:
        raise ValueError("Invalid MGRS zone, grid square, or coordinates.") from exc
    coordinates(lat, lon)
    resolution = 10 ** (5 - len(match[1]) // 2)
    return Location(lat, lon, code, f"MGRS grid reference point · {resolution:,} m grid resolution")


def encode_mgrs(latitude, longitude):
    coordinates(latitude, longitude)
    return mgrs.MGRS().toMGRS(latitude, longitude, MGRSPrecision=5)


def decode_plus_code(text, reference=None):
    code = text.strip().upper()
    if olc.isShort(code):
        if reference is None:
            raise ValueError(
                "A short Plus Code needs a nearby reference latitude and longitude, or use its full code."
            )
        origin = coordinates(*reference)
        code = olc.recoverNearest(code, origin.latitude, origin.longitude)
    if not olc.isFull(code):
        raise ValueError("Enter a full Plus Code, such as 87JC9W64+4C. Short codes need a nearby reference.")
    area = olc.decode(code)
    return Location(area.latitudeCenter, area.longitudeCenter, code, "Plus Code area center · WGS84")


def encode_plus_code(latitude, longitude):
    coordinates(latitude, longitude)
    return olc.encode(latitude, longitude, codeLength=11)


def pointer_from_launch(launch, heading, distance, height_difference):
    """Place a nearby antenna using the launch vector in the antenna's ENU frame.

    Heading points from antenna to pad, clockwise from true north. Distance is
    horizontal (not slant range); height difference is antenna minus launch
    ellipsoid altitude. Solve WGS84 coordinates so the virtual pointer sees that
    exact horizontal vector, including across the longitude seam.
    """
    coordinates(*launch[:2])
    if not all(finite(v) for v in (*launch, heading, distance, height_difference)):
        raise ValueError("Relative antenna coordinates must be finite")
    if not 0 <= heading <= 360 or not 0 <= distance <= 100_000:
        raise ValueError("Use a heading from 0 to 360° and a horizontal distance from 0 to 100,000 m")
    altitude = launch[2] + height_difference
    if distance == 0:
        return coordinates(*launch[:2]), altitude
    angle = math.radians(heading)
    target = np.array([distance * math.sin(angle), distance * math.cos(angle)])
    lat, lon = launch[:2]
    # Work in metres of tangent displacement to keep the Jacobian well scaled.
    for _ in range(30):
        origin = (lat, lon, altitude)
        residual = np.asarray(to_enu(*launch, origin)[:2]) - target
        if np.linalg.norm(residual) < 1e-5:
            return coordinates(lat, lon), altitude
        cos_lat = math.cos(math.radians(lat))
        if abs(cos_lat) < 1e-8:
            break
        dlat, dlon = 1 / 111_320, 1 / (111_320 * cos_lat)
        jacobian = np.column_stack([
            np.asarray(to_enu(*launch, (lat + dlat, lon, altitude))[:2]) - residual - target,
            np.asarray(to_enu(*launch, (lat, lon + dlon, altitude))[:2]) - residual - target,
        ])
        try:
            step = np.linalg.solve(jacobian, residual)
        except np.linalg.LinAlgError:
            break
        lat -= step[0] * dlat
        lon = (lon - step[1] * dlon + 180) % 360 - 180
        if not -90 < lat < 90:
            break
    raise ValueError("Cannot resolve this relative offset; enter antenna latitude/longitude or MGRS instead")
