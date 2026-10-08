"""ウィジェットを**並びそのまま**倍率ぶん縮める道具。

ゲーム画面は ScaledHost が倍率をかけた painter へ描くので素直に小さくなるが、
命令パネルや下部パネルは**ふつうのウィジェット**なので倍率がかからない。
小さい画面(ノートPC)では、そこだけ原寸のまま残って右や下が切れていた。

窓の大きさは変えない・並びも変えない・文字が小さくなるのは構わない、という
指定(利用者 2026-10-02)なので、固定サイズ・余白・文字の大きさを倍率ぶん
掛け直す。**必ず原寸から**計算する — いまの値へ掛けていくと、縮小を重ねる
たびに丸めの誤差がたまって並びが狂う。

自分で絵を描くウィジェット(波形・作譜ペイン)は、中の px の定数まではここでは
面倒を見られない。そういう相手は自分で `set_ui_scale` を持ち、UiScaler が
その場で呼ぶ。
"""

from PySide6.QtWidgets import QLayout, QWidget

#: Qt の「上限なし」。setFixedSize で入った上下限を見分けるのに使う。
QT_MAX = 16777215


def px(v, s):
    """px を倍率ぶん縮める。0 は 0 のまま(余白なしを作らない)。"""
    if v <= 0:
        return 0
    return max(1, int(round(v * s)))


class UiScaler:
    """root 以下の原寸を覚えておき、倍率を当て直す。

    root 自身の大きさには触らない — 下部パネルのように高さを外から
    決めている相手が居るので、そこは持ち主に任せる。
    """

    def __init__(self, root: QWidget, custom=()):
        self.root = root
        #: 自分で絵を描く子。外の大きさはここで縮め、中身は本人に任せる。
        self._custom = tuple(w for w in custom if w is not None)
        self.scale = 1.0
        self._capture()

    # ------------------------------------------------------------------
    def _own(self, w):
        """自分が面倒を見る相手か。

        自前で縮める子は、その子自身も中身も見ない。**大きさも本人に任せる**
        — 両方から触ると、こちらが縮めたあとの値を本人が「原寸」と思い込んで
        二重に掛かる(作譜ペインが 300 -> 225 -> 169 になった)。"""
        for c in self._custom:
            if c is w or c.isAncestorOf(w):
                return False
        return True

    def _capture(self):
        self._fixed = [(w, w.minimumWidth(), w.maximumWidth(),
                        w.minimumHeight(), w.maximumHeight())
                       for w in self.root.findChildren(QWidget) if self._own(w)]
        self._lay = []
        for lay in self.root.findChildren(QLayout):
            pw = lay.parentWidget()
            if pw is not None and not self._own(pw):
                continue
            m = lay.contentsMargins()
            hs = vs = lay.spacing()
            if hasattr(lay, "horizontalSpacing"):
                hs, vs = lay.horizontalSpacing(), lay.verticalSpacing()
            self._lay.append(
                (lay, (m.left(), m.top(), m.right(), m.bottom()), hs, vs))
        # 文字の大きさは**1枚ずつ**覚えて1枚ずつ当てる。親へ setFont しても
        # 子へ伝わらない — アプリに QSS を当てていると、Qt が各ウィジェットの
        # font を「自分で決めた」印付きで書くので、親からの継承が切れる
        # (実測: 下部パネルを 5pt にしても「再生速度:」は 12px のままだった)。
        self._fonts = [(w, w.font().pointSizeF(), w.font().pixelSize())
                       for w in self.root.findChildren(QWidget) if self._own(w)]
        f = self.root.font()
        self._fonts.append((self.root, f.pointSizeF(), f.pixelSize()))
        #: 自前で縮める子。
        self._scalers = [w for w in self._custom if hasattr(w, "set_ui_scale")]

    # ------------------------------------------------------------------
    def apply(self, s: float):
        """倍率 s を当てる(1.0 で原寸)。当たったら True。"""
        # 上限を 1.0 から 4.0 へ。全画面では**中身ぜんぶを同じ倍率で拡大**する
        # (利用者の指定 2026-10-05「すべてのオブジェクトを拡大してほしい」)。
        # 通常の窓の 100/75/50% は 1.0 以下なので、見え方は1pxも変わらない。
        s = max(0.25, min(4.0, float(s)))
        if abs(s - self.scale) < 1e-6:
            return False
        self.scale = s
        for w, pt, fpx in self._fonts:
            wf = w.font()
            if fpx > 0:
                wf.setPixelSize(max(7, int(round(fpx * s))))
            elif pt > 0:
                wf.setPointSizeF(max(5.0, pt * s))
            else:
                continue
            w.setFont(wf)
        for lay, m, hs, vs in self._lay:
            lay.setContentsMargins(px(m[0], s), px(m[1], s),
                                   px(m[2], s), px(m[3], s))
            if hasattr(lay, "setHorizontalSpacing"):
                if hs >= 0:
                    lay.setHorizontalSpacing(px(hs, s))
                if vs >= 0:
                    lay.setVerticalSpacing(px(vs, s))
            elif hs >= 0:
                lay.setSpacing(px(hs, s))
        for w, mw, xw, mh, xh in self._fixed:
            # 先に上下限を外す。min > max の順で入れると Qt に叱られる。
            w.setMinimumSize(0, 0)
            w.setMaximumSize(QT_MAX, QT_MAX)
            if xw < QT_MAX:
                w.setMaximumWidth(px(xw, s))
            if mw > 0:
                w.setMinimumWidth(px(mw, s))
            if xh < QT_MAX:
                w.setMaximumHeight(px(xh, s))
            if mh > 0:
                w.setMinimumHeight(px(mh, s))
        for w in self._scalers:
            w.set_ui_scale(s)
        return True
