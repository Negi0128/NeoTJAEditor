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

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QWidget)  # noqa: F401  (QWidget は型注釈用)

from neotja import note_edit
from neotja.waveform_widget import WaveformWidget

# 分割数の候補。TJA でよく使う値(constants.VALID_MEASURE_COUNTS の部分集合)。
GRID_CHOICES = [4, 8, 12, 16, 24, 32, 48, 64]

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
    (数値でないときなど、入れ直してもらう)。"""

    def __init__(self, parent, title, text, on_accept):
        super().__init__(parent, Qt.Popup)
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
        self.edit.setFixedWidth(84)
        self.edit.selectAll()
        lay.addWidget(self.edit)
        hint = QLabel("Enter 決定 / 空欄で削除")
        hint.setObjectName("hint")
        lay.addWidget(hint)
        self._on_accept = on_accept
        self.edit.returnPressed.connect(self.accept)

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


class ChartEditWaveform(WaveformWidget):
    """作譜モードの波形。グリッド + カーソル + キー入力。"""

    # (小節番号, スロット番号, 分割数, 音符文字) を上へ投げる。
    noteEdited = Signal(int, int, int, str)
    # カーソルが動いたときに時刻を通知(上位がシークするかは上位の判断)。
    cursorMoved = Signal(float)
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

    def __init__(self, parent=None, toggle_play_cb=None):
        super().__init__(parent, toggle_play_cb=toggle_play_cb, force_dark=True)
        self._grid = 16
        self._cur_measure = 0
        self._cur_slot = 0
        self._bar_times_raw = []   # 小節の開始時刻(譜面時間。権威データ)
        self._bar_times = []       # 上を OFFSET で音源時間へ直したもの
        # 「小節線」の行に出す、隠れている区間。譜面時間と音源時間。
        self._barline_raw = None
        self._barline_audio = None
        # 命令を種類ごとに分けた列(_rebuild_cmd_kinds)。
        self._cmd_by_kind = None
        self._cmd_kind_times = None
        # 選んだ範囲の音符をつかんで動かしている最中の情報。
        self._note_drag = None
        # 選んだオブジェクトの鍵(エクスプローラー式の選択)。
        self._sel = set()
        # 何も無い所を左ドラッグして囲っている最中の四角。
        self._band = None
        # 小節が1つも無いとき/末尾より先を外挿するときの1小節の長さ。
        # ヘッダの BPM から入れてもらう(既定は BPM120 の 4/4)。
        self._default_measure_len = 2.0
        self._show_legend = True
        # 楽観的表示用。置いた直後、再解析が届くまでのあいだ描く音符。
        # {(小節, スロット, 分割数): 文字}
        self._pending = {}
        # PeepoDrumKit 式の操作を実行する口(preview_dock が入れる)。
        # op dict を渡すと、結果(暫定表示用)を返す。
        self._op_cb = None
        # 範囲選択。(小節, 小節の中の割合) の組。終わりが決まるまで end は None。
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

    def set_commands(self, *args, **kwargs):
        # ゴーゴーの薄い色と小節線は帯に焼いてある(_static_strip を参照)。
        super().set_commands(*args, **kwargs)
        self._bump_strip()

    def _rebuild_bar_times(self):
        self._bump_strip()             # 小節線・目盛りは帯に焼いてある
        self._bar_times = [max(0.0, t - self.offset)
                           for t in (self._bar_times_raw or [])]
        raw = getattr(self, "_barline_raw", None)
        self._barline_audio = ([(s - self.offset, e - self.offset) for s, e in raw]
                               if raw else None)

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

    #: 停止中、カーソルが左右それぞれこの割合まで寄ったら表示を動かす。
    #: 真ん中に貼り付けていた頃は、1グリッド動かすたびに景色のほうが流れて
    #: 目が落ち着かなかった(利用者の指摘 2026-09-25)。
    VIEW_MARGIN_FRAC = 0.25

    def _follow_view_start(self, seconds, span):
        """追従表示の左端。

        再生中は今までどおり、再生位置を窓の中の一定の場所に保って流す。
        停止中(カーソルで動かしているとき)は、真ん中の帯にいるあいだは動かさず、
        端(左右 VIEW_MARGIN_FRAC)まで寄ったときだけ真ん中へ戻すように滑らせる。
        曲の終わりより先へもカーソルを出せるよう、duration ではクランプしない。"""
        if span <= 0:
            return self.view_start
        if self._playing:
            self._stop_view_anim()
            return super()._follow_view_start(seconds, span)
        left = self.view_target() + span * self.VIEW_MARGIN_FRAC
        right = self.view_target() + span * (1.0 - self.VIEW_MARGIN_FRAC)
        if left <= seconds <= right:
            return self.view_start          # 帯の中 = 景色は動かさない
        self.scroll_view_to(max(0.0, seconds - span * self.FOLLOW_FRAC))
        return self.view_start              # 行き先はトゥイーンが書き込む

    def _pin_playhead_to_cursor(self):
        """赤い線と表示をカーソルの位置へ直接合わせる(音源の返事を待たない)。"""
        t = self.cursor_time()
        self.position_sec = t
        span = self._visible_span()
        if self._follow_window and span > 0:
            self.view_start = self._follow_view_start(t, span)
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
        if abs(t - self.cursor_time()) < self.SYNC_TOLERANCE_SEC:
            return
        addr = self._address_from_time(max(0.0, t), nearest=nearest)
        if addr is None or addr == (self._cur_measure, self._cur_slot):
            return
        self._cur_measure, self._cur_slot = addr
        self._clamp_cursor()
        self.update()

    def set_playing(self, playing):
        """再生中かどうか。再生中は黄色いカーソルを出さない(赤い線だけ)。"""
        playing = bool(playing)
        if playing != self._playing:
            self._playing = playing
            self.update()

    def set_cursor_address(self, m, slot):
        """カーソルを (小節, スロット) へ置き、再生位置もそこへ移す。"""
        self._cur_measure, self._cur_slot = int(m), int(slot)
        self._clamp_cursor()
        self._cursor_changed()

    def _cursor_changed(self):
        """カーソルを自分で動かしたあとの共通処理。赤い線と表示を先に合わせ、
        返ってくる位置(こだま)はしばらく無視してから、シークを頼む。"""
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

    def set_notes(self, notes):
        # 音符も帯に焼いてある(_static_strip)。
        super().set_notes(notes)
        self._bump_strip()

    def set_spans(self, rolls, balloons, kusudamas):
        super().set_spans(rolls, balloons, kusudamas)
        self._bump_strip()
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
        return (self._cur_measure, self._cur_slot, self._grid)

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

    def _measure_len(self):
        """外挿に使う1小節の長さ。既知の小節があればその最後の間隔を使う。"""
        if len(self._bar_times) >= 2:
            span = self._bar_times[-1] - self._bar_times[-2]
            if span > 0:
                return span
        return max(1e-3, self._default_measure_len)

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
        t0 = self._bar_time(m)
        if t0 is None or grid <= 0:
            return None
        t1 = self._bar_time(m + 1)
        span = (t1 - t0) if (t1 is not None and t1 > t0) else self._measure_len()
        return t0 + span * (slot / grid)

    def _clamp_cursor(self):
        n = self._measure_count()
        self._cur_measure = max(0, min(self._cur_measure, n - 1))
        self._cur_slot = max(0, min(self._cur_slot, self._grid - 1))

    def cursor_time(self):
        t = self._address_time(self._cur_measure, self._cur_slot, self._grid)
        return 0.0 if t is None else t

    def move_cursor(self, delta):
        """カーソルを delta グリッド動かす。小節をまたぐ。"""
        if self._measure_count() <= 0:
            return
        total = self._cur_measure * self._grid + self._cur_slot + delta
        if total < 0:
            total = 0
        max_total = self._measure_count() * self._grid - 1
        total = min(total, max_total)
        self._cur_measure, self._cur_slot = divmod(total, self._grid)
        self._clamp_cursor()
        self._cursor_changed()

    def _address_from_time(self, t, nearest=False):
        """時刻から (小節, スロット)。譜面の末尾より先の外挿ぶんも当てる。"""
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
        pos = (t - t0) / span * self._grid
        slot = int(round(pos)) if nearest else int(math.floor(pos + 1e-6))
        if slot < 0:
            slot = 0
        elif slot >= self._grid:
            if i + 1 < total:
                return (i + 1, 0)
            slot = self._grid - 1
        return (i, slot)

    def mousePressEvent(self, event):
        """左クリック: その位置へ編集カーソルを置く(再生位置のシークは親)。
        右ドラッグ: 範囲選択(PeepoDrumKit の右ドラッグの箱選択に当たる。
        こちらの帯は1行しか無いので、時間の範囲だけを選ぶ)。

        左の行名の列(x < LANE_X0)は時間軸の外なので、何も起きない。"""
        if event.position().x() < self.LANE_X0 and not self.offset_mode:
            self.setFocus(Qt.MouseFocusReason)
            return
        if event.button() == Qt.RightButton and not self.offset_mode:
            # 囲って選ぶのは右ドラッグだけ(利用者の指定 2026-09-26)。四角は
            # 縦にも効く — スクロールの行だけを払えば、その命令だけ選べる。
            self.setFocus(Qt.MouseFocusReason)
            x, y = event.position().x(), event.position().y()
            self._band = {"x0": x, "y0": y, "x1": x, "y1": y,
                          "add": set(self._sel)
                          if (event.modifiers() & Qt.ControlModifier) else set()}
            if not (event.modifiers() & Qt.ControlModifier):
                self.clear_selection()
            self.update()
            return
        if event.button() == Qt.LeftButton and not self.offset_mode:
            self.setFocus(Qt.MouseFocusReason)
            x, y = event.position().x(), event.position().y()
            ctrl = bool(event.modifiers() & Qt.ControlModifier)
            obj = self._object_at(x, y)
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
                    self._note_drag = {"x0": x, "dx": 0.0, "anchor": obj[-1],
                                       "obj": obj, "ctrl": ctrl}
                    self.setCursor(Qt.SizeHorCursor)
                # 押した所へカーソルも動かす(選ぶだけだと、そのあと打ちたい
                # 位置と食い違う)。
                self._move_cursor_to_x(x)
                return
            # 何も無い所 = 選択を外してカーソルを置く(押したまま引っぱると
            # カーソルが付いてくる)。囲って選ぶのは右ドラッグだけ
            # (利用者の指定 2026-09-26)。
            if not ctrl:
                self.clear_selection()
            self._dragging = True
            self._move_cursor_to_x(x)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._note_drag is not None and (event.buttons() & Qt.LeftButton):
            # 掴んでいるあいだは 1px ごとに付いてくる(置くときだけグリッド)。
            self._note_drag["dx"] = event.position().x() - self._note_drag["x0"]
            self.update()
            return
        if self._band is not None and (event.buttons() & Qt.RightButton):
            self._band["x1"] = event.position().x()
            self._band["y1"] = event.position().y()
            self.update()
            return
        # オブジェクトの上ではカーソルの形を変える(つかめることが分かる)。
        if not (event.buttons() & (Qt.LeftButton | Qt.RightButton)):
            hit = self._object_at(event.position().x(), event.position().y())
            self.setCursor(Qt.SizeHorCursor if hit is not None else Qt.ArrowCursor)
        # ドラッグでシークするあいだ、編集カーソルも一緒に付いていく
        # (シークはカーソルがグリッドの位置で出す)。
        if self._dragging and not self.offset_mode:
            if event.buttons() & Qt.LeftButton:
                self._move_cursor_to_x(event.position().x())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._note_drag is not None:
            self._finish_note_drag()
            return
        if event.button() == Qt.RightButton and self._band is not None:
            bd, self._band = self._band, None
            if abs(bd["x1"] - bd["x0"]) >= 4 or abs(bd["y1"] - bd["y0"]) >= 4:
                self._select_band(bd)
            self.update()
            return
        super().mouseReleaseEvent(event)

    def _move_cursor_to_x(self, x):
        # 一番近いグリッドへ合わせる(利用者の指定 2026-09-25)。切り捨てだと
        # 音符の少し右を押したときに1つ手前へ落ちて、置く場所がずれる。
        addr = self._address_from_time(max(0.0, self._x_to_sec(x)), nearest=True)
        if addr is None:
            return
        if addr == (self._cur_measure, self._cur_slot):
            return          # 同じグリッドの中で動いただけならシークし直さない
        self._cur_measure, self._cur_slot = addr
        self._clamp_cursor()
        self._cursor_changed()

    def set_cursor_from_time(self, t):
        """再生位置などからカーソルを合わせる。"""
        addr = note_edit.time_to_address(self._bar_times, t, self._grid)
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
    _CMD_NAMES = {"bpm": "BPMCHANGE", "hs": "SCROLL", "measure": "MEASURE"}

    def _pos_of_time(self, t):
        """音源時刻 → 位置(分数)。"""
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
        m = int(pos)
        frac = pos - m
        a = self._bar_time(m)
        b = self._bar_time(m + 1)
        if a is None:
            return None
        if b is None or b <= a:
            b = a + self._default_measure_len
        return a + float(frac) * (b - a)

    def _objects(self):
        """いま画面に出ているオブジェクト [(鍵, 行, 時刻, つかめる半幅px)]。"""
        out = []
        for t, c in (self._note_audio or []):
            p = self._pos_of_time(t)
            if p is not None:
                r = self.NOTE_R_BIG if c in ("3", "4") else self.NOTE_R
                out.append((("note", p), "note", t, r))
        for st, _e, kind in (self._span_audio or []):
            p = self._pos_of_time(st)
            if p is not None:
                out.append((("note", p), "note", st,
                            self.SPAN_TH_BIG // 2 if kind == "roll_big" else self.SPAN_TH // 2))
        for item in (self._cmd_audio or []):
            if len(item) < 4:
                continue
            name = self._CMD_NAMES.get(item[3])
            p = self._pos_of_time(item[0])
            if name and p is not None:
                out.append((("cmd", name, p), item[3], item[0], 16))
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
            x0, x1 = self._object_hrange(orow, t, half)
            if not (x0 <= x <= x1):
                continue
            d = abs((x0 + x1) / 2.0 - x)
            if best_d is None or d < best_d:
                best, best_d = key, d
        return best

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
        する(利用者の指定 2026-09-26: 右側に判定が無くて動かしにくい)。"""
        x = self._sec_to_x(t)
        if orow == "note":
            return (x - half, x + half)
        if orow == "gogo":
            # 帯の端。左右どちらからでもつまめるよう、中心に幅を取る。
            return (x - 8, x + 8)
        return (x - 2, x - 2 + half * 3)

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

        時間の帯(_range_start/_range_end)は別もの。敷き詰め(Shift+F/J/D/K)や
        W/Q は「空いているグリッドも含む帯」に効かせたいので、選んだものとは
        分けて持つ。囲って選んだときは、呼ぶ側が両方を立てる。"""
        self._sel = set(keys)
        self.update()
        self.selectionChanged.emit()

    def clear_selection(self):
        self.set_selection(())

    def _select_band(self, bd):
        """囲った四角の中のものを選び、時間の帯も張る。"""
        hit = self._objects_in_rect(bd["x0"], bd["y0"], bd["x1"], bd["y1"])
        self.set_selection(set(bd["add"]) | hit)
        lo_x, hi_x = sorted((bd["x0"], bd["x1"]))
        a0 = self._address_from_time(max(0.0, self._x_to_sec(lo_x)))
        a1 = self._address_from_time(max(0.0, self._x_to_sec(hi_x)))
        if a0 is not None and a1 is not None:
            self._range_start = self._range_key(a0[0], a0[1], self._grid)
            self._range_end = self._range_key(a1[0], a1[1], self._grid)

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

    def _drag_keys(self):
        """いま動かしているものの鍵。ふだんは選んでいるもの全部。"""
        edge = self._drag_edge_name()
        if edge is None:
            return set(self._sel)
        return {k for k in self._sel if k[0] == "cmd" and k[1] == edge}

    def selected_items(self):
        """note_edit へ渡す形 [{kind, name, pos}]。"""
        out = []
        for k in self._sel:
            pos = k[-1]
            if k[0] == "note":
                out.append({"kind": "note", "name": None,
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
        slot = int(round(frac * self._grid))
        if slot >= self._grid:
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

        帯は「空いているグリッドも含む時間の範囲」で、敷き詰め(Shift+F/J/D/K)
        や W/Q が効く相手。選んだオブジェクト(_sel)とは別もの。"""
        here = self._range_key(self._cur_measure, self._cur_slot, self._grid)
        if self._range_start is None or self._range_end is not None:
            self._range_start = here
            self._range_end = None
        else:
            self._range_end = here
            if self._range_end == self._range_start:
                self.clear_range()
        self.update()

    def _range_addresses(self):
        """範囲を今のグリッドの住所2つで。範囲が無ければ None。"""
        if not self.has_range():
            return None
        return self._key_to_slot(self._range_start), self._key_to_slot(self._range_end)

    # ------------------------------------------------------------------
    # PeepoDrumKit 式の操作
    # ------------------------------------------------------------------
    def _run_op(self, op):
        """操作を実行し、返ってきた暫定表示を反映する。"""
        if self._op_cb is None:
            return None
        op.setdefault("grid", self._grid)
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

    def _cursor_addr(self):
        return (self._cur_measure, self._cur_slot)

    def _peepo_note_key(self, char, mods):
        if mods & Qt.AltModifier:
            char = _BIG[char]
        rng = self._range_addresses()
        if (mods & Qt.ShiftModifier) and rng is not None:
            self._run_op({"kind": "fill", "a": rng[0], "b": rng[1], "char": char})
            return
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

    def _delete_or_transform(self, kind):
        if kind == "delete" and self._sel:
            # 選んだものを消す(音符も命令も。利用者の指定 2026-09-25)。
            self._run_op({"kind": "delete_items", "items": self.selected_items()})
            self.clear_selection()
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
    _COMMAND_LABELS = {"BPMCHANGE": "BPM", "SCROLL": "スクロール(HS)"}

    def _build_command_menu(self):
        """右クリックのメニュー(と、窓の「作譜」メニューの中身)。"""
        menu = QMenu(self)
        self.populate_command_menu(menu)
        return menu

    def populate_command_menu(self, menu):
        """命令の項目を menu へ足す。カーソルの位置で内容が変わるので、
        窓のメニュー側は出す直前に clear() して呼び直す。

        命令はキーではなくメニューから置く(利用者の希望 2026-09-25)。以前は
        B/S/G/L のキーも受けていて、項目の右に "\\t B" のようにキーを出して
        いたが、覚えるキーを増やさない方針にしたのでキーは外した。"""
        m, s = self._cursor_addr()
        head = menu.addAction("%d小節目  %d/%d" % (m + 1, s, self._grid))
        head.setEnabled(False)
        menu.addSeparator()
        for name in ("BPMCHANGE", "SCROLL"):
            act = menu.addAction("%sを変える…" % self._COMMAND_LABELS[name])
            act.setData(name)
            act.triggered.connect(lambda _c=False, n=name: self.open_command_input(n))
        menu.addSeparator()
        # 開始・終了の命令。範囲ではなく、この位置に1つずつ置く(1小節ずつ書き
        # 進めるとき、終わりの位置は後から決まるため)。その位置にもう同じ行が
        # あれば「〜を消す」になる。
        for kind in ("GOGO", "BARLINE"):
            info = self._marker_info(kind) or {}
            for which in ("on", "off"):
                base = self._MARKER_LABELS[kind][which]
                present = bool(info.get("here_" + which))
                text = base + ("を消す" if present else "")
                act = menu.addAction(text)
                act.setData((kind, which))
                act.triggered.connect(
                    lambda _c=False, k=kind, wh=which, pr=not present:
                    self._run_op({"kind": "marker", "a": self._cursor_addr(),
                                  "region": k, "which": wh, "present": pr}))
            menu.addSeparator()
        clr = menu.addAction("範囲を解除")
        clr.setEnabled(self.has_range())
        clr.triggered.connect(self.clear_range)

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
                            "grid": self._grid, "region": kind})

    def _exec_menu(self, menu, global_pos):
        """メニューを出す(テストではここを差し替えて、出したメニューを調べる)。"""
        menu.exec(global_pos)

    def open_command_input(self, name):
        """カーソルの位置に命令 name(BPMCHANGE / SCROLL)を置く入力欄を出す。

        入力欄の初期値は、その位置に同じ命令があればその値、無ければその位置で
        今効いている値。初期値のまま決定しても命令は増やさない。"""
        if self._op_cb is None or name not in self._COMMAND_LABELS:
            return None
        m, s = self._cursor_addr()
        peek = self._op_cb({"kind": "peek_command", "a": (m, s), "grid": self._grid,
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

        if self._cmd_popup is not None:
            self._cmd_popup.close()
        popup = _CommandInput(self, self._COMMAND_LABELS[name][0], text, accept)
        x = self._sec_to_x(self.cursor_time())
        _wh, top, _strip, _b, _c = self._strip_rects()
        popup.adjustSize()
        pos = self.mapToGlobal(QPoint(max(0, x - popup.width() // 2),
                                      max(0, top - popup.height() - 4)))
        popup.move(pos)
        popup.show()
        popup.edit.setFocus(Qt.PopupFocusReason)
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
        try:
            i = GRID_CHOICES.index(self._grid)
        except ValueError:
            i = GRID_CHOICES.index(16)
        j = max(0, min(len(GRID_CHOICES) - 1, i + direction))
        if j == i:
            return
        frac = self._cur_slot / self._grid if self._grid else 0.0
        self._grid = GRID_CHOICES[j]
        self._cur_slot = int(round(frac * self._grid))
        if self._cur_slot >= self._grid:
            self._cur_slot = self._grid - 1
        self._clamp_cursor()
        self.update()

    # ------------------------------------------------------------------
    # 入力
    # ------------------------------------------------------------------
    #: このペインが自分で使うキー。窓のショートカット(ゲーム窓の Esc = 全画面
    #: 解除 など)に先を越されないよう、ShortcutOverride で先に名乗り出る。
    _OWN_KEYS = frozenset((
        Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Home, Qt.Key_End,
        Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
        Qt.Key_W, Qt.Key_Q, Qt.Key_H,
    )) | frozenset(_PLAIN_KEYS) | frozenset(_PEEPO_NOTE_KEYS) | frozenset(_PEEPO_LONG_KEYS)

    def event(self, e):
        if e.type() == QEvent.ShortcutOverride:
            mods = e.modifiers()
            key = e.key()
            # Ctrl 付き(Ctrl+Z など)はアプリのショートカットのまま通す。
            if not (mods & (Qt.ControlModifier | Qt.MetaModifier)):
                # Esc は「範囲や連打の途中を取り消す」ときだけこちらで使う。
                # それ以外は今までどおり窓の全画面解除へ。
                if key == Qt.Key_Escape and (self._sel or self.has_range()
                                             or self._long is not None
                                             or self._range_start is not None):
                    e.accept()
                    return True
                if key in self._OWN_KEYS:
                    e.accept()
                    return True
        # Tab は keyPressEvent に届く前にフォーカス移動で消費されるので、
        # ここで横取りする(PeepoDrumKit と同じく範囲選択に使う)。
        if e.type() == QEvent.KeyPress and e.key() == Qt.Key_Tab:
            if not e.isAutoRepeat():
                self.toggle_range_at_cursor()
            e.accept()
            return True
        return super().event(e)

    def wheelEvent(self, event):
        """ホイールはカーソルを1グリッドずつ動かす(利用者の指定 2026-09-25)。

        以前はレーンと同じ「小節ごとの移動」だった。作譜では打つ位置を細かく
        合わせたいので、グリッドに乗ったまま動くほうが合う。拡大縮小
        (修飾キー + ホイール)は今までどおり親へ渡す。

        **動き方はレーンの上で回したときと同じ**(滑らせる。利用者の指定
        2026-09-28)。以前はその場へ飛んでいて、1グリッドでも景色が飛ぶので
        どこへ動いたのか目で追えなかった。"""
        if self.offset_mode or (event.modifiers() & self.ZOOM_MODIFIERS):
            super().wheelEvent(event)
            return
        self.grid_step(1 if event.angleDelta().y() > 0 else -1)
        event.accept()

    # ホイールでの移動を「レーンと同じ速さで滑らせる」ための口。
    #   _scroll_target_cb … いまの行き先(トゥイーン中はその目標)を返す
    #   _scroll_to_cb     … その時刻へ滑らせて移る
    # どちらも無ければ今までどおりその場へ飛ぶ(ペイン単体でも動くように)。
    _scroll_target_cb = None
    _scroll_to_cb = None

    def set_scroll_cbs(self, target_cb, scroll_cb):
        """レーンの「滑らせて移る」に繋ぐ(chart_preview の同名メソッド)。"""
        self._scroll_target_cb = target_cb
        self._scroll_to_cb = scroll_cb

    def grid_step(self, direction):
        """グリッド1つぶん先/手前へ、レーンと同じ速さで滑らせて移る。

        続けて回したときは**行き先から足す**(回した回数ぶんきっちり進む)。
        基準をいまの表示位置にすると、滑っている途中の位置から数え直して
        しまい、速く回したぶんが取りこぼされる。
        """
        if self._scroll_to_cb is None or self._grid <= 0:
            self.move_cursor(direction)          # 繋がっていなければ飛ぶ
            return
        base = (self._scroll_target_cb() if self._scroll_target_cb is not None
                else self.position_sec)
        addr = self._address_from_time(max(0.0, float(base)), nearest=True)
        if addr is None:
            self.move_cursor(direction)
            return
        total = addr[0] * self._grid + addr[1] + int(direction)
        total = max(0, total)
        count = self._measure_count()
        if count > 0:
            total = min(total, count * self._grid - 1)
        m, s = divmod(total, self._grid)
        t = self._address_time(m, s, self._grid)
        if t is None:
            return
        # カーソルはレーンのクロックに付いてくる(_follow_playhead)。ここで
        # 先に動かすと、滑っている途中の位置に引き戻されて競り合う。
        self._scroll_to_cb(t)

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

        if key == Qt.Key_Escape and not self.offset_mode and (
                self._sel or self._range_start is not None):
            self.clear_selection()
            self.clear_range()
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
        # 命令(BPM / HS / ゴーゴー / 小節線)はキーでは置かない。右クリックの
        # メニューか、窓の「作譜」メニューから置く(利用者の希望 2026-09-25)。
        if key == Qt.Key_Home:
            self.jump_to_edge(False)
            return
        if key == Qt.Key_End:
            self.jump_to_edge(True)
            return

        if key == Qt.Key_Left:
            self.move_cursor(-1)
            return
        if key == Qt.Key_Right:
            self.move_cursor(1)
            return
        if key == Qt.Key_Up:
            self.change_grid(1)
            return
        if key == Qt.Key_Down:
            self.change_grid(-1)
            return
        if key == Qt.Key_H:
            self.set_legend_visible(not self._show_legend)
            self.legendToggled.emit(self._show_legend)
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
        addr = (self._cur_measure, self._cur_slot, self._grid)
        self._pending[addr] = char
        if char == "0":
            self._hide_note_at(*addr)
        self.noteEdited.emit(self._cur_measure, self._cur_slot, self._grid, char)
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
    LANE_GAIN = 3.0
    #: 音符の行のうち、下から何割を波形に使うか。
    WAVE_FRAC = 0.45
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
        ("barline", "小節線", 28),
        ("gogo", "GOGO", 28),
    )
    #: 行の中身の色。区間の行(小節線・ゴーゴー)は同じ灰色にそろえる
    #: (利用者の指定 2026-09-25: 色が増えると認識しにくい)。
    ROW_CONTENT_COLOR = "#c8c8c8"

    #: 帯へ焼いている最中か(_static_strip)。焼くあいだは膨らませない。
    _baking = False

    def _note_scale(self, t):
        """再生中、再生位置が通り過ぎた直後の音符を少しだけ大きく描く。"""
        if not self._playing or self._baking:
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
        cur = Fraction(self._cur_measure) + Fraction(self._cur_slot, self._grid)
        out = {}
        for item in (self._cmd_audio or []):
            if len(item) < 4 or item[3] not in self._CMD_NAMES:
                continue
            p = self._pos_of_time(item[0])
            if p is None:
                continue
            # 拍子だけは「その小節のもの」。小節の頭にしか置けない(音符の
            # 間隔を保って組み直す都合。note_edit.op_measure を参照)ので、
            # 小節のどこにカーソルがあってもその拍子を指す。
            hit = (int(p) == self._cur_measure if item[3] == "measure"
                   else p == cur)
            if hit:
                out[item[3]] = (p, item[0])
        return out

    def set_command_value(self, name, pos, value):
        """すでに置いてある命令の値を書き換える(命令パネルの「変更」から)。"""
        pos = Fraction(pos)
        return self._run_op({"kind": "command_value",
                             "pos": (pos.numerator, pos.denominator),
                             "name": str(name).upper(), "value": value})

    def place_marker(self, kind, which):
        """カーソルの位置に開始/終了の印を置く(命令パネルのボタンから)。"""
        m, s = self._cursor_addr()
        return self._run_op({"kind": "marker", "a": (m, s),
                             "region": str(kind).upper(), "which": str(which),
                             "present": True})

    def _drag_delta_slots(self, dx, anchor=None):
        """つかんで動かした px を、グリッド何個ぶんかに直す。

        anchor(つかんだものの位置)があればそこを基準に、無ければ選んだものの
        先頭を基準にする。"""
        if anchor is not None:
            t = self._time_of_pos(anchor)
            m0, s0 = int(anchor), int(round(float(anchor - int(anchor)) * self._grid))
        else:
            a = self._range_addresses()
            if a is None:
                return 0
            (m0, s0), _b = a
            t = self._address_time(m0, s0, self._grid)
        if t is None:
            return 0
        addr = self._address_from_time(max(0.0, t + dx * self._seconds_per_pixel()),
                                       nearest=True)
        if addr is None:
            return 0
        return (addr[0] * self._grid + addr[1]) - (m0 * self._grid + s0)

    def _finish_note_drag(self):
        """離したところの一番近いグリッドへ、選んだものを置き直す。"""
        dr, self._note_drag = self._note_drag, None
        self.setCursor(Qt.ArrowCursor)
        if dr is None:
            return
        if abs(dr["dx"]) < 4 or not self._sel:
            obj = dr.get("obj")
            if obj is not None and not dr.get("ctrl") and self._sel != {obj}:
                self.set_selection({obj})
            self.update()
            return
        steps = self._drag_delta_slots(dr["dx"], dr.get("anchor"))
        if steps:
            d = Fraction(int(steps), int(self._grid))
            # 帯の端をつかんだときは、同じ側の端だけを動かす(_drag_edge_name)。
            self._note_drag = dr          # _drag_keys が見るので戻しておく
            move_keys = self._drag_keys()
            self._note_drag = None
            items = [{"kind": "cmd" if k[0] == "cmd" else "note",
                      "name": k[1] if k[0] == "cmd" else None,
                      "pos": (k[-1].numerator, k[-1].denominator)}
                     for k in move_keys]
            res = items and self._run_op(
                {"kind": "move_items", "items": items,
                 "delta_num": d.numerator, "delta_den": d.denominator})
            if res:
                # 動かしたものは選んだまま付いていく(続けて動かせるように)。
                self.set_selection({
                    ((k[0], k[1], k[2] + d) if k[0] == "cmd" else (k[0], k[1] + d))
                    if k in move_keys else k
                    for k in self._sel})
        self.update()

    def _shift_addr(self, addr, delta):
        """(小節, スロット) を delta グリッドぶんずらす。"""
        k = addr[0] * self._grid + addr[1] + int(delta)
        k = max(0, k)
        return (k // self._grid, k % self._grid)

    def _draw_selection(self, p, note_cy):
        """選んだオブジェクトを1つずつ枠で囲む(エクスプローラーの選択と同じ
        考え方で、範囲ではなく「選ばれているもの」を示す)。"""
        if not self._sel and self._band is None:
            return
        rows = self._row_rects()
        r, g, b = self.RANGE_COLOR
        if self._sel:
            p.setPen(QPen(QColor(r, g, b, 235), 2))
            for key, orow, t, half in self._objects():
                if key not in self._sel:
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
                        dx = int(dr["dx"])
                    x0 += dx
                    x1 += dx
                if orow == "note":
                    cy = note_cy
                    hh = half + 3
                    p.drawRect(x0 - 3, cy - hh, (x1 - x0) + 6, 2 * hh)
                else:
                    ry, rh = rect
                    p.drawRect(x0, ry + 2, x1 - x0, rh - 4)
        if self._band is not None:
            bd = self._band
            lo_x, hi_x = sorted((bd["x0"], bd["x1"]))
            lo_y, hi_y = sorted((bd["y0"], bd["y1"]))
            p.fillRect(int(lo_x), int(lo_y), int(hi_x - lo_x), int(hi_y - lo_y),
                       QColor(r, g, b, 30))
            pen = QPen(QColor(r, g, b, 210), 1)
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawRect(int(lo_x), int(lo_y), int(hi_x - lo_x), int(hi_y - lo_y))

    def _draw_note_drag(self, p, top, strip, note_cy=None):
        """つかんで動かしている最中の見せ方。選んだものを掴んだぶんだけ横へ
        ずらして薄く描く(置くのはグリッドだが、動きは滑らかに)。"""
        dr = self._note_drag
        if dr is None or not self._sel:
            return
        dx = int(dr["dx"])
        # 帯の端をつかんでいる間は、動くのは同じ側の端だけ(_drag_edge_name)。
        move_keys = self._drag_keys()
        rows = self._row_rects()
        r, g, b = self.RANGE_COLOR
        cy = note_cy if note_cy is not None else top + strip // 2
        steps = self._drag_delta_slots(dr["dx"], dr.get("anchor"))
        d = Fraction(int(steps), int(self._grid))
        for key, orow, t, half in self._objects():
            if key not in move_keys:
                continue
            x = self._sec_to_x(t) + dx
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
            # 置かれる位置(グリッド)に細い線
            gt = self._time_of_pos(key[-1] + d)
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

    def _row_rects(self):
        """{種類: (上端 y, 高さ)}。音符の行が残りを全部取る。"""
        fixed = sum(h for _k, _n, h in self.ROWS)
        note_h = max(40, self.height() - self.RULER_H - fixed)
        out = {}
        y = self.RULER_H
        for kind, _name, h in self.ROWS:
            hh = note_h if kind == "note" else h
            out[kind] = (y, hh)
            y += hh
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
                self._grid, self._cur_measure, self._playing, self._strip_rev,
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

    def _paint_static(self, p, w, t0, t1):
        """時間の関数でしかない絵。帯へ焼くときも、直に描くときも同じ道。

        w は描き先の幅(帯のときは画面より広い)。呼ぶ側が self.view_start を
        t0 に合わせておくこと。"""
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
        # 音符も焼く。**叩いた瞬間の膨らみだけは焼かない**(再生位置との
        # 差で決まるので、帯に焼くと置き去りになる)。膨らむのは再生位置の
        # 直後 HIT_POP_SEC ぶんの数個だけなので、そこは毎コマ上から描く。
        self._baking = True
        try:
            note_cy = note_top + (note_h - wave_h) // 2
            self._draw_notes(p, lane_w, t0, t1, note_cy)
        finally:
            self._baking = False
        # 命令の行(BPM・拍子・スクロール)も時間の関数。右端は帯の幅で見る。
        rows = self._row_rects()
        for kind in ("bpm", "measure", "hs"):
            y, rh = rows[kind]
            self._draw_cmd_row(p, kind, y, rh, t0, t1, right=w)
        self._draw_ruler(p, t0, t1)

    def _paint_rows(self, p):
        saved_view = self.view_start
        try:
            self._paint_rows_inner(p)
        finally:
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
        if self._playing:
            # 叩いた瞬間だけ膨らむ音符(帯には等倍で焼いてある)。膨らんだ絵の
            # ほうが大きいので、上から描けばそのまま隠れる。
            self._draw_notes(p, self._lane_w(),
                             max(0.0, self.position_sec - self.HIT_POP_SEC),
                             self.position_sec, note_cy)
        self._draw_long_preview(p, note_top, upper_h)
        self._draw_pending(p, note_top, upper_h)
        # 小節線は帯ではなく、ON/OFF の札(スクロールと同じ見せ方。利用者の
        # 指定 2026-09-26)。
        self._draw_marker_row(p, rows["barline"], self._barline_audio,
                              ("BARLINEOFF", "BARLINEON"), ("OFF", "ON"), t0, t1)
        # GOGO は帯のまま(長さが目で分かるように)。端をつかんでいる間は、
        # その端が付いてきて帯が伸び縮みする。
        self._draw_span_row(p, rows["gogo"], self._gogo_audio,
                            self.ROW_CONTENT_COLOR, t0, t1)

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

        # --- 再生位置の線は全部の行を貫く ---
        xp = self._sec_to_x(self.position_sec)
        p.setPen(QPen(QColor(pal["err"]), 2))
        p.drawLine(xp, self.RULER_H, xp, h)
        # 黄色いカーソルは出さない(利用者の指定 2026-09-25)。停止中は赤い線が
        # カーソルの位置に貼り付いているので、それで足りる。

        # --- 目盛りと左の列は最後(譜面がはみ出しても上から隠す) ---
        if strip_off is None:
            self._draw_ruler(p, t0, t1)
        else:
            # 目盛りは帯に焼いてあるので、その帯を上の高さぶんだけ貼り直す。
            p.setClipRect(0, 0, w, self.RULER_H)
            p.drawPixmap(-strip_off, 0, self._strip_pm)
            p.setClipping(False)
        self._draw_row_labels(p, rows)

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

    def _draw_legend(self, p):
        pal = self._pal
        lr, lg, lb = GRID_COLORS.get(self._grid, (255, 210, 60))
        dpr = self.devicePixelRatioF()
        key = (self._time_text(self.position_sec), self._grid,
               pal.get("fg_dim"), (lr, lg, lb), self.RULER_H, self.LANE_X0, dpr)
        now = time.monotonic()
        if self._legend_pm is None or (
                key != self._legend_key
                and now - self._legend_at >= 1.0 / self.LEGEND_FPS):
            w, h = self.LANE_X0, self.RULER_H
            pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            try:
                f = self.font()
                f.setPixelSize(11)
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
        f.setPixelSize(12)
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

    def _draw_ruler(self, p, t0, t1):
        """上の帯に小節番号と時刻を出す。"""
        pal = self._pal
        p.fillRect(0, 0, self.width(), self.RULER_H, QColor(pal["bg2"]))
        f = self.font()
        f.setPixelSize(11)
        p.setFont(f)
        for m in range(*self._measure_range(t0, t1)):
            t = self._bar_time(m)
            if t is None or t < t0 or t > t1:
                continue
            x = self._sec_to_x(t)
            nxt = self._bar_time(m + 1)
            room = (self._sec_to_x(nxt) - x) if nxt is not None else 999
            p.setPen(QPen(QColor(pal["border"])))
            p.drawLine(x, 0, x, self.RULER_H)
            # 小節番号は必ず、時刻は入るときだけ(小節が詰まっているところで
            # 数字が重なって読めなくなるのを防ぐ)。
            p.setPen(QColor(pal["fg"]))
            p.setClipRect(x + 2, 0, max(1, room - 3), self.RULER_H)
            p.drawText(x + 4, 0, 60, 12, Qt.AlignVCenter | Qt.AlignLeft, str(m + 1))
            if room >= 64:
                p.setPen(QColor(pal["fg_dim"]))
                f.setPixelSize(9)
                p.setFont(f)
                p.drawText(x + 4, 10, 70, 11, Qt.AlignVCenter | Qt.AlignLeft,
                           self._time_text(t))
                f.setPixelSize(11)
                p.setFont(f)
            p.setClipping(False)

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
        f.setPixelSize(11)
        p.setFont(f)
        col = QColor(self.ROW_CONTENT_COLOR)
        if right is None:
            right = self.width()
        lo = max(0, bisect.bisect_left(times, t0) - 1)
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
            p.setPen(QPen(col, 1))
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
        if not snap:
            return int(dr["dx"])
        steps = self._drag_delta_slots(dr["dx"], dr.get("anchor"))
        gt = self._time_of_pos(pos + Fraction(int(steps), int(self._grid)))
        if gt is None:
            return int(dr["dx"])
        return self._sec_to_x(gt) - self._sec_to_x(t)

    def _draw_span_row(self, p, rect, spans, color, t0, t1):
        """ゴーゴーの帯。端をつかんでいる間は、その端だけが付いてくる。"""
        y, rh = rect
        if not spans:
            return
        col = QColor(color)
        fill = QColor(col)
        fill.setAlpha(70)
        for s_t, e_t in spans:
            if e_t < t0 or s_t > t1:
                continue
            xs = self._sec_to_x(s_t) + self._drag_dx_for("GOGOSTART", s_t, snap=True)
            xe = self._sec_to_x(e_t) + self._drag_dx_for("GOGOEND", e_t, snap=True)
            lo, hi = sorted((xs, xe))
            p.fillRect(lo, y + 3, max(2, hi - lo), rh - 6, fill)
            p.setPen(QPen(col, 1))
            p.drawRect(lo, y + 3, max(2, hi - lo), rh - 6)

    def _draw_marker_row(self, p, rect, spans, names, labels, t0, t1):
        """小節線のような「ここから / ここまで」の印。札で出す。"""
        y, rh = rect
        if not spans:
            return
        f = self.font()
        f.setPixelSize(11)
        p.setFont(f)
        col = QColor(self.ROW_CONTENT_COLOR)
        fm = p.fontMetrics()
        for s_t, e_t in spans:
            for t, name, text in ((s_t, names[0], labels[0]),
                                  (e_t, names[1], labels[1])):
                if t < t0 or t > t1:
                    continue
                x = self._sec_to_x(t) + self._drag_dx_for(name, t)
                tw = fm.horizontalAdvance(text)
                p.setPen(QPen(col, 1))
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
        # グリッドの目盛りは「いま編集している小節」と「次の小節」だけに引く
        # (利用者の指定)。画面いっぱいに引くと、どこを編集しているのかが
        # かえって読みにくい。末尾より先の小節線(破線)は範囲外でも引く。
        # 再生中は1つ前の小節にも引く(利用者の指定 2026-09-25)。流れていく
        # 譜面の後ろ側が無地だと、いまどの位置を通ったのかが読めない。
        grid_measures = ((self._cur_measure - 1, self._cur_measure,
                          self._cur_measure + 1, self._cur_measure + 2)
                         if self._playing
                         else (self._cur_measure, self._cur_measure + 1,
                               self._cur_measure + 2))
        for m in range(*self._measure_range(t0, t1)):
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
            if span / self._grid * self._xs_val < 4:
                continue
            for k in range(1, self._grid):
                t = m_start + span * (k / self._grid)
                if t < t0 or t > t1:
                    continue
                (r, g, b), frac = self._slot_style(k)
                p.setPen(QPen(QColor(r, g, b, 110), 1))
                x = self._sec_to_x(t)
                p.drawLine(x, bottom - int(strip * frac), x, bottom)

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
        th = self._address_time(m, slot, self._grid)
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


