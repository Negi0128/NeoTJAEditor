# -*- mode: python ; coding: utf-8 -*-
#
# NeoTJAPlayer（再生・録画専用）の実行ファイル。
#
# NeoTJAEditor.spec とほぼ同じだが、入口が neotja/player/__main__.py で、
# yt_dlp を積まない。あれは「YouTube から新規作成」のためのもので、Player に
# その機能は無い。積まないぶん exe が小さくなり、起動も速くなる。
#
# ffmpeg は積む。録画（まとめて録画を含む）が音声のデコードと動画の書き出しに
# 使うので、これだけは外せない。

import os

import imageio_ffmpeg

# imageio-ffmpeg は実行時に importlib.resources で
# imageio_ffmpeg.binaries からの相対で自分の exe を探すので、binaries/
# フォルダごと同じ相対位置へ入れる必要がある。
_ffmpeg_bin_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())

# sounddevice は import した瞬間に _sounddevice_data からの相対パスで
# PortAudio の DLL を読む。PyInstaller の hook が無いので明示的に入れないと、
# ミキサー音声が黙って旧経路へ落ちる。
import _sounddevice_data
_portaudio_dir = os.path.join(os.path.dirname(_sounddevice_data.__file__),
                              'portaudio-binaries')

a = Analysis(
    ['neotja/player/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        (_ffmpeg_bin_dir, 'imageio_ffmpeg/binaries'),
        (_portaudio_dir, '_sounddevice_data/portaudio-binaries'),
        # 素材(TNDE の System)は同梱しない。入れるのは作者の自作物だけ
        # (デモ用の譜面と無音WAV)で、skin_cache が内蔵スキンを組み立てる
        # ときの取り出し元になる。
        ('neotja/assets', 'neotja/assets'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtMultimedia',
        'imageio_ffmpeg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['yt_dlp'],
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
    name='NeoTJAPlayer',
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
