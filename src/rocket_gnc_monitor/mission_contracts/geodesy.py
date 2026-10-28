"""Explicit-height WGS84 primitives; no default geoid offset and no terrain lookup."""

import math


def ellipsoidal_height(msl, separation):
    if not all(math.isfinite(x) for x in (msl, separation, msl + separation)):
        raise ValueError("Finite height and explicit geoid separation required")
    return msl + separation


def ecef(latitude, longitude, height):
    if (
        not all(math.isfinite(x) for x in (latitude, longitude, height))
        or abs(latitude) > 90
        or abs(longitude) > 180
    ):
        raise ValueError("Invalid WGS84 location")
    lat, lon = math.radians(latitude), math.radians(longitude)
    f = 1 / 298.257223563
    e2 = f * (2 - f)
    n = 6378137 / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return (
        (n + height) * math.cos(lat) * math.cos(lon),
        (n + height) * math.cos(lat) * math.sin(lon),
        (n * (1 - e2) + height) * math.sin(lat),
    )
