"""作譜モード: 波形の譜面帯にグリッドとカーソルを出し、キーで音符を置く。

波形・音符・命令帯の描画は WaveformWidget のものをそのまま使い、この派生
クラスは「グリッド線」「編集カーソル」「キー入力」「凡例」だけを足す。

カーソルは時刻ではなく **(小節番号, スロット番号)** で持つ。TJA の音符は
「ある小節のあるスロットの1文字」なので、この住所のままテキストへ書き戻せる
(neotja/note_edit.py)。時刻との変換は小節の開始/終了時刻からの比例計算。

音符を置いた直後は、正式な再解析(600ms デバウンス)を待たずに手元の音符列へ
同じ変更を当てて描く。待っていると打ち込みのたびに引っかかるため。
"""

import bisect
import math
import os
import time
from fractions import Fraction

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QPainter, QPen, QPixmap,
                           QPolygon, QKeySequence)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QSlider, QVBoxLayout,
                               QWidget)  # noqa: F401  (QWidget は型注釈用)

from neotja import note_edit
from neotja.waveform_widget import WaveformWidget

# 分割数の候補。1/1 から 1/192 まで(利用者の指定 2026-10-01。PeepoDrumKit の
# グリッドと同じ範囲)。TJA に書ける分割(constants.VALID_MEASURE_COUNTS)と、
# 3連・6連で使う値を並べてある。↑↓ キーとホイールはこの並びを1段ずつ動く。
GRID_CHOICES = [1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 48, 64, 96, 128, 192]

# 定規のような目盛りにする。4分(拍)の位置だけ白い長い線を引き、その間は
# 今の分割の色で短い線を等間隔に並べる。長さで拍が読めて、色で今どの分割で
# 打っているかが分かる。
BEAT_COLOR = (235, 235, 235)
BEAT_FRAC = 1.00       # 4分の線の長さ(帯の高さに対する割合)
SUB_FRAC = 0.34        # その間の線の長さ

# 分割ごとの色。線の長さは分割によらず同じ(SUB_FRAC)。
GRID_COLORS = {
    4:  BEAT_COLOR,
    8:  ( 70, 160, 255),   # 青
    12: (175, 115, 255),   # 紫
    16: (255, 205,  60),   # 黄
    24: (255, 110, 185),   # 桃
    32: ( 90, 220, 130),   # 緑
    48: (255, 150,  70),   # 橙
    64: (110, 225, 225),   # 水
}

# 音符文字 → (表示名, 色キー)。色は theme のキー名。
NOTE_INFO = {
    "1": ("ドン", "don"),
    "2": ("カッ", "ka"),
    "3": ("大ド", "don"),
    "4": ("大カ", "ka"),
    "5": ("連打", "roll"),
    "6": ("大連", "roll"),
    "7": ("風船", "roll"),
    "8": ("終端", "fg_dim"),
    "9": ("くす", "roll"),
    "0": ("消去", "fg_dim"),
}

# 数字キー → 音符文字。エディタに打つのと同じ対応。置いてもカーソルは進まない
# (利用者の指定。F/J などと同じ)。
_PLAIN_KEYS = {
    Qt.Key_1: "1", Qt.Key_2: "2", Qt.Key_3: "3", Qt.Key_4: "4", Qt.Key_5: "5",
    Qt.Key_6: "6", Qt.Key_7: "7", Qt.Key_8: "8", Qt.Key_9: "9", Qt.Key_0: "0",
    Qt.Key_T: "8", Qt.Key_Y: "8",
}

# PeepoDrumKit と同じ並びのキー(chart_editor_settings.h の既定値)。
# こちらは PeepoDrumKit の決まりで動く — 置いてもカーソルは進まず、同じ色を
# もう一度押すと消える。Alt で大きいほう。
_PEEPO_NOTE_KEYS = {Qt.Key_F: "1", Qt.Key_J: "1", Qt.Key_D: "2", Qt.Key_K: "2"}
# 押しているあいだ ←→ で長さを決め、離したところで置く。
_PEEPO_LONG_KEYS = {Qt.Key_R: "5", Qt.Key_U: "5", Qt.Key_E: "7", Qt.Key_I: "7"}
_BIG = {"1": "3", "2": "4", "5": "6", "7": "9"}


class _CommandInput(QFrame):
    """命令の値を入れる小さな入力欄。カーソルのすぐ上に出る。

    Enter で決定、Esc で取り消し。空欄で決定するとその位置の命令を消す。
    決定の中身は on_accept(文字列) が決め、False を返したら閉じない
    (数値でないときなど、入れ直してもらう)。

    **Qt.Popup ではなく枠なしの Tool 窓。** Windows では Popup は
    「アクティブな窓」にならないので、日本語入力(IME)の変換中の文字が
    この欄ではなく後ろのペイン(IME を切ってある)へ行ってしまい、歌詞に
    日本語が打てなかった(利用者の報告 2026-11-13)。自分で窓として前に
    出てアクティブになる。Popup がしてくれていた「外を押したら閉じる」は、
    窓がアクティブでなくなったら閉じることで代わりにする。"""

    def __init__(self, parent, title, text, on_accept):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName("chartEditCommandInput")
        self.setStyleSheet(
            "#chartEditCommandInput { background: #20232b; border: 1px solid #ffd23c; }"
            "QLabel { color: #e8e8e8; }"
            "QLabel#hint { color: #9aa0aa; }"
            "QLineEdit { background: #111318; color: #ffffff; border: 1px solid #555;"
            " padding: 2px 4px; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(6)
        lay.addWidget(QLabel(title))
        self.edit = QLineEdit(text)
        # ペインは IME を切っているが、この欄では日本語を打つ。
        self.edit.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.edit.setFixedWidth(84)
        self.edit.selectAll()
        lay.addWidget(self.edit)
        hint = QLabel("Enter 決定 / 空欄で削除")
        hint.setObjectName("hint")
        lay.addWidget(hint)
        self._on_accept = on_accept
        #: 一度アクティブになったか。なる前の「アクティブでない」で閉じない。
        self._was_active = False
        self.edit.returnPressed.connect(self.accept)

    def open_at(self, global_pos):
        """global_pos に出して、前に出し、入力欄へ打てるようにする。"""
        self.adjustSize()
        self.move(global_pos)
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus(Qt.ActiveWindowFocusReason)

    def accept(self):
        if self._on_accept(self.edit.text().strip()):
            self.close()
        else:
            self.edit.selectAll()
            self.edit.setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)

    def event(self, e):
        t = e.type()
        if t == QEvent.WindowActivate:
            self._was_active = True
        elif t == QEvent.WindowDeactivate and self._was_active and self.isVisible():
            # 外(ペインやほかの窓)を押した = Popup のときと同じく、決めずに閉じる。
            # このイベントの最中に消すと Qt が嫌うので、ひと呼吸おく。
            QTimer.singleShot(0, self.close)
        return super().event(e)

    def closeEvent(self, e):
        # Enter / Esc で閉じたとき(まだこちらがアクティブ)は、打鍵を
        # ペインへ戻す。外を押して閉じたときは押した先に任せる
        # (ほかのアプリへ移ったのに、こちらの窓を前へ戻したりしない)。
        pane = self.parentWidget()
        if self.isActiveWindow() and pane is not None:
            pane.window().activateWindow()
            pane.setFocus(Qt.OtherFocusReason)
        super().closeEvent(e)


class _GridPicker(QFrame):
    """分割(1/1〜1/192)を選ぶ小さな窓。左上の「1/16」を押すと出る。

    横のつまみを引くと、その場で分割が変わって譜面の目盛りも付いてくる
    (PeepoDrumKit と同じ手触り。利用者の指定 2026-10-01)。決定も取り消しも
    いらない — 引いた先がそのまま今の分割で、外を押せば閉じる。"""

    def __init__(self, parent, choices, current, on_change):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("chartEditGridPicker")
        self.setStyleSheet(
            "#chartEditGridPicker { background: #20232b; border: 1px solid #ffd23c; }"
            "QLabel { color: #e8e8e8; }"
            "QLabel#value { color: #ffd23c; font-weight: bold; }"
            "QLabel#hint { color: #9aa0aa; }")
        self._choices = list(choices)
        self._on_change = on_change
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel("グリッド"))
        self.value = QLabel("1/%d" % current)
        self.value.setObjectName("value")
        self.value.setFixedWidth(52)
        top.addWidget(self.value)
        hint = QLabel("← → でも動かせます")
        hint.setObjectName("hint")
        top.addWidget(hint)
        lay.addLayout(top)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, len(self._choices) - 1)
        self.slider.setPageStep(1)
        self.slider.setFixedWidth(240)
        try:
            self.slider.setValue(self._choices.index(current))
        except ValueError:
            self.slider.setValue(self._choices.index(16))
        self.slider.valueChanged.connect(self._changed)
        lay.addWidget(self.slider)
        self.slider.setFocus()

    def _changed(self, i):
        g = self._choices[max(0, min(len(self._choices) - 1, i))]
        self.value.setText("1/%d" % g)
        self._on_change(g)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)


class ChartEditWaveform(WaveformWidget):
    """作譜モードの波形。グリッド + カーソル + キー入力。"""

    # (小節番号, スロット番号, 分割数, 音符文字) を上へ投げる。
    noteEdited = Signal(int, int, int, str)
    # カーソルが動いたときに時刻を通知(上位がシークするかは上位の判断)。
    cursorMoved = Signal(float)
    #: 上の目盛りで選んだ小節を消してほしい(小節番号の一覧)。
    measuresDeleteRequested = Signal(list)
    # 凡例の表示を「ユーザーが」切り替えたときだけ飛ぶ。設定へ覚えさせるため。
    # set_legend_visible() では出さない(起動時の復元で保存を呼び返さないよう)。
    legendToggled = Signal(bool)
    # 選んだものが変わった(命令パネルを「追加/変更」へ切り替えるため)。
    selectionChanged = Signal()

    LEGEND_H = 18          # 凡例の帯の高さ
    #: 再生位置(＝編集カーソル)をペインの真ん中に置く(利用者の指定)。
    #: 作譜は「いま置いた音符」と「これから置く場所」を行き来して見るので、
    #: 左右が同じ幅で見えるほうがよい。音声波形ページは親の 0.3 のまま。
    FOLLOW_FRAC = 0.33
    # 譜面の末尾より後ろへ、これだけ先までカーソルを進められる。置いた時点で
    # 足りない小節はテキスト側に自動で足される(note_edit.set_slot)。小節が
    # 1つも無い譜面でも打ち始められるようにするための仕組みでもある。
    EXTEND_MEASURES = 64

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 再生中の塗り直しの回数 (settings.json の peepo_edit_redraw_fps)
    # ------------------------------------------------------------------
    # このペインはふつうの Qt ウィジェットで、ゲーム画面のほうは GPU の面へ
    # 出している。**この2つが同じ窓の中で1枚に組み立てられていると、ここを
    # 1回塗り直すたびに窓ぜんたいの組み直しが走る** — 中身を空にしても
    # 87 fps しか出なかったので、値段はこちらの描画ではなく組み立てのほう。
    # そのときはレーンの fps を上げる道が「ペインを塗る回数を減らす」しか
    # なく、レーンのフレームごとに塗るのをやめて間引いていた(位置そのものは
    # 毎フレーム受け取って進めるので、赤い線の場所は間引いても正しい。
    # 見え方が粗くなるだけ)。
    #
    # **ゲーム画面を別の窓(GLScreenHost)にすると、この綱引きが消える。**
    # そのときは間引かないほうが素直に速い。実測(1280x708 / 120Hz / GTX970):
    #
    #                    上限      レーン     ペイン
    #   同じ窓(従来)       60      215 fps     47 回/秒
    #   同じ窓(従来)        0       76 fps     76 回/秒  ← 綱引き
    #   別の窓(既定)       60      259 fps     73 回/秒
    #   別の窓(既定)        0      196 fps    212 回/秒  ← これが一番良い
    #   CPU 描画           60      121 fps     44 回/秒
    #   CPU 描画            0       95 fps     95 回/秒
    #
    # よって既定は「おまかせ(-1)」。ゲーム画面が別の窓なら上限なし、同じ窓
    # (従来の GPU 描画・CPU 描画)なら 60 にする。数を書けばそれに従う
    # (0 で毎フレーム、5〜240 でその回数)。
    REDRAW_FPS_DEFAULT = -1
    #: 綱引きがあるときの上限。モニタが出せるのは毎秒 120 コマまでなので、
    #: レーンがそれを上回る範囲でペインへ回せるぶんを回した値。
    REDRAW_FPS_COUPLED = 60
    # 親の __init__ の途中で update() が呼ばれても落ちないように、クラス側にも
    # 既定値を置く(下の __init__ で本物を入れる)。
    _quiet = False
    _redraw_fps = 0
    _redraw_setting = -1
    #: ゲーム画面が別の窓か(綱引きが無いか)。上位が set_screen_uncoupled で
    #: 教える。分からないうちは「同じ窓」= 安全側で見ておく。
    _screen_uncoupled = False
    _last_paint_wall = 0.0

    def _load_redraw_fps(self):
        """設定に書いてある値。-1 は「おまかせ」。"""
        try:
            from neotja import settings as settings_mod
            v = int(settings_mod.load_settings().get("peepo_edit_redraw_fps",
                                                    self.REDRAW_FPS_DEFAULT))
        except Exception:  # noqa: BLE001
            return self.REDRAW_FPS_DEFAULT
        if v < 0:
            return -1
        if v == 0:
            return 0
        return max(5, min(240, v))

    def set_screen_uncoupled(self, uncoupled: bool):
        """ゲーム画面が別の窓かどうかを教える(おまかせの判断に使う)。"""
        self._screen_uncoupled = bool(uncoupled)
        self._apply_redraw_cap()

    def _apply_redraw_cap(self):
        v = self._redraw_setting
        if v < 0:
            v = 0 if self._screen_uncoupled else self.REDRAW_FPS_COUPLED
        self._redraw_fps = v

    def __init__(self, parent=None, toggle_play_cb=None, show_demo=True,
                 show_stereo=True):
        super().__init__(parent, toggle_play_cb=toggle_play_cb,
                         force_dark=True, show_demo=show_demo,
                         show_stereo=show_stereo)
        self._grid = 16
        self._cur_measure = 0
        self._cur_slot = 0
        self._bar_times_raw = []   # 小節の開始時刻(譜面時間。権威データ)
        self._bar_times = []       # 上を OFFSET で音源時間へ直したもの
        # 「小節線」の行に出す、隠れている区間。譜面時間と音源時間。
        self._barline_raw = None
        self._barline_audio = None
        # 範囲の命令そのもの [(譜面時刻, 名前)]。解析の region_marks。
        # 札はこちらから作る(_objects / _draw_cmd_marks)。
        self._region_raw = None
        self._region_audio = None
        # 命令を種類ごとに分けた列(_rebuild_cmd_kinds)。
        self._cmd_by_kind = None
        self._cmd_kind_times = None
        # 選んだ範囲の音符をつかんで動かしている最中の情報。
        self._note_drag = None
        # 選んだオブジェクトの鍵(エクスプローラー式の選択)。
        self._sel = set()
        # 上の目盛りの小節番号を押して選んだ小節。音符や命令の選択
        # (_sel)とは別に持つ — 消す以外のことはしないので、_sel の
        # 道(まとめて動かす・コピー)へ混ぜない。
        self._sel_measures = set()
        # 目盛りの番号を押した直後。引かずに離せばその小節を選ぶ。
        self._pending_measure = None
        # 何も無い所を左ドラッグして囲っている最中の四角。
        self._band = None
        # 小節が1つも無いとき/末尾より先を外挿するときの1小節の長さ。
        # ヘッダの BPM から入れてもらう(既定は BPM120 の 4/4)。
        self._default_measure_len = 2.0
        self._show_legend = True
        # 波形の縦の倍率(設定に覚える)。音が小さい曲ではアタックが見えない、
        # という報告への対応(利用者の要望 2026-10-08)。
        try:
            from neotja import settings as settings_mod
            self._wave_gain = float(settings_mod.load_settings().get(
                "chart_edit_wave_gain", 1.0) or 1.0)
        except Exception:  # noqa: BLE001
            self._wave_gain = 1.0
        self._wave_gain = max(0.25, min(8.0, self._wave_gain))
        # 楽観的表示用。置いた直後、再解析が届くまでのあいだ描く音符。
        # {(小節, スロット, 分割数): 文字}
        self._pending = {}
        # PeepoDrumKit 式の操作を実行する口(preview_dock が入れる)。
        # op dict を渡すと、結果(暫定表示用)を返す。
        self._op_cb = None
        # 範囲選択。(小節, 小節の中の割合) の組。終わりが決まるまで end は None。
        # 最後に打った音符。Shift+←→ でこれを引きずる
        # (利用者の言い直し 2026-10-30)。
        self._paint_char = None
        self._range_start = None
        self._range_end = None
        # 右ドラッグでの範囲選択の起点(画面 x)。
        # 連打・風船を置いている途中。{"key", "char", "head": (小節, スロット)}
        self._long = None
        # 終端 8 が無い長い音符の開始時刻(譜面時刻)。先頭の音符だけで描く。
        self._open_starts = []
        # 解析が返してきた帯そのもの(書きかけの帯を縮める前)。
        self._spans_full = None
        # 自分でカーソルを動かしてシークした直後は、返ってくる位置を無視する
        # 期限(time.monotonic)。SEEK_ECHO_SEC を参照。
        self._own_seek_until = 0.0
        # 再生中か(preview_dock が入れる)。再生中は黄色いカーソルを描かない。
        self._playing = False
        self._follow_transition = None
        # 右クリックを押した時点の範囲と位置。動かさずに離したとき(=メニュー)は
        # 範囲を押す前に戻し、その位置でメニューを出す。
        # いま出ている命令の入力欄(テストから触るため覚えておく)。
        self._cmd_popup = None
        # --- 再生中の塗り直しの間引き(REDRAW_FPS_DEFAULT の説明を参照) ---
        self._redraw_setting = self._load_redraw_fps()
        self._apply_redraw_cap()
        self._last_paint_wall = 0.0
        # True のあいだ update() を飲み込む(状態だけ進めて塗らない)。
        self._quiet = False
        # 間引いたぶんを最後に1回出すためのタイマー。取りこぼした位置が
        # 描かれないまま残らないように、必ず追いの1枚を出す。
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.timeout.connect(self.update)
        # 左の行名の列は動かないので、1枚に焼いて使い回す。
        self._labels_pm = None
        self._labels_key = None

    # ------------------------------------------------------------------
    # 外から入れるもの
    # ------------------------------------------------------------------
    def set_bar_times(self, times, offset=None):
        """小節の開始時刻(譜面時間)を持つ。音源時間への変換は self.offset で行う。

        build_preview_timeline の "bar_times" は (時刻, BPM, SCROLL, 表示) の
        タプル列。時刻だけあればよいので取り出す(素の float が来ても通す)。

        譜面時刻のまま覚えておき、音源時間へは _apply_offset_local で直す。
        親が音符を置くのと同じ変換(譜面時刻 - OFFSET)を必ず通すためで、
        ここで一度きり引き算してしまうと、後から OFFSET が変わったときに
        音符だけが動いてグリッドが取り残される。"""
        raw = []
        last_bpm = None
        vis = []
        for item in (times or []):
            if isinstance(item, (tuple, list)):
                raw.append(float(item[0]))
                if len(item) > 1 and item[1]:
                    last_bpm = float(item[1])
                vis.append(bool(item[3]) if len(item) > 3 else True)
            else:
                raw.append(float(item))
                vis.append(True)
        self._bar_times_raw = raw
        # 「小節線」の行に出す、隠している区間(譜面時間)。bar_times の表示フラグが
        # 落ちている小節を繋げる。#BARLINEOFF/#BARLINEON そのものの行位置では
        # なく「結果として隠れている範囲」なので、見たままになる。
        hidden = []
        start = None
        for i, v in enumerate(vis):
            if not v and start is None:
                start = raw[i]
            elif v and start is not None:
                hidden.append((start, raw[i]))
                start = None
        if start is not None:
            hidden.append((start, raw[-1] if raw else start))
        self._barline_raw = hidden or None
        if self._region_raw is not None:
            self._rebuild_barline_regions()
        # 小節が1つしか無いと間隔から長さを測れない。解析が返してきた BPM を
        # 使う(ヘッダの BPM が空の新規譜面でも、ここは埋まっている)。
        if last_bpm and last_bpm > 0:
            self._default_measure_len = 240.0 / last_bpm
        if offset is not None and offset != self.offset:
            # 渡された OFFSET を正とする(親の音符もこの値で置き直される)。
            self._apply_offset_local(offset)
        else:
            self._rebuild_bar_times()
        self._clamp_cursor()
        self.update()

    def set_region_marks(self, marks):
        """範囲の命令 [(譜面時刻, 名前)] を受け取る。

        ゴーゴー・小節線の札は、今まで「結果としてこうなった範囲」から
        作っていた。範囲は小節の境目でしか測れないので、小節の途中へ
        動かした命令が元の所へ戻って見え、同じ命令が2本あると後ろの1本が
        札として出ず、選べない=消せなかった(利用者の報告 2026-11-05)。
        命令そのものを受け取って、1本ずつ札にする。
        """
        self._region_raw = ([(float(t0), str(n)) for t0, n in marks]
                            if marks is not None else None)
        if self._region_raw is not None:
            self._rebuild_barline_regions()
        self._rebuild_bar_times()
        self.update()

    def _rebuild_barline_regions(self):
        """非表示の帯と命令の縦線を、同じ命令時刻から作る。"""
        hidden = []
        start = None
        for at, name in self._region_raw or []:
            if name == 'BARLINEOFF' and start is None:
                start = at
            elif name == 'BARLINEON' and start is not None:
                if at > start:
                    hidden.append((start, at))
                start = None
        if start is not None:
            timing = getattr(self, 'note_timing', None)
            raw = self._bar_times_raw or []
            end = float(timing.end) if timing is not None else (raw[-1] + self._default_measure_len if raw else start)
            if end > start:
                hidden.append((start, end))
        self._barline_raw = hidden or None

    def set_time_offset(self, offset):
        """譜面の時刻 ↔ 音の位置の OFFSET だけを差し替える。

        音源が無い譜面は「無音の音源」の頭に助走を足して鳴らすので、
        その助走ぶんを含んだ値がここへ来る。入れ忘れると、届く再生位置は
        助走ぶん進んでいるのにペインの目盛りは素のままで、赤い線が
        助走ぶん先に立つ(利用者の報告 2026-11-05)。
        """
        try:
            offset = float(offset)
        except (TypeError, ValueError):
            return
        if offset != self.offset:
            self._apply_offset_local(offset)

    def _rebuild_cmd_kinds(self):
        """命令を種類ごとに分けて持つ(行ごとの描画用)。"""
        by = {}
        for item in (self._cmd_audio or []):
            if len(item) < 4:
                continue
            by.setdefault(item[3], []).append((item[0], item[1]))
        self._cmd_by_kind = by or None
        self._cmd_kind_times = ({k: [t for t, _x in v] for k, v in by.items()}
                                if by else None)

    #: 歌詞の札の色。ほかの命令と見分けが付く、落ち着いた緑。
    LYRIC_COLOR = "#7fd6a0"
    #: 札に出す歌詞の長さ(これより長ければ「…」で切る)。
    LYRIC_CHIP_CHARS = 8

    @classmethod
    def lyric_chip_text(cls, text):
        """札に出す字面。長い歌詞は頭だけ。空の #LYRIC は「(消す)」。"""
        s = " ".join(str(text or "").split())
        if not s:
            return "(消す)"
        if len(s) > cls.LYRIC_CHIP_CHARS:
            return s[:cls.LYRIC_CHIP_CHARS] + "…"
        return s

    def set_commands(self, bpm_changes, scroll_changes, measure_changes,
                     gogo_regions, *args, **kwargs):
        # ゴーゴーの薄い色と小節線は帯に焼いてある(_static_strip を参照)。
        # 拍子の変化点は**全部**覚えておく(親は先頭の基準値を落として
        # 「変わった所」だけを札にする)。グリッドを拍の長さで割るのに、
        # 小節ごとの拍子が要るため — _measure_beats を参照。
        self._meter_changes = sorted(
            (float(t), int(n), int(d)) for t, n, d in (measure_changes or []))
        super().set_commands(bpm_changes, scroll_changes, measure_changes,
                             gogo_regions)
        # 歌詞(#LYRIC)も札にする。**この作譜ペインだけ** — 波形のほうは
        # 同じ _cmd_raw を別に持っているので混ざらない。
        lyrics = kwargs.get("lyrics")
        if lyrics is None and args:
            lyrics = args[0]
        self._lyrics_raw = list(lyrics or [])
        if self._lyrics_raw:
            raw = list(self._cmd_raw or [])
            for t, txt in self._lyrics_raw:
                raw.append((float(t), self.lyric_chip_text(txt),
                            self.LYRIC_COLOR, "lyric"))
            self._cmd_raw = raw
            self._apply_offset_local(self.offset)
        self._bump_strip()

    #: いまの歌詞 [(譜面時刻, 文字)]。札の字面ではなく**元の文字**。
    _lyrics_raw = ()
    _lyric_drag = None

    def _lyric_spans(self, t0, t1):
        """表示範囲にかかる歌詞 (開始, 終了, 動かせる上限, 全文)。時刻は音源基準。"""
        lyrics = self._lyrics_raw
        if not lyrics:
            return
        times = (self._cmd_kind_times or {}).get("lyric", ())
        course_end = self._time_of_pos(Fraction(self._known_measures()))
        if course_end is None:
            return
        lo = max(0, bisect.bisect_left(times, t0) - 1)
        for i in range(lo, len(lyrics)):
            t, txt = lyrics[i]
            start = t - self.offset
            if start > t1:
                break
            if not txt:
                continue
            j = i + 1
            end = lyrics[j][0] - self.offset if j < len(lyrics) else course_end
            if j < len(lyrics) and not lyrics[j][1]:
                j += 1
            limit = lyrics[j][0] - self.offset if j < len(lyrics) else course_end
            if end > start:
                yield start, end, limit, txt

    def _lyric_handle_at(self, x, y):
        top, height = self._row_rects()["lyric"]
        if not top + 3 <= y < top + height - 3:
            return None
        # 境界のすぐ右は次の歌詞の開始札。右端の内側だけをつかむ。
        for span in self._lyric_spans(self._x_to_sec(x - 1), self._x_to_sec(x + 6)):
            edge = self._sec_to_x(span[1])
            if self._sec_to_x(span[0]) < x and edge - 5 <= x <= edge:
                return span
        return None

    def _update_lyric_drag(self, x):
        drag = self._lyric_drag
        address = self._address_from_time(self._x_to_sec(x), nearest=True)
        if address is None:
            return
        m, slot = address
        dest = self._address_time(m, slot, self._measure_grid(m))
        if dest is not None:
            # 開始と同じ位置に停止印を置くと歌詞を消すので、最低1目盛を残す。
            start, _, limit, _ = drag["span"]
            first = self._address_from_time(start, nearest=False)
            if first is None:
                return
            fm, fs = first
            minimum = self._address_time(fm, fs + 1, self._measure_grid(fm))
            drag["end"] = min(limit, max(dest, minimum))
            self.update()

    def _draw_lyric_bands(self, p, y, h, t0, t1):
        col = QColor(self.LYRIC_COLOR)
        fill = QColor(col)
        fill.setAlpha(70)
        p.save()
        p.setPen(col)
        f = self.font()
        f.setPixelSize(self.fpx(11))
        p.setFont(f)
        # 小さな絵の一辺。行の高さに合わせて縮む(表示倍率)。帯は上下 3px
        # ずつ内側なので、それより 2px ずつ小さく。
        ts = max(6, min(self.spx(self.JACKET_THUMB_PX), h - 10))
        # 絵を出すのは「絵 + 2文字ぶん」より広い帯だけ。狭い帯では絵を
        # 諦めて字を残す(何の歌詞かが読めるほうが大事)。
        room = ts + 6 + 2 * p.fontMetrics().horizontalAdvance("あ") + 10
        jackets = self._lyric_jackets
        for start, end, _, txt in self._lyric_spans(t0, t1):
            if self._lyric_drag and self._lyric_drag["span"][0] == start:
                end = self._lyric_drag["end"]
            x, r = self._sec_to_x(start), self._sec_to_x(end)
            w = max(1, r - x)
            p.fillRect(x, y + 3, w, h - 6, fill)
            p.drawRect(x, y + 3, w, h - 6)
            p.fillRect(r - min(3, w), y + 5, min(3, w), h - 10, col)
            tx = x + 5
            if jackets and w >= room:
                pm = self._jacket_thumb(jackets.get(txt.strip()), ts)
                if pm is not None:
                    p.drawPixmap(x + 4, y + (h - ts) // 2, pm)
                    tx = x + 4 + ts + 4
            if r - 5 - tx > 0:
                p.drawText(tx, y, r - 5 - tx, h,
                           Qt.AlignVCenter | Qt.AlignLeft, txt)
        p.restore()

    def _draw_lyric_sticky(self, p, y, h, t0, t1):
        """頭が左へ外れた歌詞の帯に、見えている左端から字を出し直す。

        利用者の指定 2026-11-13。帯の字は帯の頭に焼いてあるので、長い
        歌詞の頭が画面の左へ流れると、帯は見えているのに何の歌詞か読め
        なかった。毎コマ描く側(_paint_rows)で、左端(LANE_X0)をまたぐ
        帯だけ、帯の中を地の色+帯の色で塗り直してから字(と小さな絵)を
        左端 + 5 に描く。塗り直すのは、焼いてある字が下から透けて二重に
        見えないようにするため。頭が見えている帯は焼いたままで触らない。
        左端をまたぐ帯はふつう 1 本なので、1コマの増えは字 1〜2 回ぶん。"""
        left = self.LANE_X0
        spans = None
        for start, end, _, txt in self._lyric_spans(t0, t0):
            if self._lyric_drag and self._lyric_drag["span"][0] == start:
                end = self._lyric_drag["end"]
            x, r = self._sec_to_x(start), self._sec_to_x(end)
            if x < left < r:
                if spans is None:
                    spans = []
                spans.append((r, txt))
        if not spans:
            return
        col = QColor(self.LYRIC_COLOR)
        fill = QColor(col)
        fill.setAlpha(70)
        bg = QColor(self._pal["bg2"])      # 帯の下の地(焼いた帯と同じ色)
        p.save()
        p.setPen(col)
        f = self.font()
        f.setPixelSize(self.fpx(11))
        p.setFont(f)
        ts = max(6, min(self.spx(self.JACKET_THUMB_PX), h - 10))
        room = ts + 6 + 2 * p.fontMetrics().horizontalAdvance("あ") + 10
        jackets = self._lyric_jackets
        for r, txt in spans:
            # 枠線(上下 y+3 / y+h-3)と右端のつまみ(r-3〜)は残して中だけ塗る。
            iw = r - 3 - (left + 1)
            if iw <= 0:
                continue
            p.fillRect(left + 1, y + 4, iw, h - 7, bg)
            p.fillRect(left + 1, y + 4, iw, h - 7, fill)
            tx = left + 5
            if jackets and r - left >= room:
                pm = self._jacket_thumb(jackets.get(txt.strip()), ts)
                if pm is not None:
                    p.drawPixmap(left + 4, y + (h - ts) // 2, pm)
                    tx = left + 4 + ts + 4
            if r - 5 - tx > 0:
                p.drawText(tx, y, r - 5 - tx, h,
                           Qt.AlignVCenter | Qt.AlignLeft, txt)
        p.restore()

    # ------------------------------------------------------------------
    # 歌詞に付けたジャケット(利用者の承認 2026-11-13)
    # ------------------------------------------------------------------
    #: 帯の左に出す小さな絵の一辺(px、原寸)。
    JACKET_THUMB_PX = 18
    #: 歌詞の文字 → 絵の道。MainWindow が set_lyric_jackets で入れる。
    _lyric_jackets = {}
    #: 絵の更新日時 {道: mtime}。set_lyric_jackets のときに1回だけ見る —
    #: 帯を焼くたび(=描くたび)にファイルを見に行かないため。
    _jacket_mtimes = {}
    #: 縮めた絵 {(道, mtime, 一辺, 倍率): QPixmap か None}。
    _jacket_thumbs = None
    _jacket_cb = None

    def set_lyric_jackets(self, mapping):
        """歌詞ごとの絵を入れ替える。帯を焼き直して小さな絵に反映する。"""
        mapping = {str(k).strip(): str(v) for k, v in dict(mapping or {}).items()
                   if str(k).strip() and v}
        mt = {}
        for path in set(mapping.values()):
            try:
                mt[path] = os.path.getmtime(path)
            except OSError:
                mt[path] = None          # 無い絵。帯には出さない
        self._lyric_jackets = mapping
        self._jacket_mtimes = mt
        if self._jacket_thumbs is None:
            self._jacket_thumbs = {}
        # 使われなくなった絵は捨てる(付け替えを繰り返しても溜まらない)。
        for key in [k for k in self._jacket_thumbs if k[0] not in mt]:
            del self._jacket_thumbs[key]
        self._bump_strip()
        self.update()

    def set_jacket_cb(self, cb):
        """右クリックの「ジャケットを挿入…/外す」の出口。cb(歌詞, 付けるか)。"""
        self._jacket_cb = cb

    def _jacket_thumb(self, path, side):
        """帯に出す小さな絵。読めない・無いなら None。覚えて使い回す。"""
        if not path:
            return None
        mtime = self._jacket_mtimes.get(path)
        if mtime is None:
            return None
        dpr = self.devicePixelRatioF()
        key = (path, mtime, int(side), round(dpr, 3))
        cache = self._jacket_thumbs
        if cache is None:
            cache = self._jacket_thumbs = {}
        if key not in cache:
            from neotja.dialogs.lyric_jacket_dialog import square_pixmap
            cache[key] = square_pixmap(path, side, max(2, side // 5), dpr)
        return cache[key]

    def lyric_band_at(self, x=None, y=None):
        """右クリックした歌詞の帯 (開始, 終了, 文字)。無ければ None。

        マウスの位置(x, y)を渡せば**その下の帯**(カーソルの位置では
        ない — 帯を右クリックしたのに、カーソルが別の帯にあってそちらに
        付く、ということが無いように)。y が歌詞の行の外なら None。
        x を渡さなければカーソルの位置の帯(窓の「作譜」メニュー用)。
        空の #LYRIC は帯にならないので、ここでも返らない。"""
        if x is not None and y is not None:
            top, height = self._row_rects()["lyric"]
            if not top <= y < top + height:
                return None
            t = self._x_to_sec(x)
        elif x is not None:
            t = self._x_to_sec(x)
        else:
            t = self.cursor_time()
        for start, end, _limit, txt in self._lyric_spans(t - 1e-6, t + 1e-6):
            if start <= t < end:
                return start, end, txt
        return None

    def lyric_at(self, t, tol=None):
        """その時刻に置いてある歌詞。無ければ None。

        t は**このペインの時刻**(cursor_time / 帯 / 波形と同じ、譜面時刻 -
        self.offset)。_lyrics_raw は譜面時刻のままなので、比べる前に
        offset を引く。以前は引いておらず、self.offset は OFFSET に頭の
        余白も足した値なので OFFSET:0 の譜面でもずれて、歌詞の頭に
        カーソルを置いても「歌詞を直す…」にならなかった(2026-11-13)。"""
        if not self._lyrics_raw:
            return None
        tol = self.SYNC_TOLERANCE_SEC if tol is None else float(tol)
        best, bd = None, None
        for tt, txt in self._lyrics_raw:
            d = abs(float(tt) - self.offset - float(t))
            if d <= tol and (bd is None or d < bd):
                best, bd = txt, d
        return best

    def open_lyric_popup(self):
        """カーソルの所へ歌詞を置く/直す小さな欄を出す(右クリックから)。

        空で決定すればその歌詞を消す。値が文字なので、ほかの命令より
        欄を広く取る。"""
        m, s = self._cursor_addr()
        cur = self.lyric_at(self.cursor_time())

        def accept(txt):
            self._run_op({"kind": "command", "a": (m, s), "name": "LYRIC",
                          "value": None if txt == "" else txt})
            return True

        popup = self._show_command_popup("歌詞", cur or "", accept)
        if popup is not None:
            popup.edit.setFixedWidth(260)
            popup.adjustSize()
        return popup

    def open_lyric_fix_popup(self):
        """カーソルが乗っている帯の歌詞を直す欄(帯の**頭の #LYRIC** を直す)。

        カーソルが帯の途中にあるときの「歌詞を直す…」。直す相手はカーソルの
        位置ではなく帯の頭の行なので、その位置(分数)から住所を作る。
        分割数はその位置の分母にする — 今の目盛りに乗っていない所に
        置いた歌詞でも、同じ行を指せる(新しい行を増やさない)。"""
        band = self.lyric_band_at()
        if band is None:
            return None
        pos = self._pos_of_time(band[0])
        if pos is None:
            return None
        m = int(pos)
        frac = pos - m

        def accept(txt):
            self._run_op({"kind": "command", "a": (m, frac.numerator),
                          "grid": frac.denominator, "name": "LYRIC",
                          "value": None if txt == "" else txt})
            return True

        popup = self._show_command_popup("歌詞", band[2], accept)
        if popup is not None:
            popup.edit.setFixedWidth(260)
            popup.adjustSize()
        return popup

    def _jacket_menu(self, menu, at_x=None, at_y=None):
        """歌詞の帯の上なら「ジャケットを挿入…」「ジャケットを外す」を足す。

        右クリックならマウスの下の帯、窓のメニューならカーソルの帯。
        帯の外(と空の #LYRIC)では出さない — 付ける相手が無い。"""
        if self._jacket_cb is None:
            return
        band = self.lyric_band_at(at_x, at_y)
        if band is None:
            return
        txt = band[2]
        has = bool(self._lyric_jackets.get(txt.strip()))
        menu.addSeparator()
        ins = menu.addAction("ジャケットを変更…" if has else "ジャケットを挿入…")
        ins.setData("lyric_jacket")
        ins.triggered.connect(lambda _c=False, t=txt: self._jacket_cb(t, True))
        # 付いていないときは灰色にして、右に理由を添える(見本どおり)。
        rm = menu.addAction("ジャケットを外す" if has
                            else "ジャケットを外す\t(付いていない)")
        rm.setData("lyric_jacket_remove")
        rm.setEnabled(has)
        rm.triggered.connect(lambda _c=False, t=txt: self._jacket_cb(t, False))

    def place_lyric_stop(self):
        """カーソルの所に「ここで歌詞を消す」印(文字の無い #LYRIC)を置く。

        利用者の指定 2026-11-07。「歌詞…」の空欄決定は**その命令を消す**
        (置き間違えたときの取り消し)ので、歌詞を止める印を置く道が別に
        要る。札には「(消す)」と出る。
        """
        m, s = self._cursor_addr()
        # 値が空文字 = 「文字の無い #LYRIC」。None(= 命令を消す)とは別もの。
        return self._run_op({"kind": "command", "a": (m, s),
                             "name": "LYRIC", "value": ""})


    #: 拍子の変化点 [(譜面時刻, 分子, 分母)]。既定は 4/4。
    _meter_changes = ((0.0, 4, 4),)

    def _measure_beats(self, m):
        """m 小節目の長さ(4分音符いくつぶん)。4/4 なら 4、1/4 なら 1。

        グリッドは拍(BPM)が基準なので、1小節をいくつに割るかは小節の長さで
        変わる(利用者の指定 2026-10-01)。"""
        mc = self._meter_changes or ((0.0, 4, 4),)
        raw = self._bar_times_raw or []
        if not raw:
            return Fraction(4)
        t = raw[m] if 0 <= m < len(raw) else raw[-1]
        i = bisect.bisect_right([c[0] for c in mc], t + 1e-9) - 1
        if i < 0:
            i = 0
        _t, num, den = mc[i]
        if num <= 0 or den <= 0:
            return Fraction(4)
        return Fraction(4 * num, den)

    def _measure_grid(self, m):
        """m 小節目をいくつに割るか。

        画面の分割 1/G は「G分音符」なので、1小節に入る数は
        その小節の拍数 × G ÷ 4。4/4 なら今までどおり G そのもの。"""
        g = self._grid
        if g <= 0:
            return 1
        n = self._measure_beats(m) * g / 4
        return max(1, int(round(float(n))))

    def _beat_period(self, m, mg):
        """何本ごとが「拍(4分音符)」の線か。乗る線が無ければ 0。

        以前は 4本ごとと決め打ちしていた。1/16 のときだけ正しく、三連
        (1/3・1/6・1/12・1/24)はもちろん 1/8・1/20・1/32 でも拍に乗って
        いなかった(利用者の報告 2026-10-05)。

        線 k は小節の頭から k/mg、拍の数にすると k * 拍数 / mg。これが
        整数になる k が拍の上。拍数は分数(7/8 拍子なら 7/2)なので、
        約分して「何本ごと」の整数にして返す — 1本ずつ分数を割ると
        塗り直しのたびに効いてくる(ここは1コマで数百回まわる)。
        1/12 なら 3本ごと、1/24 なら 6本ごと。"""
        beats = self._measure_beats(m)
        mg = int(mg)
        if mg <= 0 or beats <= 0:
            return 0
        bn, bd = beats.numerator, beats.denominator
        period = (mg * bd) // math.gcd(bn, mg * bd)
        return period if 0 < period < mg else 0

    def _rebuild_bar_times(self):
        self._bump_strip()             # 小節線・目盛りは帯に焼いてある
        self._bar_times = [max(0.0, t - self.offset)
                           for t in (self._bar_times_raw or [])]
        raw = getattr(self, "_barline_raw", None)
        self._barline_audio = ([(s - self.offset, e - self.offset) for s, e in raw]
                               if raw else None)
        rm = getattr(self, "_region_raw", None)
        self._region_audio = ([(t0 - self.offset, n) for t0, n in rm]
                              if rm is not None else None)

    def _apply_offset_local(self, offset):
        super()._apply_offset_local(offset)
        self._rebuild_cmd_kinds()
        # 親のコンストラクタからも呼ばれるので、まだ属性が無いことがある。
        if getattr(self, "_bar_times_raw", None) is not None:
            self._rebuild_bar_times()
            self._clamp_cursor()

    #: 再生位置がカーソルの時刻からこれ以内なら、カーソルを動かさない。
    SYNC_TOLERANCE_SEC = 0.003
    #: 自分でカーソルを動かしてシークしたあと、返ってくる位置を「こだま」と
    #: して無視する時間。MixerAudioEngine.seek() は **シーク前の位置** を
    #: すぐに出し、効いたあとの位置は出力レイテンシぶん手前になる(実測)ので、
    #: これを拾うとカーソルが元の場所や1つ手前へ引き戻される。
    SEEK_ECHO_SEC = 0.8

    def set_position(self, seconds: float):
        """停止中/シーク時の位置(preview_dock._on_position_changed から)。"""
        if self._echo_active():
            self._pin_playhead_to_cursor()
            return
        super().set_position(seconds)
        self._follow_playhead(seconds, nearest=True)
        # **よそからのシークでも景色を連れていく。** 下のシークバーや波形を
        # 押して遠くへ飛ぶと、赤い線だけ動いて譜面が元の所に残っていた
        # (利用者の報告 2026-11-07「下のバーで移動しようとすると線だけ
        # 動いて、見ている背景が移動しない」)。窓の中に居るうちは動かさない
        # ので、少しのシークでは今までどおり景色は動かない。
        if not self._playing and not self._hand_busy():
            self._scroll_line_into_view(seconds, self._visible_span())

    def set_position_smooth(self, seconds: float):
        """再生中の位置(レーンの 120fps クロックから)。

        こちらはこだまを無視しない。再生中にここを止めると、←→を押してから
        0.8 秒のあいだ赤い線が止まって見える。レーンのクロックはシーク先へ
        1フレームで移るし、ここからシークを出し直すことも無いので、
        引き戻し合いにはならない。

        塗り直しは REDRAW_FPS_DEFAULT の回数までに間引く。位置・表示範囲・
        カーソルは毎フレームぶん進める(間引くのは絵だけ)。

        **止まっているあいだだけは、自分で動かした直後を守る。** 停止中でも
        小節移動のトゥイーンが残っているとレーンのクロックは動き続けていて、
        その位置に素直に従うと、置いたばかりのカーソルが引き戻される
        (実測: ペインを画面の中で描くようにして塗り直しが速くなったら、
        毎回これに負けるようになった)。再生中は今までどおり従う — 止めると
        ←→ を押してから 0.8 秒、赤い線が止まって見えるため。"""
        if self._echo_active() and not self._playing:
            return
        if self._repaint_due():
            super().set_position_smooth(seconds)
            self._follow_playhead(seconds, nearest=False)
            return
        self._quiet = True
        try:
            super().set_position_smooth(seconds)
            self._follow_playhead(seconds, nearest=False)
        finally:
            self._quiet = False
        self._arm_redraw()

    def _repaint_due(self):
        """いま塗ってよいか。再生中だけ間引く(止まっているときは即座に)。"""
        if self._redraw_fps <= 0 or not self._playing:
            return True
        return (time.monotonic() - self._last_paint_wall
                >= 1.0 / self._redraw_fps)

    def _arm_redraw(self):
        """間引いたぶんの追いの1枚を予約する。"""
        if self._redraw_timer.isActive():
            return
        wait = 1.0 / self._redraw_fps - (time.monotonic() - self._last_paint_wall)
        self._redraw_timer.start(max(1, int(wait * 1000.0)))

    def update(self, *args):
        """間引いている最中(_quiet)は塗らない。

        set_position_smooth の中から呼ばれる update() を全部まとめて止める
        ための入口。ここを通さないと、位置を進める途中で呼ばれる
        _follow_playhead → _cursor_changed などが個別に塗り直してしまう。"""
        if self._quiet:
            return
        super().update(*args)

    def _echo_active(self):
        return time.monotonic() < self._own_seek_until

    def _follow_view_start(self, seconds, span):
        """追従表示の左端。

        **景色が動くのは2つだけ** — ホイールと、再生中の追従。
        カーソルや赤い線を動かしても景色は動かさない。端や画面の外へ
        出ても動かさない(どこに居るかは端の赤い三角で分かる)。
        以前は停止中に「カーソルが端へ寄ったら真ん中へ戻す」をやって
        いたが、見ている所が勝手に変わるのが邪魔だった(利用者の指定
        2026-11-05「ホイール以外では見てる範囲を遷移しない」)。

        追従は再生を始めたところで入り(set_playing)、途中でホイールを
        回したら切れて、もう勝手には戻らない。

        **オブジェクトを掴んでいるあいだも動かさない**(利用者の指定
        2026-10-08)。掴んだまま右へ運ぶと、寄せ直しで景色が滑って
        掴んだものと指先がずれていった。
        """
        if span <= 0:
            return self.view_start
        if self._note_drag is not None or self._lyric_drag is not None:
            return self.view_start
        if not self._playing:
            return self.view_start      # 停止中は追従しない
        if not self._follow_now(seconds, span):
            return self.view_start      # ホイールで送った景色のまま
        self._stop_view_anim()
        target = super()._follow_view_start(seconds, span)
        if self._follow_transition is None:
            return target
        started, offset = self._follow_transition
        u = (time.monotonic() - started) / self.VIEW_ANIM_SEC
        if u >= 1.0:
            self._follow_transition = None
            return target
        # 再生時刻はそのまま進め、停止時の景色と追従位置の差だけを減らす。
        # シークしても target はすぐ新しい時刻に追従する。
        remaining = (1.0 - u) ** 2 * (1.0 + 2.0 * u)
        return max(0.0, min(target + offset * remaining,
                            max(0.0, self.duration - span)))

    def scroll_view_free(self, step, span=None):
        self._follow_transition = None
        super().scroll_view_free(step, span)

    #: 1小節目より前(頭の助走の中)に置いた赤い線の時刻。小節もグリッドも
    #: 無いのでカーソルの住所では表せない。置いていなければ None。
    _free_time = None

    #: 停止中、赤い線がこの割合より端に寄ったら景色を連れていく。
    #: 窓の中に居るあいだは動かさない — 1グリッドずつ歩くたびに景色が
    #: 動くと、見ている所が落ち着かない(利用者の指定 2026-11-05
    #: 「ホイール以外では見てる範囲を遷移しない」の趣旨は残す)。
    EDGE_FRAC = 0.04

    def _hand_busy(self):
        """いま指で何かを引いている最中か。

        引いている最中に景色が動くと、押した所と離した所の時刻が食い違って
        **囲んだ範囲が壊れる**(右ドラッグの帯が張られない)。シークの追従は
        そのあいだ休ませる。
        """
        return bool(self._band is not None or self._note_drag is not None or self._lyric_drag is not None
                    or self._pending_measure is not None
                    or self._dragging)

    def _scroll_line_into_view(self, t, span):
        """停止中、赤い線が窓から出そうなら景色を連れていく。

        利用者の要望 2026-11-07:
          「作譜モードで Home や End を押したときに、現在位置を現す赤い線
            のみの移動で、画面自体はそこに飛ばない」
          「矢印キーで譜面移動したときに、赤い線だけでなく画面も追尾して
            ほしい」
        置き場所は再生中の追従と同じ(窓の左 FOLLOW_FRAC)。窓の中を歩いて
        いるあいだは動かさないので、端に着いたときだけ送られる。
        """
        if span <= 0:
            return
        rel = (float(t) - self.view_target()) / span
        if self.EDGE_FRAC <= rel <= 1.0 - self.EDGE_FRAC:
            return                      # まだ窓の中。動かさない
        hi = (max(0.0, self.duration - span) if self.duration > 0
              else max(0.0, float(t)))
        dest = max(0.0, min(float(t) - span * self.FOLLOW_FRAC, hi))
        if abs(dest - self.view_target()) < 1e-6:
            return
        self.scroll_view_to(dest)

    def _pin_playhead_to_cursor(self):
        """赤い線と表示をカーソルの位置へ直接合わせる(音源の返事を待たない)。

        助走の中に置いた線は、カーソル(1小節目の頭)ではなくその時刻へ。"""
        t = self.cursor_time() if self._free_time is None else self._free_time
        self.position_sec = t
        span = self._visible_span()
        if self._follow_window and span > 0:
            if self._playing:
                self.view_start = self._follow_view_start(t, span)
            else:
                # 停止中は、窓から出そうなときだけ連れていく。
                self._scroll_line_into_view(t, span)
        self.update()

    def _follow_playhead(self, t, nearest):
        """再生位置が動いたら、編集カーソルをそのグリッドへ合わせる。

        カーソルと再生位置は同じものとして扱う(PeepoDrumKit と同じ)。
          再生中(nearest=False) … グリッドへの切り捨て。拍ごとに進んでいく。
          停止中(nearest=True)  … 最寄りのグリッド。よそからのシーク(シーク
                                  バー・小節移動)は位置が出力レイテンシぶん
                                  手前で届くので、切り捨てると1つ手前へ落ちる。"""
        if getattr(self, "_grid", 0) <= 0 or getattr(self, "_bar_times_raw", None) is None:
            return
        self._free_time = None
        if abs(t - self.cursor_time()) < self.SYNC_TOLERANCE_SEC:
            return
        addr = self._address_from_time(max(0.0, t), nearest=nearest)
        if addr is None or addr == (self._cur_measure, self._cur_slot):
            return
        self._cur_measure, self._cur_slot = addr
        self._clamp_cursor()
        self.update()

    def set_playing(self, playing):
        """再生中かどうか。再生中は黄色いカーソルを出さない(赤い線だけ)。

        **再生を始めたところで追従に戻す。** ホイールで景色を送ると追従は
        切れるが、もう一度再生を始めれば入り直す — それが唯一の戻り道
        (利用者の指定 2026-11-05)。
        """
        playing = bool(playing)
        if playing != self._playing:
            self._playing = playing
            self._follow_transition = None
            if playing:
                self._view_free = False
                if self._follow_window:
                    self._stop_view_anim()
                    target = super()._follow_view_start(self.position_sec,
                                                        self._visible_span())
                    self._follow_transition = (time.monotonic(),
                                               self.view_start - target)
            self.update()

    def set_cursor_address(self, m, slot):
        """カーソルを (小節, スロット) へ置き、再生位置もそこへ移す。"""
        self._cur_measure, self._cur_slot = int(m), int(slot)
        self._clamp_cursor()
        self._cursor_changed()

    def _cursor_changed(self):
        """カーソルを自分で動かしたあとの共通処理。赤い線と表示を先に合わせ、
        返ってくる位置(こだま)はしばらく無視してから、シークを頼む。"""
        self._free_time = None
        self._own_seek_until = time.monotonic() + self.SEEK_ECHO_SEC
        self._pin_playhead_to_cursor()
        self.cursorMoved.emit(self.cursor_time())

    def set_default_measure_len(self, sec):
        """小節が無い/末尾より先へ出たときに使う1小節の長さ(秒)。"""
        try:
            sec = float(sec)
        except (TypeError, ValueError):
            return
        if sec > 0:
            self._default_measure_len = sec
            self.update()

    def set_spans(self, rolls, balloons, kusudamas):
        super().set_spans(rolls, balloons, kusudamas)
        self._spans_full = list(self._spans_raw or [])
        self._collapse_open_spans()

    #: 書きかけの頭の文字 → 帯の種類(set_spans と同じ呼び名)。
    _OPEN_KIND = {"5": "roll", "6": "roll_big", "7": "balloon", "9": "kusudama"}

    def set_open_spans(self, starts):
        """終端 8 が無い連打・風船を受け取る。要素は (譜面時刻, 文字)。

        時刻だけ(float)でも受ける。文字が分からないときは連打の色で描く。"""
        out = []
        for it in (starts or []):
            if isinstance(it, (tuple, list)):
                out.append((float(it[0]), str(it[1]) if len(it) > 1 else "5"))
            else:
                out.append((float(it), "5"))
        self._open_starts = out
        self._collapse_open_spans()

    def _collapse_open_spans(self):
        """書きかけ(終端が無い)の帯を長さ0にして、先頭の音符だけを描かせる。

        解析は終端の無い連打を「コースの最後まで続く」として返すので、そのまま
        描くと 5 を1つ置いた瞬間に曲の終わりまで黄色い帯が伸びてしまう。"""
        full = self._spans_full
        if full is None:
            return
        opens = self._open_starts
        raw = []
        for st, en, kind in full:
            if any(abs(st - o) < 1e-6 for o, _c in opens):
                en = st
            raw.append((st, en, kind))
        # 後ろの連打に上書きされて、解析の帯に入ってこなかった頭もある
        # (5 を書いたあとに別の 6...8 があると、5 は帯として返ってこない)。
        # それも頭だけ描く — 描かないと、置いた 5 が再解析で消えて見える。
        for o, c in opens:
            if not any(abs(st - o) < 1e-6 for st, _e, _k in raw):
                raw.append((o, o, self._OPEN_KIND.get(c, "roll")))
        self._spans_raw = raw or None
        self._apply_offset_local(self.offset)

    def set_legend_visible(self, on: bool):
        self._show_legend = bool(on)
        self.update()

    def legend_visible(self) -> bool:
        return self._show_legend

    def clear_pending(self):
        """正式な再解析が届いたので暫定表示を捨てる。"""
        if self._pending:
            self._pending = {}
            self.update()

    def set_op_cb(self, cb):
        self._op_cb = cb

    def grid(self) -> int:
        return self._grid

    def cursor_address(self):
        # 3つめは「この小節をいくつに割っているか」(画面の 1/G ではない)。
        return (self._cur_measure, self._cur_slot,
                self._measure_grid(self._cur_measure))

    # ------------------------------------------------------------------
    # カーソル
    # ------------------------------------------------------------------
    def _known_measures(self):
        """解析が返してきた実在の小節数。"""
        return len(self._bar_times)

    def _measure_count(self):
        """カーソルを置ける小節数。譜面が空でも1小節ぶんは打てるようにし、
        末尾より先へも EXTEND_MEASURES ぶん出られるようにする。"""
        return max(1, self._known_measures()) + self.EXTEND_MEASURES

    #: 外挿の1小節として認める短さの下限(ふつうの1小節に対する割合)。
    #: これより短い小節が末尾に来ていたら、外挿には使わない。
    EXTEND_MIN_FRAC = 0.25

    def _measure_len(self):
        """譜面の末尾より先へ伸ばすときの、1小節の長さ。

        ふつうは「最後の小節の長さ」をそのまま使う(変拍子で終わる譜面でも、
        その続きとして自然な位置に線が出る)。

        **ただし、極端に短い小節で終わっているときは使わない。** `#MEASURE
        1/96` のような小節を末尾の近くへ入れると、最後の間隔が 0.02 秒に
        なり、その長さで 64 小節ぶん伸ばすので、譜面の終わりのすぐ右に
        黄色い破線の壁ができていた(利用者の報告 2026-10-01)。音符も波形も
        その下に隠れる。ふつうの1小節(BPM から出した 4/4)の 1/4 より短い
        ときは、そのふつうの長さのほうを使う。"""
        normal = max(1e-3, self._default_measure_len)
        if len(self._bar_times) >= 2:
            span = self._bar_times[-1] - self._bar_times[-2]
            if span >= normal * self.EXTEND_MIN_FRAC:
                return span
        return normal

    def _bar_time(self, m):
        """m 小節目の開始時刻。既知の範囲より先は等間隔で外挿する。"""
        if m < 0:
            return None
        n = self._known_measures()
        if m < n:
            return self._bar_times[m]
        # 小節がまだ1つも無いときの仮の1小節目は「譜面時刻 0」の音源時刻に
        # 置く。0.0 にしてしまうと、再解析で本物の bar_times[0](= -OFFSET)が
        # 届いた瞬間にカーソルと表示が OFFSET ぶん飛ぶ。
        base = self._bar_times[-1] if n else max(0.0, -self.offset)
        return base + self._measure_len() * (m - (n - 1 if n else 0))

    def _measure_range(self, t0, t1):
        """[t0, t1] に掛かっている小節の番号の範囲 (最初, 最後+1)。

        描画のたびに全小節を見に行かないための下ごしらえ。既知の小節は開始
        時刻が並んでいるので bisect で切り出し、末尾より先の外挿ぶんは等間隔
        なので割り算で出す(式は _bar_time と同じものを解く)。長い曲では
        ここが効く — 300小節の譜面なら、以前は定規とグリッドで毎コマ
        600回の _bar_time を呼んでいた。"""
        total = self._measure_count()
        n = self._known_measures()
        lo, hi = 0, 0
        if n:
            lo = max(0, bisect.bisect_right(self._bar_times, t0) - 1)
            hi = bisect.bisect_right(self._bar_times, t1) + 1
        if hi >= n:
            base = self._bar_times[-1] if n else max(0.0, -self.offset)
            m0 = (n - 1) if n else 0
            hi = m0 + int(max(0.0, t1 - base) / self._measure_len()) + 2
        lo = min(lo, total)
        return lo, min(total, max(lo + 1, hi))

    def _address_time(self, m, slot, grid):
        """(小節, スロット) の時刻。既知の小節の外でも返す。"""
        if getattr(self, 'note_timing', None) is not None and grid > 0:
            return float(self.note_timing.time_at(Fraction(m) + Fraction(slot, grid))) - self.offset
        t0 = self._bar_time(m)
        if t0 is None or grid <= 0:
            return None
        t1 = self._bar_time(m + 1)
        span = (t1 - t0) if (t1 is not None and t1 > t0) else self._measure_len()
        return t0 + span * (slot / grid)

    def _clamp_cursor(self):
        n = self._measure_count()
        self._cur_measure = max(0, min(self._cur_measure, n - 1))
        self._cur_slot = max(
            0, min(self._cur_slot, self._measure_grid(self._cur_measure) - 1))

    def cursor_time(self):
        t = self._address_time(self._cur_measure, self._cur_slot,
                               self._measure_grid(self._cur_measure))
        return 0.0 if t is None else t

    def move_cursor(self, delta):
        """カーソルを delta グリッド動かす。小節をまたぐ。

        1小節に入るグリッドの数は小節ごとに違う(拍子で変わる)ので、
        通し番号では数えられない。1つずつ繰り上げ/繰り下げる。"""
        count = self._measure_count()
        if count <= 0:
            return
        if self._free_time is not None and delta and self._move_free(delta):
            return
        m, slot = self._cur_measure, self._cur_slot
        step = 1 if delta > 0 else -1
        for _ in range(abs(int(delta))):
            slot += step
            if slot >= self._measure_grid(m):
                if m + 1 >= count:
                    slot = self._measure_grid(m) - 1
                    break
                m += 1
                slot = 0
            elif slot < 0:
                if m <= 0:
                    slot = 0
                    break
                m -= 1
                slot = self._measure_grid(m) - 1
        self._cur_measure, self._cur_slot = m, slot
        self._clamp_cursor()
        self._cursor_changed()

    def _address_from_time(self, t, nearest=False):
        """時刻から (小節, スロット)。譜面の末尾より先の外挿ぶんも当てる。"""
        if getattr(self, 'note_timing', None) is not None:
            try:
                pos = self.note_timing.position_at(Fraction(str(t + self.offset)))
                m = int(pos)
                g = self._measure_grid(m)
                s = float((pos - m) * g)
                slot = int(round(s)) if nearest else int(math.floor(s + 1e-7))
                return (m + 1, 0) if slot >= g else (m, max(0, slot))
            except ValueError:
                pass
        if self._grid <= 0:
            return None
        n = self._known_measures()
        i = 0
        if n:
            i = max(0, bisect.bisect_right(self._bar_times, t) - 1)
        # 既知の小節より先は外挿。等間隔なので順に見ていけば足りる。
        total = self._measure_count()
        while i + 1 < total:
            nxt = self._bar_time(i + 1)
            if nxt is None or nxt > t:
                break
            i += 1
        t0 = self._bar_time(i)
        if t0 is None:
            return None
        t1 = self._bar_time(i + 1)
        span = (t1 - t0) if (t1 is not None and t1 > t0) else self._measure_len()
        # PeepoDrumKit と同じく **切り捨て**(FloorBeatToCurrentGrid)。
        # 浮動小数でちょうどグリッド上が 2.9999 になって1つ手前へ落ちないよう、
        # わずかに足してから切る。nearest のときは最寄りのグリッド。
        grid = self._measure_grid(i)
        pos = (t - t0) / span * grid
        slot = int(round(pos)) if nearest else int(math.floor(pos + 1e-6))
        if slot < 0:
            slot = 0
        elif slot >= grid:
            if i + 1 < total:
                return (i + 1, 0)
            slot = grid - 1
        return (i, slot)

    def mouseDoubleClickEvent(self, event):
        # サイドボタンの素早い2回押しは、2回目が DblClick で届く。既定の
        # 処理は mousePressEvent を呼び直すので、分割がもう1段余計に動いて
        # いた(2回押して3段。利用者の報告 2026-11-13)。1段ぶんは直前の
        # Press で動かしてあるので、ここでは受け取って捨てるだけ。
        # (窓のイベントフィルタでも止めているが、ペイン単体で使われた
        # ときのための保険)
        if event.button() in (Qt.BackButton, Qt.ForwardButton):
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        """左クリック: その位置へ編集カーソルを置く(再生位置のシークは親)。
        右ドラッグ: 範囲選択(PeepoDrumKit の右ドラッグの箱選択に当たる。
        こちらの帯は1行しか無いので、時間の範囲だけを選ぶ)。

        左の行名の列(x < LANE_X0)は時間軸の外なので、何も起きない。"""
        # マウスのサイドボタンで分割を変える(利用者の希望 2026-11-02)。
        # ↑ ↓ と同じ向き — 進む(前)で細かく、戻る(後ろ)で粗く。帯の
        # どこで押しても効くので、いちばん手前で見る。
        if event.button() in (Qt.BackButton, Qt.ForwardButton):
            if not self.offset_mode:
                self.setFocus(Qt.MouseFocusReason)
                self.change_grid(1 if event.button() == Qt.ForwardButton
                                 else -1)
            event.accept()
            return
        if event.position().x() < self.LANE_X0 and not self.offset_mode:
            self.setFocus(Qt.MouseFocusReason)
            # 左上の「1/16」だけは押せる。分割を選ぶつまみが出る
            # (利用者の指定 2026-10-01)。
            if (event.button() == Qt.LeftButton
                    and self.grid_label_rect().contains(event.position().toPoint())):
                self.open_grid_picker()
            return
        if event.button() == Qt.RightButton and not self.offset_mode:
            # 囲って選ぶのは右ドラッグだけ(利用者の指定 2026-09-26)。四角は
            # 縦にも効く — スクロールの行だけを払えば、その命令だけ選べる。
            self.setFocus(Qt.MouseFocusReason)
            x, y = event.position().x(), event.position().y()
            ctrl = bool(event.modifiers() & Qt.ControlModifier)
            # 始点は px ではなく**時刻**で持つ。引いている最中にホイールで
            # 景色を送ると、px のままでは始点が譜面と一緒に流れてしまい、
            # 1画面より広い範囲が囲えなかった(利用者の報告 2026-11-05)。
            self._band = {"t0": self._x_to_sec(x), "y0": y,
                          "x1": x, "y1": y,
                          "add": set(self._sel) if ctrl else set(),
                          "ctrl": ctrl, "cleared": False}
            # 押した時点では選択を外さない。引かずに離せばただの右クリック
            # (作譜メニュー)なので、選んだ音符をそのままメニューへ渡したい
            # (利用者の報告 2026-10-08: 複数選んで右クリックすると外れる)。
            # 外すのは四角が実際に動き出したとき(mouseMoveEvent)。
            self.update()
            return
        if event.button() == Qt.LeftButton and not self.offset_mode:
            self.setFocus(Qt.MouseFocusReason)
            x, y = event.position().x(), event.position().y()
            ctrl = bool(event.modifiers() & Qt.ControlModifier)
            # 上の目盛りの小節番号 = その小節をまるごと選ぶ(利用者の希望
            # 2026-11-05)。Delete で消せる。赤い線は動かさない — 番号より
            # 右の帯は今までどおり擦って運べる。
            mm = self.ruler_measure_at(x, y)
            if mm is not None:
                # **押しただけなら選ぶ、引いたら今までどおり擦る。**
                # 番号の所でも帯を擦れないと、小節が短い譜面では目盛りの
                # かなりの幅で赤い線を運べなくなる。
                self._pending_measure = (mm, x, ctrl)
                self._dragging = True
                return
            handle = None if ctrl else self._lyric_handle_at(x, y)
            if handle:
                self._stop_view_anim()
                self._lyric_drag = {"span": handle, "end": handle[1], "x0": x}
                self.setCursor(Qt.SizeHorCursor)
                self.update()
                event.accept()
                return
            obj = self._object_at(x, y)
            if obj is None and self.near_playhead(x, y):
                # 赤い線そのものをつかんで運ぶ(利用者の指定 2026-11-05
                # 「シークバー(赤い線)まだつかめない」)。音符や命令の上は
                # そちらが優先 — 線は上に重なって描かれているだけなので、
                # つかめるのは**何も無い所**に限る。
                self._dragging = True
                self.setCursor(Qt.SizeHorCursor)
                event.accept()
                return
            if obj is not None:
                # オブジェクトを押した = 選ぶ(Ctrl なら足し引き)。そのまま
                # 横へ引っぱると、選んだものをまとめて動かせる。
                if ctrl:
                    sel = set(self._sel)
                    sel.symmetric_difference_update({obj})
                    self.set_selection(sel)
                elif obj not in self._sel:
                    self.set_selection({obj})
                if obj in self._sel:
                    # 既に選ばれているものを押したときは、選択をそのままにして
                    # つかむ(まとめて動かせる)。動かさずに離したら、その1つ
                    # だけにする — エクスプローラーと同じ。
                    # view0 / seek0 は「掴んでいるあいだ景色を止める」ための
                    # 控え(_follow_view_start / _drag_dx)。滑っている途中で
                    # 掴んだときは、その場で止める。
                    self._stop_view_anim()
                    self._note_drag = {"x0": x, "dx": 0.0, "anchor": obj[-1],
                                       "obj": obj, "ctrl": ctrl,
                                       "view0": self.view_start}
                    self.setCursor(Qt.SizeHorCursor)
                # 押した所へカーソルも動かす(選ぶだけだと、そのあと打ちたい
                # 位置と食い違う)。
                self._move_cursor_to_x(x)
                return
            band = self._span_keys_at(x, y) or self._roll_keys_at(x, y)
            if band:
                # 帯の**本体**を押した = その帯の両端をまとめて選ぶ(本文は
                # 変えない)。端の点だけしか選べないと、ゴーゴーを複数選んで
                # まとめて伸縮するのに細かすぎて使えなかった(利用者の要望
                # 2026-10-05)。つかんで動かすのは今までどおり端だけ。
                if ctrl:
                    # 足し引きは**帯ごと**(端ひとつずつの symmetric_difference
                    # だと、片方だけ選ばれている帯で両端がちぐはぐになる)。
                    sel = set(self._sel)
                    self.set_selection(sel - band if band <= sel else sel | band)
                else:
                    self.set_selection(band)
                self._move_cursor_to_x(x)
                return
            # 何も無い所 = 選択を外してカーソルを置く。囲って選ぶのは
            # 右ドラッグだけ(利用者の指定 2026-09-26)。
            #
            # **擦って赤い線を運べるのは上の目盛りの帯だけ。** 帯の本体でも
            # 付いてくる作りだったので、1/16 の目盛が数 px しかない所では
            # 手の揺れだけで隣へ飛び、再生中は景色が流れているぶん大きく
            # 飛んでいた(利用者の報告 2026-11-05)。PeepoDrumKit も
            # 目盛りの三角でだけ運ぶ。
            if not ctrl:
                self.clear_selection()
            self._dragging = y < self.RULER_H
            self._move_cursor_to_x(x)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._lyric_drag is not None and event.buttons() & Qt.LeftButton:
            self._update_lyric_drag(event.position().x())
            return
        if self._note_drag is not None and (event.buttons() & Qt.LeftButton):
            # 掴んでいるあいだは 1px ごとに付いてくる(置くときだけグリッド)。
            self._note_drag["dx"] = event.position().x() - self._note_drag["x0"]
            self.update()
            return
        if self._band is not None and (event.buttons() & Qt.RightButton):
            bd = self._band
            bd["x1"] = event.position().x()
            bd["y1"] = event.position().y()
            # 四角が動き出した所で、前の選択を外す(Ctrl なら足すので残す)。
            # 離すときの「引いたかどうか」と同じ 4px で判断する。
            if (not bd["ctrl"] and not bd["cleared"]
                    and (abs(bd["x1"] - self._band_x0(bd)) >= 4
                         or abs(bd["y1"] - bd["y0"]) >= 4)):
                bd["cleared"] = True
                self.clear_selection()
            self.update()
            return
        # オブジェクトの上ではカーソルの形を変える(つかめることが分かる)。
        if not (event.buttons() & (Qt.LeftButton | Qt.RightButton)):
            hx, hy = event.position().x(), event.position().y()
            hit = self._object_at(hx, hy)
            grab = hit is not None or self.near_playhead(hx, hy) or self._lyric_handle_at(hx, hy)
            self.setCursor(Qt.SizeHorCursor if grab else Qt.ArrowCursor)
        # 目盛りの帯を擦っているあいだ、編集カーソルも一緒に付いていく
        # (シークはカーソルがグリッドの位置で出す)。
        if self._dragging and not self.offset_mode:
            if event.buttons() & Qt.LeftButton:
                pm = self._pending_measure
                if pm is not None:
                    if (abs(event.position().x() - pm[1])
                            < self.spx(self.MEASURE_PICK_SLOP)):
                        return          # まだ押しただけ(選ぶかもしれない)
                    # 引き始めた = 擦るほう。小節の選択は外す。
                    self._pending_measure = None
                    self.clear_selection()
                self._move_cursor_to_x(event.position().x())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._lyric_drag is not None:
            drag = self._lyric_drag
            x = event.position().x()
            if abs(x - drag["x0"]) >= 3:
                self._update_lyric_drag(x)
                start, end, _, _ = drag["span"]
                if abs(drag["end"] - end) > 1e-8:
                    head, dest = self._pos_of_time(start), self._pos_of_time(drag["end"])
                    if head is not None and dest is not None:
                        self._run_op({"kind": "lyric_end", "head": (head.numerator, head.denominator),
                                      "dest": (dest.numerator, dest.denominator)})
            self._lyric_drag = None
            self.unsetCursor()
            self.update()
            return
        if event.button() == Qt.LeftButton and self._pending_measure is not None:
            mm, _x, ctrl = self._pending_measure
            self._pending_measure = None
            self._dragging = False
            self._pick_measure(mm, ctrl)
            return
        if event.button() == Qt.LeftButton and self._dragging:
            # 目盛りを擦っていた/赤い線をつかんでいた、のどちらも here で終い。
            self._dragging = False
            self.setCursor(Qt.ArrowCursor)
            return
        if event.button() == Qt.LeftButton and self._note_drag is not None:
            self._finish_note_drag()
            return
        if event.button() == Qt.RightButton and self._band is not None:
            bd, self._band = self._band, None
            if (abs(bd["x1"] - self._band_x0(bd)) >= 4
                    or abs(bd["y1"] - bd["y0"]) >= 4):
                self._select_band(bd)
            else:
                # 引かずに離した = ただの右クリック。作譜メニューを出す
                # (利用者の指定 2026-10-01)。囲って選ぶほうは今までどおり。
                self.update()
                self._exec_menu(
                    self._build_command_menu(self._band_x0(bd), bd["y0"]),
                    event.globalPosition().toPoint())
                return
            self.update()
            return
        super().mouseReleaseEvent(event)

    #: 赤い線をつかめる幅(px)。線の左右それぞれにこのぶん。
    PLAYHEAD_GRAB_PX = 6
    #: 音符の行などではその半分。線のすぐ横のマスを押したつもりが線を
    #: つかんでしまい、カーソルが動かなかった(利用者の報告 2026-11-13
    #: 「赤い線の判定が広すぎる」)。命令の札がまばらな BPM・拍子記号の行は
    #: 線をつかむ場所として使われているので今までの幅のまま。
    PLAYHEAD_GRAB_PX_NARROW = 3
    _PLAYHEAD_WIDE_ROWS = ("bpm", "measure")

    def near_playhead(self, x, y=None):
        """赤い線をつかめる所か(利用者の指定 2026-11-05)。

        目盛りの帯は小節を選ぶ所なので、つかめるのはその下だけ。
        幅は行で変わる(BPM・拍子記号の行は広く、ほかは半分)。押したときと
        カーソルの形(手の形)で同じ判定を使う。"""
        if y is not None and y < self.RULER_H:
            return False
        if self._visible_span() <= 0:
            return False
        px = self.PLAYHEAD_GRAB_PX
        if y is not None and self._row_at(y) not in self._PLAYHEAD_WIDE_ROWS:
            px = self.PLAYHEAD_GRAB_PX_NARROW
        return (abs(float(x) - self._sec_to_x(self.position_sec))
                <= self.spx(px))

    def _free_seek(self, t):
        """グリッドに合わせず、その時刻へ赤い線を置いてシークを頼む。

        1小節目より前(音源の頭に足した助走の中)には小節もグリッドも無いので、
        押した所へそのまま置く(利用者の希望 2026-11-05「普通にどこでも
        再生できるようにして欲しい」)。編集カーソルは1小節目の頭に置いておく —
        そこへ音符を置けるわけではないので、住所は持たせない。"""
        t = max(0.0, float(t))
        self._cur_measure, self._cur_slot = 0, 0
        self._free_time = t
        self._own_seek_until = time.monotonic() + self.SEEK_ECHO_SEC
        self.position_sec = t
        self.update()
        self.cursorMoved.emit(t)

    def ruler_measure_at(self, x, y=None):
        """上の目盛りの、どの小節の所を押したか。小節番号(0 始まり)か None。

        当たりは**その小節の区間ぜんぶ**(小節線から次の小節線まで)。前は
        番号の文字の幅(34px)だけだったので、帯の大半が「擦る」側のままで、
        たまたま番号の上を押したときしか選べなかった(利用者の報告
        2026-11-05「いろんなとこクリックすると選択できることもある」)。

        押しただけなら選ぶ・引いたら擦る、は mousePressEvent 側で分ける。
        1小節目より前(頭の助走)と、譜面の終わりより先は None。"""
        if y is not None and y >= self.RULER_H:
            return None
        if x < self.LANE_X0 or self._visible_span() <= 0:
            return None
        t = self._x_to_sec(x)
        t0 = self._bar_time(0)
        if t0 is None or t < t0:
            return None
        addr = self._address_from_time(t)
        if addr is None:
            return None
        m = int(addr[0])
        n = self._measure_count()
        if m < 0 or (n > 0 and m >= n):
            return None
        return m

    def selected_measures(self):
        """上の目盛りで選んでいる小節(小節番号の昇順)。"""
        return sorted(self._sel_measures)

    def set_selected_measures(self, measures):
        ms = {int(m) for m in (measures or []) if int(m) >= 0}
        if ms == self._sel_measures:
            return False
        self._sel_measures = ms
        self.update()
        return True

    def _pick_measure(self, m, ctrl):
        """小節番号を押した。Ctrl なら足し引き。"""
        ms = set(self._sel_measures) if ctrl else set()
        if ctrl and m in ms:
            ms.discard(m)
        else:
            ms.add(m)
        self._sel_measures = ms
        if not ctrl:
            self.clear_selection(keep_measures=True)
        self.update()

    def _move_cursor_to_x(self, x):
        t = self._x_to_sec(x)
        t0 = self._bar_time(0)
        if t0 is not None and t < t0 - 1e-6:
            self._lead_seek(t)
            return
        # 一番近いグリッドへ合わせる(利用者の指定 2026-09-25)。切り捨てだと
        # 音符の少し右を押したときに1つ手前へ落ちて、置く場所がずれる。
        addr = self._address_from_time(max(0.0, t), nearest=True)
        if addr is None:
            return
        if (addr == (self._cur_measure, self._cur_slot)
                and self._free_time is None):
            return          # 同じグリッドの中で動いただけならシークし直さない
        self._cur_measure, self._cur_slot = addr
        self._clamp_cursor()
        self._cursor_changed()

    def _lead_step(self):
        """1小節目の頭 (0,0) の時刻と、今の分割での1グリッドの長さ。

        助走の中(1小節目より前)には小節もグリッドも無いが、1小節目の
        グリッドを左へそのまま伸ばしたものを目盛りとして使う。取れなければ
        (None, None)。"""
        g = self._measure_grid(0)
        t0 = self._address_time(0, 0, g)
        t1 = self._address_time(0, 1, g)
        if t0 is None or t1 is None or t1 - t0 <= 1e-9:
            return None, None
        return t0, t1 - t0

    def _lead_seek(self, t):
        """1小節目より前を押した/そこを歩いた。伸ばしたグリッドへ合わせて置く。

        以前は押した時刻へそのまま置いていたので、1小節目の頭のすぐ左を
        押すと線が頭からわずかに離れたままになり、1小節目の最初の音符の
        所へぴったり置けなかった。そこから押し直すと (0,1) へ飛ぶことも
        あった(利用者の報告 2026-11-13)。1小節目のグリッドを左へ伸ばして
        一番近い目盛りへ合わせ、それが頭 (0,0) かその先なら、ふつうの
        カーソルとして (0,0) へ置く。0 秒より前へは行かない。"""
        t0, step = self._lead_step()
        if step is None:
            self._free_seek(t)
            return
        k = int(round((float(t) - t0) / step))
        if k >= 0:
            self.set_cursor_address(0, 0)
            return
        self._free_seek(max(0.0, t0 + k * step))

    def _move_free(self, delta):
        """助走の中に置いた赤い線を ←→ で1グリッドずつ歩かせる。

        目盛りは _lead_step の伸ばしたグリッド。1小節目の頭に着いたら
        ふつうのカーソル (0,0) に戻り、残りの歩数はそこから先を歩く
        (頭の手前から → で (0,1) まで飛ばない。利用者の報告 2026-11-13)。"""
        t0, step = self._lead_step()
        if step is None:
            return False
        t = self._free_time
        sgn = 1 if delta > 0 else -1
        for i in range(abs(int(delta))):
            # 0 秒に寄せた半端な位置からでも、隣の目盛りへ歩く。
            k = (t - t0) / step
            if sgn > 0:
                k = math.floor(k + 1e-6) + 1
            else:
                k = math.ceil(k - 1e-6) - 1
            if k >= 0:
                rest = abs(int(delta)) - i - 1
                self.set_cursor_address(0, 0)
                if rest:
                    self.move_cursor(rest)
                return True
            t = max(0.0, t0 + k * step)
        self._free_seek(t)
        return True

    def set_cursor_from_time(self, t):
        """再生位置などからカーソルを合わせる。"""
        addr = note_edit.time_to_address(
            self._bar_times, t, self._measure_grid(
                max(0, bisect.bisect_right(self._bar_times, t) - 1)))
        if addr is None:
            return
        self._cur_measure, self._cur_slot = addr
        self._clamp_cursor()
        self.update()

    # ------------------------------------------------------------------
    # オブジェクトの選択(Windows のエクスプローラー式)
    # ------------------------------------------------------------------
    # 選ぶ単位は「音符1つ」「命令1つ」。左クリックで選び、Ctrl で足し引き、
    # 何も無い所を左ドラッグすると四角で囲って複数選べる(利用者の指定
    # 2026-09-25)。選んだものは _sel に鍵(key)で持つ:
    #   ("note", 位置)          … 音符・連打・風船の頭
    #   ("cmd", 命令名, 位置)   … #BPMCHANGE / #SCROLL / #MEASURE /
    #                             #GOGOSTART / #GOGOEND / #BARLINEOFF / #BARLINEON
    # 位置は「小節番号 + 小節の中の割合」の分数(1/192 に丸め)。時刻ではなく
    # 位置で持つのは、BPM が変わっても同じものを指し続けるため。
    POS_DEN = 192
    #: 命令の行の種類 → TJA の命令名。
    _CMD_NAMES = {"bpm": "BPMCHANGE", "hs": "SCROLL", "measure": "MEASURE",
                  "lyric": "LYRIC"}

    def _pos_of_time(self, t):
        """音源時刻 → 位置(分数)。"""
        if getattr(self, 'note_timing', None) is not None:
            try:
                return self.note_timing.position_at(Fraction(str(t + self.offset))).limit_denominator(note_edit.MAX_DIVISION)
            except ValueError:
                pass
        n = self._measure_count()
        if n <= 0:
            return None
        i = max(0, bisect.bisect_right(self._bar_times, t + 1e-9) - 1)
        if i >= n:
            i = n - 1
        a = self._bar_time(i)
        b = self._bar_time(i + 1)
        if a is None or b is None or b <= a:
            return Fraction(i)
        frac = (t - a) / (b - a)
        return Fraction(i) + Fraction(int(round(frac * self.POS_DEN)), self.POS_DEN)

    def _time_of_pos(self, pos):
        """位置(分数) → 音源時刻。"""
        if getattr(self, 'note_timing', None) is not None:
            return float(self.note_timing.time_at(pos)) - self.offset
        m = int(pos)
        frac = pos - m
        a = self._bar_time(m)
        b = self._bar_time(m + 1)
        if a is None:
            return None
        if b is None or b <= a:
            b = a + self._default_measure_len
        return a + float(frac) * (b - a)

    def _ensure_object_index(self):
        """譜面データが差し替わった時だけ、正確な時刻から住所を引き直す。"""
        sources = (self._note_audio, self._span_audio, self._cmd_audio,
                   self._region_audio, self._gogo_audio, self._barline_audio,
                   self._bar_times, getattr(self, 'note_timing', None))
        key = (tuple(id(value) for value in sources), self.offset,
               self.NOTE_R, self.NOTE_R_BIG, self.SPAN_TH, self.SPAN_TH_BIG,
               self._default_measure_len)
        if getattr(self, '_object_index_key', None) == key:
            return
        objects = self._build_objects()
        by_key = {}
        for obj in objects:
            by_key.setdefault(obj[0], []).append(obj)
        spans = {}
        for row, names in self._SPAN_ROWS.items():
            for st, end in self._span_rows_audio(row):
                ps, pe = self._pos_of_time(st), self._pos_of_time(end)
                if ps is not None and pe is not None:
                    k0, k1 = ('cmd', names[0], ps), ('cmd', names[1], pe)
                    spans.setdefault(k0, []).append((k1, row, st, end, names))
        for st, end, kind in self._span_audio or []:
            ps, pe = self._pos_of_time(st), self._pos_of_time(end)
            if ps is not None and pe is not None:
                spans.setdefault(('note', ps), []).append(
                    (('tail', pe), 'note', st, end, kind))
        self._indexed_objects = objects
        self._objects_by_key = by_key
        self._spans_by_start = spans
        # 古い配列も保持し、解放後の id 再利用で誤って一致するのを防ぐ。
        self._object_index_sources = sources
        self._object_index_key = key

    def _objects(self):
        self._ensure_object_index()
        return self._indexed_objects

    def _selected_objects(self, keys):
        self._ensure_object_index()
        for key in keys:
            yield from self._objects_by_key.get(key, ())

    def _selected_spans(self):
        self._ensure_object_index()
        for k0 in self._sel:
            for k1, row, st, end, detail in self._spans_by_start.get(k0, ()):
                if k1 in self._sel:
                    yield k0, k1, row, st, end, detail

    def _build_objects(self):
        """いま画面に出ているオブジェクト [(鍵, 行, 時刻, つかめる半幅px)]。"""
        out = []
        for t, c in (self._note_audio or []):
            p = self._pos_of_time(t)
            if p is not None:
                r = self.NOTE_R_BIG if c in ("3", "4") else self.NOTE_R
                out.append((("note", p), "note", t, r))
        for st, e, kind in (self._span_audio or []):
            half = (self.SPAN_TH_BIG // 2 if kind == "roll_big"
                    else self.SPAN_TH // 2)
            p = self._pos_of_time(st)
            if p is not None:
                out.append((("note", p), "note", st, half))
            # 終端(8)もつかめる。ゴーゴーの端と同じように、引っぱって連打・
            # 風船の長さを変えられる(利用者の要望 2026-10-08)。
            pe = self._pos_of_time(e)
            if pe is not None:
                out.append((("tail", pe), "note", e, half))
        for item in (self._cmd_audio or []):
            if len(item) < 4:
                continue
            name = self._CMD_NAMES.get(item[3])
            p = self._pos_of_time(item[0])
            if name and p is not None:
                out.append((("cmd", name, p), item[3], item[0], 16))
        if self._region_audio is not None:
            # **命令そのもの**から札を作る(重なった2本目も出る)。
            for t, name in self._region_audio:
                p = self._pos_of_time(t)
                if p is not None:
                    out.append((("cmd", name, p),
                                self._CMD_ROW.get(name, "barline"), t, 8))
        else:
            for spans, names, row in ((self._gogo_audio, ("GOGOSTART", "GOGOEND"), "gogo"),
                                      (self._barline_audio, ("BARLINEOFF", "BARLINEON"), "barline")):
                for st, e in (spans or []):
                    for t, name in ((st, names[0]), (e, names[1])):
                        p = self._pos_of_time(t)
                        if p is not None:
                            out.append((("cmd", name, p), row, t, 8))
        return out

    def _object_at(self, x, y):
        """押した場所にあるオブジェクトの鍵。無ければ None。"""
        row = self._row_at(y)
        if row is None:
            return None
        best, best_d = None, None
        for key, orow, t, half in self._objects():
            if orow != row:
                continue
            # **そのものが実際に描かれている高さ**で見る。行で見ていたころは、
            # 音符の行の下半分(波形の場所)を押しても一番近い音符が選ばれていた
            # (利用者の報告 2026-10-03)。囲んで選ぶほう(_objects_in_rect)は
            # 元からここを見ている。
            v = self._object_vrange(orow, half)
            if v is not None and not (v[0] <= y <= v[1]):
                continue
            x0, x1 = self._object_hrange(orow, t, half)
            if not (x0 <= x <= x1):
                continue
            d = abs((x0 + x1) / 2.0 - x)
            if best_d is None or d < best_d:
                best, best_d = key, d
        return best

    #: 帯の行 → (開始の命令名, 終了の命令名)。_objects と同じ組み合わせ。
    #: 範囲の命令の名前 → どの行に出すか / 札の字。
    _CMD_ROW = {"GOGOSTART": "gogo", "GOGOEND": "gogo",
                "BARLINEOFF": "barline", "BARLINEON": "barline"}
    #: 札の字。**入りきるときだけ**出す(_draw_cmd_marks)。短いゴーゴーが
    #: 並ぶと「開始 終了 開始 終了…」の壁になって、どこが区間なのか
    #: 読めなくなっていた(利用者の報告 2026-11-05)。
    _CMD_LABEL = {"GOGOSTART": "開始", "GOGOEND": "終了",
                  "BARLINEOFF": "OFF", "BARLINEON": "ON"}
    #: 字が入らないときに代わりに出す印。向きで開始/終了が分かる。
    _CMD_GLYPH = {"GOGOSTART": "▶", "GOGOEND": "◀",
                  "BARLINEOFF": "▶", "BARLINEON": "◀"}

    _SPAN_ROWS = {"gogo": ("GOGOSTART", "GOGOEND"),
                  "barline": ("BARLINEOFF", "BARLINEON")}

    def _span_rows_audio(self, row):
        return (self._gogo_audio if row == "gogo" else self._barline_audio) or []

    def _roll_keys_at(self, x, y):
        """連打・風船の**本体**を押したときの、頭と終端の鍵。

        ゴーゴーの帯と同じ扱い(利用者の要望 2026-10-08)。両端が選ばれるので、
        いくつも選んで終端をつかめば、まとめて長さを変えられる。頭と終端の
        点は _object_at が先に拾うので、ここは内側を押したときだけ出番が来る。
        """
        if self._row_at(y) != "note":
            return None
        for st, e, kind in (self._span_audio or []):
            half = (self.SPAN_TH_BIG // 2 if kind == "roll_big"
                    else self.SPAN_TH // 2)
            v = self._object_vrange("note", half)
            if v is not None and not (v[0] <= y <= v[1]):
                continue
            x0, x1 = sorted((self._sec_to_x(st), self._sec_to_x(e)))
            if not (x0 <= x <= x1):
                continue
            keys = set()
            for t, k in ((st, "note"), (e, "tail")):
                p = self._pos_of_time(t)
                if p is not None:
                    keys.add((k, p))
            return keys or None
        return None

    def _span_keys_at(self, x, y):
        """帯(ゴーゴー・小節線)の本体を押したときの、その帯の両端の鍵。

        帯の中ならその帯の開始と終了の**両方**を返す。端の点(±8px)だけが
        選べる作りでは、ゴーゴーを複数選んでまとめて長さを変えるのに細かすぎ
        たため(利用者の要望 2026-10-05)。端は _object_at が先に拾うので、
        ここは「端ではない内側」を押したときにだけ出番が来る。
        """
        row = self._row_at(y)
        names = self._SPAN_ROWS.get(row)
        if names is None:
            return None
        v = self._object_vrange(row, 8)
        if v is not None and not (v[0] <= y <= v[1]):
            return None
        for st, e in self._span_rows_audio(row):
            x0, x1 = sorted((self._sec_to_x(st), self._sec_to_x(e)))
            if not (x0 <= x <= x1):
                continue
            keys = set()
            for t, name in ((st, names[0]), (e, names[1])):
                p = self._pos_of_time(t)
                if p is not None:
                    keys.add(("cmd", name, p))
            return keys or None
        return None

    def _objects_in_rect(self, x0, y0, x1, y1):
        """四角で囲んだ中のオブジェクト(鍵の集まり)。"""
        lo_x, hi_x = sorted((x0, x1))
        lo_y, hi_y = sorted((y0, y1))
        hit = set()
        for key, orow, t, half in self._objects():
            v = self._object_vrange(orow, half)
            if v is None:
                continue
            # そのものが実際に描かれている高さで見る。行の高さで見ていたころは、
            # スクロールの行だけを囲んだつもりでも、すぐ上の(背の高い)音符の行に
            # かかって音符まで選ばれていた(利用者の報告 2026-09-26)。
            if v[1] < lo_y or v[0] > hi_y:
                continue
            # エクスプローラーと同じで、四角に**重なった**ものを選ぶ
            # (中心が入っていなくても、かかっていれば選ぶ)。
            hx0, hx1 = self._object_hrange(orow, t, half)
            if hx1 >= lo_x and hx0 <= hi_x:
                hit.add(key)
        return hit

    def _object_hrange(self, orow, t, half):
        """そのオブジェクトが占める横の範囲 (左, 右)。

        命令の札は右へ伸びて描かれる。つかめる場所も**描いてある四角と同じ**に
        する(利用者の指定 2026-09-26: 右側に判定が無くて動かしにくい)。

        **隣と重なるほど詰まっている所では、隣とのまんなかで切る**
        (利用者の指定 2026-11-05)。札の幅も音符の丸も決め打ちなので、
        16分で並ぶような所では隣の上まで伸びていた。そのせいで
          ・押した所と選ばれるものがずれる
          ・選んだ囲みが隣へはみ出して、ひと塊に見える
        の両方が起きていた。見えている幅・つかめる幅・囲みの幅は同じにする。"""
        x = self._sec_to_x(t)
        if orow == "gogo":
            # 帯の端。左右どちらからでもつまめるよう、中心に幅を取る。
            return (x - 8, x + 8)
        if orow == "note":
            x0, x1 = x - half, x + half
            lo, hi = self._note_neighbors(t)
            if lo is not None:
                x0 = max(x0, (self._sec_to_x(lo) + x) * 0.5)
            if hi is not None:
                x1 = min(x1, (self._sec_to_x(hi) + x) * 0.5)
            return (x0, max(x0 + 2, x1))
        x0 = x - 2
        return (x0, min(x0 + half * 3, self._cmd_next_x(orow, t, x0)))

    #: 音符の隣を探すときの同時刻とみなす幅(秒)。
    _NEIGHBOR_EPS = 1e-6

    def _note_neighbors(self, t):
        """その時刻の前後にある音符の時刻 (手前, 先)。無ければ None。"""
        times = getattr(self, "_note_time_list", None)
        if not times:
            return (None, None)
        eps = self._NEIGHBOR_EPS
        i = bisect.bisect_left(times, t - eps)
        j = bisect.bisect_right(times, t + eps)
        lo = times[i - 1] if i > 0 else None
        hi = times[j] if j < len(times) else None
        return (lo, hi)

    def _object_vrange(self, orow, half):
        """そのオブジェクトが描かれている縦の範囲 (上, 下)。"""
        rows = self._row_rects()
        r = rows.get(orow)
        if r is None:
            return None
        ry, rh = r
        if orow == "note":
            # 音符は行の上側(波形の上)に、丸の大きさぶんだけ描かれる。
            wave_h = int(rh * self.WAVE_FRAC)
            cy = ry + (rh - wave_h) // 2
            return (cy - half - 3, cy + half + 3)
        return (ry + 2, ry + rh - 2)

    def selection(self):
        return set(self._sel)

    def set_selection(self, keys):
        """選んだものを入れ替える。

        時間の帯(_range_start/_range_end)は別もの。範囲の Delete や
        W/Q は「空いているグリッドも含む帯」に効かせたいので、選んだものとは
        分けて持つ。囲って選んだときは、呼ぶ側が両方を立てる。"""
        self._sel = set(keys)
        self.update()
        self.selectionChanged.emit()

    def clear_selection(self, keep_measures=False):
        """選択を外す。小節(上の目盛りで選んだぶん)も一緒に外す。

        keep_measures=True は「小節を選んだ直後」に、音符や命令の選択だけを
        外すためのもの。"""
        if not keep_measures and self._sel_measures:
            self._sel_measures = set()
            self.update()
        self.set_selection(())

    def _band_x0(self, bd):
        """四角の始点のいまの画面位置。控えてある時刻から出し直す。"""
        return self._sec_to_x(bd["t0"])

    def _select_band(self, bd):
        """囲った四角の中のものを選び、時間の帯も張る。"""
        x0 = self._band_x0(bd)
        hit = self._objects_in_rect(x0, bd["y0"], bd["x1"], bd["y1"])
        self.set_selection(set(bd["add"]) | hit)
        lo_x, hi_x = sorted((x0, bd["x1"]))
        a0 = self._address_from_time(max(0.0, self._x_to_sec(lo_x)))
        a1 = self._address_from_time(max(0.0, self._x_to_sec(hi_x)))
        if a0 is not None and a1 is not None:
            self._range_start = self._range_key(a0[0], a0[1],
                                                self._measure_grid(a0[0]))
            self._range_end = self._range_key(a1[0], a1[1],
                                              self._measure_grid(a1[0]))

    #: 帯(ゴーゴー)の端の名前。つかんだときは「同じ側の端」だけを動かす。
    SPAN_EDGE_NAMES = ("GOGOSTART", "GOGOEND")

    def _drag_edge_name(self):
        """いま帯の端をつかんでいるなら、その端の名前。でなければ None。

        端をつかんだときは、選んでいる**同じ側の端だけ**を動かす。両端を
        一緒に動かすと帯がそのまま平行移動するだけで、長さが変わらない。
        ゴーゴーを複数選んで端を持ち、まとめて伸縮できるようにするための
        決まり(利用者の指定 2026-09-28)。
        """
        dr = self._note_drag
        if dr is None:
            return None
        obj = dr.get("obj")
        if obj is None or obj[0] != "cmd" or obj[1] not in self.SPAN_EDGE_NAMES:
            return None
        return obj[1]

    def _drag_tail(self):
        """いま連打・風船の終端(8)をつかんでいるか。"""
        dr = self._note_drag
        obj = dr.get("obj") if dr is not None else None
        return obj is not None and obj[0] == "tail"

    def _drag_keys(self):
        """いま動かしているものの鍵。ふだんは選んでいるもの全部。"""
        if self._drag_tail():
            # 終端をつかんだら終端だけ。選んだ連打・風船の長さがまとめて
            # 変わる(ゴーゴーの端と同じ。利用者の要望 2026-10-08)。
            return {k for k in self._sel if k[0] == "tail"}
        edge = self._drag_edge_name()
        if edge is None:
            # 音符そのものを動かすときは終端を外す。頭を動かせば終端も一緒に
            # 付いてくる(op_move_items)ので、二重に動かさないため。
            return {k for k in self._sel if k[0] != "tail"}
        return {k for k in self._sel if k[0] == "cmd" and k[1] == edge}

    def selected_items(self):
        """note_edit へ渡す形 [{kind, name, pos}]。"""
        out = []
        for k in self._sel:
            pos = k[-1]
            if k[0] in ("note", "tail"):
                # "tail" は連打・風船の終端(8)。長さを変える操作で使う。
                out.append({"kind": k[0], "name": None,
                            "pos": (pos.numerator, pos.denominator)})
            else:
                out.append({"kind": "cmd", "name": k[1],
                            "pos": (pos.numerator, pos.denominator)})
        return out

    # ------------------------------------------------------------------
    # 範囲選択(Tab / 右ドラッグ)
    # ------------------------------------------------------------------
    @staticmethod
    def _range_key(m, slot_or_frac, grid=None):
        if grid is None:
            return (int(m), Fraction(slot_or_frac))
        return (int(m), Fraction(int(slot_or_frac), int(grid)))

    def _key_to_slot(self, key):
        """範囲の端(小節, 割合)を今のグリッドの (小節, スロット) へ丸める。"""
        m, frac = key
        grid = self._measure_grid(m)
        slot = int(round(frac * grid))
        if slot >= grid:
            return (m + 1, 0)
        return (m, max(0, slot))

    def has_range(self):
        return (self._range_start is not None and self._range_end is not None
                and self._range_start != self._range_end)

    def clear_range(self):
        if self._range_start is not None or self._range_end is not None:
            self._range_start = None
            self._range_end = None
            self.update()

    def toggle_range_at_cursor(self):
        """Tab: 1回目でカーソルの位置を帯の始まり、2回目で終わりにする。

        帯は「空いているグリッドも含む時間の範囲」で、範囲の Delete
        や W/Q が効く相手。選んだオブジェクト(_sel)とは別もの。"""
        here = self._range_key(self._cur_measure, self._cur_slot,
                               self._measure_grid(self._cur_measure))
        if self._range_start is None or self._range_end is not None:
            self._range_start = here
            self._range_end = None
        else:
            self._range_end = here
            if self._range_end == self._range_start:
                self.clear_range()
        self.update()

    def paint_move(self, delta):
        """Shift+←→: 動いた先に、最後に打った音符を置いていく。

        音符キーを押したあと Shift を押しっぱなしで左右に動かすと
        通った所が同じ音符で埋まる(利用者の言い直し 2026-10-30)。
        まだ一度も打っていないときは、ただ動くだけ。

        置くのは「敷き詰め」の操作(fill)。音符キーの操作(key)は
        同じ音符がもう在ると**消す**ので、行ったり来たりすると
        せっかく置いた音符が消えてしまう。"""
        before = self._cursor_addr()
        self.move_cursor(delta)
        if self._paint_char is None:
            return
        here = self._cursor_addr()
        if here == before:
            return                      # 端に着いていて動けなかった
        self._run_op({"kind": "fill", "a": here, "b": here,
                      "char": self._paint_char})

    def _range_addresses(self):
        """範囲を今のグリッドの住所2つで。範囲が無ければ None。"""
        if not self.has_range():
            return None
        return self._key_to_slot(self._range_start), self._key_to_slot(self._range_end)

    def selection_pos_span(self):
        """選んだ音符の、いちばん早い位置といちばん遅い位置(分数)。

        音符を1つも選んでいなければ None。音符を囲まずに(Ctrl+)クリックで
        選んだときは時間の帯(_range_start/_range_end)が張られないので、
        道具(ハイスピ変換など)はこちらを相手にする
        (利用者の報告 2026-10-08)。"""
        ps = [k[-1] for k in self._sel if k[0] == "note"]
        if not ps:
            return None
        return (min(ps), max(ps))

    def selection_scroll_positions(self):
        """選んでいる #SCROLL の札の位置(分数)。早い順。1つも無ければ空。

        ハイスピ変換は「範囲の中の音符すべてに #SCROLL を付ける」道具なので、
        HS の札を選んで呼ぶと**帯に入った音符ぜんぶ**に付いていた(利用者の
        報告 2026-11-04「選択したSCROLL以外にもSCROLLがついてしまう」)。
        selection_pos_span が音符しか見ていないので、札を選んでも帯のほうに
        落ちていたのが元。札を選んでいるときは、その札の所だけを相手にする。
        """
        return sorted(k[-1] for k in self._sel
                      if k[0] == "cmd" and k[1] == "SCROLL")

    def has_tool_target(self):
        """道具(ハイスピ変換など)を効かせる相手があるか。

        時間の帯か、選んだ音符か、選んだ #SCROLL の札のどれかがあればよい
        (札は利用者の報告 2026-11-04 で足した)。"""
        return (self.has_range() or self.selection_pos_span() is not None
                or bool(self.selection_scroll_positions()))

    # ------------------------------------------------------------------
    # PeepoDrumKit 式の操作
    # ------------------------------------------------------------------
    def _run_op(self, op):
        """操作を実行し、返ってきた暫定表示を反映する。"""
        if self._op_cb is None:
            return None
        # note_edit へは「その小節をいくつに割るか」を渡す(画面の 1/G では
        # ない)。拍子が 4/4 でない小節では数が変わる。
        a = op.get("a")
        m0 = int(a[0]) if a is not None else self._cur_measure
        op.setdefault("grid", self._measure_grid(m0))
        # 範囲が拍子の変わる所をまたぐと、小節ごとに入る数が違う。ひとつの
        # 数で通すと連打の終端が別の小節へ飛び、風船の打数もずれていた
        # (利用者の報告 2026-10-03)。関わる小節の数をまとめて渡す。
        op.setdefault("grids", self._grids_for(op))
        res = self._op_cb(op)
        if res:
            for m, slot, grid, char in res.get("visual") or []:
                self._pending[(m, slot, grid)] = char
                if char == "0":
                    self._hide_note_at(m, slot, grid)
                else:
                    # 置き換えのときは下の音符が見えたままにならないよう消す。
                    self._hide_note_at(m, slot, grid)
                    self._pending[(m, slot, grid)] = char
        self.update()
        return res

    def _grids_for(self, op):
        """操作が関わる小節の「いくつに割るか」を {小節: 数} で。

        住所(a/b)が無い操作(選んだものを動かす/消す)もあるので、そのときは
        譜面ぜんたいぶん渡す。小節数は数百なので辞書1つぶんで足りる。"""
        ms = []
        for key in ("a", "b"):
            v = op.get(key)
            if v is not None:
                ms.append(int(v[0]))
        # 住所を持たない操作(選んだものを動かす/消す)は、選んだものの位置から
        # 小節を割り出す。譜面ぜんたいぶん作っていたころは、小節が1万4千ある
        # 譜面で1操作あたり 140ms かかっていた(実測 2026-10-03)。
        for it in (op.get("items") or []):
            pos = it.get("pos")
            if pos and pos[1]:
                ms.append(int(Fraction(int(pos[0]), int(pos[1]))))
        count = self._measure_count()
        if not ms:
            # それでも分からないときだけ、カーソルのまわりを渡す。
            ms = [self._cur_measure]
        # 動かす先は数グリッド先なので、少し広めに取っておく。
        margin = max(2, abs(int(op.get('steps', 0))))
        lo, hi = max(0, min(ms) - margin), min(count, max(ms) + margin + 1)
        return {m: self._measure_grid(m) for m in range(lo, hi)}

    # ------------------------------------------------------------------
    # コピー / 切り取り / 貼り付け (Ctrl+C / Ctrl+X / Ctrl+V)
    # ------------------------------------------------------------------
    #: コピーしたもの。窓ごとに1つ(OS のクリップボードは使わない — 中身が
    #: 音符と命令の組なので、ほかのアプリへ渡す形が無い)。
    _clip = None

    def _clip_source(self):
        """コピーする相手。選んだものがあればそれ、無ければ範囲。"""
        if self._sel:
            return {"items": self.selected_items()}
        rng = self._range_addresses()
        if rng is not None:
            return {"a": rng[0], "b": rng[1]}
        return None

    def copy_selection(self, cut=False):
        """選んだもの(または範囲)の中身をコピーする。cut なら消す。"""
        src = self._clip_source()
        if src is None:
            return False
        op = {"kind": "peek_copy"}
        op.update(src)
        res = self._run_op(op)
        clip = (res or {}).get("clip")
        if not clip:
            return False
        ChartEditWaveform._clip = clip
        if cut:
            if self._sel:
                self._run_op({"kind": "delete_items",
                              "items": self.selected_items()})
                self.clear_selection()
            else:
                a, b = src["a"], src["b"]
                self._run_op({"kind": "delete", "a": a, "b": b})
        self.update()
        return True

    def paste_at_cursor(self):
        """コピーしたものをカーソルの位置から貼る。

        位置は**いちばん早いものからの隔たり**で覚えてあるので、コピーした形の
        まま、カーソルを先頭にして並ぶ。"""
        clip = ChartEditWaveform._clip
        if not clip:
            return False
        m, slot, grid = self.cursor_address()
        dest = Fraction(m) + Fraction(int(slot), int(grid))
        res = self._run_op({"kind": "paste_items", "clip": clip, "dest": dest})
        self.update()
        return bool(res)

    def can_paste(self):
        return bool(ChartEditWaveform._clip)

    def _cursor_addr(self):
        return (self._cur_measure, self._cur_slot)

    def _peepo_note_key(self, char, mods):
        if mods & Qt.AltModifier:
            char = _BIG[char]
        # 打った音符を覚える。このあと Shift+←→ で引きずれる
        # (利用者の言い直し 2026-10-30)。
        self._paint_char = char
        self._run_op({"kind": "key", "a": self._cursor_addr(), "char": char})

    def _begin_long(self, key, char):
        if self._long is not None:
            return
        self._long = {"key": key, "char": char, "head": self._cursor_addr()}
        self.update()

    def _finish_long(self, mods):
        lg, self._long = self._long, None
        if lg is None:
            return
        tail = self._cursor_addr()
        if tail != lg["head"]:
            char = _BIG[lg["char"]] if (mods & Qt.AltModifier) else lg["char"]
            self._run_op({"kind": "long", "a": lg["head"], "b": tail, "char": char})
        self.update()

    def apply_transform(self, kind="flip"):
        """外(右クリックのメニュー・ツールバー)から W/Q と同じ変換を呼ぶ口。

        本文側で「小節まるごと」を選び直す道(run_chart_tool → reverse_don_ka)
        だと、囲んだ所だけでなくその小節が全部反転していた(利用者の報告
        2026-11-07)。"""
        self._delete_or_transform(kind)

    def _delete_or_transform(self, kind):
        # **選んだ音符があれば、それだけ**に効かせる(帯より選択が先)。
        if self._sel:
            if kind == "delete":
                # 選んだものを消す(音符も命令も。利用者の指定 2026-09-25)。
                self._run_op({"kind": "delete_items",
                              "items": self.selected_items()})
                self.clear_selection()
                return
            if kind in ("flip", "size"):
                items = self.selected_items()
                if any(i.get("kind") == "note" for i in items):
                    # 入れ替えても音符は残るので、選択はそのままにする
                    # (続けて W / Q を押せる)。
                    self._run_op({"kind": "transform_items",
                                  "items": items, "mode": kind})
                    return
        rng = self._range_addresses()
        if rng is not None:
            self._run_op({"kind": kind, "a": rng[0], "b": rng[1]})
        else:
            self._run_op({"kind": kind, "a": self._cursor_addr(), "b": None})

    # ------------------------------------------------------------------
    # 命令(BPM / HS)を置く
    # ------------------------------------------------------------------
    #: 命令 → メニューと入力欄の見出し
    _COMMAND_LABELS = {"BPMCHANGE": "BPM", "SCROLL": "スクロール(HS)",
                       "LYRIC": "歌詞"}

    def _build_command_menu(self, at_x=None, at_y=None):
        """右クリックのメニュー(と、窓の「作譜」メニューの中身)。"""
        menu = QMenu(self)
        self.populate_command_menu(menu, at_x, at_y)
        return menu

    def populate_command_menu(self, menu, at_x=None, at_y=None):
        """命令の項目を menu へ足す。カーソルの位置で内容が変わるので、
        窓のメニュー側は出す直前に clear() して呼び直す。

        命令はキーではなくメニューから置く(利用者の希望 2026-09-25)。以前は
        B/S/G/L のキーも受けていて、項目の右に "\\t B" のようにキーを出して
        いたが、覚えるキーを増やさない方針にしたのでキーは外した。"""
        if self._sel_measures:
            # 小節を選んでいるときは、小節に効くものだけ(利用者の指定
            # 2026-11-05)。音符や命令の話は混ぜない。
            self._measure_menu(menu)
            return
        m, s = self._cursor_addr()
        head = menu.addAction("%d小節目  %d/%d"
                              % (m + 1, s, self._measure_grid(m)))
        head.setEnabled(False)
        menu.addSeparator()
        # コピー / 切り取り / 貼り付け。キー(Ctrl+C / X / V)と同じもの。
        has_src = self._clip_source() is not None
        for text, key, fn, enabled in (
                ("コピー", "Ctrl+C", lambda: self.copy_selection(False), has_src),
                ("切り取り", "Ctrl+X", lambda: self.copy_selection(True), has_src),
                ("貼り付け", "Ctrl+V", self.paste_at_cursor, self.can_paste())):
            act = menu.addAction("%s	%s" % (text, key))
            act.setEnabled(enabled)
            act.triggered.connect(lambda _c=False, f=fn: f())
        menu.addSeparator()
        # 選んだ音符にまとめて付ける(選んでいるときだけ出る)。
        self._selection_menu(menu)
        if self._sel:
            menu.addSeparator()
        # BPM / スクロール / ゴーゴー / 小節線 は**命令パネル**にあるので、
        # 右クリックからは外した(利用者の指定 2026-10-31)。同じ物が2か所に
        # あると、どちらを使えばいいのか分からなくなる。
        # 歌詞は値が文字なので、命令パネル(数の欄しか無い)ではなく
        # ここから置く(利用者の要望 2026-11-07)。
        # 比べる時刻はどれも**このペインの時刻**(cursor_time / 帯と同じ)。
        here = self.lyric_at(self.cursor_time())
        lyr = menu.addAction("歌詞…" if here is None else "歌詞を直す…")
        # 右に「L」を出す。キーでも同じ欄が出るという案内(2026-11-13)。
        # 名前に \t で足すと項目の文字が変わるので、ショートカットとして
        # 持たせる(メニューを開いている間に L を押しても同じ物が選ばれる)。
        lyr.setShortcut(QKeySequence(Qt.Key_L))
        lyr.setShortcutVisibleInContextMenu(True)
        lyr.setData("lyric")
        lyr.triggered.connect(self.open_lyric_popup)
        if here is None and self.lyric_band_at() is not None:
            # 帯の途中: 「歌詞…」はここへ新しく置く(歌詞は切れ目なく続く
            # ので、帯の中に置けないと次の歌詞を足せない)。帯の歌詞を直す
            # 道も別に出す。直すのは帯の頭の行。
            fix = menu.addAction("歌詞を直す…")
            fix.setData("lyric_fix")
            fix.triggered.connect(self.open_lyric_fix_popup)
        stop = menu.addAction("歌詞をここで消す")
        stop.setData("lyric_stop")
        stop.setToolTip("文字の無い #LYRIC を置きます。ここで歌詞が消えます。")
        stop.triggered.connect(self.place_lyric_stop)
        self._jacket_menu(menu, at_x, at_y)
        menu.addSeparator()
        ins = menu.addAction("小節を前に挿入…")
        ins.triggered.connect(self.open_measure_insert)
        # 押した所までを譜面の終わりにする(利用者の希望 2026-11-06
        # 「自分で終了位置を決めて小節を敷き詰めたい」
        #  「クリックしたところを終了地点にした方がわかりやすい」)。
        t_end = self._x_to_sec(at_x) if at_x is not None else None
        need = self.measures_to_time(t_end)
        fill = menu.addAction("終了位置の設定（あと%d小節）" % need
                              if need else "終了位置の設定")
        fill.setData("fill_measures")
        fill.setEnabled(need > 0)
        fill.setToolTip("押した所まで、空の小節を敷き詰めます。")
        fill.triggered.connect(
            lambda _c=False, _t=t_end: self.fill_measures_to_time(_t))
        menu.addSeparator()
        # 波形の縦の倍率(見るためのものなので、譜面は1文字も変わらない)。
        self._wave_gain_menu(menu)
        menu.addSeparator()
        # エディタのツールバーにある道具。範囲(または選んだ音符)があれば
        # そこ、無ければカーソルの小節に効く(利用者の指定 2026-10-01)。
        # 中身は今までと同じ道具で、効かせる範囲を作譜ペインから渡すだけ。
        if self._tool_cb is not None:
            has = self.has_tool_target()
            for key, text, needs_range in self.CHART_TOOLS:
                act = menu.addAction(text)
                act.setData(key)
                act.setEnabled(has or not needs_range)
                act.triggered.connect(lambda _c=False, k=key: self._tool_cb(k))
            menu.addSeparator()
        clr = menu.addAction("範囲を解除")
        clr.setEnabled(self.has_range())
        clr.triggered.connect(self.clear_range)

    #: 目盛りを押したとき、「押しただけ」と見なす手ブレの幅(px)。
    #: 4px では手が少し動いただけで擦り扱いになり、小節を選べたり選べ
    #: なかったりした(利用者の報告 2026-11-06「押せるの押せないのあるよ」)。
    MEASURE_PICK_SLOP = 10

    def _delete_selected_measures(self):
        """上の目盛りで選んだ小節を消してもらう(Delete と右クリックの両方)。"""
        if not self._sel_measures:
            return False
        ms = self.selected_measures()
        self._sel_measures = set()
        self.update()
        self.measuresDeleteRequested.emit(ms)
        return True

    def _measure_menu(self, menu):
        """小節を選んでいるときの右クリックメニュー。

        出すのは「どの小節か」「小節を前に挿入」「選択を解除」の3つだけ
        (利用者の指定 2026-11-05)。"""
        ms = self.selected_measures()
        if len(ms) == 1:
            label = "%d小節目" % (ms[0] + 1)
        else:
            label = "%d〜%d小節目  %d個" % (ms[0] + 1, ms[-1] + 1, len(ms))
        head = menu.addAction(label)
        head.setEnabled(False)
        menu.addSeparator()
        ins = menu.addAction("小節を前に挿入…")
        ins.setData("measure_insert")
        ins.triggered.connect(self.open_measure_insert)
        # 消すのは Delete と同じ所へ(利用者の指定 2026-11-05)。
        dele = menu.addAction("小節を削除	Delete")
        dele.setData("measure_delete")
        dele.triggered.connect(self._delete_selected_measures)
        menu.addSeparator()
        clr = menu.addAction("選択を解除")
        clr.setData("measure_clear")
        clr.triggered.connect(lambda: self.set_selected_measures([]))
        return menu

    #: 右クリックから呼べる、エディタの道具。(鍵, 見出し, 範囲が要るか)。
    #: ストロボ生成はカーソルの位置に作るので範囲は要らない。
    # ノーツ間隔リサイズとストロボ生成は、ツールバーにあるのでここからは
    # 外した(利用者の指定 2026-10-31)。
    CHART_TOOLS = (("highspeed", "ハイスピ変換…", True),
                   ("reverse", "あべこべ反転", True))
    _tool_cb = None

    def set_tool_cb(self, cb):
        """エディタの道具を呼ぶ出口。cb(鍵) を呼ぶ。"""
        self._tool_cb = cb

    def measures_to_time(self, t_end=None):
        """その時刻まで敷き詰めるのに、あと何小節要るか。

        その時刻を**含む**小節まで足す(そこで終わる譜面にしたい、という
        指定)。もう先まであるなら 0。小節が1つも無いときは数えられないので
        0(足す先が決まらない)。t_end を渡さなければ赤い線の所。

        数えるのは**実在の小節**(_known_measures)まで。_measure_count は
        「カーソルを置ける所」なので末尾より EXTEND_MEASURES(64小節)先まで
        含んでおり、そちらで数えると「もう足りている」と判断されて、
        メニューの項目が灰色のままだった(利用者の報告 2026-11-07
        「終了位置の設定が押せない」)。"""
        n = self._known_measures()
        if n <= 0:
            return 0
        t_end = (float(self.position_sec) if t_end is None
                 else float(t_end))
        last = self._bar_time(n)          # 最後の小節の終わり(= n番目の頭)
        if last is None:
            return 0
        if t_end <= last + 1e-6:
            return 0                      # もう足りている
        step = self._measure_len()
        if step is None or step <= 0:
            return 0
        # 線を含む小節まで。端にぴったり乗ったときは増やさない。
        need = int(math.ceil((t_end - last) / step - 1e-9))
        return max(0, need)

    def fill_measures_to_time(self, t_end=None):
        """その時刻まで空の小節を敷き詰める(利用者の希望 2026-11-06)。

        終わりにしたい所は**右クリックで押した所**(利用者の指定 2026-11-06
        「クリックしたところを終了地点にした方がわかりやすい」)。窓の
        「作譜」メニューから呼んだときは押した所が無いので赤い線を使う。

        足したかどうかを返す。すでに先まで譜面があるときは何もしない —
        勝手に消さない。"""
        need = self.measures_to_time(t_end)
        if need <= 0:
            return False
        self._run_op({"kind": "append_measures", "count": need,
                      "grid": self._measure_grid(self._cur_measure)})
        return True

    def open_measure_insert(self):
        """カーソルの小節の手前に、長さを指定した空の小節を1つ入れる。

        長さは拍子記号と同じ「分子/分母」で打ち込む(利用者の指定 2026-10-01)。
        入れた小節の後ろで元の拍子へ戻すので、2小節目以降は動かない。

        上の目盛りで小節を選んでいるときは、**選んだ最初の小節**の手前へ
        (利用者の指定 2026-11-05)。選んでいなければカーソルの小節の手前。"""
        ms = self.selected_measures()
        m = ms[0] if ms else self._cursor_addr()[0]


        def accept(txt):
            if not note_edit._parse_meter(txt):
                return False               # 「3/4」の形でないときは入れ直し
            self._run_op({"kind": "measure_insert", "a": (m, 0), "value": txt})
            return True

        if self._cmd_popup is not None:
            self._cmd_popup.close()
        popup = _CommandInput(self, "%d小節目の手前へ" % (m + 1), "4/4", accept)
        x = self._sec_to_x(self.cursor_time())
        _wh, top, _strip, _b, _c = self._strip_rects()
        popup.adjustSize()
        popup.open_at(self.mapToGlobal(QPoint(max(0, x - popup.width() // 2),
                                              max(0, top - popup.height() - 4))))
        self._cmd_popup = popup
        return popup

    #: 開始・終了の命令の見出し。
    _MARKER_LABELS = {
        "GOGO": {"on": "ゴーゴー開始", "off": "ゴーゴー終了"},
        "BARLINE": {"on": "小節線を隠す", "off": "小節線を出す"},
    }

    def _marker_info(self, kind):
        """カーソル位置まわりの状態(note_edit.marker_info)。"""
        if self._op_cb is None:
            return None
        return self._op_cb({"kind": "peek_marker", "a": self._cursor_addr(),
                            "grid": self._measure_grid(self._cur_measure),
                            "region": kind})

    def wave_gain(self):
        """波形に実際に掛ける倍率。_draw_lane がここを見る。

        画面に出す「x1」は素の振幅ではなく WAVE_BASE_GAIN 倍
        (利用者の指定 2026-10-08)。"""
        return self._wave_gain * self.WAVE_BASE_GAIN

    def set_wave_gain(self, gain):
        """波形の縦の倍率を変える(x1 = WAVE_BASE_GAIN)。設定にも覚える。

        振幅を伸ばして行の高さで切るので、倍率を上げるほど大きい所は
        べったり潰れる代わりに、小さい音のアタックがはっきり出る。"""
        g = max(0.25, min(8.0, float(gain)))
        if abs(g - self._wave_gain) < 1e-9:
            return False
        self._wave_gain = g
        self._bump_strip()               # 帯に焼いた波形を描き直す
        self.update()
        try:
            from neotja import settings as settings_mod
            cfg = settings_mod.load_settings()
            cfg["chart_edit_wave_gain"] = g
            settings_mod.save_settings(cfg)
        except Exception:  # noqa: BLE001
            pass
        return True

    def _wave_gain_menu(self, menu):
        """「波形の高さ」の小メニュー。いまの倍率に印を付ける。"""
        sub = menu.addMenu("波形の高さ")
        for g in self.WAVE_GAINS:
            act = sub.addAction("x%g" % g)
            act.setData(("wave_gain", g))
            act.setCheckable(True)
            act.setChecked(abs(g - self._wave_gain) < 1e-9)
            act.triggered.connect(lambda _c=False, v=g: self.set_wave_gain(v))
        return sub

    def _exec_menu(self, menu, global_pos):
        """メニューを出す(テストではここを差し替えて、出したメニューを調べる)。"""
        menu.exec(global_pos)

    # ------------------------------------------------------------------
    # 選んだ音符にまとめて付ける
    # ------------------------------------------------------------------
    def _selection_menu(self, menu):
        """「選んだ音符に」の小メニュー。選んでいなければ出さない。

        ゴーゴーは**最初から最後まで**を1本の帯にする。音符ごとに
        ON/OFF を切り替えたいときは「音符ごとにゴーゴー」(次の音符との
        隔たりの半分ずつ)を選ぶ — 命令パネルの「!」はこちら
        (利用者の指定 2026-10-08)。"""
        if not self._sel:
            return
        items = self.selected_items()
        if not any(i.get("kind") == "note" for i in items):
            return
        sub = menu.addMenu("選んだ音符に")
        for name in ("SCROLL", "BPMCHANGE"):
            act = sub.addAction("%sを付ける…" % self._COMMAND_LABELS[name])
            act.setData(("sel_cmd", name))
            act.triggered.connect(
                lambda _c=False, n=name: self.open_selection_command(n))
        sub.addSeparator()
        for kind, on, mode, label in self._SELECTION_REGION_LABELS:
            act = sub.addAction(label)
            act.setData(("sel_region", kind, on, mode))
            act.triggered.connect(
                lambda _c=False, k=kind, o=on, md=mode:
                self.apply_selection_region(k, o, md))

    #: 「選んだ音符に」の帯の項目。(種類, 付ける/外す, 帯の切り方, 見出し)。
    #: 切り方は note_edit.op_region_items の mode。
    _SELECTION_REGION_LABELS = (
        ("GOGO", True, "span", "ゴーゴーにする"),
        ("GOGO", True, "each", "音符ごとにゴーゴー（間隔の半分）"),
        ("GOGO", False, "span", "ゴーゴーを外す"),
        ("BARLINE", True, "runs", "小節線を隠す"),
        ("BARLINE", False, "runs", "小節線を出す"),
    )

    #: 命令パネルの「!」を押したときの一押し。種類 → (付ける/外す, 切り方)。
    #: ここに無い種類は、付ける・外すを選ぶ小メニューを出す。
    _SELECTION_MARK_ONE_PUSH = {"GOGO": (True, "each")}

    def has_note_selection(self):
        """音符を選んでいるか(命令パネルの「!」を押せるかどうか)。"""
        return any(k[0] == "note" for k in self._sel)

    def apply_selection_command(self, name, value):
        """選んだ音符すべてに命令を付ける(命令パネルの欄ごとの「!」から)。

        値は欄に出ているものをそのまま使う — 入力欄を出さずに1押しで入る
        (利用者の指定 2026-10-04)。"""
        if not self.has_note_selection():
            return False
        res = self._run_op({"kind": "command_items",
                            "items": self.selected_items(),
                            "name": str(name).upper(), "value": value})
        self.update()
        return bool(res)

    def split_meter_at_selection(self):
        """選んだ音符それぞれに小節線が付くように拍子を変える
        (命令パネルの拍子の「!」。利用者の指定 2026-10-31)。

        音符から次の音符までの隔たりを、そのまま1小節の長さにする。
        範囲が終わったら元の拍子へ戻す。音符は1つも動かない。"""
        if not self.has_note_selection():
            return False
        res = self._run_op({"kind": "meter_split",
                            "a": self._cursor_addr(),
                            "items": self.selected_items()})
        self.update()
        return bool(res)

    def open_selection_region_menu(self, kind, global_pos):
        """ゴーゴー / 小節線の「!」。

        ゴーゴーは**一押しで**「音符ごとに次との隔たりの半分」を付ける
        (利用者の指定 2026-10-08)。小節線は隠す・出すのどちらか決まらない
        ので、今までどおりその場所で選んでもらう。外すのと「最初から
        最後まで」は右クリックの「選んだ音符に」にある。"""
        if not self.has_note_selection():
            return None
        kind = str(kind).upper()
        one = self._SELECTION_MARK_ONE_PUSH.get(kind)
        if one is not None:
            self.apply_selection_region(kind, one[0], one[1])
            return None
        menu = QMenu(self)
        for k, on, mode, label in self._SELECTION_REGION_LABELS:
            if k != kind:
                continue
            act = menu.addAction(label)
            act.setData(("sel_region", k, on, mode))
            act.triggered.connect(
                lambda _c=False, kk=k, o=on, md=mode:
                self.apply_selection_region(kk, o, md))
        if not menu.actions():
            return None
        self._exec_menu(menu, global_pos)
        return menu

    def apply_selection_region(self, kind, on, mode=None):
        """選んだ音符をゴーゴー / 小節線の帯で囲む(外すなら on=False)。

        mode は帯の切り方(note_edit.op_region_items)。省略したときは
        ゴーゴーなら「最初から最後まで」、小節線なら「隙間で切る」。"""
        if not self._sel:
            return False
        kind = str(kind).upper()
        if mode is None:
            mode = "span" if kind == "GOGO" else "runs"
        res = self._run_op({"kind": "region_items",
                            "items": self.selected_items(),
                            "region": kind, "on": bool(on),
                            "mode": str(mode)})
        self.update()
        return bool(res)

    def open_selection_command(self, name):
        """選んだ音符に命令(スクロール / BPM)を付ける入力欄を出す。

        入れた値はそこから先ずっと効く。戻す行は入れない
        (利用者の指定 2026-10-04)。"""
        if self._op_cb is None or name not in self._COMMAND_LABELS:
            return None
        if not self._sel:
            return None
        m, s0 = self._cursor_addr()
        peek = self._op_cb({"kind": "peek_command", "a": (m, s0),
                            "grid": self._measure_grid(m),
                            "name": name, "time": self.cursor_time()}) or {}
        default = peek.get("default")
        text = note_edit._fmt_number(default) if default is not None else ""

        def accept(txt):
            if txt == "":
                return True
            try:
                value = float(txt)
            except ValueError:
                return False
            if name == "BPMCHANGE" and value <= 0:
                return False
            self._run_op({"kind": "command_items",
                          "items": self.selected_items(),
                          "name": name, "value": value})
            return True

        self._show_command_popup(self._COMMAND_LABELS[name][0], text, accept)
        return True

    def open_command_input(self, name):
        """カーソルの位置に命令 name(BPMCHANGE / SCROLL)を置く入力欄を出す。

        入力欄の初期値は、その位置に同じ命令があればその値、無ければその位置で
        今効いている値。初期値のまま決定しても命令は増やさない。"""
        if self._op_cb is None or name not in self._COMMAND_LABELS:
            return None
        m, s = self._cursor_addr()
        peek = self._op_cb({"kind": "peek_command", "a": (m, s),
                            "grid": self._measure_grid(m),
                            "name": name, "time": self.cursor_time()}) or {}
        cur = peek.get("value")
        default = peek.get("default")
        if cur is not None:
            text = cur
        elif default is not None:
            text = note_edit._fmt_number(default)
        else:
            text = ""

        def accept(txt):
            if txt == "":
                if cur is None:
                    return True             # 消すものが無い。閉じるだけ
                value = None
            else:
                try:
                    value = float(txt)
                except ValueError:
                    return False            # 数字ではない。入れ直してもらう
                if name == "BPMCHANGE" and value <= 0:
                    return False
                if cur is None and default is not None and abs(value - float(default)) < 1e-9:
                    return True             # 今効いている値と同じ。増やさない
            self._run_op({"kind": "command", "a": (m, s), "name": name, "value": value})
            return True

        return self._show_command_popup(self._COMMAND_LABELS[name][0],
                                        text, accept)

    def _show_command_popup(self, label, text, accept):
        """数を入れる小さな入力欄を、カーソルの上に出す。"""
        if self._cmd_popup is not None:
            self._cmd_popup.close()
        popup = _CommandInput(self, label, text, accept)
        x = self._sec_to_x(self.cursor_time())
        _wh, top, _strip, _b, _c = self._strip_rects()
        popup.adjustSize()
        pos = self.mapToGlobal(QPoint(max(0, x - popup.width() // 2),
                                      max(0, top - popup.height() - 4)))
        popup.open_at(pos)
        self._cmd_popup = popup
        return popup

    def jump_to_edge(self, end):
        """Home / End: 譜面の先頭 / 最後の小節の頭へ。"""
        n = self._known_measures()
        self._cur_measure = max(0, n - 1) if end else 0
        self._cur_slot = 0
        self._clamp_cursor()
        self._cursor_changed()

    def change_grid(self, direction):
        """分割数を1段変える。カーソルの時刻上の位置はできるだけ保つ。"""
        if self._long is not None:
            return
        try:
            i = GRID_CHOICES.index(self._grid)
        except ValueError:
            i = GRID_CHOICES.index(16)
        j = max(0, min(len(GRID_CHOICES) - 1, i + direction))
        if j == i:
            return
        g0 = self._measure_grid(self._cur_measure)
        frac = self._cur_slot / g0 if g0 else 0.0
        free = self._free_time
        self._grid = GRID_CHOICES[j]
        g1 = self._measure_grid(self._cur_measure)
        self._cur_slot = min(int(round(frac * g1)), g1 - 1)
        self._clamp_cursor()
        self.update()
        # 鳴っているあいだはシークしない。分割を変えるたびに再生位置が
        # 少し巻き戻っていた(利用者の報告 2026-11-02)。再生中のカーソルは
        # 再生位置に付いていくので、ここで合わせ直す必要もない。
        if self._playing:
            return
        if free is not None:
            # 助走の中に置いた線は、新しい分割の伸ばしたグリッドへ合わせ直す
            # (古い分割の目盛りのままだと、→ で歩いても頭に乗らない)。
            self._lead_seek(free)
            return
        # **赤い線も動かす。** 1/16 → 1/24 のように割り切れない分割へ
        # 変えるとカーソルの時刻が少しずれるのに、線を引き直していな
        # かったので、線の位置と打った音符の位置が食い違っていた
        # (利用者の報告 2026-10-31)。
        self._cursor_changed()

    # ------------------------------------------------------------------
    # 入力
    # ------------------------------------------------------------------
    #: このペインが自分で使うキー。窓のショートカット(ゲーム窓の Esc = 全画面
    #: 解除 など)に先を越されないよう、ShortcutOverride で先に名乗り出る。
    _OWN_KEYS = frozenset((
        Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Home, Qt.Key_End,
        Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
        Qt.Key_W, Qt.Key_Q, Qt.Key_H, Qt.Key_G, Qt.Key_S, Qt.Key_L,
    )) | frozenset(_PLAIN_KEYS) | frozenset(_PEEPO_NOTE_KEYS) | frozenset(_PEEPO_LONG_KEYS)

    def event(self, e):
        if e.type() == QEvent.ShortcutOverride:
            mods = e.modifiers()
            key = e.key()
            # Ctrl+C / X / V はこのペインのもの(選んだ音符のコピー)。
            if ((mods & (Qt.ControlModifier | Qt.MetaModifier))
                    and key in (Qt.Key_C, Qt.Key_X, Qt.Key_V)):
                e.accept()
                return True
            # ほかの Ctrl 付き(Ctrl+Z など)はアプリのショートカットのまま。
            if not (mods & (Qt.ControlModifier | Qt.MetaModifier)):
                # Esc は「範囲や連打の途中を取り消す」ときだけこちらで使う。
                # それ以外は今までどおり窓の全画面解除へ。
                if key == Qt.Key_Escape and (self._sel or self.has_range()
                                             or self._long is not None
                                             or self._offset_drag is not None
                                             or self._range_start is not None):
                    e.accept()
                    return True
                if key in self._OWN_KEYS:
                    e.accept()
                    return True
        # Tab は keyPressEvent に届く前にフォーカス移動で消費されるので、
        # ここで横取りする(PeepoDrumKit と同じく範囲選択に使う)。
        if (e.type() == QEvent.KeyPress and e.key() == Qt.Key_Tab
                and not self.offset_mode):
            if not e.isAutoRepeat():
                self.toggle_range_at_cursor()
            e.accept()
            return True
        return super().event(e)

    #: Shift + ホイールで一度に動く小節の数。
    WHEEL_JUMP_MEASURES = 4

    def wheelEvent(self, event):
        """ホイールは**景色を1小節ぶん送るだけ**。赤い線は動かさない。

        以前はレーンの小節移動(= シーク)を呼んでいたので、先や後ろの音符を
        見ようとすると曲のほうが飛んでいた。しかも1目盛で1小節動くため、
        回しすぎると戻すのが大変だった。赤い線を動かすのはクリック・十字
        キー・再生だけにする(利用者の要望 2026-11-05)。

        Shift + ホイールは4小節ぶん。拡大縮小(修飾キー + ホイール)は
        今までどおり親へ渡す。送った景色は、赤い線が窓の追従の線まで来たら
        そのまま追従へ戻る(_follow_now)。"""
        if self.offset_mode or (event.modifiers() & self.ZOOM_MODIFIERS):
            super().wheelEvent(event)
            return
        d = self._wheel_notches(event)
        if event.modifiers() & Qt.ShiftModifier:
            d *= self.WHEEL_JUMP_MEASURES
        step = d * self._wheel_view_step()
        if self._note_drag is not None:
            # 掴んでいるあいだは**景色だけ**動かす(利用者の指定 2026-10-08)。
            # 行き先は _drag_dx が送ったぶんを足して直すので、滑らせない。
            self.scroll_view_to(max(0.0, self.view_target() + step),
                                animate=False)
            event.accept()
            return
        self.scroll_view_free(step)
        event.accept()

    def keyReleaseEvent(self, event):
        # 連打・風船のキーを離したところで置く。押しっぱなしの自動連射は
        # 離す/押すの組が届くので、自動連射ぶんは無視する。
        if (self._long is not None and not event.isAutoRepeat()
                and event.key() == self._long["key"]):
            self._finish_long(event.modifiers())
            return
        super().keyReleaseEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        # OFFSET調整のあいだは譜面を書かない。この行が無いと、← → は
        # 「OFFSET を 1ms ずつ動かす」ではなく編集カーソルの移動になり、
        # F/J や数字はそのまま音符を置いてしまう。合わせている最中に譜面が
        # 書き換わるので、作譜モードでは OFFSET を合わせられなかった。
        # 親(WaveformWidget)が Esc・← →・スペース・0 を受け持つ。
        if self.offset_mode:
            super().keyPressEvent(event)
            return

        if key == Qt.Key_Escape and not self.offset_mode and (
                self._sel or self._range_start is not None):
            self.clear_selection()
            self.clear_range()
            return

        # コピー / 切り取り / 貼り付け。音符のキーより先に見る。
        if mods & (Qt.ControlModifier | Qt.MetaModifier):
            if key == Qt.Key_C:
                self.copy_selection(cut=False)
                return
            if key == Qt.Key_X:
                self.copy_selection(cut=True)
                return
            if key == Qt.Key_V:
                self.paste_at_cursor()
                return
        if key in _PEEPO_NOTE_KEYS and not (mods & (Qt.ControlModifier | Qt.MetaModifier)):
            if not event.isAutoRepeat():
                self._peepo_note_key(_PEEPO_NOTE_KEYS[key], mods)
            return
        if key in _PEEPO_LONG_KEYS and not (mods & (Qt.ControlModifier | Qt.MetaModifier)):
            if not event.isAutoRepeat():
                self._begin_long(key, _PEEPO_LONG_KEYS[key])
            return
        if key == Qt.Key_W and not mods & (Qt.ControlModifier | Qt.AltModifier):
            self._delete_or_transform("flip")
            return
        if key == Qt.Key_Q and not mods & (Qt.ControlModifier | Qt.AltModifier):
            self._delete_or_transform("size")
            return
        # ゴーゴーだけはキーで置く(利用者の指定 2026-10-24)。音符と同じ
        # 気持ちで打てるように、開始か終了かは**今の状態**で決まる。
        # Ctrl+G / Alt+G は窓のほう(本文へ #GOGOSTART)なので取らない。
        if (key == Qt.Key_G and not mods & (Qt.ControlModifier
                                            | Qt.AltModifier
                                            | Qt.MetaModifier)):
            if not event.isAutoRepeat():
                self.toggle_gogo_at_cursor()
            return
        # 小節線を隠す/出すもキーで置く(利用者の希望 2026-11-05)。
        # ゴーゴーの G と同じで、隠すか出すかは**今の状態**で決まる。
        # Ctrl+S は上書き保存なので取らない。
        if (key == Qt.Key_S and not mods & (Qt.ControlModifier
                                            | Qt.AltModifier
                                            | Qt.MetaModifier)):
            if not event.isAutoRepeat():
                self.toggle_barline_at_cursor()
            return
        # L で歌詞の欄を出す(利用者の指定 2026-11-13)。右クリックの
        # 「歌詞…」と同じ物で、カーソルの所へ置く/直す。歌詞は譜面を流し
        # ながら続けて打つので、毎回メニューを開かずに済むようにする。
        # 修飾キー付きは窓のショートカットに残すため取らない。押しっぱなし
        # の自動連射で欄を出し直さない。
        if key == Qt.Key_L and not mods & (Qt.ControlModifier
                                           | Qt.AltModifier
                                           | Qt.MetaModifier
                                           | Qt.ShiftModifier):
            if not event.isAutoRepeat():
                self.open_lyric_popup()
            return
        # ほかの命令(BPM / HS)はキーでは置かない。右クリックの
        # メニューか、窓の「作譜」メニューから置く(利用者の希望 2026-09-25)。
        if key == Qt.Key_Home:
            self.jump_to_edge(False)
            return
        if key == Qt.Key_End:
            self.jump_to_edge(True)
            return

        if key in (Qt.Key_Left, Qt.Key_Right):
            step = -1 if key == Qt.Key_Left else 1
            # Shift を押していれば、動いた先に最後の音符を置く
            # (利用者の言い直し 2026-10-30)。
            # **帯は消さない。** Tab で張った帯は ←→ で端まで
            # 歩いてからもう一度 Tab、という使い方をする。
            if mods & Qt.ShiftModifier:
                self.paint_move(step)
            else:
                self.move_cursor(step)
            return
        if key == Qt.Key_Up:
            self.change_grid(1)
            return
        if key == Qt.Key_Down:
            self.change_grid(-1)
            return
        if key == Qt.Key_H and not (mods & (Qt.ControlModifier | Qt.AltModifier
                                            | Qt.MetaModifier)):
            self.set_legend_visible(not self._show_legend)
            self.legendToggled.emit(self._show_legend)
            return
        if key == Qt.Key_Delete or key == Qt.Key_Backspace:
            # 上の目盛りで小節を選んでいれば、その小節をまるごと消す
            # (利用者の希望 2026-11-05)。本文を触るのは窓のほう。
            if self._sel_measures:
                self._delete_selected_measures()
                return
        if key == Qt.Key_Delete:
            # 範囲があれば範囲を、無ければカーソルの音符を消す。長い音符は
            # 終端ごと消える(数字の 0 と違って終端 8 が取り残されない)。
            self._delete_or_transform("delete")
            return
        if key == Qt.Key_Backspace:
            # 置いてもカーソルは進まないので、BackSpace もその場で消す。
            self._run_op({"kind": "delete", "a": self._cursor_addr(), "b": None})
            return

        char = None
        if not (mods & (Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier)):
            char = _PLAIN_KEYS.get(key)
        if char is not None:
            self._place(char)
            return

        # ここで拾わなかったキー(Space の再生など)は親へ。
        super().keyPressEvent(event)

    def _place(self, char):
        """カーソル位置に音符を置く(数字キー)。カーソルは進めない。

        以前は「同じ音符をもう一度でトグル」だったが、判定に使えるのが
        暫定表示(_pending)だけで、再解析が届いて暫定表示が消えると同じキーが
        配置になったり削除になったりして安定しなかった。消すのは 0 /
        Delete / BackSpace に一本化してある。"""
        # 1〜4 と 0 は操作(note_edit の "place")として通す。連打・風船の頭を
        # 上書き/消したときに終端の 8 と BALLOON: の打数も一緒に片付ける
        # ため(1文字だけ書き換えると 8 が取り残されていた。利用者の報告
        # 2026-11-13)。暫定表示と打音は _run_op と窓の側が今までどおり出す。
        if char in "01234" and self._op_cb is not None:
            self._run_op({"kind": "place",
                          "a": (self._cur_measure, self._cur_slot),
                          "char": char})
            return
        addr = (self._cur_measure, self._cur_slot,
                self._measure_grid(self._cur_measure))
        self._pending[addr] = char
        if char == "0":
            self._hide_note_at(*addr)
        self.noteEdited.emit(self._cur_measure, self._cur_slot,
                             self._measure_grid(self._cur_measure), char)
        # 置いてもカーソルは進めない(利用者の指定。F/J などと同じ)。
        self.update()

    def _hide_note_at(self, m, slot, grid):
        """消したばかりの音符を、再解析を待たずに見た目から消す。

        置くときは暫定表示を上に描けばよいが、消すときは親が描いている
        権威データの音符が残ってしまい、再解析(600ms)まで消えたように
        見えなかった。手元の音符列からそのスロットぶんを抜いて描き直す。
        正式な結果が届けば set_notes で丸ごと置き換わる。"""
        raw = getattr(self, "_notes_raw", None) or []
        t = self._address_time(m, slot, grid)
        if t is None:
            return
        t_next = self._address_time(m, slot + 1, grid)
        half = abs(t_next - t) * 0.5 if t_next is not None else 0.01
        half = max(1e-3, half)
        # _notes_raw は譜面時刻。chart_time = audio_time + OFFSET。
        center = t + self.offset
        kept = [n for n in raw if not (center - half <= n[0] < center + half)]
        changed = len(kept) != len(raw)
        if changed:
            self._notes_raw = kept
        # 連打・風船の頭を消したときは帯も消す。帯は別の経路(set_spans)で
        # 持っているので、音符だけ抜くと再解析(600ms)まで帯が残っていた。
        spans = getattr(self, "_spans_raw", None)
        if spans:
            keep_spans = [sp for sp in spans if not (center - half <= sp[0] < center + half)]
            if len(keep_spans) != len(spans):
                self._spans_raw = keep_spans or None
                changed = True
        if changed:
            self._apply_offset_local(self.offset)

    # ------------------------------------------------------------------
    # 描画
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 行レイアウト(PeepoDrumKit と同じ「左に行の名前・右に時間軸」)
    # ------------------------------------------------------------------
    #: 左の行名の列の幅。時間軸はここから右(親の LANE_X0)。
    LANE_X0 = 130
    #: 音符の丸とバーは、行が高いぶん大きくする(利用者の指定 2026-09-25)。
    NOTE_R = 14
    NOTE_R_BIG = 19
    SPAN_TH = 24
    SPAN_TH_BIG = 34
    #: 波形は音符に隠れないよう、さらに縦へ伸ばす(利用者の指定 2026-09-25)。
    #: ただし _draw_lane 側で行の高さに頭打ちされるので、1.12 以上はどれも
    #: 同じ「行いっぱい」になる。実際に効く倍率は wave_gain() のほう。
    LANE_GAIN = 3.0
    #: 音符の行のうち、下から何割を波形に使うか。
    WAVE_FRAC = 0.45
    #: 「x1」のときに実際に掛ける倍率(利用者の指定 2026-10-08:
    #: 「3倍を1倍にして」)。素の振幅では細い帯にしかならないので、何も
    #: いじっていない状態をここまで持ち上げておく。
    WAVE_BASE_GAIN = 3.0
    #: 波形の縦の倍率。選べる値(x1 = WAVE_BASE_GAIN)。
    WAVE_GAINS = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)
    #: 叩かれた瞬間だけ音符を膨らませる(どこが鳴ったかが目で分かるように)。
    HIT_POP_SEC = 0.13
    HIT_POP_MAX = 1.45
    #: 上の目盛り(小節番号と時刻)の高さ。
    RULER_H = 22
    #: 行: (種類, 行の名前, 高さ)。音符の行だけは余ったぶんを全部もらう
    #: (ペインの高さが変わっても、増減するのは波形と音符の行)。
    ROWS = (
        ("bpm", "BPM", 30),
        ("measure", "拍子記号", 30),
        ("note", "音符", 0),
        ("hs", "スクロール", 30),
        # 歌詞(#LYRIC)。値が文字なので、札には頭の何文字かだけ出す
        # (利用者の要望 2026-11-07)。
        ("lyric", "歌詞", 28),
        # 帯が出ているのは「隠している区間」なので、名前もそう呼ぶ
        # (利用者の指定 2026-11-05)。
        ("barline", "小節線の非表示", 28),
        ("gogo", "GOGO", 28),
    )
    #: 行の中身の色。区間の行(小節線・ゴーゴー)は同じ灰色にそろえる
    #: (利用者の指定 2026-09-25: 色が増えると認識しにくい)。
    ROW_CONTENT_COLOR = "#c8c8c8"
    #: 小節線を隠している区間の帯。ゴーゴーの帯と見分けが付く色にする。
    BARLINE_BAND_COLOR = "#6f7fa0"

    #: 表示倍率で掛け直す px の定数(WaveformWidget.set_ui_scale 参照)。
    SCALED_PX = WaveformWidget.SCALED_PX + (
        "RULER_H", "LEGEND_H", "GRID_LABEL_X", "GRID_LABEL_W",
        "RULER_NUM_MIN_ROOM")

    def on_ui_scale_changed(self):
        """倍率が変わったら、行の高さを作り直して焼いた帯を捨てる。"""
        super().on_ui_scale_changed()
        s = self._ui_scale
        if s >= 0.999:
            self.__dict__.pop("ROWS", None)            # 原寸に戻す
        else:
            # 音符の行(高さ 0)は「余ったぶん全部」なので触らない。
            self.ROWS = tuple(
                (k, n, max(1, int(round(h * s))) if h > 0 else h)
                for k, n, h in type(self).ROWS)
        self._row_rects_cache = None
        self._row_font_cache = None
        self._row_width_cache = None
        self._grid_pen_cache = None
        self._bump_strip()

    def _note_scale(self, t):
        """再生中、再生位置が通り過ぎた直後の音符を少しだけ大きく描く。"""
        if not self._playing:
            return 1.0
        d = self.position_sec - t
        if 0.0 <= d < self.HIT_POP_SEC:
            u = 1.0 - d / self.HIT_POP_SEC
            return 1.0 + (self.HIT_POP_MAX - 1.0) * u
        return 1.0

    def _lane_qcolor(self):
        """波形は灰色(利用者の指定)。青いままだと音符と主張し合う。"""
        return QColor("#9aa3b2")

    def _buttons_y(self):
        """「合成 / OFFSET調整」は音符の行の中へ(上は小節番号の目盛りなので)。"""
        top, _h = self._row_rects()["note"]
        return top + 4

    # ------------------------------------------------------------------
    # 行の上での編集(命令の行をクリック / 帯の端をつかんで動かす)
    # ------------------------------------------------------------------
    #: 命令の行 → その行が持つ命令の名前。拍子(#MEASURE)はまだ置けない。
    _ROW_COMMANDS = {"bpm": "BPMCHANGE", "hs": "SCROLL"}

    def _row_at(self, y):
        """y がどの行か。行の外なら None。"""
        for kind, (top, h) in self._row_rects().items():
            if top <= y < top + h:
                return kind
        return None

    def place_command(self, name, value):
        """カーソルの位置に命令を置く(命令パネルの「追加」から)。"""
        m, s = self._cursor_addr()
        return self._run_op({"kind": "command", "a": (m, s),
                             "name": str(name).upper(), "value": value})

    def commands_at_cursor(self):
        """いま値をいじれる命令 {種類: (位置(Fraction), 音源時刻)}。

        種類は "bpm" / "hs" / "measure"(命令の行と同じ)。見つけ方は2通り:
          ・命令を1つだけ選んでいれば、それ。行の札をクリックして選んだ
            ものが、そのまま対象になる(グリッドに乗っていない位置でもよい)。
          ・何も選んでいなければ、カーソルの位置にある命令。
        どちらも無ければ空の辞書(= パネルは「追加」のまま)。"""
        by_name = {v: k for k, v in self._CMD_NAMES.items()}
        sel = [k for k in self._sel if k[0] == "cmd" and k[1] in by_name]
        if len(sel) == 1:
            kind = by_name[sel[0][1]]
            return {kind: (sel[0][2], self._time_of_pos(sel[0][2]))}
        if self._sel:
            return {}               # 複数選んでいるときは触らせない
        cur = (Fraction(self._cur_measure)
               + Fraction(self._cur_slot, self._measure_grid(self._cur_measure)))
        out = {}
        # **近くの命令だけを見る。** 種類ごとの時刻は時間順に並んでいるので
        # (_rebuild_cmd_kinds。_draw_cmd_row も並び順を前提にしている)、
        # 二分探索でカーソルのまわりだけ切り出す。全部を舐めていたころは、
        # 命令が1万4千個ある譜面でカーソルを1つ動かすたびに 280ms かかって
        # いた(実測。利用者の報告 2026-10-03「操作するとめっちゃ重い」)。
        times = self._cmd_kind_times or {}
        m = self._cur_measure
        mt0 = self._bar_time(m)
        mt1 = self._bar_time(m + 1)
        t_cur = self.cursor_time()
        # 位置は 1/POS_DEN に丸めて比べるので、その丸め幅ぶんは拾っておく
        # (丸めたあとの Fraction の一致が本当の判定)。
        span = (mt1 - mt0) if (mt0 is not None and mt1 is not None
                               and mt1 > mt0) else self._measure_len()
        eps = max(1e-6, span / self.POS_DEN)
        for kind in self._CMD_NAMES:
            ts = times.get(kind)
            if not ts:
                continue
            if kind == "measure":
                # 拍子だけは「その小節のもの」。小節の頭にしか置けない(音符の
                # 間隔を保って組み直す都合。note_edit.op_measure を参照)ので、
                # 小節のどこにカーソルがあってもその拍子を指す。
                lo = bisect.bisect_left(ts, (mt0 if mt0 is not None else 0.0) - eps)
                hi = (bisect.bisect_right(ts, mt1 - eps) if mt1 is not None
                      else len(ts))
            else:
                lo = bisect.bisect_left(ts, t_cur - eps)
                hi = bisect.bisect_right(ts, t_cur + eps)
            for i in range(lo, hi):
                t = ts[i]
                p = self._pos_of_time(t)
                if p is None:
                    continue
                if (int(p) == m) if kind == "measure" else (p == cur):
                    out[kind] = (p, t)      # 同じ位置に複数あれば後ろを採る
        return out

    def set_command_value(self, name, pos, value):
        """すでに置いてある命令の値を書き換える(命令パネルの「変更」から)。"""
        pos = Fraction(pos)
        return self._run_op({"kind": "command_value",
                             "pos": (pos.numerator, pos.denominator),
                             "name": str(name).upper(), "value": value})

    def toggle_gogo_at_cursor(self):
        """G。ゴーゴーの開始/終了を、**今の状態に合わせて**置く。

        ・ゴーゴーの外 → 開始を置く
        ・ゴーゴーの中 → 終了を置く
        ・その位置にもう印がある → それを消す(押し間違いを同じキーで戻す)

        音符と同じ気持ちで打てるように(利用者の指定 2026-10-24)。
        音符を選んでいるときの「開始」は、今までどおり選んだ範囲を覆う。"""
        return self._toggle_marker_at_cursor("GOGO")

    def toggle_barline_at_cursor(self):
        """S。小節線を隠す/出すを、**今の状態に合わせて**置く。

        ゴーゴーの G と同じ考え方(利用者の希望 2026-11-05)。
        ・小節線が出ている → #BARLINEOFF(隠す)を置く
        ・隠れている → #BARLINEON(出す)を置く
        ・その位置にもう命令がある → それを消す"""
        return self._toggle_marker_at_cursor("BARLINE")

    def _toggle_marker_at_cursor(self, kind):
        """G / S の中身。帯の外なら開始、中なら終了、その場にあれば消す。"""
        kind = str(kind).upper()
        info = self._marker_info(kind) or {}
        if info.get("here_on") or info.get("here_off"):
            which = "on" if info.get("here_on") else "off"
            return self._run_op({"kind": "marker", "a": self._cursor_addr(),
                                 "region": kind, "which": which,
                                 "present": False})
        return self.place_marker(kind, "off" if info.get("before") else "on")

    def place_marker(self, kind, which):
        """カーソルの位置に開始/終了の印を置く(命令パネルのボタンから)。

        **音符を選んでいるときの「ゴーゴー開始」だけは別**(利用者の指定
        2026-10-08)。選んだ範囲をゴーゴーで覆う — 開始は最初の音符、終了は
        次の音符(その手前に小節線が来るならその小節線)に置く。"""
        if (str(kind).upper() == "GOGO" and str(which) == "on"
                and self.has_note_selection()):
            return self.apply_selection_region("GOGO", True, mode="cover")
        m, s = self._cursor_addr()
        return self._run_op({"kind": "marker", "a": (m, s),
                             "region": str(kind).upper(), "which": str(which),
                             "present": True})

    #: ホイール1目盛で動く量。表示幅に対する割合。
    #:
    #: **小節の長さではなく時間で決める**(利用者の要望 2026-11-07
    #: 「ホイール移動の時は時間で移動してほしい。大量に小節線があると
    #: そこだけ速度が遅い」)。以前は「いまの小節の長さ × 0.7」だったので、
    #: #MEASURE で刻んだ所や BPM の速い所だけ進みが遅かった。
    #:
    #: 0.25 は今までの手触りに合わせた値(窓6秒・BPM120 の 4/4 で 1.5秒
    #: ≒ 今までの 1.4秒)。
    WHEEL_TIME_FRAC = 0.25

    def _wheel_view_step(self):
        """ホイール1目盛で動かす秒数(表示幅の WHEEL_TIME_FRAC 倍)。"""
        return max(0.05, self._visible_span() * self.WHEEL_TIME_FRAC)

    def _drag_dx(self, dr=None):
        """掴んだ所からの px。掴んでいるあいだに景色が動いたぶんを足して直す。

        ふだんは景色を止めているので 0 を足すだけだが、掴んだままホイールで
        送ったときは景色だけが動く。dx をそのまま使うと、送ったぶん行き先が
        手前へずれる。離したあと(_note_drag を外したあと)も呼べるよう、
        控えを直接渡せる。"""
        if dr is None:
            dr = self._note_drag
        if dr is None:
            return 0.0
        spp = self._seconds_per_pixel()
        if not spp:
            return float(dr["dx"])
        # 描いている最中は view_start が帯の起点へずらされている。ここで
        # 読むのは**本当の左端**のほう(_paint_view0)。
        now = self._paint_view0
        if now is None:
            now = self.view_start
        return float(dr["dx"]) + (now - dr.get("view0", now)) / spp

    def _drag_delta_slots(self, dx, anchor=None):
        """つかんで動かした px を、グリッド何個ぶんかに直す。

        anchor(つかんだものの位置)があればそこを基準に、無ければ選んだものの
        先頭を基準にする。"""
        if anchor is not None:
            t = self._time_of_pos(anchor)
            m0 = int(anchor)
            s0 = int(round(float(anchor - m0) * self._measure_grid(m0)))
        else:
            a = self._range_addresses()
            if a is None:
                return 0
            (m0, s0), _b = a
            t = self._address_time(m0, s0, self._measure_grid(m0))
        if t is None:
            return 0
        addr = self._address_from_time(max(0.0, t + dx * self._seconds_per_pixel()),
                                       nearest=True)
        if addr is None:
            return 0
        # 「何グリッドぶん動いたか」。小節ごとに割る数が違うので、
        # 住所の差ではなく 1 ずつ数える。
        return self._slots_between((m0, s0), addr)

    def _finish_note_drag(self):
        """離したところの一番近いグリッドへ、選んだものを置き直す。"""
        dr, self._note_drag = self._note_drag, None
        self.setCursor(Qt.ArrowCursor)
        if dr is None:
            return
        if abs(self._drag_dx(dr)) < 4 or not self._sel:
            obj = dr.get("obj")
            if obj is not None and not dr.get("ctrl") and self._sel != {obj}:
                self.set_selection({obj})
            self.update()
            return
        steps = self._drag_delta_slots(self._drag_dx(dr), dr.get("anchor"))
        if steps:
            # 「何グリッドぶん」の長さは小節ごとに違う(拍子で変わる)ので、
            # 小節単位の分数ではなくグリッドの数のまま渡す。以前はカーソルの
            # いる小節の分割数で割っていたので、別の拍子の音符を掴むと
            # ずれた位置へ落ちた(利用者の報告 2026-10-03)。
            # 帯の端をつかんだときは、同じ側の端だけを動かす(_drag_edge_name)。
            self._note_drag = dr          # _drag_keys が見るので戻しておく
            move_keys = self._drag_keys()
            tails = self._drag_tail()
            self._note_drag = None
            items = [{"kind": k[0] if k[0] in ("cmd", "tail") else "note",
                      "name": k[1] if k[0] == "cmd" else None,
                      "pos": (k[-1].numerator, k[-1].denominator)}
                     for k in move_keys]
            # 終端をつかんだときは「長さを変える」操作。頭は動かさない。
            res = items and self._run_op(
                {"kind": "tail_items" if tails else "move_items",
                 "items": items, "steps": int(steps),
                 "anchor": (dr['anchor'].numerator, dr['anchor'].denominator)
                 if dr.get('anchor') is not None else None})
            if res:
                # 動かしたものは選んだまま付いていく(続けて動かせるように)。
                def shifted(k):
                    # 音符は本文側(op_move_items)が目盛へ丸めるので、枠の
                    # 行き先も同じに丸める。命令はどちらも丸めない。
                    cmd = k[0] == "cmd"
                    moved = res.get('moved_positions')
                    if moved is not None:
                        old = min(moved, key=lambda p: abs(p - k[-1]))
                        p = moved[old]
                    else:
                        p = self._shift_pos(k[-1], steps, snap=not cmd)
                    return (k[0], k[1], p) if cmd else (k[0], p)
                self.set_selection({
                    shifted(k) if k in move_keys else k for k in self._sel})
        self.update()

    def _shift_pos(self, pos, steps, snap=False):
        """位置(小節 + 小節の中の割合)を steps グリッドぶんずらす。

        note_edit._Grids.shift と同じ歩き方。選んだものの行き先を、画面側でも
        同じに出すために持つ(小節ごとに割る数が違うので引き算では出せない)。

        snap は本文側(_Grids.shift)と揃えるためのもの。音符は今のグリッドの
        目盛へ乗るようになった(利用者の指定 2026-10-05)ので、ここで丸めないと
        選択の枠だけが実際の音符からずれる。命令の位置は本文側も丸めない。"""
        steps = int(steps)
        pos = Fraction(pos)
        if steps == 0:
            return pos
        m = int(pos)
        g = self._measure_grid(m)
        s = (pos - m) * g + steps
        while s >= g:
            s -= g
            m += 1
            g = self._measure_grid(m)
        while s < 0:
            if m <= 0:
                return Fraction(0)      # 画面の表示用なので頭で止める
            m -= 1
            g = self._measure_grid(m)
            s += g
        if snap:
            # いちばん近い目盛へ(真ん中なら大きいほう)。_Grids.shift と同じ。
            s = math.floor(s + Fraction(1, 2))
            while s >= g:
                s -= g
                m += 1
                g = self._measure_grid(m)
        return m + Fraction(s) / g

    def _drag_time_positions(self):
        """本文と同じ計算でドラッグの行き先を得る。1移動につき1回だけ。"""
        dr = self._note_drag
        if dr is None or self._op_cb is None or self._drag_tail():
            return None
        steps = self._drag_delta_slots(self._drag_dx(dr), dr.get('anchor'))
        keys = self._drag_keys()
        cache_key = (steps, tuple(sorted(keys)), dr.get('anchor'))
        if dr.get('time_plan_key') != cache_key:
            items = [{'kind': k[0] if k[0] in ('cmd', 'tail') else 'note',
                      'name': k[1] if k[0] == 'cmd' else None,
                      'pos': (k[-1].numerator, k[-1].denominator)} for k in keys]
            anchor = dr.get('anchor')
            result = self._op_cb({'kind': 'peek_move_items', 'items': items,
                                 'steps': steps, 'grid': self._grid,
                                 'grids': self._grids_for({'items': items, 'steps': steps}),
                                 'anchor': (anchor.numerator, anchor.denominator)
                                 if anchor is not None else None}) or {}
            dr['time_plan'] = result.get('positions', {})
            dr['time_plan_key'] = cache_key
        return dr.get('time_plan')

    def _slots_between(self, a, b):
        """住所 a から b まで、グリッドいくつぶんか(符号つき)。

        小節ごとに割る数が違うので引き算では出せない。小節の境目で
        その小節の数を足しながら数える。"""
        (m0, s0), (m1, s1) = a, b
        if (m0, s0) == (m1, s1):
            return 0
        sign = 1
        if (m1, s1) < (m0, s0):
            (m0, s0), (m1, s1) = (m1, s1), (m0, s0)
            sign = -1
        n = -s0
        for m in range(m0, m1):
            n += self._measure_grid(m)
        return sign * (n + s1)

    def _shift_addr(self, addr, delta):
        """(小節, スロット) を delta グリッドぶんずらす。"""
        m, s = addr[0], addr[1]
        step = 1 if delta > 0 else -1
        for _ in range(abs(int(delta))):
            s += step
            if s >= self._measure_grid(m):
                m, s = m + 1, 0
            elif s < 0:
                m = max(0, m - 1)
                s = self._measure_grid(m) - 1
        return (m, s)

    def _draw_span_selection(self, p, rows):
        """両端がそろって選ばれている帯を、1つの枠で囲む。囲んだ鍵を返す。

        ゴーゴー・小節線の帯と、連打・風船(頭と終端)が対象。端を別々に
        囲むと、長い帯では緑の四角が離れた2つに見えて分かりにくかった
        (利用者の指定 2026-10-08)。"""
        done = set()
        for k0, k1, row, st, e, detail in self._selected_spans():
            if row != "note":
                rect = rows.get(row)
                if rect is None:
                    continue
                names = detail
                x0 = self._sec_to_x(st) + self._drag_dx_for(names[0], st, snap=True)
                x1 = self._sec_to_x(e) + self._drag_dx_for(names[1], e, snap=True)
                ry, rh = rect
                p.drawRect(int(min(x0, x1)) - 4, ry + 2,
                           int(abs(x1 - x0)) + 8, rh - 4)
            else:
                half = self.SPAN_TH_BIG // 2 if detail == "roll_big" else self.SPAN_TH // 2
                v = self._object_vrange("note", half)
                if v is None:
                    continue
                dx0 = dx1 = 0
                if self._note_drag is not None:
                    if self._drag_tail():
                        dx1 = int(self._drag_dx())
                    else:
                        dx0 = dx1 = int(self._drag_dx())
                x0 = self._sec_to_x(st) + dx0
                x1 = self._sec_to_x(e) + dx1
                p.drawRect(int(min(x0, x1)) - half - 3, v[0],
                           int(abs(x1 - x0)) + 2 * half + 6, v[1] - v[0])
            done.update((k0, k1))
        return done

    def _draw_selection(self, p, note_cy):
        """選んだオブジェクトを1つずつ枠で囲む(エクスプローラーの選択と同じ
        考え方で、範囲ではなく「選ばれているもの」を示す)。"""
        if not self._sel and self._band is None:
            return
        rows = self._row_rects()
        r, g, b = self.RANGE_COLOR
        if self._sel:
            p.setPen(QPen(QColor(r, g, b, 235), 2))
            # 両端がそろって選ばれている帯は、端を2つ囲むのではなく**帯ぜんたい**
            # を1つの枠で囲む(利用者の指定 2026-10-08: 端だけ緑だと見にくい)。
            done = self._draw_span_selection(p, rows)
            for key, orow, t, half in self._selected_objects(self._sel):
                if key in done:
                    continue
                rect = rows.get(orow)
                if rect is None:
                    continue
                x = self._sec_to_x(t)
                x0, x1 = self._object_hrange(orow, t, half)
                # つかんで動かしている間は、枠も一緒に付いてくる。
                dr = self._note_drag
                if dr is not None and key in self._sel:
                    if orow == "gogo":
                        dx = self._drag_dx_for(key[1], t, snap=True)
                    elif self._drag_edge_name() is not None:
                        dx = 0      # 端をつかんでいる間は帯の端だけが動く
                    else:
                        positions = self._drag_time_positions()
                        if positions:
                            old = min(positions, key=lambda p: abs(p - key[-1]))
                            dx = self._sec_to_x(self._time_of_pos(positions[old])) - self._sec_to_x(t)
                        else:
                            dx = int(self._drag_dx())
                    x0 += dx
                    x1 += dx
                if orow == "note":
                    cy = note_cy
                    hh = half + 3
                    p.drawRect(x0 - 3, cy - hh, (x1 - x0) + 6, 2 * hh)
                else:
                    ry, rh = rect
                    p.drawRect(x0, ry + 2, x1 - x0, rh - 4)
                    # 命令が詰まっている所では、細い四角だけではどれを
                    # 選んだのか見えない(利用者の報告 2026-11-05:
                    # SCROLL が大量に並ぶ譜面)。その命令の縦線そのものを
                    # 太く塗って、行の上に三角の印を出す。
                    self._draw_cmd_pick(p, x0 + 2, ry, rh)
                    p.setPen(QPen(QColor(r, g, b, 235), 2))
        if self._band is not None:
            bd = self._band
            lo_x, hi_x = sorted((self._band_x0(bd), bd["x1"]))
            lo_y, hi_y = sorted((bd["y0"], bd["y1"]))
            p.fillRect(int(lo_x), int(lo_y), int(hi_x - lo_x), int(hi_y - lo_y),
                       QColor(r, g, b, 30))
            pen = QPen(QColor(r, g, b, 210), 1)
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawRect(int(lo_x), int(lo_y), int(hi_x - lo_x), int(hi_y - lo_y))

    #: 選んだ命令を指す印。縦線の太さと、行の上に出す三角の大きさ(px)。
    CMD_PICK_W = 3
    CMD_PICK_TRI = 5

    def _cmd_next_x(self, kind, t, x0):
        """同じ行の次の命令の1つ手前の x。次が無ければ上限なし。"""
        times = (self._cmd_kind_times or {}).get(kind) or []
        i = bisect.bisect_right(times, t + 1e-9)
        if i >= len(times):
            return float("inf")
        return max(x0 + 3, self._sec_to_x(times[i]) - 1)

    def _draw_cmd_pick(self, p, x, ry, rh):
        """選んだ命令の位置に、太い縦線と三角の印を出す。

        命令が 1px 間隔で並ぶ譜面でも「どれを選んでいるか」が見えるように
        するためのもの(利用者の報告 2026-11-05)。札の文字は細い縦線で
        位置を示すだけなので、選んだ印も**その線の所**へ重ねる。"""
        r, g, b = self.RANGE_COLOR
        col = QColor(r, g, b, 235)
        x = int(x)
        w = self.spx(self.CMD_PICK_W)
        p.fillRect(x - w // 2, int(ry) + 2, max(1, w), int(rh) - 4, col)
        s = self.spx(self.CMD_PICK_TRI)
        tri = QPolygon([QPoint(x - s, int(ry)), QPoint(x + s, int(ry)),
                        QPoint(x, int(ry) + s)])
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(col))
        p.drawPolygon(tri)
        p.setBrush(Qt.NoBrush)

    def _draw_note_drag(self, p, top, strip, note_cy=None):
        """つかんで動かしている最中の見せ方。選んだものを掴んだぶんだけ横へ
        ずらして薄く描く(置くのはグリッドだが、動きは滑らかに)。"""
        dr = self._note_drag
        if dr is None or not self._sel:
            return
        dx = int(self._drag_dx())
        # 帯の端をつかんでいる間は、動くのは同じ側の端だけ(_drag_edge_name)。
        move_keys = self._drag_keys()
        rows = self._row_rects()
        r, g, b = self.RANGE_COLOR
        cy = note_cy if note_cy is not None else top + strip // 2
        steps = self._drag_delta_slots(self._drag_dx(), dr.get("anchor"))
        for key, orow, t, half in self._selected_objects(move_keys):
            x = self._sec_to_x(t) + dx
            positions = self._drag_time_positions()
            if positions:
                old = min(positions, key=lambda p: abs(p - key[-1]))
                x = self._sec_to_x(self._time_of_pos(positions[old]))
            if orow == "note":
                p.setOpacity(0.75)
                c = self._char_at_time(t)
                pix = self._note_pixmap(
                    QColor(self._pal["don"] if c in ("1", "3") else self._pal["ka"]),
                    self.NOTE_R_BIG if c in ("3", "4") else self.NOTE_R)
                p.drawPixmap(x - pix.width() // 2, cy - pix.height() // 2, pix)
                p.setOpacity(1.0)
            elif orow in ("bpm", "measure", "hs"):
                rect = rows.get(orow)
                if rect is None:
                    continue
                ry, rh = rect
                p.fillRect(x - 2, ry + 2, half * 3, rh - 4, QColor(r, g, b, 70))
            # 置かれる位置(グリッド)に細い線。離したときの行き先と同じ
            # 歩き方で出す — 以前はカーソルのいる分割数で割った分数を
            # 足していたので、4/4 以外の小節では線が実際の落ち先とずれた。
            # 音符は目盛へ丸める(利用者の指定 2026-10-05)。
            positions = self._drag_time_positions()
            if positions:
                old = min(positions, key=lambda p: abs(p - key[-1]))
                gt = self._time_of_pos(positions[old])
            elif positions is not None:
                gt = None
            else:
                gt = self._time_of_pos(
                    self._shift_pos(key[-1], steps, snap=key[0] != "cmd"))
            if gt is not None:
                p.setPen(QPen(QColor(255, 210, 60, 180), 1))
                gx = self._sec_to_x(gt)
                p.drawLine(gx, self.RULER_H, gx, self.height())

    def _char_at_time(self, t):
        """その時刻の音符の文字(うつしを描くときの色に使う)。"""
        for tt, c in (self._note_audio or []):
            if abs(tt - t) < 1e-6:
                return c
        return "1"

    _row_rects_cache = None

    def _row_rects(self):
        """{種類: (上端 y, 高さ)}。音符の行が残りを全部取る。

        高さが変わるまで同じものを返す(**中身を書き換えないこと**)。1コマの
        あいだに、描画・当たり判定・ボタンの位置決めから十数回呼ばれるので、
        そのたびに辞書を組み立て直すのはもったいない。"""
        h_now = self.height()
        got = self._row_rects_cache
        if got is not None and got[0] == h_now:
            return got[1]
        fixed = sum(h for _k, _n, h in self.ROWS)
        note_h = max(self.spx(40), h_now - self.RULER_H - fixed)
        out = {}
        y = self.RULER_H
        for kind, _name, h in self.ROWS:
            hh = note_h if kind == "note" else h
            out[kind] = (y, hh)
            y += hh
        self._row_rects_cache = (h_now, out)
        return out

    def _strip_rects(self):
        """音符の行を「譜面帯」として返す(親の描画・既存の処理と同じ形)。"""
        top, h = self._row_rects()["note"]
        return (top, top, h, top + h, 0)

    def paint_pane(self, p):
        # 描き先は自分のウィジェットのことも、ゲーム画面の GPU の面のことも
        # ある(WaveformWidget.set_drawn_by を参照)。どちらでも同じ絵になる。
        self._last_paint_wall = time.monotonic()
        self._paint_rows(p)

    # ------------------------------------------------------------------
    # 時間の関数でしかない絵は、幅の広い帯に焼いてずらして貼る
    # ------------------------------------------------------------------
    # 波形・目盛り・小節線・編集グリッド・ゴーゴーの薄い色・行の地は、どれも
    # 「いつからいつまでを映しているか」だけで決まる。再生中はその窓が右へ
    # 少しずつ動くだけなので、毎コマ描き直すのはまるごとむだになる。
    #
    # 実測(1280x300 の1コマ 2.10ms の内訳):
    #   波形 0.44 / 目盛り 0.35 / グリッド 0.33 / 小節線 0.03 / 地の塗り 約0.4
    # 合わせて 1.5ms ぶんが、帯からの貼り付け1回(約0.1ms)に変わる。
    #
    # 画面の STRIP_FACTOR 倍の幅で焼いておき、窓が端まで流れたら焼き直す。
    # 焼き直しは1回ぶんの描画より少し重いだけで、数秒に1回しか起きない。
    STRIP_FACTOR = 2.5
    #: 焼く帯の幅の上限(px)。極端に広い窓・細かい表示でも取り過ぎないように。
    STRIP_MAX_PX = 9000
    _strip_pm = None
    _strip_t0 = 0.0
    _strip_span = 0.0
    _strip_key = None
    #: 譜面の中身が変わったら数を増やす。帯の焼き直しの合図(_bump_strip)。
    _strip_rev = 0

    def _bump_strip(self):
        """帯に焼いた中身(小節線・波形・ゴーゴー等)が変わったことを知らせる。"""
        self._strip_rev += 1
        self._strip_pm = None

    def _strip_key_now(self):
        pal = self._pal
        return (self.width(), self.height(), self.LANE_X0,
                self.devicePixelRatioF(), round(self._visible_span(), 6),
                # グリッドは表示範囲の全小節に引くので、再生位置に依存しない。
                # ドラッグ開始・終了で範囲命令を静的/動的描画に切り替える。
                self._note_drag is not None, self._lyric_drag is not None,
                self._grid, self._strip_rev, self._wave_gain,
                self._show_legend, pal.get("bg"), pal.get("bg2"),
                pal.get("border"), pal.get("fg"),
                # 波形そのものが差し替わったとき(曲を開き直した等)も焼き直す。
                id(self.mips), round(float(self.duration or 0.0), 3))

    def _static_strip(self):
        """いまの表示に使える帯 (帯, 左端の時刻)。作れないときは None。"""
        span = self._visible_span()
        if span <= 0 or self.height() < 8:
            return None
        self._sec_to_x(0.0)                 # 1秒あたりの px を最新にする
        xs = self._xs_val
        if xs <= 0:
            return None
        t0 = self.view_start
        key = self._strip_key_now()
        if (self._strip_pm is not None and self._strip_key == key
                and self._strip_t0 <= t0
                and t0 + span <= self._strip_t0 + self._strip_span):
            return self._strip_pm, self._strip_t0
        strip_px = int(span * self.STRIP_FACTOR * xs) + 2
        if strip_px > self.STRIP_MAX_PX:
            strip_px = self.STRIP_MAX_PX
        strip_span = strip_px / xs
        # 少し後ろ(左)にも余裕を持たせる。巻き戻しでも焼き直さずに済む。
        base = max(0.0, t0 - span * 0.5)
        dpr = self.devicePixelRatioF()
        img_w = self.LANE_X0 + strip_px
        pm = QPixmap(max(1, int(img_w * dpr)), max(1, int(self.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(self._pal["bg2"]))
        q = QPainter(pm)
        old_view = self.view_start
        self.view_start = base              # 帯の中では左端が base 時刻
        try:
            self._paint_static(q, img_w, base, base + strip_span)
        finally:
            self.view_start = old_view
            q.end()
        self._strip_pm = pm
        self._strip_t0 = base
        self._strip_span = strip_span
        self._strip_key = key
        return pm, base

    def _draw_region_tracks(self, p, rows, t0, t1):
        """通常は帯に保存し、ドラッグ中だけ直接描く範囲命令。"""
        # 小節線は帯ではなく、ON/OFF の札(スクロールと同じ見せ方。利用者の
        # 指定 2026-09-26)。
        if self._region_audio is not None:
            # 小節線を隠している区間も帯で見せる(ゴーゴーと同じ読み方)。
            # 字をやめたので、隠れているかどうかは帯の有無で読む。
            self._draw_span_row(p, rows["barline"], self._barline_audio,
                                self.BARLINE_BAND_COLOR, t0, t1,
                                ("BARLINEOFF", "BARLINEON"))
            self._draw_cmd_marks(p, rows["barline"], "barline", t0, t1)
        else:
            self._draw_marker_row(p, rows["barline"], self._barline_audio,
                                  ("BARLINEOFF", "BARLINEON"), ("OFF", "ON"),
                                  t0, t1)
        # GOGO は帯のまま(長さが目で分かるように)。端をつかんでいる間は、
        # その端が付いてきて帯が伸び縮みする。
        self._draw_span_row(p, rows["gogo"], self._gogo_audio,
                            self.ROW_CONTENT_COLOR, t0, t1)
        # 帯の上に、#GOGOSTART / #GOGOEND を**1本ずつ**札で出す。帯だけだと
        # 重なった2本目が見えず、選べない=消せなかった(利用者の報告
        # 2026-11-05)。
        if self._region_audio is not None:
            self._draw_cmd_marks(p, rows["gogo"], "gogo", t0, t1)

    def _paint_static(self, p, w, t0, t1):
        """時間の関数でしかない絵。帯へ焼くときも、直に描くときも同じ道。

        w は描き先の幅(帯のときは画面より広い)。呼ぶ側が self.view_start を
        t0 に合わせておくこと。

        グリッドは全表示小節に共通なので保存できる。音符は再生位置で変わる
        ため直接描く。範囲命令はドラッグ中だけ直接描き、端の動きに追従する。
        """
        pal = self._pal
        rows = self._row_rects()
        note_top, note_h = rows["note"]
        lane_w = max(1, w - self.LANE_X0)
        p.fillRect(self.LANE_X0, note_top, lane_w, note_h, QColor(pal["bg"]))
        # 波形は行の下側へ寄せる(利用者の指定 2026-09-25)。音符と重ねると
        # 丸に隠れてしまうので、音符は上、波形は下。
        wave_h = int(note_h * self.WAVE_FRAC)
        wave_top = note_top + note_h - wave_h
        mips = self.mips
        if mips and not mips.is_empty():
            self._draw_lane(p, mips.MIX, wave_top, wave_h, t0, t1, lane_w, None)
        # ゴーゴーは専用の行に帯で出すので、音符の行は薄く色を敷くだけにする
        # (行が高くなったぶん、前と同じ濃さだと真っ赤に見える)。
        for s, e in (self._gogo_audio or []):
            if e < t0 or s > t1:
                continue
            xs, xe = self._sec_to_x(s), self._sec_to_x(e)
            p.fillRect(xs, note_top, max(1, xe - xs), note_h,
                       QColor(255, 120, 120, 18))
        self._draw_measure_lines(p, note_top, note_h, t0, t1)
        self._draw_edit_grid(p, note_top, note_h - wave_h, t0, t1)
        if self._note_drag is None:
            self._draw_region_tracks(p, rows, t0, t1)
        # 命令の行(BPM・拍子・スクロール・歌詞)も時間の関数。右端は帯の
        # 幅で見る。
        rows = self._row_rects()
        for kind in ("bpm", "measure", "hs", "lyric"):
            y, rh = rows[kind]
            if kind == "lyric":
                if self._lyric_drag is None:
                    self._draw_lyric_bands(p, y, rh, t0, t1)
            else:
                self._draw_cmd_row(p, kind, y, rh, t0, t1, right=w)
        self._draw_ruler(p, t0, t1)

    #: 描いているあいだの**本当の**表示の左端。帯に合わせて view_start を
    #: ずらすので、掴んだぶんの px を出す _drag_dx がそちらを読むと、
    #: ずらした秒数ぶん行き先が飛ぶ(利用者の報告 2026-10-09:
    #: 「GOGOや連打を選択して長さを変えようとすると位置がおかしい」)。
    _paint_view0 = None

    def _paint_rows(self, p):
        saved_view = self.view_start
        self._paint_view0 = saved_view
        try:
            self._paint_rows_inner(p)
        finally:
            self._paint_view0 = None
            # 帯に合わせて起点をずらしていることがある(下を参照)。描き終えたら
            # 必ず戻す — マウスの座標変換も同じ式を通るため。
            self.view_start = saved_view
            self._x_shift = 0

    def _paint_rows_inner(self, p):
        self._check_theme()
        w, h = self.width(), self.height()
        rows = self._row_rects()
        t0 = self.view_start
        t1 = t0 + self._visible_span()
        pal = self._pal

        note_top, note_h = rows["note"]
        wave_h = int(note_h * self.WAVE_FRAC)
        note_cy = note_top + (note_h - wave_h) // 2
        upper_h = note_h - wave_h          # 音符が並ぶ側(波形の上)
        strip = self._static_strip()
        if strip is None:                  # 帯が作れないときは直に描く
            p.fillRect(self.rect(), QColor(pal["bg2"]))
            self._paint_static(p, w, t0, t1)
            strip_off = None
        else:
            pm, base = strip
            strip_off = int((t0 - base) * self._xs_val)
            p.drawPixmap(-strip_off, 0, pm)
            # 動くもの(音符・命令の札・再生位置の線)も、**帯とまったく同じ
            # 式で** x を出す。帯の中では x = LANE_X0 + int((t-base)*xs) で、
            # それを strip_off だけ左へ貼っているので、こちらも起点を base に
            # して同じぶん引く(_x_shift)。合わせないと、同じ時刻でも丸めの
            # 違いで小節線と音符が 1px 食い違う(実測: 小節の境目で起きた)。
            self.view_start = base
            self._x_shift = strip_off
            t0 = base + strip_off / self._xs_val
            t1 = t0 + self._visible_span()
        # ここから下は目盛りの帯(上の RULER_H px)へは描かない。以前は描いて
        # しまってから帯をもう一度貼って消していたが、そのために幅 3000px の
        # 帯を1コマに2回貼ることになっていた。先に切っておけば1回で済む。
        p.setClipRect(0, self.RULER_H, w, h - self.RULER_H)
        # 音符だけを直接描く。グリッドとの座標丸めは上の帯に揃える。
        self._draw_notes(p, self._lane_w(), t0, t1, note_cy)
        self._draw_long_preview(p, note_top, upper_h)
        self._draw_pending(p, note_top, upper_h)
        if self._lyric_drag is not None:
            self._draw_lyric_bands(p, *rows["lyric"], t0, t1)
        # 頭が左へ外れた歌詞の帯にも字を出す(_draw_lyric_sticky)。
        self._draw_lyric_sticky(p, *rows["lyric"], t0, t1)
        if self._note_drag is not None:
            self._draw_region_tracks(p, rows, t0, t1)

        # --- 行の区切り線 ---
        p.setPen(QPen(QColor(pal["border"])))
        for kind, _name, _h in self.ROWS:
            y, rh = rows[kind]
            p.drawLine(0, y, w, y)
        p.drawLine(0, self.RULER_H, w, self.RULER_H)

        # --- 範囲の箱は全部の行にかける(命令も一緒に選べる) ---
        rows_top, rows_h = self.RULER_H, h - self.RULER_H
        note_cy = note_top + (note_h - wave_h) // 2
        self._draw_selection(p, note_cy)
        self._draw_note_drag(p, rows_top, rows_h, note_cy)

        # --- 合作: 相手のカーソル(自分の線より下に敷く) ---
        self._draw_peer_marks(p, h)

        # --- 再生位置の線は全部の行を貫く ---
        xp = self._sec_to_x(self.position_sec)
        p.setPen(QPen(QColor(pal["err"]), 2))
        p.drawLine(xp, self.RULER_H, xp, h)
        # 黄色いカーソルは出さない(利用者の指定 2026-09-25)。停止中は赤い線が
        # カーソルの位置に貼り付いているので、それで足りる。

        p.setClipping(False)
        # --- 目盛りと左の列は最後(左の列は目盛りの帯にも掛かる) ---
        if strip_off is None:
            self._draw_ruler(p, t0, t1)
        self._draw_measure_sel(p, h)
        self._draw_row_labels(p, rows)
        # 赤い線が画面の外にあるときは、その側の端に印(ホイールが景色だけを
        # 動かすようになったため。利用者の指定 2026-11-05)。左の行名の列に
        # 隠れないよう、列を描いたあとに、列より右へ出す。
        self._draw_offscreen_mark(p, w, self.RULER_H, h - self.RULER_H,
                                  t0, t1, x0=self.LANE_X0)
        self._draw_offset_mode(p, w, h)

    # ------------------------------------------------------------------
    # 合作: 相手のカーソル(利用者の要望 2026-10-16)
    #
    # 「ここ見て」が通じるようにするためだけの飾りなので、**譜面には一切
    # 触らない**。外(main_window)が置いていった印を描くだけ。
    # ------------------------------------------------------------------
    #: [(名前, 譜面時刻, 色)]。合作していなければ空。
    _peer_marks = ()

    def set_peer_marks(self, marks):
        """相手のカーソルを置き直す。中身が同じなら塗り直さない。"""
        marks = tuple(marks or ())
        if marks == self._peer_marks:
            return
        self._peer_marks = marks
        self.update()

    def _draw_peer_marks(self, p, h):
        if not self._peer_marks:
            return
        f = p.font()
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1.0))
        p.setFont(f)
        fm = p.fontMetrics()
        for name, t, color in self._peer_marks:
            x = self._sec_to_x(float(t))
            col = QColor(color)
            left, right = self.LANE_X0, self.width()
            # **画面の外に居るときは端へ寄せて矢印を出す。** 消してしまうと
            # 「ここ見て」と言われてもどちらへ送ればいいか分からない
            # (利用者の要望 2026-10-16)。
            out = "" if left <= x <= right else ("←" if x < left else "→")
            if not out:
                p.setPen(QPen(col, 2, Qt.DashLine))
                p.drawLine(x, self.RULER_H, x, h)
            label = (out + str(name)) if out else str(name)
            tw = fm.horizontalAdvance(label) + 8
            th = fm.height() + 2
            if out == "←":
                lx = left + 2
            elif out == "→":
                lx = right - tw - 2
            else:
                lx = x + 3
                if lx + tw > right:
                    lx = x - 3 - tw
            ly = self.RULER_H + 2
            if out:
                col.setAlpha(150)       # 外に居るぶんは控えめに
            p.fillRect(lx, ly, tw, th, col)
            p.setPen(QColor("#10141d"))
            p.drawText(lx + 4, ly + fm.ascent() + 1, label)

    def _draw_offset_mode(self, p, w, h):
        """OFFSET調整中の見た目。音声波形ペインと同じ「強調色の枠 + 読み値」。

        作譜ペインは親の paintEvent を使わず自分で並べ直しているので、この
        2つが抜け落ちていた。合わせている最中に何も変わらないように見えて、
        作譜モードでは OFFSET を合わせられない、という状態になっていた。"""
        if not self.offset_mode:
            return
        p.setPen(self._pen("accent2", 2))
        p.drawRect(1, 1, w - 2, h - 2)
        if self._readout:
            p.setPen(QColor(self._pal["bg"]))
            p.fillRect(6, h - 24, 240, 18, QColor(self._pal["accent2"]))
            p.drawText(10, h - 10, self._readout)

    #: 選んだ小節を覆う色。薄い帯で覆うだけ(利用者と決めた見せ方)。
    MEASURE_SEL_COLOR = "#ffd23c"

    def _draw_measure_sel(self, p, h):
        """上の目盛りで選んだ小節を、薄い帯で覆って見せる。

        **範囲を切るのは時刻ではなく画面の x。** 焼いた帯を貼っている間は
        view_start が帯の起点(半画面ぶん左)に差し替わっているので、それを
        画面の左端だと思って時刻で切ると、画面の右半分にある小節が「外」と
        見なされて描かれなかった(利用者の報告 2026-11-06「左に送ったら
        選択した表示になった」)。_sec_to_x は帯のずれ(_x_shift)を織り込んで
        あるので、x で比べれば貼り方に関係なく正しい。"""
        if not self._sel_measures:
            return
        if self._visible_span() <= 0:
            return
        col = QColor(self.MEASURE_SEL_COLOR)
        fill = QColor(col)
        fill.setAlpha(38)
        edge = QColor(col)
        edge.setAlpha(170)
        band = QColor(col)
        band.setAlpha(90)
        right = self.width()
        p.save()
        p.setClipRect(self.LANE_X0, 0, max(0, right - self.LANE_X0), h)
        for m in sorted(self._sel_measures):
            a = self._bar_time(m)
            if a is None:
                continue
            b = self._bar_time(m + 1)
            if b is None or b <= a:
                b = a + self._measure_len()
            x0 = self._sec_to_x(a)
            x1 = self._sec_to_x(b)
            if x1 < self.LANE_X0 or x0 > right:
                continue                    # 画面の外
            w = max(1, int(x1 - x0))
            p.fillRect(int(x0), self.RULER_H, w, h - self.RULER_H, fill)
            p.fillRect(int(x0), 0, w, self.RULER_H, band)
            p.setPen(QPen(edge, 2))
            p.drawLine(int(x0), 0, int(x0), h)
            p.drawLine(int(x1), 0, int(x1), h)
        p.restore()

    def _draw_row_labels(self, p, rows):
        """左の列に行の名前を並べる。

        名前と区切り線は動かないので1枚に焼いて使い回す(高さ・テーマ・行の
        並びが変わったときだけ焼き直す)。変わるのは左上の時刻と分割だけ。"""
        pal = self._pal
        h = self.height()
        key = (self.LANE_X0, h, pal.get("bg2"), pal.get("fg"), pal.get("border"),
               self.devicePixelRatioF(), tuple(rows.items()))
        if self._labels_key != key or self._labels_pm is None:
            dpr = self.devicePixelRatioF()
            # 右端の縦の区切り線(x = LANE_X0)まで入れるので 1px 広く焼く。
            pm = QPixmap(max(1, int((self.LANE_X0 + 1) * dpr)),
                         max(1, int(h * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(QColor(pal["bg2"]))
            q = QPainter(pm)
            try:
                self._paint_row_labels_static(q, rows, h)
            finally:
                q.end()
            self._labels_pm = pm
            self._labels_key = key
        p.drawPixmap(0, 0, self._labels_pm)
        if self._show_legend:
            self._draw_legend(p)

    # ------------------------------------------------------------------
    # 左上の「いまの時刻」と「グリッドの分割」(本家と同じ場所)
    # ------------------------------------------------------------------
    # 文字の組み立ては高い。毎コマ書き直していた頃は、この2つだけで
    # 1コマ 0.09ms(実測。作譜モードの 396 -> 431fps ぶん)かかっていた。
    #
    # 1枚に焼いて貼るだけにして、**書き換えるのは毎秒 LEGEND_FPS 回まで**。
    # 時刻は 1/1000 秒まで出るので毎コマ変わるが、毎秒 400 回書き換えたところで
    # 目には読めない(むしろ 20 回のほうが読める)。
    LEGEND_FPS = 20
    _legend_pm = None
    _legend_key = None
    _legend_at = 0.0

    #: 左上の「1/16」の当たり判定(凡例の描き位置に合わせてある)。
    GRID_LABEL_X = 80
    GRID_LABEL_W = 46

    def grid_label_rect(self):
        """「1/16」の当たり判定。凡例を出していないときは無効(空)。"""
        if not self._show_legend:
            return QRect()
        return QRect(self.GRID_LABEL_X, 0, self.GRID_LABEL_W, self.RULER_H)

    def set_grid(self, grid):
        """分割を変える。カーソルの時刻上の位置はできるだけ保つ。"""
        if self._long is not None:
            return
        grid = int(grid)
        if grid <= 0 or grid == self._grid:
            return
        g0 = self._measure_grid(self._cur_measure)
        frac = self._cur_slot / g0 if g0 else 0.0
        free = self._free_time
        self._grid = grid
        g1 = self._measure_grid(self._cur_measure)
        self._cur_slot = min(int(round(frac * g1)), g1 - 1)
        self._clamp_cursor()
        self.update()
        # 鳴っているあいだはシークしない。分割を変えるたびに再生位置が
        # 少し巻き戻っていた(利用者の報告 2026-11-02)。再生中のカーソルは
        # 再生位置に付いていくので、ここで合わせ直す必要もない。
        if self._playing:
            return
        if free is not None:
            # 助走の中に置いた線は、新しい分割の伸ばしたグリッドへ合わせ直す
            # (古い分割の目盛りのままだと、→ で歩いても頭に乗らない)。
            self._lead_seek(free)
            return
        # **赤い線も動かす。** 1/16 → 1/24 のように割り切れない分割へ
        # 変えるとカーソルの時刻が少しずれるのに、線を引き直していな
        # かったので、線の位置と打った音符の位置が食い違っていた
        # (利用者の報告 2026-10-31)。
        self._cursor_changed()

    def open_grid_picker(self):
        """左上の「1/16」を押したときに出す、分割のつまみ。"""
        picker = _GridPicker(self, GRID_CHOICES, self._grid, self.set_grid)
        r = self.grid_label_rect()
        picker.adjustSize()
        picker.move(self.mapToGlobal(QPoint(max(0, r.left() - 6), r.bottom() + 2)))
        picker.show()
        self._grid_picker = picker
        return picker

    _grid_picker = None

    def _draw_legend(self, p):
        pal = self._pal
        lr, lg, lb = GRID_COLORS.get(self._grid, (255, 210, 60))
        dpr = self.devicePixelRatioF()
        key = (self._time_text(self.position_sec), self._grid,
               pal.get("fg_dim"), (lr, lg, lb), self.RULER_H, self.LANE_X0, dpr)
        now = time.monotonic()
        # 分割が変わったときだけは間引かずにすぐ描き直す。続けて変えると
        # 間引きに当たって古い数字が残り、1/48 を飛ばしたように見えた
        # (利用者の報告 2026-11-13)。時刻の文字は毎フレーム変わるので
        # 間引いたままにする。
        grid_changed = (self._legend_key is None
                        or self._legend_key[1] != self._grid)
        if self._legend_pm is None or grid_changed or (
                key != self._legend_key
                and now - self._legend_at >= 1.0 / self.LEGEND_FPS):
            w, h = self.LANE_X0, self.RULER_H
            pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            try:
                f = self.font()
                f.setPixelSize(self.fpx(11))
                q.setFont(f)
                q.setPen(QColor(pal["fg_dim"]))
                q.drawText(10, 0, 70, h, Qt.AlignVCenter | Qt.AlignLeft, key[0])
                q.setPen(QColor(lr, lg, lb))
                q.drawText(84, 0, 40, h, Qt.AlignVCenter | Qt.AlignLeft,
                           "1/%d" % self._grid)
            finally:
                q.end()
            self._legend_pm = pm
            self._legend_key = key
            self._legend_at = now
        p.drawPixmap(0, 0, self._legend_pm)

    # ------------------------------------------------------------------
    # 譜面分岐(いまどの系統を編集しているか)
    # ------------------------------------------------------------------
    # 系統の字はゲーム画面のレーンに出るので、ここ(行名の列)には出さない
    # (利用者の指定 2026-09-27。同じ字が2か所に出て煩いため)。どの系統を
    # 編集しているかは命令パネルの「譜面分岐」で分かる。ここで覚えるのは、
    # 今後この行の見せ方を変えるときのため。
    _branch_level = None
    _has_branches = False

    def set_branch(self, level, has_branches):
        """いま見て(編集して)いる系統を控える。"""
        level = level if level in ("N", "E", "M") else None
        has_branches = bool(has_branches)
        if (level, has_branches) == (self._branch_level, self._has_branches):
            return
        self._branch_level = level
        self._has_branches = has_branches

    def _paint_row_labels_static(self, p, rows, h):
        """行名の列の、動かない部分(名前・区切り線)。焼き付け用。"""
        pal = self._pal
        f = self.font()
        f.setPixelSize(self.fpx(12))
        p.setFont(f)
        for kind, name, _h in self.ROWS:
            y, rh = rows[kind]
            # 行の名前は全部同じ色。種類ごとに色を振ると、目に入る情報が増えて
            # かえって読みにくい(利用者の指定 2026-09-25)。
            p.setPen(QColor(pal["fg"]))
            p.drawText(10, y, self.LANE_X0 - 16, rh,
                       Qt.AlignVCenter | Qt.AlignLeft, name)
            p.setPen(QPen(QColor(pal["border"])))
            p.drawLine(0, y, self.LANE_X0, y)
        p.setPen(QPen(QColor(pal["border"])))
        p.drawLine(self.LANE_X0, 0, self.LANE_X0, h)

    @staticmethod
    def _time_text(t):
        t = max(0.0, float(t))
        return "%02d:%06.3f" % (int(t // 60), t % 60)

    #: 定規の目盛りどうしの間隔の下限(px)。譜面帯の小節線と同じ 3px。
    RULER_MIN_GAP = 3.0
    #: 小節番号を出すのに要る幅(px)。これより狭いと、番号は1画素ぶんの
    #: 切れ端しか出ず(clipRect で切るので)読めないうえ、その切れ端のために
    #: 小節ごとに drawText を1回払うことになる。
    RULER_NUM_MIN_ROOM = 10

    def _draw_ruler(self, p, t0, t1):
        """上の帯に小節番号と時刻を出す。

        小節が詰まっているところは、譜面帯の小節線と同じく間引く
        (_draw_measure_lines を参照。落とす目盛りは bisect で跳ばす)。
        番号も、読める幅が無いときは出さない。"""
        pal = self._pal
        p.fillRect(0, 0, self.width(), self.RULER_H, QColor(pal["bg2"]))
        f = self.font()
        f.setPixelSize(self.fpx(11))
        p.setFont(f)
        lo, hi = self._measure_range(t0, t1)
        self._sec_to_x(t0)                 # 1秒あたりの px を最新にする
        dt = (self.RULER_MIN_GAP / self._xs_val) if self._xs_val > 0 else 0.0
        bars = self._bar_times
        known = self._known_measures()
        pen_line = QPen(QColor(pal["border"]))
        col_fg = QColor(pal["fg"])
        col_dim = QColor(pal["fg_dim"])
        last_x = None
        m = lo
        while m < hi:
            t = self._bar_time(m)
            if t is None or t > t1:
                break
            if t < t0:
                m += 1
                continue
            x = self._sec_to_x(t)
            if last_x is None or (x - last_x) >= self.RULER_MIN_GAP:
                nxt = self._bar_time(m + 1)
                room = (self._sec_to_x(nxt) - x) if nxt is not None else 999
                p.setPen(pen_line)
                p.drawLine(x, 0, x, self.RULER_H)
                # 小節番号は入るときだけ、時刻はもっと空いているときだけ
                # (小節が詰まっているところで数字が重なって読めなくなるのを
                # 防ぐ)。
                if room >= self.RULER_NUM_MIN_ROOM:
                    p.setPen(col_fg)
                    p.setClipRect(x + 2, 0, max(1, room - 3), self.RULER_H)
                    p.drawText(x + self.spx(4), 0, self.spx(60), self.spx(12),
                               Qt.AlignVCenter | Qt.AlignLeft, str(m + 1))
                    if room >= self.spx(64):
                        p.setPen(col_dim)
                        f.setPixelSize(self.fpx(9))
                        p.setFont(f)
                        p.drawText(x + self.spx(4), self.spx(10),
                                   self.spx(70), self.spx(11),
                                   Qt.AlignVCenter | Qt.AlignLeft,
                                   self._time_text(t))
                        f.setPixelSize(self.fpx(11))
                        p.setFont(f)
                    p.setClipping(False)
                last_x = x
                if dt > 0.0 and m + 1 < known:
                    nm = bisect.bisect_left(bars, t + dt, m + 1, known)
                    if nm > m:
                        m = nm
                        continue
            m += 1

    def _draw_cmd_row(self, p, kind, y, rh, t0, t1, right=None):
        """BPM / 拍子 / スクロールの行。その種類の命令だけを時間順に並べる。

        詰まっていても間引かない(利用者の指定 2026-09-25)。細い縦線でその位置を
        示し、文字は次の命令の手前までで切る — 本家 PeepoDrumKit と同じで、
        値が連続で変わっていく場所でも「何が並んでいるか」が読める。"""
        items = (self._cmd_by_kind or {}).get(kind)
        if not items:
            return
        times = (self._cmd_kind_times or {}).get(kind) or []
        f = self.font()
        f.setPixelSize(self.fpx(11))
        p.setFont(f)
        col = QColor(self.ROW_CONTENT_COLOR)
        if right is None:
            right = self.width()
        lo = max(0, bisect.bisect_left(times, t0) - 1)
        # ペンは1本だけ作って使い回す。命令が 1px 間隔で並ぶ譜面(幸福な死を:
        # #SCROLL が 14395 行)では、ここで QPen を作り直すのが行の値段の大半
        # だった(1本あたり約 5μs × 数千本)。色は札の文字と同じなので、
        # 外で1回立てればそのまま両方に効く。
        pen = QPen(col, 1)
        p.setPen(pen)
        last_x = None
        for i in range(lo, len(items)):
            t, txt = items[i]
            if t > t1:
                break
            x = self._sec_to_x(t)
            if x > right:
                break
            nxt = items[i + 1][0] if i + 1 < len(items) else None
            xr = self._sec_to_x(nxt) if nxt is not None else x + 200
            if xr <= x + 2:
                xr = x + 2
            # 同じ画素に重なる線は1本でよい(間引きではない — 2本目を引いても
            # 1画素も変わらないので、そのぶんだけ省いている)。
            if x == last_x and xr - x <= 5:
                continue
            last_x = x
            p.drawLine(x, y + 1, x, y + rh - 1)
            if xr - x > 5:
                p.setClipRect(x + 2, y, max(1, xr - x - 3), rh)
                p.drawText(x + 3, y, xr - x, rh,
                           Qt.AlignVCenter | Qt.AlignLeft, txt)
                p.setClipping(False)

    def _drag_dx_for(self, name, t, snap=False):
        """その印をいまつかんで動かしているなら、動かした px。

        snap=True なら、そのままの px ではなく「置かれるグリッド」までの px を
        返す(GOGO の帯は伸びる長さが目で分かるよう、グリッドに乗せたまま
        伸び縮みさせる — 利用者の指定 2026-09-26)。"""
        dr = self._note_drag
        if dr is None:
            return 0
        pos = self._pos_of_time(t)
        if pos is None or ("cmd", name, pos) not in self._sel:
            return 0
        edge = self._drag_edge_name()
        if edge is not None and name != edge:
            return 0            # 端をつかんでいる間は、反対側の端は動かさない
        positions = self._drag_time_positions()
        if positions:
            old = min(positions, key=lambda p: abs(p - pos))
            return self._sec_to_x(self._time_of_pos(positions[old])) - self._sec_to_x(t)
        if not snap:
            return int(self._drag_dx())
        steps = self._drag_delta_slots(self._drag_dx(), dr.get("anchor"))
        gt = self._time_of_pos(
            pos + Fraction(int(steps), int(self._measure_grid(int(pos)))))
        if gt is None:
            return int(self._drag_dx())
        return self._sec_to_x(gt) - self._sec_to_x(t)

    def _draw_span_row(self, p, rect, spans, color, t0, t1,
                       names=("GOGOSTART", "GOGOEND")):
        """区間の帯。端をつかんでいる間は、その端だけが付いてくる。

        names は端の命令の名前(つかんで動かしているぶんを足すのに要る)。
        小節線の行にも同じ帯を出すので、行ごとに差し替えられるようにした。
        """
        y, rh = rect
        if not spans:
            return
        col = QColor(color)
        fill = QColor(col)
        fill.setAlpha(70)
        for s_t, e_t in spans:
            if e_t < t0 or s_t > t1:
                continue
            xs = self._sec_to_x(s_t) + self._drag_dx_for(names[0], s_t,
                                                        snap=True)
            xe = self._sec_to_x(e_t) + self._drag_dx_for(names[1], e_t,
                                                        snap=True)
            lo, hi = sorted((xs, xe))
            p.fillRect(lo, y + 3, max(2, hi - lo), rh - 6, fill)
            p.setPen(QPen(col, 1))
            p.drawRect(lo, y + 3, max(2, hi - lo), rh - 6)

    _row_font_cache = None
    _row_width_cache = None

    def _row_font(self):
        """行の札に使う 11px のフォント。族が変わるまで使い回す。"""
        key = self.font().family()
        got = self._row_font_cache
        if got is None or got[0] != key:
            f = self.font()
            f.setPixelSize(self.fpx(11))
            self._row_font_cache = (key, f)
            self._row_width_cache = {}     # 族が変われば幅も変わる
        return self._row_font_cache[1]

    def _row_text_widths(self, fm):
        if self._row_width_cache is None:
            self._row_width_cache = {}
        return self._row_width_cache

    def _draw_cmd_marks(self, p, rect, row, t0, t1):
        """範囲の命令を**1本ずつ**印で出す(_region_audio から)。

        **字は出さない。** 「開始/終了」と書いていたころは、短いゴーゴーが
        並ぶと字が隣へ逃げて壁になり、どこが区間なのかも読めなくなって
        いた(利用者の報告 2026-11-05「文字みたくない。情報量が増える」)。
        区間は帯の色で読み、ここは「命令がここに1本ある」ことだけを
        2px の縦線で示す。同じ所に2本あれば線も2本出るので、余分な1本を
        選んで消せる。
        """
        y, rh = rect
        marks = [(t, n) for t, n in (self._region_audio or [])
                 if self._CMD_ROW.get(n) == row and t0 <= t <= t1]
        if not marks:
            return
        p.setPen(QPen(QColor(self.ROW_CONTENT_COLOR), 2))
        for t, name in marks:
            x = self._sec_to_x(t) + self._drag_dx_for(name, t)
            p.drawLine(x, y + 2, x, y + rh - 2)

    def _draw_marker_row(self, p, rect, spans, names, labels, t0, t1):
        """小節線のような「ここから / ここまで」の印。札で出す。

        この行は毎コマ描く(帯に焼いていない — つかんで動かせるため)。
        フォントと文字幅は作り直さずに使い回す。字は "OFF"/"ON" の2種類しか
        無いのに、QFont の組み立てと horizontalAdvance を毎コマ払うと、
        印が数個しか無くてもこの行だけで 0.2ms かかっていた(実測)。"""
        y, rh = rect
        if not spans:
            return
        p.setFont(self._row_font())
        col = QColor(self.ROW_CONTENT_COLOR)
        pen = QPen(col, 1)
        p.setPen(pen)
        fm = p.fontMetrics()
        widths = self._row_text_widths(fm)
        for s_t, e_t in spans:
            for t, name, text in ((s_t, names[0], labels[0]),
                                  (e_t, names[1], labels[1])):
                if t < t0 or t > t1:
                    continue
                x = self._sec_to_x(t) + self._drag_dx_for(name, t)
                tw = widths.get(text)
                if tw is None:
                    tw = widths[text] = fm.horizontalAdvance(text)
                p.drawLine(x, y + 1, x, y + rh - 1)
                p.drawText(x + 3, y, tw + 6, rh,
                           Qt.AlignVCenter | Qt.AlignLeft, text)

    def _slot_style(self, k):
        """スロット k の線の色と長さ。

        4分(拍)にあたる位置は白い長い線、それ以外は今の分割の色で短い線。
        定規の「大きい目盛りと小さい目盛り」と同じ考え方。"""
        if self._grid > 0 and (k * 4) % self._grid == 0:
            return (BEAT_COLOR, BEAT_FRAC)
        return (GRID_COLORS.get(self._grid, GRID_COLORS[64]), SUB_FRAC)

    def _grid_measures(self):
        """グリッドの目盛りを引く小節の番号。None なら**見えている所ぜんぶ**。

        以前は「編集している小節と次の小節」だけに引いていた(画面いっぱい
        だと読みにくい、という指定)。前の小節にも引いてほしい、と言い直しが
        あったので、見えている範囲は全部引く(利用者の指定 2026-10-31
        「前の小節のグリッドも見える範囲で全部表示してほしい」)。

        細かすぎる所は描く側で間引くので(1グリッド 4px 未満は引かない)、
        密な譜面で線が潰れることはない。"""
        return None

    _grid_pen_cache = None

    def _grid_pens(self):
        """グリッドの線のペン。{拍かどうか: (ペン, 長さの割合)}。

        分割ごとに2本しか要らないので、分割が変わるまで使い回す。"""
        got = self._grid_pen_cache
        if got is not None and got[0] == self._grid:
            return got[1]
        pens = {}
        for beat in (False, True):
            (r, g, b), frac = (self._slot_style(0) if beat
                               else (GRID_COLORS.get(self._grid,
                                                     GRID_COLORS[64]), SUB_FRAC))
            pens[beat] = (QPen(QColor(r, g, b, 110), 1), frac)
        self._grid_pen_cache = (self._grid, pens)
        return pens

    def _draw_edit_grid(self, p, top, strip, t0=None, t1=None):
        """小節をグリッド分割で割る線。小節線そのものは親が描く。

        t0/t1 を渡すとその範囲に引く(帯へ焼くとき。既定は画面の範囲)。

        定規と同じで、4分(拍)にだけ長い白線、その間は今の分割の色で短い線。
        帯の下端から生やす。全部同じ長さにすると細かいグリッドで画面が
        埋まって音符が読めなくなるため。"""
        if strip <= 0:
            return
        if t0 is None:
            t0 = self.view_start
        if t1 is None:
            t1 = t0 + self._visible_span()
        known = self._known_measures()
        # 譜面の末尾より先の小節線は親が描かないので、ここで描く
        # (どこまでが既存の譜面かが分かるように色と線種を変える)。
        pen_virtual = QPen(QColor(255, 210, 60, 110), 1, Qt.DashLine)
        bottom = top + strip
        grid_measures = self._grid_measures()
        # 見るのは「目盛りを引く数小節」と「末尾より先の小節」だけ。範囲の
        # 小節を1つずつ見に行くと、1小節が 1px しかない譜面(幸福な死を:
        # 1秒に781小節)では毎回数千回まわることになるが、実際に線を引くのは
        # せいぜい数小節ぶんなので、初めからその番号だけを見る。
        lo, hi = self._measure_range(t0, t1)
        if grid_measures is None:        # 見えている所ぜんぶ
            grid_measures = set(range(lo, hi))
        for m in sorted({m for m in grid_measures if lo <= m < hi}
                        | set(range(max(known, lo), hi))):
            m_start = self._bar_time(m)
            m_end = self._bar_time(m + 1)
            if m_start is None or m_end is None or m_end < t0 or m_start > t1:
                continue
            if m >= known and t0 <= m_start <= t1:
                p.setPen(pen_virtual)
                bx = self._sec_to_x(m_start)
                p.drawLine(bx, top, bx, bottom)
            if m not in grid_measures:
                continue
            span = m_end - m_start
            if span <= 0:
                continue
            # 線が潰れるほど細かいときは引かない(見づらいだけなので)。
            # 1秒あたりの px は _xs_val を使う(帯へ焼くときは t1-t0 が
            # 画面より広いので、幅から割り出すと細かさを読み違える)。
            mg = self._measure_grid(m)
            if span / mg * self._xs_val < 4:
                continue
            # ペンは「拍の線」と「その間の線」の2本だけ。**種類ごとにまとめて
            # 引く**(1本ごとに setPen を呼ばない)。分割が 64 だと1小節で
            # 63 回、3小節で 189 回まわるので、1本ごとにペンを立て直すと
            # それだけで1コマ 0.4〜0.6ms になっていた(実測)。
            # 拍の線とその間の線は同じ位置には来ないので、まとめて引いても
            # 重なりの順番は変わらない。
            pens = self._grid_pens()
            beats, subs = [], []
            grid = mg
            period = self._beat_period(m, mg)
            for k in range(1, grid):
                t = m_start + span * (k / grid)
                if t < t0 or t > t1:
                    continue
                ((beats if (period and k % period == 0) else subs)
                 .append(self._sec_to_x(t)))
            for xs, is_beat in ((subs, False), (beats, True)):
                if not xs:
                    continue
                pen, frac = pens[is_beat]
                p.setPen(pen)
                y0 = bottom - int(strip * frac)
                for x in xs:
                    p.drawLine(x, y0, x, bottom)

    def _draw_pending(self, p, top, strip):
        """再解析が届くまでのあいだ、置いたばかりの音符を描く。"""
        if not self._pending:
            return
        cy = top + strip // 2
        for (m, slot, grid), char in self._pending.items():
            if char == "0":
                continue
            t = self._address_time(m, slot, grid)
            if t is None:
                continue
            x = self._sec_to_x(t)
            if x < -20 or x > self.width() + 20:
                continue
            big = char in ("3", "4", "6")
            r = 11 if big else 8
            key = {"1": "don", "3": "don", "2": "ka",
                   "4": "ka"}.get(char, "roll")
            col = QColor(self._pal.get(key, self._pal["fg"]))
            p.setPen(QPen(QColor(255, 255, 255, 200), 2))
            p.setBrush(col)
            p.drawEllipse(x - r, cy - r, r * 2, r * 2)
        p.setBrush(Qt.NoBrush)

    def _key_time(self, key):
        m, frac = key
        return self._address_time(m, frac.numerator, frac.denominator)

    #: 範囲選択の色(PeepoDrumKit と同じ、緑の箱で囲う)。
    RANGE_COLOR = (124, 207, 124)

    def _draw_long_preview(self, p, top, strip):
        """連打・風船のキーを押している間、置かれる予定の帯を出す。"""
        lg = self._long
        if lg is None:
            return
        m, slot = lg["head"]
        th = self._address_time(m, slot, self._measure_grid(m))
        tt = self.cursor_time()
        if th is None:
            return
        xh, xt = self._sec_to_x(th), self._sec_to_x(tt)
        lo, hi = min(xh, xt), max(xh, xt)
        cy = top + strip // 2
        col = QColor(252, 219, 56, 170) if lg["char"] == "5" else QColor(255, 159, 67, 170)
        p.setPen(QPen(QColor(255, 255, 255, 220), 2, Qt.DashLine))
        p.setBrush(col)
        p.drawRoundedRect(lo, cy - 8, max(4, hi - lo), 16, 8, 8)
        p.setBrush(Qt.NoBrush)


