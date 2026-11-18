"""画面の切り替えのフェード。PlayerHost の画面の重ね(QStackedWidget)の代わりに使う。

- cross … いまの画面を1枚の絵に撮って上に重ね、新しい画面へ切り替えてから
  その絵を薄くしていく(クロスフェード)。
- black … いまの画面の上で黒を濃くし、真っ黒になったところで切り替え、
  黒を薄くしていく。GL の窓(native)の上にはふつうのウィジェットを重ねても
  OS の窓の下に沈んで見えないので、画面自身が黒を描ける相手
  (supports_fade_dim / set_fade_dim を持つ再生画面)には、上に重ねる代わりに
  その黒の濃さを動かす(入るときも出るときも)。描けない native の画面は
  今までどおり真っ黒からそのまま切り替える。

あとで結果画面などの切り替えにもそのまま使う。
"""

from PySide6.QtCore import QEasingCurve, QEvent, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget


class _FadeOverlay(QWidget):
    """フェードの絵。cross のときは新しい画面も1枚の絵(under)にして下に敷き、
    自分だけで塗りきる(不透明)。こうしないと1コマごとに下の画面
    (曲名表示・Player の地の光)まで描き直しになり、1コマ 15ms 前後 GUI を
    ふさいで、そのあいだ流れている試聴の音声コールバックが GIL を待たされた。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.pixmap = None
        #: 新しい画面の絵(cross のときだけ)。撮るまでは None。
        self.under = None
        #: under を撮る口(1回目に要ったときに呼ぶ)。
        self.under_fn = None
        self.opacity = 0.0
        self.hide()

    def set_opaque(self, on):
        self.setAttribute(Qt.WA_OpaquePaintEvent, bool(on))

    def paintEvent(self, ev):
        p = QPainter(self)
        op = max(0.0, min(1.0, self.opacity))
        if self.pixmap is not None and self.under_fn is not None:
            # cross: 下に新しい画面、上に前の画面を op の濃さで。
            # 最初のコマ(op=1)は前の画面だけで塗りきれるので、新しい画面を
            # 撮るのは薄くなり始めてから(撮る重さを1コマに重ねない)。
            if op < 1.0:
                if self.under is None:
                    fn = self.under_fn
                    self.under = fn() if fn is not None else None
                if self.under is not None:
                    p.drawPixmap(self.rect(), self.under)
                else:
                    p.fillRect(self.rect(), QColor(0, 0, 0))
            p.setOpacity(op)
            p.drawPixmap(self.rect(), self.pixmap)
            p.end()
            return
        p.setOpacity(op)
        if self.pixmap is not None:
            p.drawPixmap(self.rect(), self.pixmap)
        else:
            p.fillRect(self.rect(), QColor(0, 0, 0))
        p.end()

    # 切り替えのあいだは押せない(重ねた絵の下の画面へ届かせない)。
    def mousePressEvent(self, ev):
        ev.accept()


class FadeStack(QStackedWidget):
    """フェードで切り替えられる QStackedWidget。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._overlay = _FadeOverlay(self)
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._on_finished)
        self._phase = None
        self._target = None
        self._done_cb = None
        self._ms = 350
        #: 黒を自分で描いてもらっている画面(フェードの間だけ)。
        self._dim_widget = None

    def is_fading(self):
        return self._phase is not None

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._overlay.setGeometry(self.rect())

    # ------------------------------------------------------------------
    # 出す前に大きさを決める。QStackedWidget は**いま出ている画面だけ**に
    # 大きさを渡すので、初めて出す画面は前の(既定の)大きさのまま1コマ
    # 塗られ、resizeEvent で置く部品(結果画面のボタンなど)が一瞬ずれて
    # 見えていた。切り替える前・絵に撮る前に、ここで大きさと中の配置を
    # 済ませておく。
    def presize(self, widget):
        if widget is None:
            return
        r = self.contentsRect()
        if r.width() <= 0 or r.height() <= 0:
            return
        if widget.geometry() != r:
            widget.setGeometry(r)
        if widget.testAttribute(Qt.WA_PendingResizeEvent):
            # 隠れている部品の resizeEvent は「出す直前」まで待たされる。
            # 配置をここで確定させるため、今すぐ届ける。
            QApplication.sendEvent(widget, _resize_event(widget))
            widget.setAttribute(Qt.WA_PendingResizeEvent, False)
        QApplication.sendPostedEvents(widget, QEvent.LayoutRequest)
        lay = widget.layout()
        if lay is not None:
            lay.activate()

    def addWidget(self, widget):
        idx = super().addWidget(widget)
        self.presize(widget)
        return idx

    def setCurrentWidget(self, widget):
        self.presize(widget)
        super().setCurrentWidget(widget)

    def setCurrentIndex(self, index):
        self.presize(self.widget(index))
        super().setCurrentIndex(index)

    def fade_to(self, widget, mode="cross", ms=350, done=None):
        """widget へフェードで切り替える。done は終わったときに呼ぶ。"""
        self._finish_now()
        self._target = widget
        self._done_cb = done
        self._ms = max(1, int(ms))
        ov = self._overlay
        ov.setGeometry(self.rect())
        self.presize(widget)
        ov.under = None
        ov.under_fn = None
        ov.set_opaque(False)
        if mode == "cross":
            cur = self.currentWidget()
            ov.pixmap = cur.grab() if cur is not None else None
            self.setCurrentWidget(widget)
            if ov.pixmap is not None:
                ov.under_fn = widget.grab
                ov.set_opaque(True)
            self._start("out", 1.0, 0.0, self._ms)
        else:
            ov.pixmap = None
            cur = self.currentWidget()
            if cur is not None and cur is not widget and _dim_capable(cur):
                # 再生画面から出る: 画面自身に黒を濃くしてもらう。
                self._dim_widget = cur
                _set_dim(cur, 0.0)
                self._start("in", 0.0, 1.0, self._ms // 2)
                return
            self._start("in", 0.0, 1.0, self._ms // 2)
        ov.opacity = self._anim.startValue()
        ov.show()
        ov.raise_()

    def _start(self, phase, a, b, ms):
        self._phase = phase
        self._anim.stop()
        # 値を入れ替えるだけで前の位置の値が流れてくる(終わった位置で
        # 終点を替えると、その値が出る)。始める前の分は受けない。
        self._anim.blockSignals(True)
        self._anim.setDuration(max(1, ms))
        self._anim.setStartValue(float(a))
        self._anim.setEndValue(float(b))
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.blockSignals(False)
        self._anim.start()

    def _on_value(self, v):
        w = self._dim_widget
        if w is not None:
            _set_dim(w, float(v))
            return
        self._overlay.opacity = float(v)
        self._overlay.update()

    def _on_finished(self):
        if self._phase == "in":
            # 真っ黒。ここで切り替えて、黒を薄くする。
            prev, self._dim_widget = self._dim_widget, None
            target = self._target
            ms = self._ms - self._ms // 2
            if _dim_capable(target):
                # 先に真っ黒にしてから出す(1コマも素の絵を見せない)。
                _set_dim(target, 1.0)
                self.setCurrentWidget(target)
                self._overlay.hide()
                if prev is not None and prev is not target:
                    _set_dim(prev, 0.0)
                self._dim_widget = target
                self._start("out", 1.0, 0.0, ms)
                return
            self.setCurrentWidget(target)
            if prev is not None:
                _set_dim(prev, 0.0)
            if getattr(target, "native_gl", False):
                self._end()         # GL の窓の上には重ねられない
                return
            self._overlay.under = None
            self._overlay.under_fn = None
            self._overlay.set_opaque(False)
            self._overlay.opacity = 1.0
            self._overlay.show()
            self._overlay.raise_()
            self._overlay.update()
            self._start("out", 1.0, 0.0, ms)
            return
        self._end()

    def _clear_dim(self):
        w, self._dim_widget = self._dim_widget, None
        if w is not None:
            _set_dim(w, 0.0)

    def _release_overlay(self):
        ov = self._overlay
        ov.hide()
        ov.pixmap = None
        ov.under = None
        ov.under_fn = None
        ov.set_opaque(False)

    def _end(self):
        self._phase = None
        self._clear_dim()
        self._release_overlay()
        cb, self._done_cb = self._done_cb, None
        if cb is not None:
            cb()

    def _finish_now(self):
        """途中のフェードを打ち切る(次のフェードや画面の直接切り替えの前)。"""
        if self._phase is None:
            return
        self._anim.stop()
        if self._target is not None:
            self.setCurrentWidget(self._target)
        self._phase = None
        self._clear_dim()
        self._release_overlay()
        self._done_cb = None

    def cancel(self):
        self._finish_now()


def _dim_capable(widget):
    """画面自身が黒を描けるか(再生画面)。"""
    fn = getattr(widget, "supports_fade_dim", None)
    try:
        return bool(fn()) if fn is not None else False
    except Exception:  # noqa: BLE001
        return False


def _set_dim(widget, v):
    fn = getattr(widget, "set_fade_dim", None)
    if fn is not None:
        try:
            fn(v)
        except Exception:  # noqa: BLE001
            pass


def _resize_event(widget):
    from PySide6.QtGui import QResizeEvent
    return QResizeEvent(widget.size(), widget.size())
