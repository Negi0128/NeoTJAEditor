# -*- mode: python ; coding: utf-8 -*-

import os

import imageio_ffmpeg
from PyInstaller.utils.hooks import collect_submodules

# ---------------------------------------------------------------------------
# exe 縮小用の除外リスト。
#
# PyInstaller の PySide6 hook は「Qt 一式」を丸ごと持ってくるので、実際には
# import していない Qt モジュールと、その依存 DLL・プラグインが大量に入る。
# 実測(build/*/Analysis-00.toc)したところ 300MB 近い中身のうち 40MB 以上が
# 一度も触らないものだったので、名前で落とす。
#
# 「使っているものは絶対に外さない」ため、下の判断はソースの grep 結果に
# 基づく:
#   - PySide6 は QtCore / QtGui / QtWidgets / QtMultimedia のみ import
#   - QOpenGL* / QQuick / QtQml / QtPdf の使用箇所はゼロ
#   - QMediaPlayer / QSoundEffect は使うので Qt の av*.dll と
#     plugins/multimedia は残す(ここを外すと音が出なくなる)
#   - ffmpeg(imageio_ffmpeg)は録画に必須なので当然残す
# ---------------------------------------------------------------------------

# 丸ごと要らない Qt の DLL。QML/Quick 系は宣言的 UI 用で、この app は
# QtWidgets のみ。Pdf は PDF 表示、VirtualKeyboard はタッチ端末用ソフト
# キーボードで、どちらも呼び出していない。
_DROP_EXACT = {
    # OpenGL のソフトウェア実装(約 20MB)。描画は QPainter のラスタ経路なので
    # 使われない。GPU ドライバが無い環境向けの保険だが、費用対効果が悪すぎる。
    'PySide6/opengl32sw.dll',
    'PySide6/Qt6Quick.dll',
    'PySide6/Qt6Qml.dll',
    'PySide6/Qt6QmlModels.dll',
    'PySide6/Qt6QmlMeta.dll',
    'PySide6/Qt6QmlWorkerScript.dll',
    'PySide6/Qt6Pdf.dll',
    'PySide6/Qt6VirtualKeyboard.dll',
    'PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll',
    'PySide6/plugins/generic/qtuiotouchplugin.dll',  # TUIO マルチタッチ卓用
    # 読み書きしない画像形式のプラグイン。素材は PNG、ユーザー動画/画像で
    # 想定されるのは JPEG/GIF/WebP/ICO なので、そちらは残してある。
    'PySide6/plugins/imageformats/qicns.dll',   # macOS アイコン
    'PySide6/plugins/imageformats/qtga.dll',
    'PySide6/plugins/imageformats/qwbmp.dll',
    'PySide6/plugins/imageformats/qtiff.dll',
    'PySide6/plugins/imageformats/qpdf.dll',
    # Qt の TLS は Windows 標準の schannel backend で足りる。openssl backend を
    # 残すと下の libcrypto-3 まで引きずられる。
    'PySide6/plugins/tls/qopensslbackend.dll',
    # PyInstaller が PATH 上の Git for Windows(mingw64/bin)から拾ってしまう
    # OpenSSL 3。Python の _ssl は libcrypto-1_1 の方を使うので、これは
    # ただの混入。updater の https は 1_1 側で動く。
    'libcrypto-3-x64.dll',
    'libssl-3-x64.dll',
    # Pillow の AVIF デコーダ(約 7.5MB)。AVIF を読む箇所は無い。
    'PIL/_avif.cp39-win_amd64.pyd',
}

# Qt の翻訳(約 6.7MB / 124 ファイル)。UI は自前の日本語文言で、Qt 標準
# ダイアログのボタン程度しか影響しない。
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

# imageio-ffmpeg resolves its bundled binary via importlib.resources relative
# to the imageio_ffmpeg.binaries package at runtime, so the whole binaries/
# folder (containing the platform ffmpeg exe it already picked) needs to be
# copied into the frozen bundle at that same package-relative path.
_ffmpeg_bin_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())

# sounddevice loads the PortAudio DLL at import time via a path relative to the
# _sounddevice_data package. PyInstaller ships no hook for it, so the data dir
# has to be copied in explicitly or the mixer backend silently falls back to Qt.
import _sounddevice_data
_portaudio_dir = os.path.join(os.path.dirname(_sounddevice_data.__file__),
                              'portaudio-binaries')

a = Analysis(
    ['neotja/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        (_ffmpeg_bin_dir, 'imageio_ffmpeg/binaries'),
        (_portaudio_dir, '_sounddevice_data/portaudio-binaries'),
        # 素材は TNDE の System フォルダから読むので同梱しないが、この2つは
        # 作者の自作物(デモ用の譜面と無音WAV)なので入れる。skin_cache が
        # キャッシュへ展開するときの取り出し元になる。
        ('neotja/assets', 'neotja/assets'),
        # 窓のアイコン。exe に焼いたものは Windows のシェルが使うだけで、
        # Qt の setWindowIcon には別途ファイルが要る(settings.icon_path)。
        ('app_icon.ico', '.'),
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
    # **PySide6 より先に**記録の仕掛けを掛ける。ここに書いたものは
    # PyInstaller 自身のフック(pyi_rth_pyside6 など)より前に走る。
    # shiboken の読み込みで落ちる件が crash.log に一行も残らなかったのは、
    # 仕掛けが掛かるのが本体の import 時=PySide6 のあとだったため。
    runtime_hooks=['rthook_crashlog.py'],
    # 純 Python 側の除外。QML/Quick/Pdf のバインディング(.pyd)と、
    # 対話 help 用の pydoc_data(約 0.7MB)は使わない。
    excludes=[
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
