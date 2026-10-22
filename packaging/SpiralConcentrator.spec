# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a single-file Spiral Concentrator executable.

Build on Windows, from the repository root:

    python -m PyInstaller --noconfirm --clean packaging/SpiralConcentrator.spec

The executable is dist/SpiralConcentrator.exe. PyInstaller cannot cross-compile,
so this spec has to be run on Windows to produce an .exe. Double-clicking the
exe starts the browser simulator. SpiralConcentrator.exe --web does the same.
"""

import sys
from pathlib import Path

root = Path(SPECPATH).resolve().parent
icon = str(root / "packaging" / "spiral.ico") if sys.platform == "win32" else None
version = str(root / "packaging" / "windows_version.txt") if sys.platform == "win32" else None

a = Analysis(
    [str(root / "spiral_sim" / "__main__.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "spiral_sim" / "static" / "index.html"), "spiral_sim/static"),
    ],
    hiddenimports=[
        "spiral_sim",
        "spiral_sim.cli",
        "spiral_sim.web",
        "spiral_sim.workspace_store",
        "spiral_sim.model",
        "spiral_sim.feed",
        "spiral_sim.defaults",
        "spiral_sim.simulate",
        "spiral_sim.calibrate",
        "spiral_sim.targets",
        "spiral_sim.session",
        "spiral_sim.report",
        "spiral_sim.examples",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "doctest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SpiralConcentrator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version,
    icon=icon,
)
