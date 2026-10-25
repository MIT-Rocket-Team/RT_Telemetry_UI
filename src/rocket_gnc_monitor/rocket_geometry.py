"""Illustrative rocket, ignition plume and recovery canopy; body +Z points noseward."""

from functools import lru_cache
import math
import numpy as np
from .scene_mesh import Mesh


@lru_cache(maxsize=16)
def rocket_mesh(canards=4):
    faces = []

    def face(vertices, material):
        faces.append((np.asarray(vertices, float), material, 0))

    def ring(radius, z):
        return np.array(
            [
                [radius * math.cos(i * math.tau / 24), radius * math.sin(i * math.tau / 24), z]
                for i in range(24)
            ]
        )

    lower, upper = ring(0.065, -0.4), ring(0.065, 0.28)
    for i in range(24):
        j = (i + 1) % 24
        face([lower[i], lower[j], upper[j], upper[i]], "stripe" if i < 4 else "body")
        face([upper[i], upper[j], [0, 0, 0.55]], "nose")
    face(lower[::-1], "nozzle")
    for count, z, span, chord in [(4, -0.4, 0.23, 0.25), (max(0, min(16, canards)), 0.16, 0.14, 0.1)]:
        for i in range(count):
            a = i * math.tau / count
            direction = np.array([math.cos(a), math.sin(a), 0])
            face(
                [
                    direction * 0.06 + [0, 0, z + chord],
                    direction * span + [0, 0, z - 0.03],
                    direction * 0.06 + [0, 0, z],
                ],
                "fin" if i % 2 else "stripe",
            )
    return Mesh(
        faces, dict(body="#e2eaf1", nose="#596f86", stripe="#f06c53", fin="#509bf0", nozzle="#182635")
    )


@lru_cache(maxsize=1)
def plume_mesh():
    faces = []
    for i in range(16):
        a, b = i * math.tau / 16, (i + 1) * math.tau / 16
        for r, z, tip, material in [(0.055, -0.40, -1.02, "orange"), (0.032, -0.40, -0.79, "core")]:
            faces.append(
                (
                    np.array(
                        [
                            [r * math.cos(a), r * math.sin(a), z],
                            [r * math.cos(b), r * math.sin(b), z],
                            [0, 0, tip],
                        ]
                    ),
                    material,
                    0,
                )
            )
    return Mesh(faces, dict(orange="#ff9c32", core="#fff1a5"))


@lru_cache(maxsize=1)
def canopy_mesh():
    faces = []
    for ring in range(5):
        a0, a1 = ring * math.pi / 10, (ring + 1) * math.pi / 10
        for i in range(24):
            b0, b1 = i * math.tau / 24, (i + 1) * math.tau / 24
            face = [
                [0.42 * math.cos(a) * math.cos(b), 0.42 * math.cos(a) * math.sin(b), 0.26 * math.sin(a)]
                for a, b in [(a0, b0), (a0, b1), (a1, b1), (a1, b0)]
            ]
            faces.append((np.array(face), "orange" if (i // 3) % 2 else "cream", 0))
    return Mesh(faces, dict(orange="#f38b5c", cream="#eef4f8"))
