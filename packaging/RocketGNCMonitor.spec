from pathlib import Path
import os
import sys
import mgrs.core
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH).parent
app_name = "RocketGNCMonitor-v0a"
datas = collect_data_files("imageio_ffmpeg")
datas += [(str(root / "vendor"), "vendor"), (str(root / "docs"), "docs"), (str(root / "resources"), "resources")]
datas += [
    (str(root / "references" / filename), "references")
    for filename in ("Ground Station Software Block Diagram.pdf", "GROUND_STATION_BOARD_NOTES.md", "IRIS_STATION_ROUTING.png")
]
for package in ("numpy", "pyserial", "imageio-ffmpeg", "PySide6-Essentials", "pyqtgraph", "mgrs", "openlocationcode"):
    datas += copy_metadata(package)
a = Analysis([str(root / "packaging" / "launcher.py")], pathex=[str(root / "src")],
    binaries=[(str(Path(mgrs.core.rt._name).resolve()), ".")], datas=datas, hiddenimports=["serial.tools.list_ports", "PySide6.QtSvg"],
    excludes=["PyQt5", "PyQt6", "PySide2", "matplotlib", "scipy", "pandas", "IPython",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=app_name,
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    icon=str(root / "build" / "icon.ico") if sys.platform == "win32" else None,
    codesign_identity=os.environ.get("MAC_SIGN_IDENTITY") or None,
    entitlements_file=str(root / "packaging" / "entitlements.plist") if sys.platform == "darwin" else None)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=app_name)
if sys.platform == "darwin":
    app = BUNDLE(collection, name=app_name + ".app", icon=str(root / "build" / "icon.icns"), bundle_identifier="edu.mit.rocketteam.gncmonitor.v0a",
        info_plist={"CFBundleShortVersionString": "0.1.1", "CFBundleVersion": "2",
            "CFBundleDisplayName": "Rocket GNC Monitor v0a",
            "NSHighResolutionCapable": True,
            "NSCameraUsageDescription": "Display and record the connected rocket video receiver.",
            "LSMinimumSystemVersion": "14.0"})
