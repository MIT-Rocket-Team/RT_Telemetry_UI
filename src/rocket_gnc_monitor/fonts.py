"""Private application fonts, loaded from the same resources on every platform."""

from pathlib import Path
import sys
from PySide6.QtGui import QFont, QFontDatabase

FONT_FAMILY = "Lucida Grande"


def configure_fonts(app, resource_root=None):
    if getattr(app, "_rocket_font_id", None) is not None:
        return app._rocket_font_id
    root = resource_root or (
        Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    )
    path = Path(root) / "resources" / "fonts" / "LucidaGrande.ttc"
    font_id = QFontDatabase.addApplicationFont(str(path))
    if font_id < 0 or FONT_FAMILY not in QFontDatabase.applicationFontFamilies(font_id):
        raise RuntimeError(f"Could not load the bundled {FONT_FAMILY} font: {path}")
    app.setFont(QFont(FONT_FAMILY, 10))
    app._rocket_font_id = font_id
    return font_id
