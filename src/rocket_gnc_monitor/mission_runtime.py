"""Desktop runtime qualification; the bundled headless worker stays Qt-free."""

import PySide6

from .mission_worker import capabilities as worker_capabilities


def require_supported_qt():
    # PYSIDE-3469 leaves dangling layout-item wrappers in older bindings when
    # station workspaces are rebuilt. Address reuse can crash QObject creation.
    if tuple(int(part) for part in PySide6.__version__.split('.')[:2]) < (6, 12):
        raise RuntimeError(
            f"Mission workspaces require PySide6 6.12 or newer; this environment has {PySide6.__version__}. "
            "Update the Telemetry UI environment with 'make setup' (or 'uv sync --locked'), "
            "or select a current packaged application."
        )


def capabilities():
    require_supported_qt()
    return {**worker_capabilities(), 'qtVersion': PySide6.__version__}
