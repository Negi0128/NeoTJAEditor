"""選曲の行の左に出すジャケットの小さな絵。

読むのは選曲のワーカー(InfoWorker / SearchWorker)のスレッドで、そこでは
QImage まで(縮めて真ん中を正方形に切る)。QPixmap にするのは GUI の
スレッドだけ(ThumbCache.put)。

覚えておく数は THUMB_CACHE_MAX まで(古いものから捨てる)。鍵は
(絵の道, 更新日時) なので、同じ道で絵を描き替えれば読み直す。

曲別に「ジャケットを表示しない」にした曲は、絵の道を返さない
(chart_art.existing_art)ので、空の枠になる。
"""

import os
from collections import OrderedDict

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QImage, QImageReader, QPixmap

#: 行に出す大きさ(論理の画素)。
THUMB_PX = 48
#: 読むときの大きさ(高い拡大率の画面でもにじまないように2倍で持つ)。
THUMB_LOAD_PX = THUMB_PX * 2
#: 覚えておく絵の数。
THUMB_CACHE_MAX = 300


def art_snapshot(cfg):
    """ワーカーへ渡す、曲別の絵の設定の写し(GUI が書き換えても崩れない)。"""
    raw = (cfg or {}).get("player_chart_art")
    if not isinstance(raw, dict):
        return {}
    return {"player_chart_art": {k: dict(v) if isinstance(v, dict) else v
                                 for k, v in raw.items()}}


def thumb_key(image_path):
    """(道, 更新日時)。読めなければ None。"""
    if not image_path:
        return None
    try:
        mtime = os.path.getmtime(image_path)
    except OSError:
        return None
    return (os.path.normcase(os.path.normpath(os.path.abspath(image_path))), mtime)


def load_thumb_image(image_path, size=THUMB_LOAD_PX):
    """絵を読み、短い辺を size に縮めて真ん中を正方形に切った QImage。
    読めなければ None。どのスレッドから呼んでもよい。"""
    try:
        reader = QImageReader(image_path)
        reader.setAutoTransform(True)
        src = reader.size()
        if src.isValid() and src.width() > 0 and src.height() > 0:
            k = float(size) / min(src.width(), src.height())
            if k < 1.0:
                reader.setScaledSize(QSize(max(1, round(src.width() * k)),
                                           max(1, round(src.height() * k))))
        img = reader.read()
    except Exception:  # noqa: BLE001
        return None
    if img is None or img.isNull():
        return None
    w, h = img.width(), img.height()
    side = min(w, h)
    img = img.copy(QRect((w - side) // 2, (h - side) // 2, side, side))
    if side != size:
        from PySide6.QtCore import Qt
        img = img.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return img


def thumb_for_song(cfg_snapshot, tja_path, known=()):
    """曲の行に出す絵を用意する(ワーカーのスレッドから)。

    返すのは (鍵, QImage または None)。鍵が None なら絵は無い(空の枠)。
    鍵が known(GUI がもう持っている鍵)に入っていれば、読まずに None を返す。"""
    from neotja.chart_art import existing_art
    try:
        img_path = existing_art(cfg_snapshot, tja_path, "jacket")
    except Exception:  # noqa: BLE001
        img_path = ""
    if not img_path:
        return None, None
    key = thumb_key(img_path)
    if key is None:
        return None, None
    if key in known:
        return key, None
    return key, load_thumb_image(img_path)


class ThumbCache:
    """鍵 → QPixmap。GUI のスレッドだけで触る。"""

    def __init__(self, limit=THUMB_CACHE_MAX):
        self.limit = int(limit)
        self._d = OrderedDict()

    def __len__(self):
        return len(self._d)

    def keys(self):
        return frozenset(self._d.keys())

    def get(self, key):
        if key is None:
            return None
        pm = self._d.get(key)
        if pm is not None:
            self._d.move_to_end(key)
        return pm

    def put(self, key, image):
        """QImage を QPixmap にして覚える。覚えた QPixmap を返す。"""
        if key is None or image is None or image.isNull():
            return self.get(key)
        pm = QPixmap.fromImage(image)
        self._d[key] = pm
        self._d.move_to_end(key)
        while len(self._d) > self.limit:
            self._d.popitem(last=False)
        return pm

    def clear(self):
        self._d.clear()


#: アプリでひとつ。
CACHE = ThumbCache()
