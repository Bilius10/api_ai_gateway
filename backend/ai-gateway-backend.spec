# PyInstaller build description for the native FastAPI sidecar.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("ai_gateway") + collect_submodules("uvicorn")

analysis = Analysis(
    ["sidecar_entry.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="ai-gateway-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
