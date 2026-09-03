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

# ---------------------------------------------------------------------------
# exe 縮小用の除外リスト。理由は NeoTJAEditor.spec の同じ場所と全く同じで、
# 両者がずれると片方だけ壊れて気づきにくいので内容は必ず揃えること。
#
# 要点: PySide6 hook は Qt を丸ごと持ってくるが、この app が import するのは
# QtCore / QtGui / QtWidgets / QtMultimedia だけ。QMediaPlayer / QSoundEffect
# は使うので Qt の av*.dll と plugins/multimedia は残す。ffmpeg も録画に必須。
# ---------------------------------------------------------------------------
_DROP_EXACT = {
    # OpenGL のソフトウェア実装(約 20MB)。描画は QPainter のラスタ経路。
    'PySide6/opengl32sw.dll',
    'PySide6/Qt6Quick.dll',
    'PySide6/Qt6Qml.dll',
    'PySide6/Qt6QmlModels.dll',
    'PySide6/Qt6QmlMeta.dll',
    'PySide6/Qt6QmlWorkerScript.dll',
    'PySide6/Qt6Pdf.dll',
    'PySide6/Qt6VirtualKeyboard.dll',
    'PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll',
    'PySide6/plugins/generic/qtuiotouchplugin.dll',
    'PySide6/plugins/imageformats/qicns.dll',
    'PySide6/plugins/imageformats/qtga.dll',
    'PySide6/plugins/imageformats/qwbmp.dll',
    'PySide6/plugins/imageformats/qtiff.dll',
    'PySide6/plugins/imageformats/qpdf.dll',
    # Qt の TLS は Windows 標準の schannel で足りる。
    'PySide6/plugins/tls/qopensslbackend.dll',
    # PATH 上の Git for Windows から誤って拾われる OpenSSL 3。Python の _ssl は
    # libcrypto-1_1 を使うので不要。
    'libcrypto-3-x64.dll',
    'libssl-3-x64.dll',
    # Pillow の AVIF デコーダ(約 7.5MB)。AVIF を読む箇所は無い。
    'PIL/_avif.cp39-win_amd64.pyd',
}

# Qt の翻訳(約 6.7MB)。UI 文言は自前の日本語。
_DROP_PREFIX = ('PySide6/translations/',)


def _slim(entries):
    """TOC から上の除外リストに載っているものを取り除く。"""
    kept = []
    for entry in entries:
        dest = entry[0].replace('\\', '/')
        if dest in _DROP_EXACT or dest.startswith(_DROP_PREFIX):
            continue
        kept.append(entry)
    return kept

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
        # 窓のアイコン。exe に焼いたものは Windows のシェルが使うだけで、
        # Qt の setWindowIcon には別途ファイルが要る(settings.icon_path)。
        ('app_icon_player.ico', '.'),
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
    # 純 Python 側の除外。yt_dlp は Player に無い機能のぶん。あとは
    # QML/Quick/Pdf のバインディングと pydoc_data で、どれも未使用。
    excludes=[
        'yt_dlp',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuickWidgets',
        'PySide6.QtQuickControls2',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.Qt3DCore',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtBluetooth',
        'PySide6.QtNfc',
        'PySide6.QtSerialPort',
        'PySide6.QtDesigner',
        'PySide6.QtHelp',
        'PySide6.QtTest',
        'PIL.AvifImagePlugin',
        'tkinter',
        'pydoc_data',
    ],
    noarchive=False,
    optimize=0,
)

# hook が集めた後の TOC から、上の名前リストぶんを落とす。
a.binaries = _slim(a.binaries)
a.datas = _slim(a.datas)

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
    icon='app_icon_player.ico',
)
