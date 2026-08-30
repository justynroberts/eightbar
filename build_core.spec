# -*- mode: python ; coding: utf-8 -*-
"""Freeze the Python core so a packaged Eightbar can chat on a machine that
has no checkout, no virtualenv and no Python.

Until this existed the Electron app spawned `../.venv/bin/python`, so a signed
DMG connected to Live and showed the set but could not answer a single message
anywhere but the developer's own machine.
"""

a = Analysis(
    ["core_entry.py"],
    pathex=["src"],
    binaries=[],
    datas=[("web", "web")],
    # Imported through the tool registry rather than by name, so PyInstaller's
    # static analysis does not see them.
    hiddenimports=[
        "ableton_ai.agent", "ableton_ai.analysis", "ableton_ai.arrangement",
        "ableton_ai.basslines", "ableton_ai.bridge", "ableton_ai.corpus",
        "ableton_ai.generators", "ableton_ai.groove", "ableton_ai.harmony",
        "ableton_ai.leads", "ableton_ai.melody", "ableton_ai.mixing",
        "ableton_ai.motif", "ableton_ai.presets", "ableton_ai.schemas",
        "ableton_ai.sounds", "ableton_ai.theory", "ableton_ai.tools",
        "ableton_ai.variations", "ableton_ai.voicings",
        "ableton_ai.llm.anthropic_backend", "ableton_ai.llm.claude_cli_backend",
        "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ableton-ai-core",
    console=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name="core",
)
