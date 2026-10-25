"""Graph-limit controls in a reserved row above each telemetry plot."""

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QGraphicsProxyWidget, QHBoxLayout, QPushButton, QWidget

from .fonts import FONT_FAMILY
from .widgets import COLORS


class PlotViewControls(QObject):
    def __init__(self, graph, name):
        super().__init__(graph)
        self.graph = graph
        self.plot_item = graph.getPlotItem()
        self.view = self.plot_item.getViewBox()
        self.initial_range = tuple(tuple(axis) for axis in self.view.viewRange())
        self.widget = QWidget()
        self.widget.setFixedHeight(24)
        layout = QHBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(4)
        layout.addStretch()
        self.reset_button = QPushButton("Reset view")
        self.reset_button.setAccessibleName(f"Reset {name} graph view")
        self.reset_button.setToolTip("Restore the initial graph limits (0 to 1 on both axes).")
        self.reset_button.clicked.connect(self.reset_view)
        self.fit_button = QPushButton("Fit")
        self.fit_button.setAccessibleName(f"Fit {name} graph to data")
        self.fit_button.setToolTip("Fit the visible plotted data with a small margin.")
        self.fit_button.clicked.connect(self.fit_data)
        for button in (self.reset_button, self.fit_button):
            button.setAutoDefault(False)
            layout.addWidget(button)
        # The factory does not use PlotItem's title; its card owns the title.
        # Reserve that row for controls, leaving the ViewBox unobstructed.
        self.plot_item.layout.removeItem(self.plot_item.titleLabel)
        self.plot_item.titleLabel.hide()
        self.proxy = QGraphicsProxyWidget(self.plot_item)
        self.proxy.setWidget(self.widget)
        self.plot_item.layout.addItem(self.proxy, 0, 1)
        self.plot_item.layout.setRowFixedHeight(0, 24)
        self.apply_theme()
        graph.installEventFilter(self)

    def apply_theme(self):
        self.widget.setStyleSheet(
            f"QWidget {{ background: transparent; color: {COLORS['text']}; "
            f"font-family: '{FONT_FAMILY}'; font-size: 10px; }} "
            f"QPushButton {{ background: {COLORS['raised']}; border: 1px solid {COLORS['line']}; "
            "border-radius: 3px; padding: 1px 5px; } "
            f"QPushButton:hover {{ border-color: {COLORS['accent']}; }}"
        )

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.PaletteChange):
            self.apply_theme()
        return super().eventFilter(watched, event)

    def reset_view(self):
        self.view.setRange(xRange=self.initial_range[0], yRange=self.initial_range[1],
                           padding=0, disableAutoRange=True)

    def fit_data(self):
        items = [item for item in self.plot_item.listDataItems() if item.isVisible()]
        if not items or all(axis is None for axis in self.view.childrenBounds(items=items)):
            return
        self.view.autoRange(items=items, padding=0.05)
