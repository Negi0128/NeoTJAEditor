# -*- coding: utf-8 -*-
"""録画の背景が「この位置で」どう重なるかを1コマ出して見せる。

利用者の仕様 2026-10-24。背景動画の開始位置(OFFSET)を合わせるための窓。

仕様では「背景の1コマと譜面画面を**並べて**出す」だったが、ここでは
**重ねたもの**を出す。合っているかどうかは重なった絵を見るのが一番早く、
並べると結局うまく見比べられないため。

出来上がりとまったく同じ道(ffmpeg の overlay)ではなく、こちらで
QPainter を使って重ねている。確認のための1枚なので ffmpeg を起こす必要が
無く、押してすぐ出るほうが大事。暗さと位置の出し方は本番と同じ式。
"""
import os
import subprocess
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QVBoxLayout,
)

from neotja import recorder


def _bg_frame(bg, at_sec, w, h):
    """背景の1コマを (w, h) に敷き詰めて返す。取れなければ None。

    at_sec は**背景の中の秒**。負のとき(まだ始まっていない)は黒を返す。
    """
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(Qt.black)
    if at_sec < 0.0 and not bg.is_image:
        return img                      # 本番も tpad で黒を足す所
    src = None
    if bg.is_image:
        src = QImage(bg.path)
        if src.isNull():
            src = None
    else:
        tmp = tempfile.mkdtemp(prefix="neotja_bgchk_")
        png = os.path.join(tmp, "f.png")
        try:
            subprocess.run(
                [recorder._ffmpeg_exe(), "-v", "error", "-y", "-nostdin",
                 "-ss", "%.3f" % max(0.0, at_sec), "-i", bg.path,
                 "-frames:v", "1", png],
                capture_output=True, timeout=20, **recorder._no_window())
            if os.path.exists(png):
                src = QImage(png)
                if src.isNull():
                    src = None
        except (OSError, subprocess.SubprocessError):
            src = None
        finally:
            try:
                if os.path.exists(png):
                    os.remove(png)
                os.rmdir(tmp)
            except OSError:
                pass
    if src is None:
        return None
    # 本番と同じ「**上下を合わせて、余る左右は黒**」(利用者の指定
    # 2026-10-25)。録画の画面は 16:9 より横長なので、MV をそのまま
    # 敷き詰めると上下が大きく切れてしまう。高さをぴったり合わせて、
    # 足りない左右は黒のまま残す(横長すぎる素材なら左右を切る)。
    scaled = src.scaledToHeight(h, Qt.SmoothTransformation)
    p = QPainter(img)
    p.drawImage((w - scaled.width()) // 2, 0, scaled)
    p.end()
    if bg.dim > 0.0:
        d = 1.0 - bg.dim
        dark = QImage(img)
        dp = QPainter(dark)
        dp.setCompositionMode(QPainter.CompositionMode_Multiply)
        v = int(round(255 * d))
        dp.fillRect(dark.rect(), Qt.GlobalColor.white if v >= 255
                    else _gray(v))
        dp.end()
        img = dark
    return img


def _gray(v):
    from PySide6.QtGui import QColor
    return QColor(v, v, v)


def compose_check_frame(widget, bg, song_sec):
    """背景の上に譜面画面を1コマ重ねた絵を作る。駄目なら None。

    song_sec は**音源の時刻**。背景の中の秒は offset + song_sec
    (本番の -ss の出し方と同じ)。
    """
    w, h = widget.width(), widget.height()
    if w <= 0 or h <= 0:
        return None
    base = _bg_frame(bg, bg.offset + float(song_sec), w, h)
    if base is None:
        return None
    # 画面は背景を描かずに透明のまま。重ねるのはこちらでやる。
    shot = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    shot.fill(0)
    recorder.set_background_passthrough(widget, True, bg.lane)
    try:
        widget.begin_offline_render()
        widget.set_render_time(float(song_sec))
        widget.render(shot)
    finally:
        try:
            widget.end_offline_render()
        except RuntimeError:
            pass
        recorder.set_background_passthrough(widget, False)
    p = QPainter(base)
    p.drawImage(0, 0, shot)
    p.end()
    return base


class BackgroundCheckDialog(QDialog):
    """重ねた1コマを出すだけの窓。"""

    MAX_W = 960

    def __init__(self, parent, image, song_sec, bg):
        super().__init__(parent)
        self.setWindowTitle("背景の位置を確認")
        lay = QVBoxLayout(self)
        pm = QPixmap.fromImage(image)
        if pm.width() > self.MAX_W:
            pm = pm.scaledToWidth(self.MAX_W, Qt.SmoothTransformation)
        view = QLabel()
        view.setPixmap(pm)
        lay.addWidget(view)
        lay.addWidget(QLabel(
            "音源 %.3f 秒 ＝ 背景の %.3f 秒（開始位置 %.3f 秒）"
            % (song_sec, bg.offset + song_sec, bg.offset)))
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        lay.addWidget(box)


def show_background_check(parent, widget, bg, song_sec):
    """確認の窓を出す。作れなければ知らせるだけ。"""
    from PySide6.QtWidgets import QMessageBox
    img = compose_check_frame(widget, bg, song_sec)
    if img is None:
        QMessageBox.warning(parent, "背景の位置を確認",
                            "背景のコマを取り出せませんでした。\n"
                            "ファイルの形式を確かめてください。")
        return None
    dlg = BackgroundCheckDialog(parent, img, float(song_sec), bg)
    dlg.exec()
    return dlg
