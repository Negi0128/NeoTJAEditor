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
import shutil
import subprocess
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
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


#: 焼いたジャケット(枠つき)の覚え。**毎回焼かない。**
#: 枠を毎コマ描いて重くなったことがある(利用者の注意 2026-10-26)ので、
#: 同じ見た目なら焼いた絵をそのまま使い回す。
_ART_CACHE = {}
_ART_CACHE_MAX = 4


def _baked_art(bg, h):
    """枠まで付けて焼いたジャケット。作れなければ None。"""
    path = getattr(bg, "art", "")
    if not path or not os.path.exists(path):
        return None
    ah = max(8, int(round(h * bg.art_pct / 100.0)))
    e = int(getattr(bg, "art_edge", 0) or 0)
    col = getattr(bg, "art_edge_color", "#ffffff")
    try:
        key = (path, os.path.getmtime(path), ah, e, col)
    except OSError:
        return None
    got = _ART_CACHE.get(key)
    if got is not None:
        return got
    src = QImage(path)
    if src.isNull():
        return None
    img = src.scaledToHeight(ah, Qt.SmoothTransformation)
    if e > 0:
        out = QImage(img.width() + e * 2, img.height() + e * 2,
                     QImage.Format_ARGB32_Premultiplied)
        out.fill(QColor(col))
        q = QPainter(out)
        q.drawImage(e, e, img)
        q.end()
        img = out
    if len(_ART_CACHE) >= _ART_CACHE_MAX:
        _ART_CACHE.clear()
    _ART_CACHE[key] = img
    return img


def _draw_art(p, bg, w, h):
    """ジャケット写真。本番(ffmpeg)と同じ置き方・同じ大きさ。

    置き場所は art_x / art_y(余白の内側を 0〜100%)。既定は左下。"""
    img = _baked_art(bg, h)
    if img is None:
        return
    pad = int(bg.art_pad)
    x = pad + max(0, w - img.width() - pad * 2) * float(bg.art_x) / 100.0
    y = pad + max(0, h - img.height() - pad * 2) * float(bg.art_y) / 100.0
    p.drawImage(int(round(x)), int(round(y)), img)


#: 焼いた棒の覚え。つまみを動かすたびに ffmpeg を呼ばないため。
_WAVE_CACHE = {}
_WAVE_CACHE_MAX = 6


def _wave_image(bg, song, at, w, h):
    """その時刻の音から棒を1枚作る。作れなければ None。

    本番と同じ式(モノラルにまとめてから showfreqs)。見本なので
    ffmpeg を1回だけ呼び、焼いた絵は覚えて使い回す。
    """
    if not getattr(bg, "wave", False) or not song or not os.path.exists(song):
        return None
    wh = max(8, int(round(h * bg.wave_pct / 100.0)))
    col = "0x" + bg.wave_color[1:]
    at = max(0.0, float(at))
    try:
        key = (song, os.path.getmtime(song), round(at, 2), w, wh, col)
    except OSError:
        return None
    got = _WAVE_CACHE.get(key)
    if got is not None:
        return got
    tmp = tempfile.mkdtemp(prefix="neotja_wv_")
    png = os.path.join(tmp, "w.png")
    img = None
    try:
        subprocess.run(
            [recorder._ffmpeg_exe(), "-v", "error", "-y", "-nostdin",
             "-ss", "%.3f" % at, "-t", "0.5", "-i", song,
             "-filter_complex",
             "[0:a]aformat=channel_layouts=mono,"
             "showfreqs=s=%dx%d:mode=bar:ascale=log:fscale=log:colors=%s[v]"
             % (w, wh, col),
             "-map", "[v]", "-frames:v", "1", png],
            capture_output=True, timeout=30, **recorder._no_window())
        if os.path.exists(png):
            got = QImage(png)
            img = None if got.isNull() else got
    except (OSError, subprocess.SubprocessError):
        img = None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if img is None:
        return None
    if len(_WAVE_CACHE) >= _WAVE_CACHE_MAX:
        _WAVE_CACHE.clear()
    _WAVE_CACHE[key] = img
    return img


def _draw_wave(p, bg, song, at, w, h):
    """画面の下へ棒を重ねる。濃さはそのまま効かせる。"""
    img = _wave_image(bg, song, at, w, h)
    if img is None:
        return
    p.save()
    p.setOpacity(max(0.05, min(1.0, bg.wave_alpha / 100.0)))
    p.drawImage(0, max(0, h - img.height()), img)
    p.restore()


def compose_check_frame(widget, bg, song_sec, canvas=None, song=""):
    """背景の上に譜面画面を1コマ重ねた絵を作る。駄目なら None。

    song_sec は**音源の時刻**。背景の中の秒は offset + song_sec
    (本番の -ss の出し方と同じ)。

    canvas は出来上がりの大きさ (w, h)。渡すと**本番と同じ画面比**で
    組み立てる — 画面は縦横比のままその中へ収め、余りは背景が見える
    (利用者の指定 2026-10-26「全部プレビューしてほしい」)。渡さなければ
    今までどおりウィジェットの大きさで作る。
    """
    sw, sh = widget.width(), widget.height()
    if sw <= 0 or sh <= 0:
        return None
    w, h = (canvas if canvas else (sw, sh))
    if w <= 0 or h <= 0:
        return None
    base = _bg_frame(bg, bg.offset + float(song_sec), w, h)
    if base is None:
        return None
    # 画面は背景を描かずに透明のまま。重ねるのはこちらでやる。
    shot = QImage(sw, sh, QImage.Format_ARGB32_Premultiplied)
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
    # **飾りを先、画面を最後。** 本番(ffmpeg)と同じ順にしないと、
    # 画面の下のほうにある歌詞(#LYRIC)が棒やジャケットに隠れる
    # (利用者の指定 2026-10-26)。
    _draw_wave(p, bg, song, bg.offset + float(song_sec), w, h)
    _draw_art(p, bg, w, h)
    if (sw, sh) == (w, h):
        p.drawImage(0, 0, shot)
    else:
        # 本番(ffmpeg)と同じ「縦横比のまま収めて、余りは透明」。
        fit = shot.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawImage((w - fit.width()) // 2, (h - fit.height()) // 2, fit)
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
