"""Cached polygon geometry with vectorized rigid transforms and projection."""

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPen, QPolygonF


class Mesh:
    def __init__(self, faces, palette):
        self.counts = np.array([len(v) for v, _, _ in faces])
        self.starts = np.r_[0, np.cumsum(self.counts)[:-1]]
        self.vertices = np.vstack([v for v, _, _ in faces])
        self.groups = np.array([g for _, _, g in faces])
        self.vertex_groups = np.repeat(self.groups, self.counts)
        self.centers = np.array([np.mean(v, axis=0) for v, _, _ in faces])
        normals = np.array([np.cross(v[1] - v[0], v[2] - v[0]) for v, _, _ in faces])
        self.normals = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
        self.materials = [m for _, m, _ in faces]
        self.colors = {}
        for key, color in palette.items():
            base = QColor(color)
            shades = []
            for i in range(33):
                c = QColor(base)
                factor = 0.56 + 0.44 * i / 32
                c.setRedF(c.redF() * factor)
                c.setGreenF(c.greenF() * factor)
                c.setBlueF(c.blueF() * factor)
                shades.append((c, QPen(c, 0.35)))
            self.colors[key] = shades

    def transformed(self, rotations, translations):
        vertices = np.empty_like(self.vertices)
        centers = np.empty_like(self.centers)
        normals = np.empty_like(self.normals)
        for group, (rotation, translation) in enumerate(zip(rotations, translations)):
            vmask, fmask = self.vertex_groups == group, self.groups == group
            vertices[vmask] = self.vertices[vmask] @ rotation.T + translation
            centers[fmask] = self.centers[fmask] @ rotation.T + translation
            normals[fmask] = self.normals[fmask] @ np.linalg.inv(rotation)
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
        return vertices, centers, normals

    def draw(self, painter, project, forward, rotations, translations):
        vertices, centers, normals = self.transformed(rotations, translations)
        xy = project(vertices)
        points = [QPointF(x, y) for x, y in xy]
        light = np.array([-2, 5, 4]) / np.sqrt(45)
        shades = np.rint(np.clip(abs(normals @ light), 0, 1) * 32).astype(int)
        for i in np.argsort(-(centers @ forward)):
            color, pen = self.colors[self.materials[i]][shades[i]]
            painter.setPen(pen)
            painter.setBrush(color)
            start = self.starts[i]
            painter.drawPolygon(QPolygonF(points[start : start + self.counts[i]]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
