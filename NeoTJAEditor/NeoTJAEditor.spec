# -*- mode: python ; coding: utf-8 -*-

import os

import imageio_ffmpeg
from PyInstaller.utils.hooks import collect_submodules

# imageio-ffmpeg resolves its bundled binary via importlib.resources relative
# to the imageio_ffmpeg.binaries package at runtime, so the whole binaries/
# folder (containing the platform ffmpeg exe it already picked) needs to be
# copied into the frozen bundle at that same package-relative path.
_ffmpeg_bin_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())

a = Analysis(
    ['neotja/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        (_ffmpeg_bin_dir, 'imageio_ffmpeg/binaries'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtMultimedia',
        'imageio_ffmpeg',
    ] + collect_submodules('yt_dlp'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='NeoTJAEditor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
)
