import math
import os
import time as _time

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (QKeySequence, QMouseEvent, QPainter,
                           QRegion, QShortcut)
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QDockWidget, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

#: Qt の「上限なし」。setFixedSize で入った上下限を外すのに使う。
_QWIDGETSIZE_MAX = 16777215

from neotja.audio_engine import AudioEngine, HitSoundEngine, MetronomeEngine, SongDecodeWorker
from neotja.worker_util import detach_worker
from neotja.bpm_tap import BpmTapper
from neotja.chart_preview_widget import (
    ChartPreviewWidget, SPEED_STEPS as _SPEED_STEPS, snap_speed,
    snap_speed_index,
)
from neotja.tja_analyzer import balloon_pop_spans
from neotja import theme

# NeoTJAPlayer(ゲーム窓)は情報バー・波形も含めてアプリのテーマに
# 関わらず常にダーク基調で見せる。ここで固定のダークパレットを参照する。
_DARK = theme.THEMES["dark"]
from neotja.chart_edit_widget import ChartEditWaveform
from neotja.waveform_data import bar_grid_clicks
from neotja.waveform_widget import WaveformWidget


# ゲーム画面の上に浮かせるボタン(モード切替/コース/録画)の寸法。
# 画面の絵は 1280x720 の固定寸法なので、ボタンだけ拡大率やフォント設定で
# 大きくなると位置も大きさも合わなくなる。ここで固定して外の設定から切り離す。
LANE_BUTTON_H = 26
LANE_BUTTON_FONT_PX = 12

# 再生速度の段階。レーン側(chart_preview_widget)と同じ並びを使い回す。
# スライダーの値は「倍率そのもの」ではなく SPEED_STEPS の番号(0〜3)で、
# 整数レンジにすることで中途半端な位置に止まらない=段階スナップになる。
SPEED_STEPS = _SPEED_STEPS
SPEED_DEFAULT = 1.00


class ChartInfoBar(QWidget):
    """Panel shown under the game-preview lane: transport buttons (mouse
    equivalents of the lane's Space/Q/PgUp/PgDn shortcuts), song title/
    subtitle, then stat cards - BPM/SCROLL/MEASURE (updated live off the
    playback position via set_realtime_info), roll/balloon totals plus a
    live cumulative tap count, and note count/don-ka ratio/total time.
    Cards mixing static (per-course, set once per edit) and live (per-frame)
    values keep the two halves of their text in separate labels rather than
    rebuilding one string from both, so per-frame updates don't need to know
    the static half."""

    BRANCH_LABELS = {"N": "普通", "E": "玄人", "M": "達人"}

    def __init__(self, parent=None, toggle_play_cb=None, return_anchor_cb=None,
                 seek_prev_cb=None, seek_next_cb=None, cycle_course_cb=None, cycle_branch_cb=None):
        super().__init__(parent)
        self.setFixedHeight(300)
        # (frame, header, value, color_key) per card, for refresh_theme().
        self._cards = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # 情報モードの上部にあった再生系マウス操作ボタン(再生/一時停止・
        # アンカーへ・前後の小節)は削除した(要望)。すべてキーボード
        # (Space/Q/PgUp/PgDn)で操作できるので情報モードは情報表示に専念する。
        # コース/分岐だけは「今どのコースを見ているか」の表示も兼ねるので残す。
        button_row = QHBoxLayout()
        self.btn_course = QPushButton("コース: -")
        self.btn_branch = QPushButton("分岐: -")
        self.btn_branch.setVisible(False)  # only shown for courses that actually have #BRANCHSTART
        if cycle_course_cb:
            self.btn_course.clicked.connect(cycle_course_cb)
        if cycle_branch_cb:
            self.btn_branch.clicked.connect(cycle_branch_cb)
        for btn in (self.btn_course, self.btn_branch):
            # NoFocus keeps these mouse-only so keyboard focus - and
            # Space/Q/PgUp/PgDn - always stays on the lane.
            btn.setFocusPolicy(Qt.NoFocus)
            button_row.addWidget(btn)
        layout.addLayout(button_row)

        # 曲名・サブタイトルはゲーム画面(右上)に出すようになったので、ここには
        # 置かない。以前は「値だけ入れて隠しておく」ためにラベルだけ残していたが、
        # **どのレイアウトにも入れていない親なしのラベル**だったため、
        # set_static_info が setVisible(True) を呼んだ瞬間に Qt がそれを
        # トップレベルウィンドウとして開いてしまい、サブタイトルだけが乗った
        # 小さな別窓が現れていた。ラベルごと消すのが正しい直し方。
        #
        # Cards are tinted by category so the panel reads at a glance instead
        # of everything looking the same: BPM/SCROLL/MEASURE in green (ok
        # color - distinct from the don/ka red/blue used below so tempo info
        # doesn't read as "note-related"), roll/balloon stats in roll-yellow,
        # time in neutral white, and note-count/men/fuchi in neutral/don-red/
        # ka-blue respectively (don=men, ka=fuchi - the same red/blue used
        # for don/ka notes in the lane itself).
        self.card_bpm, self.lbl_bpm = self._make_card("BPM", "ok")
        self.card_scroll, self.lbl_scroll = self._make_card("SCROLL", "ok")
        self.card_measure, self.lbl_measure = self._make_card("MEASURE", "ok")
        layout.addLayout(self._row(self.card_bpm, self.card_scroll, self.card_measure))

        self.card_roll, self.lbl_roll = self._make_card("連打(風船個数)", "roll")
        self.card_roll_est, self.lbl_roll_est = self._make_card("推定連打数(風船打数)", "roll")
        self.card_time, self.lbl_time = self._make_card("総時間", "fg_bright")
        layout.addLayout(self._row(self.card_roll, self.card_roll_est, self.card_time))

        self.card_notes, self.lbl_notes = self._make_card("ノーツ数", "fg_bright")
        self.card_men, self.lbl_men = self._make_card("面", "don")
        self.card_fuchi, self.lbl_fuchi = self._make_card("縁", "ka")
        layout.addLayout(self._row(self.card_notes, self.card_men, self.card_fuchi))
        layout.addStretch()

    def _make_card(self, label_text: str, color_key: str = None):
        """`color_key` is a COLORS *key*, not a resolved value: the palette is
        mutated in place on theme change, so keeping the key is what lets
        refresh_theme() re-resolve it."""
        frame = QFrame()
        v = QVBoxLayout(frame)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        lbl_header = QLabel(label_text)
        lbl_header.setAlignment(Qt.AlignCenter)
        lbl_value = QLabel("-")
        lbl_value.setAlignment(Qt.AlignCenter)
        value_font = lbl_value.font()
        value_font.setBold(True)
        value_font.setPointSize(18)
        lbl_value.setFont(value_font)
        v.addWidget(lbl_header)
        v.addWidget(lbl_value)
        self._cards.append((frame, lbl_header, lbl_value, color_key))
        self._style_card(frame, lbl_header, lbl_value, color_key)
        return frame, lbl_value

    @staticmethod
    def _style_card(frame, lbl_header, lbl_value, color_key):
        # 情報バーは常にダーク基調(_DARK)で描く。テーマに追従しない。
        color = _DARK[color_key] if color_key else None
        border_color = color or _DARK["border"]
        value_color = color or _DARK["fg_bright"]
        frame.setStyleSheet(
            f"QFrame {{ background-color: {_DARK['surface']}; border: 1px solid {border_color};"
            f" border-radius: 6px; }}"
        )
        lbl_header.setStyleSheet(f"color: {_DARK['fg_dim']}; font-size: 10px; border: none;")
        lbl_value.setStyleSheet(f"border: none; color: {value_color};")

    def refresh_theme(self):
        """常にダーク基調に固定しているので、テーマ切替時も同じダーク色で
        引き直すだけ(実質不変)。呼び出し側との整合のため残している。"""
        for frame, lbl_header, lbl_value, color_key in self._cards:
            self._style_card(frame, lbl_header, lbl_value, color_key)

    @staticmethod
    def _row(*cards):
        row = QHBoxLayout()
        for card in cards:
            row.addWidget(card, 1)
        return row

    def set_static_info(self, title: str, subtitle: str, course_stats: dict):
        # title / subtitle はゲーム画面側が描くので、ここでは統計カードだけ更新する
        # (引数は呼び出し側の都合でそのまま受け取る)。
        if not course_stats:
            self.lbl_notes.setText("-")
            self.lbl_men.setText("-")
            self.lbl_fuchi.setText("-")
            self.lbl_time.setText("-")
            self.lbl_roll.setText("-")
            return
        don = course_stats.get("don_count", 0)
        ka = course_stats.get("ka_count", 0)
        total = don + ka
        if total > 0:
            self.lbl_men.setText(f"{don} ({don / total * 100:.0f}%)")
            self.lbl_fuchi.setText(f"{ka} ({ka / total * 100:.0f}%)")
        else:
            self.lbl_men.setText("-")
            self.lbl_fuchi.setText("-")
        self.lbl_notes.setText(str(course_stats.get("notes", 0)))
        self.lbl_time.setText(course_stats.get("time", "-"))

        rolls_info = course_stats.get("rolls_info") or []
        balloons_info = course_stats.get("balloons_info") or []
        roll_seconds = sum(r["duration"] for r in rolls_info)
        self.lbl_roll.setText(f"{roll_seconds:.2f}秒({len(balloons_info)}個)")

    def set_course_info(self, label: str, color: str, level):
        text = f"{label or '-'} ★{level}" if level is not None else (label or "-")
        self.btn_course.setText(f"コース: {text}")
        if color:
            self.btn_course.setStyleSheet(f"color: {color}; font-weight: bold;")

    def set_branch_info(self, level: str, has_branches: bool):
        self.btn_branch.setVisible(has_branches)
        if has_branches:
            self.btn_branch.setText(f"分岐: {self.BRANCH_LABELS.get(level, level)}")

    @staticmethod
    def _trunc(value: float, decimals: int) -> float:
        factor = 10 ** decimals
        return math.trunc(value * factor) / factor

    def set_realtime_info(self, bpm, scroll, num, den, cumulative_hits):
        # BPM: shown as specified, just truncated (not rounded) past 4
        # decimals, with no padding - "150" stays "150", not "150.0000".
        bpm_str = f"{self._trunc(bpm, 4):.4f}".rstrip("0").rstrip(".")
        self.lbl_bpm.setText(bpm_str or "0")
        # SCROLL: always exactly 3 decimals (truncated, not rounded) so it
        # reads as a fixed-width "1.000"-style value regardless of how much
        # precision the chart specifies.
        self.lbl_scroll.setText(f"{self._trunc(scroll, 3):.3f}")
        self.lbl_measure.setText(f"{num}/{den}")
        self.lbl_roll_est.setText(str(cumulative_hits))


class ScaledHost(QWidget):
    """中身(ゲーム画面)を原寸で1枚に描いて、それを1回だけ縮小して貼る入れ物。

    **なぜこの方式か**: 以前 QGraphicsScene + QGraphicsProxyWidget に載せて
    拡大縮小を試したところ、倍率 1.0 でも再生の描画が 8〜11倍重くなった
    (描画呼び出しのたびに変換行列が乗るため)。ここでは「原寸で普通に1枚
    描く + 縮小ブリット1回」しか増えないので、倍率を下げても重くならない。

    倍率 1.0 のときはこの入れ物を素通りさせる(中身をそのまま子として見せ、
    オフスクリーンを挟まない)。つまり**等倍の経路は今までと1命令も変わらない**。

    縮小中は中身を隠して自前のタイマーで描き直す。隠すと中身の update() が
    効かなくなるので、代わりにここが 120fps 相当で render() を回す。
    マウスは座標を倍率で割って中身へ転送する(レーン上のボタンが押せるように)。"""

    FRAME_MS = 8          # 120fps 相当。レーン側の目標と揃える。

    def __init__(self, content, parent=None):
        super().__init__(parent)
        self._content = content
        content.setParent(self)
        content.move(0, 0)
        self._scale = 1.0
        self._buf = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self.refit()

    def scale(self) -> float:
        return self._scale

    def set_scale(self, s: float):
        # 上限を 1.0 から 4.0 へ。全画面(鑑賞会用)では 1280x720 の絵を画面
        # いっぱいまで**拡大**する必要がある。描き方は縮小のときと同じで、
        # 倍率をかけた painter に描かせるだけなので、拡大でも中間画像は要らず
        # 絵も滑らかに出る(1280 -> 4K でも 3倍まで)。
        s = max(0.1, min(4.0, float(s)))
        if abs(s - self._scale) < 1e-6:
            return
        self._scale = s
        if self._passthrough():
            self._timer.stop()
            self._content.show()
        else:
            self._content.hide()
            self._timer.start(self.FRAME_MS)
        self.refit()

    def _passthrough(self) -> bool:
        # 等倍のときだけ素通り。以前は「0.999 以上」= 拡大も素通り扱いで、
        # 全画面で倍率を上げても絵が大きくならなかった。
        return abs(self._scale - 1.0) < 0.001

    def refit(self):
        """中身の大きさが変わった/倍率が変わったときに自分の大きさを取り直す。"""
        cw, ch = self._content.width(), self._content.height()
        self.setFixedSize(max(1, int(round(cw * self._scale))),
                          max(1, int(round(ch * self._scale))))
        self._buf = None
        self.update()

    def paintEvent(self, event):
        if self._passthrough():
            return                      # 中身が子として自分で描く(従来どおり)
        # **原寸で描いてから縮小するのではなく、最初から縮小後の大きさで描く。**
        # 一度 1280x720 に描いてから縮小すると、その縮小1回だけで 75% のとき
        # 3.8ms かかっていた(中身の描画そのものより重い)。倍率をかけた painter を
        # 渡すと、Qt は円も文字もその大きさで直接ラスタライズするので、大きな
        # 中間画像も縮小工程も要らない。画面側は deviceTransform() を見て
        # 静的キャッシュを同じ倍率で焼き直すので、絵も素直に小さくなる。
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.scale(self._scale, self._scale)
        self._content.render(p, QPoint(0, 0), QRegion(),
                             QWidget.RenderFlag.DrawChildren)

    # --- マウスの転送(縮小中だけ) ---------------------------------------
    def _forward(self, event, kind) -> bool:
        if self._passthrough():
            return False
        pos = event.position() / self._scale
        pt = pos.toPoint()
        target = self._content.childAt(pt) or self._content
        local = target.mapFrom(self._content, pt)
        QApplication.sendEvent(target, QMouseEvent(
            kind, QPointF(local), event.globalPosition(),
            event.button(), event.buttons(), event.modifiers()))
        return True

    def mousePressEvent(self, event):
        if not self._forward(event, QEvent.MouseButtonPress):
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if not self._forward(event, QEvent.MouseButtonRelease):
            super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if not self._forward(event, QEvent.MouseMove):
            super().mouseMoveEvent(event)


class GamePreviewWindow(QWidget):
    """Standalone, non-modal window hosting the game-style chart preview.

    The preview's lane is a fixed pixel size (see ChartPreviewWidget) so it
    reads consistently regardless of tempo/song, which doesn't play well
    with being squeezed into a dock that shares width with the main editor -
    a dedicated window sidesteps that entirely. The window itself is a fixed
    size matching the lane (no resize handles), rather than free-floating
    dead space around a fixed-size lane looking unbalanced.

    Also auto-pauses playback when the window stops being the active one
    (alt-tab away, click back to the main editor, etc.) so the song doesn't
    keep playing - and hit sounds firing - in the background unattended."""

    closed = Signal()

    def __init__(self, chart_preview, bottom_widget: QWidget, parent=None, pause_cb=None,
                 lane_widget: ChartPreviewWidget = None):
        """chart_preview には、レーン単体(ChartPreviewWidget)でも本家レイアウト
        (GameScreenWidget)でも渡せる。後者のときは lane_widget に中のレーンを
        渡すこと(打音表記のオン/オフで高さが変わる通知を受けるため)。"""
        # 最大化ボタンは出さない。この窓は中身(ゲーム画面＋下部パネル)に
        # 合わせて setFixedSize で大きさを固定しているので、最大化しても中身は
        # 広がらず、枠だけが変わってタイトルバーしか残らない潰れた窓になる。
        # 押せてしまうこと自体が罠なので、ボタンごと消す。
        super().__init__(parent, Qt.Window | Qt.CustomizeWindowHint
                         | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
                         | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle("NeoTJAPlayer")
        # アプリのテーマに関わらず窓全体をダーク基調に固定する。ウィジェット
        # 自身のスタイルシートは app 全体の QSS より優先されるので、ライト
        # テーマに切り替えても速度スライダー・ラベル・ボタン等はダークのまま。
        self.setStyleSheet(theme.build_qss(theme.THEMES["dark"]))
        self._pause_cb = pause_cb
        self._chart_preview = chart_preview
        self._lane = lane_widget if lane_widget is not None else chart_preview
        self._bottom_widget = bottom_widget
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # ゲーム画面だけを倍率つきの入れ物に入れる。下部パネル(速度スライダー・
        # 波形・情報)は本物の Qt ウィジェットなので、縮小すると文字がにじんで
        # 押しにくくなる。小さい画面で困るのは 720px の絵のほうなので、そこだけ
        # 縮める。
        self.scaled_host = ScaledHost(chart_preview, self)
        layout.addWidget(self.scaled_host)
        layout.addWidget(bottom_widget)
        self._fullscreen = False
        self._fs_scale = 1.0
        self._fs_geometry = None
        self._refit()
        # 打音表記のオン/オフでレーン側の高さ(帯 26px の有無)が変わるので、
        # 窓の固定サイズも取り直す。
        self._lane.heightChanged.connect(self._on_preview_height_changed)
        # F11 で全画面、Esc で戻る。窓かその子にフォーカスがあれば効く
        # (レーンがフォーカスを持っているので keyPressEvent では拾えない)。
        for seq, fn in ((QKeySequence(Qt.Key_F11), self.toggle_fullscreen),
                        (QKeySequence(Qt.Key_Escape), self.exit_fullscreen)):
            sc = QShortcut(seq, self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(fn)

    def refit(self):
        """中身の高さが変わったときに窓の固定サイズを取り直す。

        モード切替でゲーム画面が 360 <-> 720 に変わり、下部パネルの高さも
        変わるので、外(preview_dock)から呼べるようにしてある。"""
        self._refit()

    def _refit(self):
        if self._fullscreen:
            # 全画面のあいだは窓の大きさを固定しない(固定すると画面いっぱいの
            # 表示が壊れる)。モード切替で中身の高さが変わったときは、倍率を
            # 取り直すだけでよい。
            self._fit_fullscreen()
            return
        # bottom_widget はモード別スタック + 速度行。ページごとに高さが違うと
        # モード切替のたびに窓がガタつくので、呼び出し側で最も高いページに合わせて
        # 固定済み。その固定高さ(=minimumHeight)を使って窓サイズも一定に保つ。
        top = self._chart_preview
        if hasattr(top, "is_compact"):
            # 本家レイアウト(GameScreenWidget)は自分で固定サイズを持っている。
            pass
        else:
            top.setFixedSize(int(ChartPreviewWidget.LANE_WIDTH), top.widget_height())
        # 窓の大きさは「倍率をかけたあとのゲーム画面」＋下部パネル。
        self.scaled_host.refit()
        self.setFixedSize(max(self.scaled_host.width(), self._bottom_widget.minimumWidth()),
                          self.scaled_host.height() + self._bottom_widget.minimumHeight())

    # ------------------------------------------------------------------
    # 全画面(鑑賞会用)
    # ------------------------------------------------------------------
    def toggle_fullscreen(self):
        if self._fullscreen:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self):
        """画面いっぱいにゲーム画面だけを出す。

        下部パネル(速度スライダー・波形・情報)とレーン上のボタン類は隠す。
        鑑賞会で見せたいのは絵だけで、操作するものが写り込むと邪魔になる。
        隠しても困らないのは、モード切替(Tab)・再生(Space)・コース切替が
        すべてキーで足りるため。"""
        if self._fullscreen:
            return
        self._fullscreen = True
        self._fs_geometry = self.saveGeometry()
        self._fs_scale = self.scaled_host.scale()
        self._bottom_widget.hide()
        self.set_overlay_visible(False)
        # setFixedSize で入っている上下限を外さないと全画面にならない。
        self.setMinimumSize(0, 0)
        self.setMaximumSize(_QWIDGETSIZE_MAX, _QWIDGETSIZE_MAX)
        self.showFullScreen()
        # 画面の大きさが確定してから倍率を決める(showFullScreen の直後は
        # まだ元の大きさのことがある)。
        QTimer.singleShot(0, self._fit_fullscreen)

    def exit_fullscreen(self):
        if not self._fullscreen:
            return
        self._fullscreen = False
        self.scaled_host.set_scale(self._fs_scale)
        self._bottom_widget.show()
        self.set_overlay_visible(True)
        self.showNormal()
        self._refit()
        if self._fs_geometry is not None:
            self.restoreGeometry(self._fs_geometry)

    def _fit_fullscreen(self):
        """画面に収まる最大の倍率にして、中央へ置く。"""
        if not self._fullscreen:
            return
        content = self._chart_preview
        cw, ch = max(1, content.width()), max(1, content.height())
        avail = self.size()
        scale = min(avail.width() / float(cw), avail.height() / float(ch))
        self.scaled_host.set_scale(max(0.05, scale))
        self.layout().setAlignment(self.scaled_host, Qt.AlignCenter)

    def set_overlay_visible(self, visible: bool):
        """レーンの上に浮かせているボタン類(モード/コース/録画/倍率/FPS)の
        表示。全画面のときに隠すためのもの。

        ScaledHost の直接の子のうち、中身(ゲーム画面)以外がそれにあたる。
        preview_dock 側がどのボタンを置いたかをここで知らずに済むよう、
        名指しではなく「中身以外の子」でまとめて扱う。"""
        for child in self.scaled_host.children():
            if not isinstance(child, QWidget):
                continue
            if child is self._chart_preview:
                continue
            child.setVisible(visible)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 全画面へ移るとき、showFullScreen() の直後はまだ元の大きさのことが
        # ある。倍率をそこで決めると等倍のままになるので(実測)、大きさが
        # 確定したこのタイミングで決め直す。
        if self._fullscreen:
            self._fit_fullscreen()

    def _on_preview_height_changed(self, _h):
        self._refit()

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        # ボタンを消しても Win+↑ やタイトルバーのダブルクリックからは最大化
        # できてしまうので、なってしまったら元に戻す。固定サイズの窓を最大化
        # すると中身が広がらないまま枠だけ変わって潰れて見えるため。
        if event.type() == QEvent.WindowStateChange and self.isMaximized():
            self.showNormal()
            return
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow() and self._pause_cb:
            self._pause_cb()


def _roll_tick_notes(spans, bpm_index):
    """Expands roll/balloon/kusudama spans - (start, end, ..., hits) tuples,
    as returned in build_preview_timeline()'s "rolls"/"balloons"/"kusudamas"
    - into evenly spaced virtual note events across the span, so
    HitSoundEngine's normal per-note schedule also produces the rapid
    drumroll sound during a roll/balloon/kusudama instead of staying silent
    between its head and tail. Always don ("men") hits, not alternating don/
    ka - a real taiko roll is struck face-only regardless of hand. This same
    expansion feeds both audio backends: preview_dock hands the resulting
    (time, char, bpm) ticks to hit_sounds.set_schedule(), which is either the
    mixer's _HitSoundAdapter (sample-accurate scheduling) or the legacy
    HitSoundEngine (16ms-tick polling) - both accept the identical tuple
    shape, so this expansion doesn't need to know which backend is active.
    `bpm_index` differs between span shapes (rolls carry char before bpm,
    balloons/kusudamas don't), so the caller passes which column holds it."""
    ticks = []
    for span in spans:
        start, end, hits = span[0], span[1], span[-1]
        bpm = span[bpm_index]
        if hits <= 0:
            continue
        interval = (end - start) / hits
        for i in range(hits):
            ticks.append((start + i * interval, "1", bpm))
    return ticks


def parse_preview_headers(content: str) -> dict:
    title = ""
    subtitle = ""
    wave = ""
    bpm = None
    offset = 0.0
    for l in content.split("\n"):
        if l.startswith("TITLE:"):
            title = l[6:].strip()
        elif l.startswith("SUBTITLE:"):
            subtitle = l[9:].strip()
            # Convention: SUBTITLE always carries a leading "--" marker, with
            # the actual subtitle text (if any) right after it - not a
            # separator to strip only when the whole value is "--".
            if subtitle.startswith("--"):
                subtitle = subtitle[2:].strip()
        elif l.startswith("WAVE:"):
            wave = l[5:].strip()
        elif l.startswith("BPM:"):
            try:
                bpm = float(l[4:].strip())
            except ValueError:
                pass
        elif l.startswith("OFFSET:"):
            try:
                offset = float(l[7:].strip())
            except ValueError:
                pass
    return {"title": title, "subtitle": subtitle, "wave": wave, "bpm": bpm, "offset": offset}


def _fmt_time(ms: int) -> str:
    total = max(0, ms) // 1000
    m, s = divmod(total, 60)
    return f"{m}:{s:02}"


class PreviewDock(QDockWidget):
    """Dockable panel: play the song referenced by WAVE:, tap-measure BPM, and
    line up OFFSET against a waveform with a beat-grid overlay. Writes the
    OFFSET result back into the editor's OFFSET: header line automatically as
    it's adjusted (see _on_offset_value_changed)."""

    def __init__(self, apply_offset_cb, parent=None, seek_cursor_cb=None, volume_cb=None,
                 duration_ready_cb=None, expanded_changed_cb=None, refresh_preview_cb=None,
                 course_select_cb=None, game_preview_changed_cb=None, branch_select_cb=None,
                 audio_backend="mixer", sfx_volume_cb=None,
                 master_volume_cb=None, audio_output_device="",
                 waveform_stereo=True, waveform_stereo_cb=None,
                 se_text_enabled=True, record_cb=None, note_edit_cb=None,
                 config_data=None, save_settings_cb=None,
                 checkpoint_lines_cb=None):
        super().__init__("音源プレビュー", parent)
        self.apply_offset_cb = apply_offset_cb
        # 作譜モードで音符が置かれたときの書き戻し(MainWindow が持つ)。
        self.note_edit_cb = note_edit_cb
        self.config_data = config_data if config_data is not None else {}
        # config_data を書き換えたあとディスクへ落とすためのもの(MainWindow が持つ)。
        self.save_settings_cb = save_settings_cb
        # チェックポイントはエディタの「行」が正。プレビューで作ったものも
        # 行へ直してエディタへ返し、エディタ側の変更もここへ流れてくる。
        self.checkpoint_lines_cb = checkpoint_lines_cb
        self._checkpoint_lines = set()
        self._bar_lines = []          # 小節ごとの開始行(1始まり)
        self._bar_times_chart = []    # 同じ並びの開始時刻(譜面時間)
        self.waveform_stereo_cb = waveform_stereo_cb
        self._waveform_stereo = bool(waveform_stereo)
        # 打音表記の表示可否(settings.json の se_text_enabled)。
        self._se_text_enabled = bool(se_text_enabled)
        self.seek_cursor_cb = seek_cursor_cb
        self.volume_cb = volume_cb
        self.sfx_volume_cb = sfx_volume_cb
        self.master_volume_cb = master_volume_cb
        # 音量は「マスター × 個別の比率」。ここで両方を覚えておき、経路
        # (ミキサー/レガシー)ごとの掛け方の違いは _apply_*_volume に閉じ込める。
        self._master_volume = 1.0
        self._sfx_ratio = 0.9
        # ワイヤレス調整(出力遅延の補正、ms)。0 = 補正なし。
        self._output_offset_ms = 0.0
        self.expanded_changed_cb = expanded_changed_cb
        self.duration_ready_cb = duration_ready_cb
        self.refresh_preview_cb = refresh_preview_cb
        self.course_select_cb = course_select_cb
        self.branch_select_cb = branch_select_cb
        self.game_preview_changed_cb = game_preview_changed_cb
        # レーン上の録画ボタンから呼ぶ(動画書き出しダイアログを開く)。
        self.record_cb = record_cb

        # 再生バックエンドの選択(settings.json の audio_backend、既定 "mixer")。
        # "mixer": sounddevice の単一ソフトウェアミキサー(曲+打音+メトロノームを
        # サンプル単位で1つのクロックにミックス、レイテンシ補正不要)。ストリームが
        # 開けない/モジュールが無い場合はレガシー三点セット(QMediaPlayer +
        # QSoundEffect×2)へ透過的に退避する。"qt" は最初からレガシー強制。
        self._mixer_active = False
        self._backend_notice = ""
        if audio_backend != "qt":
            try:
                from neotja.mixer_engine import MixerAudioEngine, list_output_devices
                self.audio = MixerAudioEngine(self, device_name=audio_output_device or "")
                self.metronome = self.audio.metronome
                self.hit_sounds = self.audio.hit_sounds
                self._mixer_active = True
                # 設定で選んだデバイスが今つながっていないときは既定で開かれる。
                # 音は出るので致命的ではないが、黙って別の口から鳴っていると
                # 分からないので一言出す。
                if audio_output_device and audio_output_device not in [
                        n for n, _label in list_output_devices()]:
                    self._backend_notice = (
                        f"設定の出力デバイス「{audio_output_device}」が見つからないため、"
                        "既定のデバイスで再生します。")
            except Exception:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                self._backend_notice = "ミキサー音声を初期化できなかったため従来方式に切り替えました。"
                self.audio = None
        if not self._mixer_active:
            self.audio = AudioEngine(self)
            self.metronome = MetronomeEngine(self)
            # レガシー経路のみ: ChartPreviewWidget の 16ms tick から
            # hit_sounds.check_and_play() が呼ばれる(下の _build_ui 参照)。
            self.hit_sounds = HitSoundEngine(self)

        self.audio.positionChanged.connect(self._on_position_changed)
        self.audio.durationChanged.connect(self._on_duration_changed)
        self.audio.playingChanged.connect(self._on_playing_changed)
        self.audio.mediaStatusChanged.connect(self._on_media_status_changed)
        # ミキサー経路ではメトロノームは内部でサンプル単位に処理されるので、この
        # 接続はアダプタの no-op に届くだけ(無害)。レガシーでは従来通り駆動する。
        self.audio.positionChanged.connect(self.metronome.on_position_changed)
        # ミキサー経路のみ: 音声コールバックが死んだ / 打音WAVを読めなかった、を
        # 利用者に見える形で伝える(以前はどちらも無言だった)。
        if hasattr(self.audio, "audioError"):
            self.audio.audioError.connect(self._on_audio_error)
        if hasattr(self.audio, "sfxLoadFailed"):
            self.audio.sfxLoadFailed.connect(self._on_sfx_load_failed)

        self.tapper = BpmTapper()

        self._wave_dir = None
        self._current_wave_path = None
        # 選曲画面の試聴で読んだ音源(load_wave_only 参照)。
        # _current_wave_path とは別に持つ。
        self._audition_wave_path = None
        self._decode_worker = None
        self._waveform_mips = None
        self._editor_bpm = None
        self._editor_offset = 0.0
        self._editor_subtitle = ""
        self._editor_metronome_clicks = []
        self._editor_notes = []
        self._preview_notes = []  # 作譜モードの波形の帯に描く音符 [(time,char,bpm,scroll,se)]
        self._preview_spans = ([], [], [])  # (rolls, balloons, kusudamas) 同上
        # 作譜モードの波形に描く命令注釈 (bpm/scroll/measure変化, gogo区間)
        self._preview_commands = ([], [], [], [])
        # 作譜モードの波形グリッド用のクリック列 [(chart_time, is_measure)]。
        # メトロノーム用の build_metronome_clicks は小節途中の #BPMCHANGE を
        # 小節全体に一括適用するため build_preview_timeline の音符/小節時刻と
        # ズレる(SUPERNOVA 等で小節線が音符とずれる)。作譜波形は音符と同じ
        # 権威データ(bar_times)から小節線を作り、確実に音符と一致させる。
        self._game_grid_clicks = []
        self._duration_ms = 0
        # positionChanged fires ~60Hz. The playhead line / time label / seek
        # slider don't need that, and the game preview extrapolates its own
        # motion from a monotonic clock (needing only occasional drift
        # correction), so running this whole handler at 60Hz just steals GUI-
        # thread time and makes the preview's frame pacing uneven (visible
        # microstutter). Throttle the handler to ~30Hz.
        self._last_pos_ui_wall = 0.0

        self._build_ui()

    def _build_ui(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        self._content_widget = content

        # Peepo式作譜(下部パネルの「作譜」モード)は実験的機能として既定オフ。
        # settings.json の peepo_chart_edit(環境設定ダイアログ「実験的機能」
        # タブ)。オフのときは「作譜」ページをスタックへ足さない(≒3モードで
        # 循環)だけにして、ChartEditWaveform 自体は常に作る。ここで作らずに
        # self.chart_edit を None のままにすると、_waveforms()/_on_preview_frame/
        # _apply_checkpoint_lines など参照箇所が全部 None チェックを持たなければ
        # ならなくなり事故りやすいので、ページに載せないだけに留める方が安全。
        self._peepo_enabled = bool(self.config_data.get("peepo_chart_edit", False))
        # 起動時のモード復元中は settings.json へ書き戻さない(_save_bottom_mode)。
        self._restoring_mode = False
        # 同じく、再生速度の復元中も書き戻さない(_save_speed)。
        self._restoring_speed = False

        self.title_label = QLabel("(WAVEファイルなし)")
        layout.addWidget(self.title_label)

        self.waveform = WaveformWidget(toggle_play_cb=self.audio.toggle_play_pause)
        self._wire_waveform(self.waveform)
        layout.addWidget(self.waveform, 1)

        self.chart_preview = ChartPreviewWidget(
            course_select_cb=self.course_select_cb,
            seek_seconds_cb=lambda sec: self.audio.seek(max(0, int(sec * 1000))),
            # Explicit play/pause (not just toggle) so the widget's own player
            # model can drive precise start-from-anchor / pause-in-place
            # transitions (机能1).
            play_cb=self.audio.play,
            pause_cb=self.audio.pause,
            # ミキサー経路では打音はサンプル単位で前もってスケジュールされるので、
            # 16ms tick からの check_and_play は不要。widget には None を渡して
            # tick を完全に黙らせる(レガシー経路のみ従来通りエンジンを渡す)。
            hit_sound_engine=None if self._mixer_active else self.hit_sounds,
            branch_select_cb=self.branch_select_cb,
            # フェーズ3: Tab で下部パネルのモード循環、Z / C で再生速度を
            # 1 段階ずつ移動(段階は chart_preview_widget.SPEED_STEPS の 4 つ)。
            cycle_bottom_mode_cb=self.cycle_bottom_mode,
            set_speed_cb=self._on_speed_from_key,
        )
        # Info-bar transport buttons mirror the lane's Space/Q shortcuts, so
        # route them through the widget's player model rather than the raw
        # audio engine.
        self.info_bar = ChartInfoBar(
            toggle_play_cb=self.chart_preview.toggle_play,
            return_anchor_cb=self.chart_preview.return_to_anchor,
            seek_prev_cb=self.chart_preview.seek_relative_measure,
            seek_next_cb=self.chart_preview.seek_relative_measure,
            cycle_course_cb=self.chart_preview.cycle_course,
            cycle_branch_cb=self.chart_preview.cycle_branch,
        )
        self.chart_preview.set_se_text_enabled(self._se_text_enabled)
        self.chart_preview.set_info_update_cb(self.info_bar.set_realtime_info)
        # 操作キー刷新: ドン/カの操作フィードバック音、f/j のリード再生。
        self.chart_preview.set_hit_feedback_cb(self._play_feedback_sound)
        self.chart_preview.set_fadein_play_cb(self._play_fadein)
        self.chart_preview.set_reveal_cb(self._end_fadein)
        # チェックポイントを作譜モードの命令レーンにも表示する(CHECK POINT)。
        # game_waveform は後(_build_sakufu_page)で作られるので遅延参照する。
        self.chart_preview.set_checkpoints_changed_cb(self._on_preview_checkpoints)

        # 下部パネルを QStackedWidget に(フェーズ3):
        #   index 0 = 非表示モード(曲名・サブタイトルだけ表示) ← 既定
        #   index 1 = 音声波形モード(見るだけ)
        #   index 2 = 作譜モード(波形 + 再生速度スライダー) ※実験的機能が有効な時のみ
        #   最後  = 情報モード(既存 ChartInfoBar)
        # 先頭(index 0)が起動時の既定表示になるので、既定を「非表示」にし、
        # Tab/トグルは 非表示→(作譜→)情報→… と循環する。非表示でも「今どの曲を
        # 見ているか」は分かるように曲名・サブタイトルだけは残す。
        self._wave_page = self._build_wave_page()
        self._edit_page = self._build_edit_page()
        self._title_page = self._build_title_page()
        # 軽量モードのページ。中身は空で、実際に表示されることは無い
        # (set_bottom_mode が通常再生と同じくページごと隠す)。それでも
        # スタックへ1枚積むのは、「bottom_stack のインデックス == モード番号」
        # という前提を崩さないため — ここを崩すと cycle_bottom_mode の
        # % count() も _mode_names の並びも一斉にずれる。
        self._lite_page = QWidget()
        self.bottom_stack = QStackedWidget()
        self.bottom_stack.addWidget(self._title_page)   # 0 非表示(曲名のみ)
        self.bottom_stack.addWidget(self._lite_page)    # 1 軽量(画面だけ・ページ無し)
        self.bottom_stack.addWidget(self._wave_page)    # 2 音声波形(見るだけ)
        if self._peepo_enabled:
            self.bottom_stack.addWidget(self._edit_page)  # 3 作譜(音符を置ける、実験的機能)
        self.bottom_stack.addWidget(self.info_bar)      # 情報(有効時4、無効時3)

        # 下部パネル = モード別スタック + 速度行(モードに関係なく常時表示)。
        # ページ高さが異なるとモード切替のたびに窓がガタつくので、最も高い
        # ページに合わせてスタックの高さを固定する。作譜ページを積んでいない
        # ときはその高さを候補から外す(でないと使わないページの高さに
        # 引きずられて下部パネルが無駄に高くなる)。
        heights = [self.info_bar.minimumHeight(), self._wave_page.sizeHint().height()]
        if self._peepo_enabled:
            heights.append(self._edit_page.sizeHint().height())
        bottom_h = max(heights)
        self.bottom_stack.setFixedHeight(bottom_h)
        self._bottom_panel = QWidget()
        bp = QVBoxLayout(self._bottom_panel)
        bp.setContentsMargins(0, 0, 0, 0)
        bp.setSpacing(0)
        self._speed_row = self._build_speed_row()
        bp.addWidget(self.bottom_stack)
        bp.addWidget(self._speed_row)
        # 通常再生ではモード別ページを隠すので、そのぶん下部パネルも縮める。
        # 縮めないと画面の下に空白が残って、窓だけ無駄に高くなる。
        self._bottom_h_full = self._bottom_panel.sizeHint().height()
        self._bottom_h_speed_only = self._speed_row.sizeHint().height()
        self._bottom_panel.setFixedHeight(self._bottom_h_full)

        # 本家レイアウト: レーンを 1280x360 の画面へ組み込む(背景・左パネル・
        # スコア・コンボ・太鼓・魂ゲージはこちらが描く)。レーン自体の描画は
        # ChartPreviewWidget のままで、寸法だけ本家に合わせて差し替わる。
        from neotja.game_screen import GameScreenWidget
        self.game_screen = GameScreenWidget(self.chart_preview, compact=True)
        self.game_preview_window = GamePreviewWindow(
            self.game_screen, self._bottom_panel, parent=self, pause_cb=self.audio.pause,
            lane_widget=self.chart_preview,
        )
        self.game_preview_window.closed.connect(self._on_game_preview_closed)

        # レーン右上に並べる3つのボタン。右から「モード切替」「コース」「録画」。
        # どれもフォーカスは奪わない(Space/Tab/PgUp/PgDn の操作対象はレーンの
        # ままにする)。録画を出すモードは set_bottom_mode 側で決めている。
        # ページ番号は上のスタック組み立てと連動させる(作譜が無効なら3つで
        # 循環)。増減したらここだけ見ればよいように名前を付ける。
        self.MODE_TITLE, self.MODE_LITE, self.MODE_WAVE = 0, 1, 2
        if self._peepo_enabled:
            self._mode_names = ["通常再生", "軽量", "音声波形", "作譜", "情報"]
            self.MODE_EDIT, self.MODE_INFO = 3, 4
        else:
            self._mode_names = ["通常再生", "軽量", "音声波形", "情報"]
            self.MODE_EDIT, self.MODE_INFO = None, 3
        # 画面の左上に「モード切替 / コース / 録画」の順で並べる。以前は
        # 右上だったが、右上は曲名が出る場所なので左へ移した。
        left = 8

        self.mode_button = self._lane_button(self._mode_names[0], 96,
                                             "下部パネルの表示切替(Tab)")
        self.mode_button.move(left, 6)
        left += 96 + 6
        self.mode_button.clicked.connect(self.cycle_bottom_mode)

        self.course_button = self._lane_button("コース: -", 150,
                                               "クリックでコース切替(シミュ・録画の両方に反映)")
        self.course_button.move(left, 6)
        left += 150 + 6
        self.course_button.clicked.connect(self.chart_preview.cycle_course)

        self.record_button = self._lane_button("● 録画", 84,
                                               "いま選んでいるコースを動画に書き出す")
        self.record_button.move(left, 6)
        left += 84 + 6
        self.record_button.clicked.connect(self._on_record_clicked)

        # 表示倍率。小さい画面で 1280x720 が入りきらないとき用。押すたびに
        # 100 -> 75 -> 50 -> 100 と回る。等倍のときは中身をそのまま
        # 見せる(ScaledHost 参照)ので、100% の描画は今までと変わらない。
        self.zoom_button = self._lane_button("表示: 100%", 96,
                                             "再生ウィンドウの表示倍率を切り替えます(100/75/50%)")
        self.zoom_button.move(left, 6)
        left += 96 + 6
        self.zoom_button.clicked.connect(self.cycle_zoom)

        # いま出ているコマ数。押せるものではないので QLabel。
        #
        # **録画した動画には入らない。** 書き出しは画面外に専用の
        # GameScreenWidget を作って描いており(recorder.py)、この表示は
        # 再生ウィンドウの入れ物(ScaledHost)の子なので、そちらには存在しない。
        #
        # 数えているのは ChartPreviewWidget.paintEvent が呼ばれた回数
        # (frames_painted)。タイマーの設定値ではなく**実際に描けた数**なので、
        # 重い譜面でコマが落ちていればそのぶん下がる。
        self.fps_label = QLabel("-- fps", self.game_preview_window.scaled_host)
        self.fps_label.setFixedSize(74, LANE_BUTTON_H)
        self.fps_label.setAlignment(Qt.AlignCenter)
        self.fps_label.setToolTip(
            "再生プレビューが実際に描けているコマ数(録画には出ません)")
        _f = self.fps_label.font()
        _f.setPixelSize(LANE_BUTTON_FONT_PX)
        self.fps_label.setFont(_f)
        self.fps_label.setStyleSheet(
            "color: #9fb4c8; background: rgba(0,0,0,140);"
            " border: 1px solid rgba(255,255,255,40); border-radius: 3px;")
        self.fps_label.move(left, 6)
        self.fps_label.raise_()

        # 0.5 秒ごとに数え直す。刻みを細かくすると数字が落ち着かず読めないし、
        # 粗くすると引っかかりに気づけない。
        self._fps_prev = (_time.perf_counter(), self.chart_preview.frames_painted)
        self._fps_timer = QTimer(self)
        self._fps_timer.timeout.connect(self._update_fps_label)
        self._fps_timer.start(500)

        # 起動時は前回終了時のモードへ戻す(既定は通常再生)。ここで一度
        # 通しておかないと、画面が compact のまま(どんちゃんも下の背景も
        # 出ない)で始まってしまう。
        # 倍率はモード復元より先に当てる(モード復元が窓の大きさを取り直すので、
        # 先に倍率を決めておくと refit が1回で済む)。
        self.set_zoom(self.config_data.get("preview_zoom", 100), save=False)
        self._restore_bottom_mode()
        # 前回の再生速度(4 段階のいずれか)へ戻す。
        self._restore_speed()

        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderMoved.connect(lambda ms: self.audio.seek(ms))
        layout.addWidget(self.seek_slider)

        transport_row = QHBoxLayout()
        self.btn_play = QPushButton("再生")
        self.btn_play.setObjectName("accentButton")
        self.btn_play.clicked.connect(self.audio.toggle_play_pause)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_seek_cursor = QPushButton("カーソル位置から再生")
        self.btn_seek_cursor.clicked.connect(self._on_seek_cursor)
        self.btn_metronome = QPushButton("メトロノーム")
        self.btn_metronome.setCheckable(True)
        self.btn_metronome.toggled.connect(self._on_metronome_toggled)
        self.btn_hit_sounds = QPushButton("打音")
        self.btn_hit_sounds.setCheckable(True)
        self.btn_hit_sounds.setChecked(True)
        self.btn_hit_sounds.setToolTip("ゲーム風プレビューでノーツが判定ラインに重なった瞬間にドン/カツの音を鳴らします。")
        self.btn_hit_sounds.toggled.connect(self._on_hit_sounds_toggled)
        self._on_hit_sounds_toggled(True)
        self.time_label = QLabel("0:00 / 0:00")
        transport_row.addWidget(self.btn_play)
        transport_row.addWidget(self.btn_stop)
        transport_row.addWidget(self.btn_seek_cursor)
        transport_row.addWidget(self.btn_metronome)
        transport_row.addWidget(self.btn_hit_sounds)
        transport_row.addWidget(self.time_label)
        transport_row.addStretch()
        layout.addLayout(transport_row)

        volume_row = QHBoxLayout()
        # マスターボリューム: 曲・打音・メトロノームすべてに掛かる大元の音量。
        # 右隣の2つは「マスターに対する比率」なので、ラベルにもそう書いてある。
        volume_row.addWidget(QLabel("マスター:"))
        self.master_volume_slider = QSlider(Qt.Horizontal)
        self.master_volume_slider.setRange(0, 100)
        self.master_volume_slider.setFixedWidth(120)
        self.master_volume_slider.setToolTip(
            "曲・打音・メトロノームすべてに掛かる音量。実際に出る音量 = マスター × 各比率。")
        self.master_volume_slider.valueChanged.connect(self._on_master_volume_changed)
        volume_row.addWidget(self.master_volume_slider)
        self.lbl_master_volume = QLabel("")
        volume_row.addWidget(self.lbl_master_volume)

        volume_row.addSpacing(16)
        volume_row.addWidget(QLabel("曲(比率):"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.setToolTip("マスターに対する曲の音量の比率。")
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        volume_row.addWidget(self.volume_slider)
        self.lbl_volume = QLabel("")
        volume_row.addWidget(self.lbl_volume)
        # 効果音(打音/メトロノーム共通)の音量比率。
        volume_row.addSpacing(16)
        volume_row.addWidget(QLabel("SE(比率):"))
        self.sfx_volume_slider = QSlider(Qt.Horizontal)
        self.sfx_volume_slider.setRange(0, 100)
        self.sfx_volume_slider.setFixedWidth(120)
        self.sfx_volume_slider.setToolTip("マスターに対する打音・メトロノームの音量の比率。")
        self.sfx_volume_slider.valueChanged.connect(self._on_sfx_volume_changed)
        volume_row.addWidget(self.sfx_volume_slider)
        self.lbl_sfx_volume = QLabel("")
        volume_row.addWidget(self.lbl_sfx_volume)

        # 音声出力を開き直すボタン。WASAPI排他モードへの切替などで鳴らなくなった
        # ときの復帰口(環境設定で選んだ出力デバイスの反映もこれで行う)。
        volume_row.addSpacing(16)
        self.btn_reopen_audio = QPushButton("音声を再接続")
        self.btn_reopen_audio.setToolTip(
            "音声出力を開き直します。ほかのアプリがWASAPI排他モードで掴んだ等の理由で"
            "音が出なくなったときに使ってください。再生位置・音量・打音の予定はそのまま復帰します。")
        self.btn_reopen_audio.clicked.connect(self.reopen_audio_output)
        volume_row.addWidget(self.btn_reopen_audio)
        volume_row.addStretch()
        layout.addLayout(volume_row)

        bpm_row = QHBoxLayout()
        bpm_row.addWidget(QLabel("BPM:"))
        self.lbl_editor_bpm = QLabel("-")
        bpm_row.addWidget(self.lbl_editor_bpm)
        self.btn_tap = QPushButton("タップ")
        self.btn_tap.clicked.connect(self._on_tap)
        bpm_row.addWidget(self.btn_tap)
        self.lbl_tap_bpm = QLabel("--")
        bpm_row.addWidget(self.lbl_tap_bpm)
        bpm_row.addStretch()
        layout.addLayout(bpm_row)

        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("OFFSET:"))
        self.spin_offset = QDoubleSpinBox()
        self.spin_offset.setRange(-60.0, 60.0)
        self.spin_offset.setDecimals(3)
        self.spin_offset.setSingleStep(0.001)
        self.spin_offset.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin_offset.valueChanged.connect(self._on_offset_value_changed)
        # OFFSET convention: audio_time = chart_time - OFFSET, so increasing
        # OFFSET shifts the beat grid/notes earlier (left) and decreasing it
        # shifts them later (right). Placing +/- this way means pressing the
        # left-side button moves things left and the right-side button moves
        # things right, matching that spatial expectation.
        for label, delta in (("+0.1", 0.1), ("+0.01", 0.01), ("+0.001", 0.001)):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, d=delta: self.spin_offset.setValue(self.spin_offset.value() + d))
            offset_row.addWidget(btn)
        offset_row.addWidget(self.spin_offset)
        for label, delta in (("-0.001", -0.001), ("-0.01", -0.01), ("-0.1", -0.1)):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, d=delta: self.spin_offset.setValue(self.spin_offset.value() + d))
            offset_row.addWidget(btn)
        offset_row.addStretch()
        layout.addLayout(offset_row)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.setWidget(content)

        # ミキサー初期化に失敗してレガシーへ退避した場合の非ブロッキング通知。
        if self._backend_notice:
            self.status_label.setText(self._backend_notice)

    def warm_skin(self):
        """ゲーム風プレビューの素材を、まだなら今のうちに読んでおく。

        レーンと画面の素材(合わせて実測 400ms 強)は、起動時ではなく
        「実際に描く直前」まで遅らせてある(ChartPreviewWidget._ensure_skin /
        GameScreenWidget._ensure_skin)。ただしそのままだと、利用者が
        ゲーム風プレビューを初めて開いたときにそのぶん待たされてしまう。

        そこでメインウィンドウが最初の描画を終えた直後の手すきに、ここから
        先に読ませておく(MainWindow.paintEvent)。起動は素材ぶん速くなり、
        開いたときの待ちは以前と変わらない、というのが狙い。"""
        self.chart_preview._ensure_skin()
        self.game_screen._ensure_skin()

    def set_game_preview_visible(self, visible: bool):
        if visible:
            # Opening the window parks カレント/アンカー at the song head and
            # rewinds the audio to 0 (机能1), so it always starts from a known
            # stopped state regardless of where the audio was left.
            self.chart_preview.reset_to_start()
            self.game_preview_window.show()
            self.game_preview_window.raise_()
            self.game_preview_window.activateWindow()
            # activateWindow() only makes the window active at the OS level;
            # it doesn't hand keyboard focus to a specific child, so without
            # this, Space/Q/PgUp/PgDn silently do nothing until the user
            # clicks inside the lane once.
            self.chart_preview.setFocus(Qt.OtherFocusReason)
        else:
            self.game_preview_window.hide()

    def is_game_preview_visible(self) -> bool:
        return self.game_preview_window.isVisible()

    def _on_game_preview_closed(self):
        # The window's own close (X) button hides it via closeEvent; let the
        # status-bar toggle button know so its checked state stays in sync.
        if self.game_preview_changed_cb:
            self.game_preview_changed_cb(False)

    # ------------------------------------------------------------------
    # Bottom-panel mode switching + playback speed (フェーズ3)
    # ------------------------------------------------------------------
    def _build_wave_page(self) -> QWidget:
        """音声波形モードのページ: 波形表示(ドック側 self.waveform と同じ配線の
        もう1つの WaveformWidget)。見るだけで編集はしない。速度スライダーは
        全モード共通なので _build_speed_row 側にある。"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(8)

        # ゲーム窓の作譜ページ用の波形。ドックの self.waveform と同様に audio の
        # 再生位置へ同期し、クリック/ドラッグで seek する(seekRequested→seek)。
        self.game_waveform = WaveformWidget(toggle_play_cb=self.audio.toggle_play_pause, force_dark=True)
        # 作譜モードの波形はドックと独立: 合成(モノ)を既定にし、固定幅の窓を
        # 再生位置に追従スクロールさせる(スクロールする楽譜のような見え方)。
        self._wire_waveform(self.game_waveform, sync_stereo=False)
        self.game_waveform.set_stereo_view(False)     # 既定=合成
        # 表示幅(秒)は前回の値から始める。Alt/Ctrl+ホイールで変えると、
        # その場で次回の既定になる(表示倍率やモードと同じ扱い)。
        self.game_waveform.set_follow_window(self._waveform_window())
        self.game_waveform.followWindowChanged.connect(
            self._on_waveform_window_changed)
        # 素のホイールはレーンと同じ「小節移動」にする。向き(上=先へ)も、移動の
        # トゥイーンも、レーンの上で回したときと完全に同じになる。
        self.game_waveform.set_measure_step_cb(self.chart_preview.seek_relative_measure)
        # 波形の描画域(wh)は従来どおりにしつつ、命令帯を高くした分だけ全体を
        # 高くする(命令ラベルの見切れ対策)。下部パネルには余白があるので窓
        # サイズは変わらない。
        self.game_waveform.setFixedHeight(170)
        # レーン(120fps外挿クロック)の毎フレームで波形も同じ時刻に追従させる。
        # 上のレーンと完全同期し、下の波形も 120fps で滑らかにスクロールする。
        # 非表示モード/情報モードでは game_waveform が隠れていて update() が
        # no-op になるため、そのときの追加コストは無い。
        self.chart_preview.set_frame_cb(self._on_preview_frame)
        v.addWidget(self.game_waveform)
        v.addStretch()
        return page

    def _on_preview_frame(self, t):
        """レーンの 120fps クロック。音声波形と作譜、両方の波形へ同じ時刻を配る。

        set_frame_cb はコールバックを1つしか持てないので、ここで束ねる。
        作譜ペインへ渡し忘れると、そちらには再生位置の線が出ず、表示も
        再生に追従しない。隠れているページの update() は no-op なので、
        見ていないモードでの追加コストは無い。"""
        self.game_waveform.set_position_smooth(t)
        # 作譜ページは音声波形ページより後に組まれるので、構築の途中で
        # 1フレーム入ってきても落ちないようにしておく。
        ce = getattr(self, "chart_edit", None)
        if ce is not None:
            ce.set_position_smooth(t)

    def _build_edit_page(self) -> QWidget:
        """作譜モードのページ: 音声波形と同じ見た目に、グリッドと編集カーソルを
        重ねて音符を置けるようにしたもの。配線は音声波形ページと同じで、
        キー入力と noteEdited だけが増える。"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(8)

        self.chart_edit = ChartEditWaveform(toggle_play_cb=self.audio.toggle_play_pause)
        self._wire_waveform(self.chart_edit, sync_stereo=False)
        self.chart_edit.set_stereo_view(False)
        self.chart_edit.set_follow_window(self._waveform_window())
        # ホイールはこちらも「小節移動」。音声波形ページと手触りをそろえる。
        self.chart_edit.set_measure_step_cb(self.chart_preview.seek_relative_measure)
        self.chart_edit.setFixedHeight(170)
        self.chart_edit.set_legend_visible(
            bool(self.config_data.get("chart_edit_legend", True)))
        self.chart_edit.noteEdited.connect(self._on_note_edited)
        self.chart_edit.legendToggled.connect(self._on_legend_toggled)
        v.addWidget(self.chart_edit)
        v.addStretch()
        return page

    # ------------------------------------------------------------------
    # チェックポイント(エディタの行が正、プレビューは時刻で持つ)
    # ------------------------------------------------------------------
    def _take_bar_lines(self, preview_data):
        """小節の開始時刻と開始行を覚え、既知のチェックポイントを引き直す。

        譜面を編集すると小節の時刻も行も動くので、解析結果が届くたびに
        行→時刻を取り直す。行のほうを正としているのはそのため。"""
        if preview_data is None:
            return
        bars = preview_data.get("bar_times") or []
        self._bar_times_chart = [float(b[0]) for b in bars]
        self._bar_lines = [int(ln) for ln in (preview_data.get("bar_lines") or [])]
        self._apply_checkpoint_lines()

    def _line_to_audio_time(self, line):
        """その行を含む(=その行以前で一番後ろの)小節の開始時刻(音源時間)。"""
        best = None
        for t, ln in zip(self._bar_times_chart, self._bar_lines):
            if ln <= line:
                best = t
            else:
                break
        if best is None:
            return None
        return max(0.0, best - self.spin_offset.value())

    def _audio_time_to_line(self, t_audio):
        """音源時刻がどの小節にあたるかを見て、その小節の開始行を返す。"""
        if not self._bar_lines:
            return None
        tc = t_audio + self.spin_offset.value()
        best = None
        for t, ln in zip(self._bar_times_chart, self._bar_lines):
            # 小節頭ちょうどを拾いたいので、丸め誤差ぶんだけ甘く見る。
            if t <= tc + 1e-4:
                best = ln
            else:
                break
        return best

    def _apply_checkpoint_lines(self):
        """覚えている行から時刻を引き直して、レーンと波形へ配る。"""
        times = []
        for ln in sorted(self._checkpoint_lines):
            t = self._line_to_audio_time(ln)
            if t is not None:
                times.append(t)
        self.chart_preview.set_checkpoints(times)
        for wf in (self.game_waveform, self.chart_edit):
            wf.set_checkpoints(times)

    def set_checkpoint_lines(self, lines):
        """エディタ側(Alt+P など)で変わったチェックポイントを受け取る。"""
        new = {int(ln) for ln in (lines or [])}
        if new == self._checkpoint_lines:
            return
        self._checkpoint_lines = new
        self._apply_checkpoint_lines()

    def _on_preview_checkpoints(self, times):
        """レーンで P を押した。時刻を行へ直してエディタへ返す。"""
        for wf in (self.game_waveform, self.chart_edit):
            wf.set_checkpoints(times)
        lines = {ln for ln in (self._audio_time_to_line(t) for t in times)
                 if ln is not None}
        self._checkpoint_lines = lines
        if self.checkpoint_lines_cb is not None:
            self.checkpoint_lines_cb(sorted(lines))

    def _on_legend_toggled(self, on):
        """H キーで凡例を出し入れした。次に開いたときも同じ状態にする。"""
        self.config_data["chart_edit_legend"] = bool(on)
        if self.save_settings_cb is not None:
            self.save_settings_cb()

    def _on_note_edited(self, m_index, slot, grid, char):
        """編集ペインで音符が置かれた。テキストへの書き戻しは MainWindow の
        担当(エディタと Undo を持っているのは向こう)なので、そのまま渡す。"""
        if self.note_edit_cb is not None:
            self.note_edit_cb(m_index, slot, grid, char)

    def _build_title_page(self) -> QWidget:
        """非表示モードのページ: 情報カードは出さず、曲名とサブタイトルだけを
        中央に表示する。今どの譜面を見ているかは常に分かるようにするため。
        ラベルは _sync_title_page() で情報バーと同じ内容に同期する。"""
        page = QWidget()
        v = QVBoxLayout(page)
        # 情報モードと同じく上寄せ(以前は上下 addStretch で中央寄せだった)。
        # マージンも情報バー(10,8)に合わせ、曲名・サブタイトルの位置を揃える。
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(2)
        self._tp_title = QLabel("-")
        self._tp_title.setAlignment(Qt.AlignCenter)
        f = self._tp_title.font()
        f.setBold(True)
        f.setPointSize(13)
        self._tp_title.setFont(f)
        v.addWidget(self._tp_title)
        self._tp_subtitle = QLabel("")
        self._tp_subtitle.setAlignment(Qt.AlignCenter)
        self._tp_subtitle.setStyleSheet(f"color: {_DARK['fg_dim']};")
        v.addWidget(self._tp_subtitle)
        v.addStretch()
        return page

    def _sync_title_page(self, title: str, subtitle: str):
        """非表示ページの曲名・サブタイトルを情報バーと同じ値に更新する。"""
        self._tp_title.setText(title or "(無題)")
        self._tp_subtitle.setText(subtitle or "")
        self._tp_subtitle.setVisible(bool(subtitle))

    def _build_speed_row(self) -> QWidget:
        """再生速度スライダー(×0.25 / ×0.50 / ×0.75 / ×1.00 の 4 段階)。
        作譜モード専用ではなく、下部パネルのどのモードでも常に見えるよう、
        モードスタックの外に置く。"""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 4, 10, 8)
        h.addWidget(QLabel("再生速度:"))
        self.speed_slider = QSlider(Qt.Horizontal)
        # スライダーの値 = SPEED_STEPS の番号。倍率(%)を直に持たせると
        # つまみが段階の間で止まってしまうので、段階そのものをレンジにする。
        # ミキサーは read_pos の増分が変わるだけでピッチも変化する(仕様)。
        self.speed_slider.setRange(0, len(SPEED_STEPS) - 1)
        self.speed_slider.setSingleStep(1)
        self.speed_slider.setPageStep(1)
        # 目盛りを出して「4 段階しかない」ことを見て分かるようにする。
        self.speed_slider.setTickPosition(QSlider.TicksBelow)
        self.speed_slider.setTickInterval(1)
        self.speed_slider.setValue(snap_speed_index(SPEED_DEFAULT))
        # Space/Tab/Z/C をレーンに残すためスライダーはフォーカスを取らない。
        self.speed_slider.setFocusPolicy(Qt.NoFocus)
        self.speed_slider.valueChanged.connect(self._on_speed_slider_changed)
        h.addWidget(self.speed_slider, 1)
        self.lbl_speed = QLabel("×%.2f" % SPEED_DEFAULT)
        h.addWidget(self.lbl_speed)
        return row

    def refresh_theme(self):
        """Called after apply_theme(): repaints the parts that don't restyle
        themselves from the app-level QSS."""
        self.info_bar.refresh_theme()
        self.chart_preview.update()
        for wf in self._waveforms():
            wf.refresh_theme()

    def _lane_button(self, text, width, tooltip):
        """レーンの上に浮かせる小さなボタン。フォーカスを取らないので、
        押したあとも Space/Tab/PgUp/PgDn はレーンに効いたまま。"""
        # 画面(GameScreenWidget)の子にする。レーンの子にすると本家レイアウトでは
        # レーンが x=333 から始まるぶんだけボタンが右へずれてしまう。
        # 親はゲーム画面ではなく ScaledHost。表示倍率を下げると ScaledHost は
        # 中身(ゲーム画面)を子ごと縮小して描くので、ゲーム画面の子にすると
        # ボタンまで小さくなって押しにくくなる。入れ物の側に置けば、絵だけが
        # 縮んでボタンは原寸のまま残る(下の速度スライダーと同じ扱い)。
        host = self.game_preview_window.scaled_host
        b = QPushButton(text, host)
        b.setFocusPolicy(Qt.NoFocus)
        b.setToolTip(tooltip)
        # 寸法と文字の大きさも固定して、フォント設定にも引きずられないようにする。
        b.setFixedSize(width, LANE_BUTTON_H)
        f = b.font()
        f.setPixelSize(LANE_BUTTON_FONT_PX)
        b.setFont(f)
        b.raise_()          # 等倍のときは中身が子として乗るので、その上へ
        return b

    def _update_fps_label(self):
        """出ているコマ数を数え直して表示する。

        前回からの「描けた回数の差」を「経過時間」で割るだけ。タイマーが
        遅れて呼ばれても経過時間の方も伸びるので、値は狂わない。"""
        now = _time.perf_counter()
        frames = self.chart_preview.frames_painted
        prev_t, prev_f = self._fps_prev
        dt = now - prev_t
        self._fps_prev = (now, frames)
        if dt <= 0:
            return
        fps = (frames - prev_f) / dt
        # 止まっているときは 0 ではなく「-」。0fps と書くと不具合に見えるが、
        # 実際は描く必要が無いだけなので。
        self.fps_label.setText("-- fps" if fps < 0.5 else "%.0f fps" % fps)

    def _on_record_clicked(self):
        if self.record_cb:
            self.record_cb()

    def _set_lane_course_label(self, label, color):
        """レーン上のコースボタンの表示を、いま映しているコースに合わせる。
        情報バーのコースボタンと同じ内容(あちらは情報モードでしか見えない)。"""
        self.course_button.setText(f"コース: {label or '-'}")
        if color:
            self.course_button.setStyleSheet(f"color: {color}; font-weight: bold;")

    # 25% は小さすぎて譜面が読めないので廃止した。
    ZOOM_STEPS = (100, 75, 50)

    def _waveform_window(self) -> float:
        """音声波形モードの表示幅(秒)。設定に無ければ 6 秒。

        壊れた値(0 や文字列)が入っていても起動できるように、ここで範囲へ
        押し込む。上限・下限は wheelEvent と同じ 1〜60 秒。"""
        try:
            v = float(self.config_data.get("waveform_window", 6.0))
        except (TypeError, ValueError):
            return 6.0
        if v != v or v <= 0:          # NaN / 0 / 負
            return 6.0
        return max(1.0, min(v, 60.0))

    def _on_waveform_window_changed(self, seconds: float):
        """表示幅が変わったので次回の既定にする。

        set_follow_window を呼び直しているのは、こちらが「既定値」も
        更新するため(reset_zoom の戻り先がここで決まる)。呼ばないと、
        戻す操作で前回の起動時の幅に戻ってしまう。"""
        seconds = max(1.0, min(float(seconds), 60.0))
        self.game_waveform.set_follow_window(seconds)
        if self.config_data.get("waveform_window") == seconds:
            return
        self.config_data["waveform_window"] = seconds
        if self.save_settings_cb is not None:
            self.save_settings_cb()

    def cycle_zoom(self):
        """表示倍率を 100 -> 75 -> 50 -> 100 と回す。"""
        cur = int(round(self.game_preview_window.scaled_host.scale() * 100))
        try:
            i = self.ZOOM_STEPS.index(cur)
        except ValueError:
            i = 0
        self.set_zoom(self.ZOOM_STEPS[(i + 1) % len(self.ZOOM_STEPS)])

    def set_zoom(self, percent: int, save: bool = True):
        """表示倍率(%)を適用する。ボタンの表示と窓の大きさも取り直す。"""
        percent = int(percent) if int(percent) in self.ZOOM_STEPS else 100
        self.game_preview_window.scaled_host.set_scale(percent / 100.0)
        self.game_preview_window.refit()
        self.zoom_button.setText(f"表示: {percent}%")
        if save and self.config_data.get("preview_zoom") != percent:
            self.config_data["preview_zoom"] = percent
            if self.save_settings_cb is not None:
                self.save_settings_cb()

    def cycle_bottom_mode(self):
        """通常再生→音声波形→(作譜→)情報→… と循環。作譜は実験的機能
        (peepo_chart_edit)が有効なときだけ挟まる(_build_ui でページ自体を
        積んでいないので % self.bottom_stack.count() が自然に3つで回る)。
        Tab キー(chart_preview)とモードトグルボタンの両方から呼ばれる。"""
        idx = (self.bottom_stack.currentIndex() + 1) % self.bottom_stack.count()
        self.set_bottom_mode(idx)

    def set_bottom_mode(self, idx: int):
        """下部パネルのモードを切り替える。

        通常再生は録画と同じ 1280x720(どんちゃん・下の背景・フッターまで出る)。
        ほかのモードは上半分(1280x360)に縮めて、下の背景があった場所に
        そのモードのペイン(音声波形/作譜/情報)を置く — あそこは屋台の絵より
        波形や情報を出したい場所なので、下の背景ごと譲る。レーンの描き方は
        軽量と同じ(set_lite)にそろえる。

        軽量モードは通常再生と同じ 1280x720(縦横比を揃えたいという要望)。
        ただし背景の絵は上下とも出さず黒で埋め、どんちゃん・魂ゲージ・
        魂の飛翔・スコア加算も落とす(set_lite)。下にペインは置かない。"""
        self.bottom_stack.setCurrentIndex(idx)
        self.mode_button.setText(self._mode_names[idx])
        # 録画ボタンを出すモード。書き出しの中身はモードに一切左右されない
        # (recorder は画面外に専用の GameScreenWidget(1280x720)を作って描く)
        # ので、これは純粋に「どこから始められるか」の話。音声波形は譜面を
        # 見ながら録りたい場面がそのまま録画したい場面なので出す。作譜と情報の
        # ときは出さない — 画面下が別物なので、そこから始めると何が録れるのか
        # 紛らわしい。
        self.record_button.setVisible(
            idx == self.MODE_TITLE or idx == self.MODE_WAVE)
        # 軽量は下にペインを置かないので 1280x720 のまま(縦横比を通常再生と
        # 揃えたいという要望)。縮めるのはペインを置くモードだけ。
        self.game_screen.set_compact(
            idx != self.MODE_TITLE and idx != self.MODE_LITE)
        # 軽量の入り切りは compact の切替より後に。set_lite が静的キャッシュを
        # 捨てるので、先に呼ぶと set_compact が焼き直した直後のキャッシュを
        # また捨てることになり、無駄が1枚ぶん増える。
        # 通常再生**以外**はすべて軽量の描き方にする。音声波形/作譜/情報の
        # ときは画面が上半分に縮んでいて、どんちゃんも下の背景も元々見えない。
        # それなら魂ゲージや魂の飛翔・スコア加算まで落として軽量と同じレーンに
        # 揃えたほうが、モードを行き来しても見え方が変わらず、そのぶん軽い。
        self.game_screen.set_lite(idx != self.MODE_TITLE)
        # 曲名はゲーム画面の中に描かれるので、曲名だけのページは出さない。
        # 軽量も同じ扱い(ページを持たない = 窓をできるだけ小さくする)。
        show_page = (idx != self.MODE_TITLE and idx != self.MODE_LITE)
        self.bottom_stack.setVisible(show_page)
        self._bottom_panel.setFixedHeight(
            self._bottom_h_full if show_page else self._bottom_h_speed_only)
        self.game_preview_window.refit()
        # 作譜モードのときだけ、キー入力を受けるのは編集ペイン。ほかのモードでは
        # レーンへ返す(Space/小節移動が今までどおり効くように)。
        if idx == self.MODE_EDIT and self.MODE_EDIT is not None:
            self.chart_edit.setFocus(Qt.OtherFocusReason)
        else:
            self.chart_preview.setFocus(Qt.OtherFocusReason)
        self._save_bottom_mode(idx)

    # ------------------------------------------------------------------
    # 最後に使ったモードの保存/復元(settings.json の preview_bottom_mode)
    # ------------------------------------------------------------------
    # 保存するのは番号ではなく**モード名**。番号は実験的機能(peepo_chart_edit)
    # の入り切りで「作譜」が挟まったり抜けたりして意味がずれるので、番号で
    # 覚えると設定を変えた次の起動で別のモードが開いてしまう。名前なら
    # _mode_names に無くなったとき(作譜を切ったあと)も素直に既定へ落ちる。

    def _save_bottom_mode(self, idx: int):
        # 起動時の復元で書き戻さない。実験的機能(作譜)を切った直後の起動では
        # 「作譜」が _mode_names に無くて通常再生へ落ちるので、そのまま保存
        # すると作譜を戻したときに設定が消えている。
        if self._restoring_mode or not (0 <= idx < len(self._mode_names)):
            return
        name = self._mode_names[idx]
        if self.config_data.get("preview_bottom_mode") == name:
            return          # 変わっていないなら書かない(切替のたびに保存しない)
        self.config_data["preview_bottom_mode"] = name
        if self.save_settings_cb is not None:
            self.save_settings_cb()

    def _restore_bottom_mode(self):
        """起動時に、前回終了時のモードへ戻す。"""
        name = self.config_data.get("preview_bottom_mode")
        try:
            idx = self._mode_names.index(name)
        except ValueError:
            idx = self.MODE_TITLE
        self._restoring_mode = True
        try:
            self.set_bottom_mode(idx)
        finally:
            self._restoring_mode = False

    def _on_speed_slider_changed(self, value: int):
        # スライダーが速度の単一ソース。ここから audio と chart_preview の両方の
        # レートを更新する。値は段階の番号なので、倍率へ引き直してから配る。
        value = max(0, min(len(SPEED_STEPS) - 1, int(value)))
        rate = SPEED_STEPS[value]
        self.lbl_speed.setText(f"×{rate:.2f}")
        self._save_speed(rate)
        self.audio.set_playback_rate(rate)
        self.chart_preview.set_playback_rate(rate)
        # 打音/メトロノームのレイテンシ補正は実時間basisなので、音声時間で
        # 組まれたスケジュールを新しい倍率で組み直す必要がある。
        self.hit_sounds.set_playback_rate(rate)
        self.metronome.set_playback_rate(rate)

    def _on_speed_from_key(self, rate: float):
        # chart_preview の Z / C キーから来る目標倍率。スライダー値を動かすと
        # valueChanged 経由で audio/chart_preview に反映される(スライダーと同期)。
        # 段階外の値が来ても snap_speed_index が丸めるので、ここでは弾かない。
        self.speed_slider.setValue(snap_speed_index(rate))

    # ------------------------------------------------------------------
    # 再生速度の保存/復元(settings.json の preview_speed)

    def _save_speed(self, rate: float):
        """選ばれた段階を settings.json へ書き戻す。倍率が変わっていなければ
        書かない(スライダーを触るたびに保存が走らないように)。"""
        if self._restoring_speed:
            return
        if self.config_data.get("preview_speed") == rate:
            return
        self.config_data["preview_speed"] = rate
        if self.save_settings_cb is not None:
            self.save_settings_cb()

    def _restore_speed(self):
        """前回の再生速度を復元する。1.00 より速い値や 4 段階に無い値
        (旧版の 1.50 / 2.00 や手書きの 0.4 など)は snap_speed が段階へ丸める
        ので、そのまま渡してよい。復元中は保存しない(起動しただけで
        settings.json を書き換えないため。丸めた値は次に速度を変えたときに
        書き戻される)。"""
        self._restoring_speed = True
        try:
            rate = snap_speed(self.config_data.get("preview_speed", SPEED_DEFAULT))
            idx = snap_speed_index(rate)
            self.speed_slider.setValue(idx)
            # 既定値と同じ段階なら valueChanged が飛ばないので、レーンと音声へ
            # 明示的に配っておく(初期化の取りこぼし防止)。
            self._on_speed_slider_changed(idx)
        finally:
            self._restoring_speed = False

    def set_expanded(self, expanded: bool):
        self._content_widget.setVisible(expanded)

    def is_expanded(self) -> bool:
        return self._content_widget.isVisible()

    def expand(self):
        self.set_expanded(True)
        if self.expanded_changed_cb:
            self.expanded_changed_cb(True)

    # ------------------------------------------------------------------
    # Sync from editor content
    # ------------------------------------------------------------------
    def refresh_from_content(self, content: str, current_file, metronome_clicks=None, preview_data=None, course_stats=None):
        headers = parse_preview_headers(content)
        self._editor_bpm = headers["bpm"]
        self._editor_offset = headers["offset"]
        self._editor_subtitle = headers["subtitle"]
        self._editor_metronome_clicks = metronome_clicks or []
        self.lbl_editor_bpm.setText(f"{headers['bpm']:g}" if headers["bpm"] else "-")
        self.title_label.setText(headers["title"] or "(無題)")
        # 小節が1つも無い譜面でも作譜モードで打ち始められるよう、外挿用の
        # 1小節の長さをヘッダの BPM から渡しておく(4/4 の4拍ぶん)。
        if headers["bpm"]:
            self.chart_edit.set_default_measure_len(240.0 / float(headers["bpm"]))

        if preview_data is not None:
            self._editor_notes = [(t, c, bpm) for t, c, bpm, _sc, _se in preview_data.get("notes", [])]
            self._editor_notes += _roll_tick_notes(preview_data.get("rolls", []), bpm_index=3)
            # 風船は「割れる時刻」まででしか鳴らさない。表示(レーン)と
            # 同じ切り詰めを通さないと、数字が 0 なのに音だけ続く。
            _spd = preview_data.get("roll_hit_speed", 45)
            self._editor_notes += _roll_tick_notes(
                balloon_pop_spans(preview_data.get("balloons", []), _spd), bpm_index=2)
            self._editor_notes += _roll_tick_notes(
                balloon_pop_spans(preview_data.get("kusudamas", []), _spd), bpm_index=2)
            self._preview_notes = list(preview_data.get("notes", []))
            self._preview_spans = (list(preview_data.get("rolls", [])),
                                   list(preview_data.get("balloons", [])),
                                   list(preview_data.get("kusudamas", [])))
            self._preview_commands = (list(preview_data.get("bpm_changes", [])),
                                      list(preview_data.get("scroll_changes", [])),
                                      list(preview_data.get("measure_changes", [])),
                                      list(preview_data.get("gogo_regions", [])))
            self._game_grid_clicks = self._bar_grid_clicks(preview_data.get("bar_times", []))

        self.waveform.set_beat_grid(headers["bpm"], self.spin_offset.value(), self._editor_metronome_clicks)
        self._set_game_grid(headers["bpm"], self.spin_offset.value())
        self.game_waveform.set_notes(self._preview_notes)  # 音声波形: 波形の下に譜面
        self.game_waveform.set_spans(*self._preview_spans)
        self.game_waveform.set_commands(*self._preview_commands)
        # 作譜モードの編集ペインにも同じものを流す。加えて小節の開始時刻を渡す
        # (カーソルの住所計算に要る)。正式な解析が届いたので暫定表示は捨てる。
        self.chart_edit.set_notes(self._preview_notes)
        self.chart_edit.set_spans(*self._preview_spans)
        self.chart_edit.set_commands(*self._preview_commands)
        if preview_data is not None:
            # 小節時刻が無いと編集カーソルの住所が決まらない。preview_data が
            # 来ていないときは前回の値を残す(消すとカーソルが死ぬ)。
            self.chart_edit.set_bar_times(preview_data.get("bar_times", []),
                                          self.spin_offset.value())
        self.chart_edit.clear_pending()
        self.metronome.set_schedule(self._editor_metronome_clicks, self.spin_offset.value())
        self.hit_sounds.set_schedule(self._editor_notes, self.spin_offset.value())
        self.chart_preview.set_offset(self.spin_offset.value())
        self._take_bar_lines(preview_data)
        if preview_data is not None:
            self.chart_preview.set_preview_data(preview_data)
            self.info_bar.set_course_info(
                preview_data.get("course_label"), preview_data.get("course_color"), preview_data.get("level"),
            )
            self._set_lane_course_label(preview_data.get("course_label"),
                                        preview_data.get("course_color"))
            self.game_screen.set_chart(preview_data, preview_data.get("course_key"))
            self.info_bar.set_branch_info(preview_data.get("branch_level"), preview_data.get("has_branches"))
        self.info_bar.set_static_info(headers["title"], headers["subtitle"], course_stats)
        self._sync_title_page(headers["title"], headers["subtitle"])

        wave = headers["wave"]
        if not current_file or not wave:
            self._current_wave_path = None
            self.status_label.setText("先にファイルを保存し、WAVE:に音源ファイルを指定してください。")
            self.btn_play.setEnabled(False)
            self.chart_preview.set_loading(False)
            return

        wave_path = os.path.join(os.path.dirname(current_file), wave)
        if wave_path == self._current_wave_path:
            return  # same song already loaded; don't reset in-progress OFFSET tweaks

        self._current_wave_path = wave_path
        self.spin_offset.blockSignals(True)
        self.spin_offset.setValue(headers["offset"])
        self.spin_offset.blockSignals(False)
        self.waveform.set_beat_grid(headers["bpm"], headers["offset"], self._editor_metronome_clicks)
        self._set_game_grid(headers["bpm"], headers["offset"])
        self.metronome.set_schedule(self._editor_metronome_clicks, headers["offset"])
        self.hit_sounds.set_schedule(self._editor_notes, headers["offset"])
        self.chart_preview.set_offset(headers["offset"])

        if not os.path.exists(wave_path):
            self.status_label.setText(f"音源ファイルが見つかりません: {wave}")
            self.btn_play.setEnabled(False)
            self.chart_preview.set_loading(False)
            return

        self.status_label.setText("音源を読み込み中...")
        self.btn_play.setEnabled(False)
        self.chart_preview.set_loading(True)
        self.audio.load(wave_path)
        self._start_waveform_decode(wave_path)

    def load_wave_only(self, wave_path):
        """譜面と切り離して、音源だけを読み込む。

        NeoTJAPlayer の選曲画面で使う — 譜面を選ぶ前の BGM と、選んだ譜面の
        DEMOSTART からの試聴。どちらも「譜面は関係なく音だけ鳴らしたい」ので、
        refresh_from_content(譜面の中身から全部を組み直す経路)は通せない。

        同じ音源なら読み直さない(選び直すたびに鳴り止むのを避ける)。"""
        if not wave_path or not os.path.exists(wave_path):
            return False
        if wave_path == self._audition_wave_path:
            return True
        # **_current_wave_path は絶対に触らない。** あれは
        # refresh_from_content(譜面から全部を組み直す経路)が「同じ曲なら
        # 何もしないで戻る」判断に使っているもので、ここで先に埋めてしまうと、
        # あとから本編を読むときにその早期リターンに入り、**OFFSET の適用と
        # 打音・メトロノームの予定まで飛ばされる**。譜面が1小節ぶんずれる、
        # という形で実際に起きた。試聴用の記録は自前で持つ。
        self._audition_wave_path = wave_path
        self.audio.load(wave_path)
        self._start_waveform_decode(wave_path)
        return True

    def _start_waveform_decode(self, wave_path):
        # 曲は1回だけデコードし、ステレオ PCM(ミキサー用)と波形ピーク/長さ
        # (波形表示用)を同時に得る。レガシー経路では PCM を無視してピークだけ使う。
        # 直前のデコードがまだ走っている状態で self._decode_worker を上書きすると、
        # 走行中の QThread への最後の参照が消えて GC され
        # "QThread: Destroyed while thread is still running" で落ちる
        # (ファイルAを開いた直後にファイルBを開く、で普通に起きる)。
        # 走り終わるまでプロセス側の待機所で保持する。
        detach_worker(self._decode_worker)
        self._decode_worker = None

        worker = SongDecodeWorker(wave_path)
        worker.path = wave_path
        worker.decoded.connect(
            lambda pcm, sr, peaks, dur, mips, p=wave_path: self._on_decoded(p, pcm, sr, peaks, dur, mips))
        worker.failed.connect(lambda msg, p=wave_path: self._on_decode_failed(p, msg))
        self._decode_worker = worker
        worker.start()

    def _on_decoded(self, path, pcm, sr, peaks, duration, mips):
        if path != self._current_wave_path:
            return
        # ミップチェインは1本だけ作って両方の波形で共有する(コピーしない)。
        self._waveform_mips = mips
        # 3つの波形すべてに同じミップチェインを配る(コピーはしない)。
        # 渡し忘れると duration が 0 のままになり、波形が出ないだけでなく
        # 追従表示の view_start が曲頭にクランプされて位置が動かなくなる。
        for wf in self._waveforms():
            wf.set_mips(mips)
        # ミキサー経路: デコード済みステレオ PCM をミキサーへ渡す(ここで
        # durationChanged / LoadedMedia が出て再生ボタンが有効になる)。
        if self._mixer_active:
            self.audio.set_song_pcm(pcm, sr)
        self.status_label.setText("")

    def _on_audio_error(self, msg: str):
        """音声コールバックが例外で止まった(=以後ずっと無音)ことの通知。
        engine 側で1回しか出ないので、ここでは素直に表示するだけ。"""
        self.status_label.setText(
            f"音声の再生が停止しました(内部エラー): {msg} / アプリを再起動してください。")

    def _on_sfx_load_failed(self, name: str):
        self.status_label.setText(
            f"打音ファイルを読み込めませんでした: {name} / 既定の音に戻しました。")

    def shutdown_audio(self):
        """アプリ終了時に音声デバイスを確定的に閉じる。MainWindow.closeEvent
        から呼ぶ。ミキサー経路(MixerAudioEngine)だけが close() を持つので、
        レガシー経路(AudioEngine)では何もしない。デコード中のスレッドが
        あれば、GC で落ちないよう待機所へ逃がしておく。"""
        try:
            self.audio.pause()
        except Exception:  # noqa: BLE001
            pass
        close = getattr(self.audio, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
        detach_worker(self._decode_worker)
        self._decode_worker = None

    def _on_decode_failed(self, path, msg):
        if path != self._current_wave_path:
            return
        self.status_label.setText(f"波形の読み込みに失敗しました: {msg}")
        # ミキサー経路ではデコードが LoadedMedia の出どころなので、失敗したら
        # ここで幕を降ろさないと「Loading Now」が出たままになる。
        self.chart_preview.set_loading(False)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def _on_position_changed(self, ms):
        # Throttle to ~30Hz while playing so the 60Hz position feed doesn't
        # crowd the GUI thread and unevenly delay the preview's own redraw
        # timer (which is what caused the "60fps but juddery" microstutter).
        # The preview extrapolates smoothly between these anchors, so 30Hz
        # drift correction is imperceptible; the playhead/label/slider are
        # equally fine at 30Hz. Not throttled when paused/seeking (rare calls).
        playing = self.audio.is_playing()
        if playing:
            now = _time.monotonic()
            if now - self._last_pos_ui_wall < 0.030:
                return
            self._last_pos_ui_wall = now
        self.waveform.set_position(ms / 1000.0)
        # 再生中は作譜モードの波形をレーンの 120fps クロック(frame_cb →
        # set_position_smooth)で駆動するので、ここでは触らない(30fpsの生値と
        # 120fpsの外挿値が競合してカクつくのを避ける)。停止/一時停止/シーク中
        # だけこちらで反映する。
        if not playing:
            self.game_waveform.set_position(ms / 1000.0)
        self.chart_preview.set_playback(ms / 1000.0, playing)
        self.time_label.setText(f"{_fmt_time(ms)} / {_fmt_time(self._duration_ms)}")
        if not self.seek_slider.isSliderDown():
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(ms)
            self.seek_slider.blockSignals(False)

    def _on_duration_changed(self, ms):
        self._duration_ms = ms
        self.seek_slider.setRange(0, max(0, ms))
        if self.duration_ready_cb:
            self.duration_ready_cb()

    def duration_seconds(self) -> float:
        return self._duration_ms / 1000.0

    def wave_path(self):
        """いま読み込んでいる音源(WAVE)のフルパス。未読込なら None。
        動画書き出しが同じ音源を自前でデコードするのに使う。"""
        return self._current_wave_path

    def _on_playing_changed(self, playing):
        self.btn_play.setText("一時停止" if playing else "再生")
        self.chart_preview.set_playback(self.audio.position() / 1000.0, playing)
        if not playing and self.refresh_preview_cb:
            # Reload from the current editor content (not whatever was last
            # cached) so edits made while playing/paused show up immediately
            # once playback stops, instead of waiting for the next cursor
            # move or edit to trigger a refresh.
            self.refresh_preview_cb()

    def _on_media_status_changed(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        Status = QMediaPlayer.MediaStatus
        if status in (Status.LoadingMedia, Status.NoMedia):
            self.btn_play.setEnabled(False)
            self.chart_preview.set_loading(True)
            self.status_label.setText("音源を読み込み中...")
        elif status == Status.InvalidMedia:
            self.btn_play.setEnabled(False)
            self.chart_preview.set_loading(False)
            self.status_label.setText("音源を再生できません(未対応の形式など)。")
        elif status in (Status.LoadedMedia, Status.BufferedMedia, Status.EndOfMedia):
            self.btn_play.setEnabled(True)
            self.chart_preview.set_loading(False)
            if self.status_label.text() == "音源を読み込み中...":
                self.status_label.setText("")

    def _on_stop(self):
        self.audio.stop()
        self.audio.seek(0)

    def _on_seek_requested(self, seconds):
        self.audio.seek(int(seconds * 1000))

    def seek_to_seconds(self, seconds: float):
        self.audio.seek(max(0, int(seconds * 1000)))
        self.audio.play()

    def _on_seek_cursor(self):
        if self.seek_cursor_cb is None:
            return
        seconds = self.seek_cursor_cb()
        if seconds is None:
            self.status_label.setText("カーソルが譜面データ(#START〜#END)の中にありません。")
            return
        self.seek_to_seconds(seconds)

    def _on_metronome_toggled(self, checked):
        self.metronome.set_enabled(checked)
        self.btn_metronome.setObjectName("accentButton" if checked else "")
        self.btn_metronome.style().unpolish(self.btn_metronome)
        self.btn_metronome.style().polish(self.btn_metronome)

    def _on_hit_sounds_toggled(self, checked):
        self.hit_sounds.set_enabled(checked)
        self.btn_hit_sounds.setObjectName("accentButton" if checked else "")
        self.btn_hit_sounds.style().unpolish(self.btn_hit_sounds)
        self.btn_hit_sounds.style().polish(self.btn_hit_sounds)

    def toggle_hit_sounds(self):
        """Flips the 打音(hit sounds) button; routed through toggled so the
        existing _on_hit_sounds_toggled handler updates enabled state and
        appearance in one place. Used by MainWindow's F1 shortcut."""
        on = not self.btn_hit_sounds.isChecked()
        self.btn_hit_sounds.setChecked(on)
        # F1 は再生中でも窓の外からでも効くので、レーンを見たまま結果が分かるよう
        # プレビュー上に出す(音のない箇所では切り替わったか分からないため)。
        self.chart_preview.show_toast(f"打音 : {'ON' if on else 'OFF'}")

    def set_metronome_clicks(self, clicks):
        self._editor_metronome_clicks = clicks or []
        self.metronome.set_schedule(self._editor_metronome_clicks, self.spin_offset.value())
        self.waveform.set_beat_grid(self._editor_bpm, self.spin_offset.value(), self._editor_metronome_clicks)
        self._set_game_grid(self._editor_bpm, self.spin_offset.value())

    def set_preview_data(self, data, course_stats=None):
        data = data or {}
        self._editor_notes = [(t, c, bpm) for t, c, bpm, _sc, _se in data.get("notes", [])]
        self._editor_notes += _roll_tick_notes(data.get("rolls", []), bpm_index=3)
        # 風船は「割れる時刻」まででしか鳴らさない。表示(レーン)と
        # 同じ切り詰めを通さないと、数字が 0 なのに音だけ続く。
        _spd = data.get("roll_hit_speed", 45)
        self._editor_notes += _roll_tick_notes(
            balloon_pop_spans(data.get("balloons", []), _spd), bpm_index=2)
        self._editor_notes += _roll_tick_notes(
            balloon_pop_spans(data.get("kusudamas", []), _spd), bpm_index=2)
        self._preview_notes = list(data.get("notes", []))
        self._preview_spans = (list(data.get("rolls", [])),
                               list(data.get("balloons", [])),
                               list(data.get("kusudamas", [])))
        self._preview_commands = (list(data.get("bpm_changes", [])),
                                  list(data.get("scroll_changes", [])),
                                  list(data.get("measure_changes", [])),
                                  list(data.get("gogo_regions", [])))
        self.game_waveform.set_notes(self._preview_notes)  # 音声波形: 波形の下に譜面
        self.game_waveform.set_spans(*self._preview_spans)
        self.game_waveform.set_commands(*self._preview_commands)
        # 作譜モードの編集ペインにも同じものを流す。加えて小節の開始時刻を渡す
        # (カーソルの住所計算に要る)。正式な解析が届いたので暫定表示は捨てる。
        self.chart_edit.set_notes(self._preview_notes)
        self.chart_edit.set_spans(*self._preview_spans)
        self.chart_edit.set_commands(*self._preview_commands)
        self.chart_edit.set_bar_times(data.get("bar_times", []), self.spin_offset.value())
        self.chart_edit.clear_pending()
        self.hit_sounds.set_schedule(self._editor_notes, self.spin_offset.value())
        self._take_bar_lines(data)
        self.chart_preview.set_preview_data(data)
        self.info_bar.set_course_info(data.get("course_label"), data.get("course_color"), data.get("level"))
        self._set_lane_course_label(data.get("course_label"), data.get("course_color"))
        self.game_screen.set_chart(data, data.get("course_key"))
        self.info_bar.set_branch_info(data.get("branch_level"), data.get("has_branches"))
        self.info_bar.set_static_info(self.title_label.text(), self._editor_subtitle, course_stats)
        self._sync_title_page(self.title_label.text(), self._editor_subtitle)

    def set_hit_sound_files(self, don_path: str, ka_path: str):
        self.hit_sounds.set_sound_files(don_path, ka_path)

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------
    def _play_feedback_sound(self, kind: str):
        """操作フィードバックのドン/カ(kind='don'|'ka')を単発で鳴らす。打音
        エンジンの play_once を使う(打音がオフのときは鳴らない)。"""
        try:
            self.hit_sounds.play_once(kind)
        except Exception:
            pass

    def _play_fadein(self, measure_time: float, lead: float = 1.0):
        """f/j 再生: 小節頭の少し前(measure_time - lead 秒)へシークして通常音量
        で再生する。リード中(開始位置に達するまで)は打音(SE)を止め、譜面も
        隠す(隠すのは chart_preview 側)。開始位置に達したら _end_fadein で SE を
        元に戻す。音源はそのまま鳴らして「頭出しの助走」を聞かせる。"""
        start = max(0.0, measure_time - lead)
        # リード中は「開始位置より前の打音だけ」を黙らせる。以前は打音エンジン
        # ごと enabled=False にして到達時に戻していたが、それだと開始位置
        # ちょうどの1打目まで消えていた(戻すのが間に合わない)。ここで先に
        # 境目を渡しておけば、助走ぶんだけ黙って1打目から鳴る。
        self._set_hit_mute_before(measure_time)
        self.audio.seek(max(0, int(start * 1000)))
        self.audio.play()

    def _end_fadein(self):
        """リード表示の終わり(再生位置が開始位置に到達 or 操作でキャンセル)。
        打音の抑制を解除する。"""
        self._set_hit_mute_before(None)

    def _set_hit_mute_before(self, audio_time):
        """打音エンジン(ミキサー/レガシーどちらでも)に抑制の境目を伝える。"""
        setter = getattr(self.hit_sounds, "set_mute_before", None)
        if setter is not None:
            setter(audio_time)

    def set_master_volume(self, volume: float):
        """マスターボリューム(0.0-1.0)を保存コールバックを呼ばずに設定する
        (settings.json の master_volume 復元用)。"""
        self._master_volume = max(0.0, min(1.0, float(volume)))
        self._apply_master_volume()
        self.master_volume_slider.blockSignals(True)
        self.master_volume_slider.setValue(round(self._master_volume * 100))
        self.master_volume_slider.blockSignals(False)
        self.lbl_master_volume.setText(f"{round(self._master_volume * 100)}%")

    def _on_master_volume_changed(self, value):
        self._master_volume = value / 100.0
        self._apply_master_volume()
        self.lbl_master_volume.setText(f"{value}%")
        if self.master_volume_cb:
            self.master_volume_cb(self._master_volume)

    def _apply_master_volume(self):
        """マスターを両経路へ流す。ミキサーでは MixerCore が曲にもボイスにも
        一様に掛けてくれるので投げるだけ。レガシーは曲(QAudioOutput)は engine
        側で掛かるが、効果音(QSoundEffect)は自前で掛ける必要がある。"""
        setter = getattr(self.audio, "set_master_volume", None)
        if setter is not None:
            setter(self._master_volume)
        self._apply_legacy_sfx_volume()

    def _apply_legacy_sfx_volume(self):
        """レガシー経路の打音/メトロノームへ「マスター × SE比率」を反映する。
        ミキサー経路では MixerCore が vol_sfx/vol_metro × vol_master を掛けるので
        何もしない。"""
        if self._mixer_active:
            return
        v = self._master_volume * self._sfx_ratio
        for engine in (self.hit_sounds, self.metronome):
            setter = getattr(engine, "set_volume", None)
            if setter is not None:
                setter(v)

    # ------------------------------------------------------------------
    # 音声出力の開き直し / ワイヤレス調整
    # ------------------------------------------------------------------
    def reopen_audio_output(self, device_name=None):
        """音声出力を開き直す(ボタン / 環境設定でデバイスを変えたとき)。

        成功しても失敗しても必ず利用者に見える形で結果を出す — 黙って無音の
        まま、が一番困るため。失敗時は警告ダイアログも出す。"""
        reopen = getattr(self.audio, "reopen_stream", None)
        if reopen is not None:
            ok, msg = reopen(device_name)
        else:
            # レガシー経路(QMediaPlayer)。デバイス選択は持たないので名前は無視。
            legacy = getattr(self.audio, "reopen_output", None)
            if legacy is None:
                ok, msg = False, "この再生方式では音声出力の開き直しに対応していません。"
            else:
                ok, msg = legacy()
        self.status_label.setText(msg)
        try:
            self.chart_preview.show_toast("音声再接続: " + ("OK" if ok else "失敗"))
        except Exception:  # noqa: BLE001
            pass
        if not ok:
            QMessageBox.warning(self, "音声出力", msg)
        return ok

    def set_output_offset_ms(self, ms: float):
        """ワイヤレス調整(出力遅延の補正、ms)。曲・打音・メトロノームすべてに
        一律で効く。0 で無効と同じ。既存の BPM 依存の打音レイテンシ補正は
        そのままで、これはその上に足される。"""
        self._output_offset_ms = float(ms)
        setter = getattr(self.audio, "set_output_offset_ms", None)
        if setter is not None:
            setter(self._output_offset_ms)
        # レガシー経路のみ: 打音/メトロノームは再生位置の報告で駆動されるので、
        # 位置をずらしたぶんをスケジュール側で戻す(audio_engine 側の説明を参照)。
        sec = self._output_offset_ms / 1000.0
        for engine in (self.hit_sounds, self.metronome):
            eng_setter = getattr(engine, "set_output_offset", None)
            if eng_setter is not None:
                eng_setter(sec)

    def set_volume(self, volume: float):
        """曲の音量比率(0.0-1.0)を保存コールバックを呼ばずに設定する
        (settings.json の preview_volume 復元用)。実際に出る音量は
        マスター × この比率。"""
        self.audio.set_volume(volume)
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(round(volume * 100))
        self.volume_slider.blockSignals(False)
        self.lbl_volume.setText(f"{round(volume * 100)}%")

    def _on_volume_changed(self, value):
        volume = value / 100.0
        self.audio.set_volume(volume)
        self.lbl_volume.setText(f"{value}%")
        if self.volume_cb:
            self.volume_cb(volume)

    def set_sfx_volume(self, volume: float):
        """効果音の音量比率(0.0-1.0)を保存コールバックを呼ばずに設定
        (settings.json の sfx_volume 復元用)。実際に出る音量はマスター × この比率。"""
        self._sfx_ratio = max(0.0, min(1.0, float(volume)))
        if self._mixer_active and hasattr(self.audio, "set_sfx_volume"):
            self.audio.set_sfx_volume(volume)
        self._apply_legacy_sfx_volume()
        self.sfx_volume_slider.blockSignals(True)
        self.sfx_volume_slider.setValue(round(volume * 100))
        self.sfx_volume_slider.blockSignals(False)
        self.lbl_sfx_volume.setText(f"{round(volume * 100)}%")

    def _on_sfx_volume_changed(self, value):
        volume = value / 100.0
        self._sfx_ratio = volume
        if self._mixer_active and hasattr(self.audio, "set_sfx_volume"):
            self.audio.set_sfx_volume(volume)
        self._apply_legacy_sfx_volume()
        self.lbl_sfx_volume.setText(f"{value}%")
        if self.sfx_volume_cb:
            self.sfx_volume_cb(volume)

    # ------------------------------------------------------------------
    # BPM tap
    # ------------------------------------------------------------------
    def _on_tap(self):
        bpm = self.tapper.tap()
        self.lbl_tap_bpm.setText(f"{bpm:.1f}" if bpm else "--")

    # ------------------------------------------------------------------
    # OFFSET adjust
    # ------------------------------------------------------------------
    @staticmethod
    def _bar_grid_clicks(bars):
        """波形のグリッド用クリックを作る。中身は waveform_data へ移した
        (録画側でも同じグリッドを引くため)。ここは呼び出し口だけ残す。"""
        return bar_grid_clicks(bars)

    def _set_game_grid(self, bpm, offset):
        """作譜波形のグリッドを bar_times 由来のクリックで引き直す。データが
        まだ無ければメトロノーム用にフォールバック(空譜面/読み込み前)。"""
        clicks = self._game_grid_clicks or self._editor_metronome_clicks
        self.game_waveform.set_beat_grid(bpm, offset, clicks)
        # 作譜モードの編集ペインにも同じものを渡す。ここを飛ばすと編集ペインの
        # offset が 0 のままになり、親が描く音符(譜面時刻 - offset)と編集
        # グリッド/カーソルが OFFSET のぶんだけ食い違う。
        self.chart_edit.set_beat_grid(bpm, offset, clicks)

    def _wire_waveform(self, wf: WaveformWidget, sync_stereo: bool = True):
        """ドック側/ゲーム窓側の2つの WaveformWidget を同じ配線にする。
        ミップチェインは共有、OFFSET調整モードの確定値は既存の OFFSET スピン
        ボックスへ流す(ヘッダ書き込みの経路を二重に持たないため)。

        sync_stereo=False のときはステレオ/合成の共有をしない(作譜モードの
        波形は常に合成を既定にしたいので、ドック側の設定と切り離す)。この波形の
        合成ボタンは自分の表示だけを切り替える。"""
        wf.seekRequested.connect(self._on_seek_requested)
        wf.offsetPreview.connect(self._on_waveform_offset_preview)
        wf.offsetCommitted.connect(self._on_waveform_offset_committed)
        if sync_stereo:
            wf.stereoToggled.connect(self._on_waveform_stereo_toggled)
            wf.set_stereo_view(self._waveform_stereo)
        if self._waveform_mips is not None:
            wf.set_mips(self._waveform_mips)

    def _waveforms(self):
        return (self.waveform, self.game_waveform, self.chart_edit)

    def _on_waveform_stereo_toggled(self, stereo: bool):
        # ドック側の波形の合成/ステレオ設定のみ同期・保存する。作譜モードの
        # 波形(game_waveform)は合成固定の独立表示なので触らない。
        self._waveform_stereo = bool(stereo)
        self.waveform.set_stereo_view(stereo)
        if self.waveform_stereo_cb:
            self.waveform_stereo_cb(self._waveform_stereo)

    def set_se_text_enabled(self, enabled: bool):
        """settings.json / 環境設定ダイアログの se_text_enabled を反映する入口。
        set_waveform_stereo と同じく、保存はメインウィンドウ側の責務。"""
        self._se_text_enabled = bool(enabled)
        self.chart_preview.set_se_text_enabled(self._se_text_enabled)

    def set_waveform_stereo(self, stereo: bool):
        """settings.json の waveform_stereo を復元するための入口(保存コール
        バックは呼ばない)。"""
        self._waveform_stereo = bool(stereo)
        self.waveform.set_stereo_view(self._waveform_stereo)

    def _on_waveform_offset_preview(self, value: float):
        """ドラッグ中の未確定 OFFSET: グリッド表示だけ両方の波形に反映し、
        TJA ヘッダには書かない(確定は offsetCommitted 側)。"""
        self.waveform.set_beat_grid(self._editor_bpm, value, self._editor_metronome_clicks)
        self._set_game_grid(self._editor_bpm, value)  # 作譜波形は bar_times 由来グリッド
        self.status_label.setText(f"OFFSET調整中: {value:+.3f} 秒")

    def _on_waveform_offset_committed(self, value: float):
        # 確定はスピンボックス経由。valueChanged → _on_offset_value_changed →
        # _on_apply_offset → apply_offset_cb と、既存の OFFSET 書き込み経路を
        # そのまま使う(元に戻す操作もスピンボックスと同等)。
        value = max(self.spin_offset.minimum(), min(value, self.spin_offset.maximum()))
        self.spin_offset.setValue(round(value, 3))
        self.status_label.setText(f"OFFSET を {self.spin_offset.value():+.3f} 秒に設定しました。")

    def _on_offset_value_changed(self, value):
        self.waveform.set_beat_grid(self._editor_bpm, value, self._editor_metronome_clicks)
        self._set_game_grid(self._editor_bpm, value)
        self.metronome.set_schedule(self._editor_metronome_clicks, value)
        self.hit_sounds.set_schedule(self._editor_notes, value)
        self.chart_preview.set_offset(value)
        # Auto-synced into the TJA's own OFFSET: line as the user adjusts it
        # (not just on button click) - this only fires for user-driven
        # changes since the "load a new/same wave" paths above set the
        # spinbox with blockSignals(True).
        self._on_apply_offset()

    def _on_apply_offset(self):
        self.apply_offset_cb(f"{self.spin_offset.value():.3f}")
