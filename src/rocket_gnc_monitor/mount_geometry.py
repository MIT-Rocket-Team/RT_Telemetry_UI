"""Photo-informed antenna geometry, compiled once in fixed, turntable and beam frames."""

from functools import lru_cache
import math
import numpy as np
from .scene_mesh import Mesh


@lru_cache(maxsize=1)
def mount_mesh():
    def identity(v):
        return np.asarray(v, dtype=float)

    def turn(v):
        return identity(v)

    def carrier(v):
        return identity(v)

    def avenger(v):
        return identity(v) + [-1.03, 0, 0.16]

    groups = {identity: 0, turn: 1, carrier: 2, avenger: 2}
    unit = lambda a: a / max(np.linalg.norm(a), 1e-10)
    faces = []

    def face(vertices, material, group):
        faces.append((np.asarray(vertices), material, group))

    palette = {
        "timber": "#b6ab80",
        "edge": "#87794f",
        "frame": "#84988f",
        "metal": "#b7c5ca",
        "grid": "#d1dce0",
        "dark": "#303c4b",
        "cap": "#202a35",
        "gold": "#b19c62",
    }

    def box(c, s, material, transform=identity):
        vertices = [
            transform(np.asarray(c) + np.asarray(v) * np.asarray(s) / 2)
            for v in [
                (-1, -1, -1),
                (1, -1, -1),
                (1, 1, -1),
                (-1, 1, -1),
                (-1, -1, 1),
                (1, -1, 1),
                (1, 1, 1),
                (-1, 1, 1),
            ]
        ]
        for ids in [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (3, 2, 6, 7), (0, 3, 7, 4), (1, 5, 6, 2)]:
            face([vertices[i] for i in ids], material, groups[transform])

    def rod(a, b, radius, material, transform=identity, segments=5):
        a, b = np.asarray(a), np.asarray(b)
        direction = unit(b - a)
        v = unit(np.cross(direction, [1, 0, 0] if abs(direction[1]) > 0.9 else [0, 1, 0]))
        vv = np.cross(direction, v)
        ends = [
            [
                transform(
                    point
                    + radius
                    * (math.cos(i * 2 * math.pi / segments) * v + math.sin(i * 2 * math.pi / segments) * vv)
                )
                for i in range(segments)
            ]
            for point in [a, b]
        ]
        face(ends[0], material, groups[transform])
        face(ends[1][::-1], material, groups[transform])
        for i in range(segments):
            j = (i + 1) % segments
            face([ends[0][i], ends[0][j], ends[1][j], ends[1][i]], material, groups[transform])

    for x in (-1, 1):
        for z in (-1, 1):
            foot = [1.22 * x, 0.34, 1.08 * z]
            rod([foot[0], 0, foot[2]], foot, 0.035, "cap")
            box(foot, [0.30, 0.095, 0.24], "timber")
            rod(foot, [0.43 * x, 2.34, 0.35 * z], 0.12, "timber", segments=4)
    box([0, 2.35, 0], [1.45, 0.085, 1.20], "edge")
    rod([0, 2.40, 0], [0, 2.47, 0], 0.40, "cap", segments=18)
    box([0, 2.49, 0], [0.96, 0.065, 0.91], "frame", turn)
    for x in (-0.40, 0.40):
        box([x, 2.96, 0], [0.04, 0.88, 0.55], "frame", turn)
    rod([-0.49, 3.28, 0], [0.49, 3.28, 0], 0.105, "metal", turn, 10)
    # Shared beam and antenna axes pass through the support bearings (y=0, z=0).
    box([0, 0, 0], [2.65, 0.10, 0.10], "metal", carrier)
    for x in (-0.40, 0.40):
        rod([x - 0.025, 3.28, 0], [x + 0.025, 3.28, 0], 0.16, "cap", turn, 16)
        rod([x - 0.035, 3.28, 0], [x + 0.035, 3.28, 0], 0.10, "metal", turn, 12)
        rod([x, 0, 0], [x, 0, 0.36], 0.035, "metal", carrier)
    grid = lambda x, y: [x - 0.03, y, 0.27 + 0.18 * (x / 0.59) ** 2 + 0.12 * (y / 0.56) ** 2]
    for x in np.linspace(-0.59, 0.59, 19):
        for y in np.linspace(-0.56, 0.56, 6)[:-1]:
            rod(grid(x, y), grid(x, y + 1.12 / 5), 0.009, "grid", carrier, 3)
    for y in (-0.56, 0, 0.56):
        for x in np.linspace(-0.59, 0.59, 9)[:-1]:
            rod(grid(x, y), grid(x + 1.18 / 8, y), 0.012, "grid", carrier, 3)
    # The dish-colored feed arm and dashed boresight share the carrier's +Z axis.
    feed_tip = [0, 0, 1.04]
    rod([0, 0, 0.27], feed_tip, 0.03, "grid", carrier)
    box([1.03, 0, 0], [0.18, 0.14, 0.24], "dark", carrier)
    rod([1.03, 0, -0.35], [1.03, 0, 3.55], 0.025, "metal", carrier, 4)
    for k in range(21):
        z, half = -0.12 + k * 0.17, 0.34 - k * 0.003
        rod([1.03 - half, 0, z], [1.03 + half, 0, z], 0.01, "metal", carrier, 3)
        rod([1.03, -half * 0.74, z], [1.03, half * 0.74, z], 0.009, "metal", carrier, 3)
    rings = [
        [
            avenger([radius * math.cos(i * math.pi / 3), radius * math.sin(i * math.pi / 3), z])
            for i in range(6)
        ]
        for z, radius in [(-0.12, 0.285), (-0.075, 0.285), (0.96, 0.185), (1.42, 0.13)]
    ]
    face(rings[0], "cap", 2)
    face(rings[-1], "cap", 2)
    for a, b in zip(rings, rings[1:]):
        for i in range(6):
            j = (i + 1) % 6
            face([a[i], a[j], b[j], b[i]], "dark", 2)
    box([-1.03, 0, 0.03], [0.18, 0.14, 0.24], "metal", carrier)
    rod([0, 0.15, -0.125], [0, 0.15, -0.285], 0.035, "gold", avenger)
    return Mesh(faces, palette)
