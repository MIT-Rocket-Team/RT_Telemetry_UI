"""Compact plot titles with legends outside the plotting area."""

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .widgets import COLORS


class PlotHeader(QWidget):
    """Draw a title and current curve pens on one shared text baseline."""

    def __init__(self, title, curves=(), parent=None):
        super().__init__(parent)
        self.title = title
        self.curves = tuple(curves)
        self.setObjectName("transparent")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(21)
        descriptions = []
        for label, curve in self.curves:
            name = curve.opts.get("name") or label
            descriptions.append(f"{label}: {name}" if label != name else label)
            signal = getattr(curve, "sigPlotChanged", None)
            if signal is not None:
                signal.connect(self._curve_changed)
        self.setToolTip("\n".join((title, *descriptions)))

    def _curve_changed(self, *args):
        self.update()

    def _fonts(self):
        title = QFont(self.font())
        title.setPixelSize(11)
        title.setWeight(QFont.Weight.DemiBold)
        legend = QFont(self.font())
        legend.setPixelSize(10)
        return title, legend

    def sizeHint(self):
        title, legend = self._fonts()
        legend_width = self._legend_width(QFontMetrics(legend))
        return QSize(QFontMetrics(title).horizontalAdvance(self.title) + 12 + legend_width, 21)

    def minimumSizeHint(self):
        return QSize(0, 21)

    def _legend_width(self, metrics):
        # Leave room for glyph bearings and integer font-metric rounding.
        return min(200, sum(20 + metrics.horizontalAdvance(label) for label, _ in self.curves)
                   + max(0, len(self.curves) - 1) * 10)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        title_font, legend_font = self._fonts()
        title_metrics = QFontMetrics(title_font)
        legend_metrics = QFontMetrics(legend_font)
        baseline = (self.height() - title_metrics.height()) / 2 + title_metrics.ascent()
        legend_width = min(self.width(), self._legend_width(legend_metrics))
        title_width = max(0, self.width() - legend_width - (12 if self.curves else 0))
        painter.setFont(title_font)
        painter.setPen(QColor(COLORS["text"]))
        painter.drawText(QPointF(0, baseline), title_metrics.elidedText(
            self.title, Qt.TextElideMode.ElideRight, title_width))
        if not self.curves:
            return
        natural_width = sum(20 + legend_metrics.horizontalAdvance(label) for label, _ in self.curves)
        label_scale = max(0, legend_width - (len(self.curves) - 1) * 10) / max(1, natural_width)
        x = self.width() - legend_width
        painter.setFont(legend_font)
        for label, curve in self.curves:
            width = (20 + legend_metrics.horizontalAdvance(label)) * label_scale
            pen = QPen(curve.opts["pen"])
            painter.setPen(pen)
            y = baseline - legend_metrics.xHeight() / 2
            painter.drawLine(QPointF(x, y), QPointF(x + min(12, width), y))
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(QPointF(x + 16, baseline), legend_metrics.elidedText(
                label, Qt.TextElideMode.ElideRight, max(0, int(width - 16))))
            x += width + 10
