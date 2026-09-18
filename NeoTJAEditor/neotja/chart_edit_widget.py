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
import time
from fractions import Fraction

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
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

    LEGEND_H = 18          # 凡例の帯の高さ
    #: 再生位置(＝編集カーソル)をペインの真ん中に置く(利用者の指定)。
    #: 作譜は「いま置いた音符」と「これから置く場所」を行き来して見るので、
    #: 左右が同じ幅で見えるほうがよい。音声波形ページは親の 0.3 のまま。
    FOLLOW_FRAC = 0.5
    CURSOR_W = 3           # 編集カーソルの太さ
    # 譜面の末尾より後ろへ、これだけ先までカーソルを進められる。置いた時点で
    # 足りない小節はテキスト側に自動で足される(note_edit.set_slot)。小節が
    # 1つも無い譜面でも打ち始められるようにするための仕組みでもある。
    EXTEND_MEASURES = 64

    def __init__(self, parent=None, toggle_play_cb=None):
        super().__init__(parent, toggle_play_cb=toggle_play_cb, force_dark=True)
        self._grid = 16
        self._cur_measure = 0
        self._cur_slot = 0
        self._bar_times_raw = []   # 小節の開始時刻(譜面時間。権威データ)
        self._bar_times = []       # 上を OFFSET で音源時間へ直したもの
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
        self._rdrag_x = None
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
        self._range_saved = (None, None)
        self._rclick_addr = None
        # いま出ている命令の入力欄(テストから触るため覚えておく)。
        self._cmd_popup = None

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
        for item in (times or []):
            if isinstance(item, (tuple, list)):
                raw.append(float(item[0]))
                if len(item) > 1 and item[1]:
                    last_bpm = float(item[1])
            else:
                raw.append(float(item))
        self._bar_times_raw = raw
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

    def _rebuild_bar_times(self):
        self._bar_times = [max(0.0, t - self.offset)
                           for t in (self._bar_times_raw or [])]

    def _apply_offset_local(self, offset):
        super()._apply_offset_local(offset)
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
        引き戻し合いにはならない。"""
        super().set_position_smooth(seconds)
        self._follow_playhead(seconds, nearest=False)

    def _echo_active(self):
        return time.monotonic() < self._own_seek_until

    def _pin_playhead_to_cursor(self):
        """赤い線と表示をカーソルの位置へ直接合わせる(音源の返事を待たない)。"""
        t = self.cursor_time()
        self.position_sec = t
        span = self._visible_span()
        if self._follow_window and span > 0:
            vs = t - span * self.FOLLOW_FRAC
            self.view_start = max(0.0, min(vs, max(0.0, self.duration - span))
                                  if self.duration > 0 else vs)
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

    def _ensure_cursor_visible(self):
        """カーソルが表示窓から出そうなら窓のほうを寄せる。

        set_position は使わない — あちらは再生位置(赤い線)も動かしてしまう。
        曲の終わりより先へも出られるよう、duration ではクランプしない。"""
        span = self._visible_span()
        if span <= 0:
            return
        t = self.cursor_time()
        margin = span * 0.1
        if t < self.view_start + margin or t > self.view_start + span - margin:
            self.view_start = max(0.0, t - span * self.FOLLOW_FRAC)

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
        こちらの帯は1行しか無いので、時間の範囲だけを選ぶ)。"""
        if event.button() == Qt.RightButton and not self.offset_mode:
            self.setFocus(Qt.MouseFocusReason)
            addr = self._address_from_time(max(0.0, self._x_to_sec(event.position().x())))
            if addr is not None:
                self._range_saved = (self._range_start, self._range_end)
                self._rclick_addr = addr
                self._rdrag_x = event.position().x()
                # 分割数を必ず渡す。(小節, スロット) だけを渡すとスロットが
                # 「小節の何個分」と解釈され、8 が 8/16 ではなく 8 小節ぶんになる
                # (範囲の終わりが次の小節の頭へ飛んでいた)。
                self._range_start = self._range_key(addr[0], addr[1], self._grid)
                self._range_end = self._range_start
                self.update()
            return
        if event.button() == Qt.LeftButton and not self.offset_mode:
            # 再生位置もグリッドに合わせた位置へ。親のクリック処理は押した
            # x の時刻そのものへシークするので、ここでは呼ばない(呼ぶと再生
            # 位置だけがグリッドからずれて、カーソルと食い違う)。
            self.setFocus(Qt.MouseFocusReason)
            self._dragging = True
            self._move_cursor_to_x(event.position().x())
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rdrag_x is not None and (event.buttons() & Qt.RightButton):
            addr = self._address_from_time(max(0.0, self._x_to_sec(event.position().x())))
            if addr is not None:
                self._range_end = self._range_key(addr[0], addr[1], self._grid)
                self.update()
            return
        # ドラッグでシークするあいだ、編集カーソルも一緒に付いていく
        # (シークはカーソルがグリッドの位置で出す)。
        if self._dragging and not self.offset_mode:
            if event.buttons() & Qt.LeftButton:
                self._move_cursor_to_x(event.position().x())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton and self._rdrag_x is not None:
            clicked = abs(event.position().x() - self._rdrag_x) < 4
            self._rdrag_x = None
            if clicked:
                # ほとんど動かさずに離した = メニュー。押したときに張り直した
                # 範囲は元に戻し(メニューの「範囲を解除」で消せるように)、
                # カーソルをその位置へ動かしてからメニューを出す。
                self._range_start, self._range_end = self._range_saved
                if self._rclick_addr is not None:
                    self.set_cursor_address(*self._rclick_addr)
                self._exec_menu(self._build_command_menu(),
                                event.globalPosition().toPoint())
            self.update()
            return
        super().mouseReleaseEvent(event)

    def _move_cursor_to_x(self, x):
        addr = self._address_from_time(max(0.0, self._x_to_sec(x)))
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
        """Tab: 1回目で範囲の始まり、2回目で終わり。同じ所なら取り消し。"""
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
        rng = self._range_addresses()
        if rng is not None:
            self._run_op({"kind": kind, "a": rng[0], "b": rng[1]})
        else:
            self._run_op({"kind": kind, "a": self._cursor_addr(), "b": None})

    # ------------------------------------------------------------------
    # 命令(BPM / HS)を置く
    # ------------------------------------------------------------------
    #: 命令 → (メニューと入力欄の見出し, キー)
    _COMMAND_LABELS = {"BPMCHANGE": ("BPM", "B"), "SCROLL": ("スクロール(HS)", "S")}

    def _build_command_menu(self):
        """右クリックのメニュー。見出しに位置、項目の右にキーを出す
        (キーを覚えていなくても使え、使っているうちにキーを覚えられる)。"""
        m, s = self._cursor_addr()
        menu = QMenu(self)
        head = menu.addAction("%d小節目  %d/%d" % (m + 1, s, self._grid))
        head.setEnabled(False)
        menu.addSeparator()
        for name in ("BPMCHANGE", "SCROLL"):
            label, key = self._COMMAND_LABELS[name]
            # "\t" の右側はメニューがキーの欄に右寄せで出すだけで、ショートカット
            # としては登録しない(キーはこのペインの keyPressEvent が受ける)。
            act = menu.addAction("%sを変える…\t%s" % (label, key))
            act.setData(name)
            act.triggered.connect(lambda _c=False, n=name: self.open_command_input(n))
        menu.addSeparator()
        # 開始・終了の命令。範囲ではなく、この位置に1つずつ置く(1小節ずつ書き
        # 進めるとき、終わりの位置は後から決まるため)。その位置にもう同じ行が
        # あれば「〜を消す」になる。キーは「いま押したら行われる項目」にだけ付ける。
        for kind in ("GOGO", "BARLINE"):
            info = self._marker_info(kind) or {}
            auto = self._marker_action(info)
            for which in ("on", "off"):
                base = self._MARKER_LABELS[kind][which]
                present = bool(info.get("here_" + which))
                text = base + ("を消す" if present else "")
                if auto == (which, not present):
                    text += "\t" + self._MARKER_LABELS[kind]["key"]
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
        return menu

    #: 開始・終了の命令の見出しとキー。
    _MARKER_LABELS = {
        "GOGO": {"on": "ゴーゴー開始", "off": "ゴーゴー終了", "key": "G"},
        "BARLINE": {"on": "小節線を隠す", "off": "小節線を出す", "key": "L"},
    }

    def _marker_info(self, kind):
        """カーソル位置まわりの状態(note_edit.marker_info)。"""
        if self._op_cb is None:
            return None
        return self._op_cb({"kind": "peek_marker", "a": self._cursor_addr(),
                            "grid": self._grid, "region": kind})

    @staticmethod
    def _marker_action(info):
        """G / L を押したときに行うこと (which, present)。

        その位置にもう開始か終了の行があれば、それを消す。無ければ、手前の状態が
        OFF なら開始を、ON なら終了を置く。"""
        if not info:
            return None
        if info.get("here_on"):
            return ("on", False)
        if info.get("here_off"):
            return ("off", False)
        return ("off", True) if info.get("before") else ("on", True)

    def toggle_marker(self, kind):
        """G(ゴーゴー)/ L(小節線): カーソルの位置で開始・終了を自動で切り替える。"""
        if kind not in self._MARKER_LABELS:
            return None
        act = self._marker_action(self._marker_info(kind))
        if act is None:
            return None
        which, present = act
        return self._run_op({"kind": "marker", "a": self._cursor_addr(),
                             "region": kind, "which": which, "present": present})

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
        Qt.Key_W, Qt.Key_Q, Qt.Key_H, Qt.Key_B, Qt.Key_S, Qt.Key_G, Qt.Key_L,
    )) | frozenset(_PLAIN_KEYS) | frozenset(_PEEPO_NOTE_KEYS) | frozenset(_PEEPO_LONG_KEYS)

    def event(self, e):
        if e.type() == QEvent.ShortcutOverride:
            mods = e.modifiers()
            key = e.key()
            # Ctrl 付き(Ctrl+Z など)はアプリのショートカットのまま通す。
            if not (mods & (Qt.ControlModifier | Qt.MetaModifier)):
                # Esc は「範囲や連打の途中を取り消す」ときだけこちらで使う。
                # それ以外は今までどおり窓の全画面解除へ。
                if key == Qt.Key_Escape and (self.has_range() or self._long is not None
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

        if key == Qt.Key_Escape and not self.offset_mode and self.has_range():
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
        if key in (Qt.Key_B, Qt.Key_S) and not mods & (Qt.ControlModifier | Qt.AltModifier):
            self.open_command_input("BPMCHANGE" if key == Qt.Key_B else "SCROLL")
            return
        if key in (Qt.Key_G, Qt.Key_L) and not mods & (Qt.ControlModifier | Qt.AltModifier):
            self.toggle_marker("GOGO" if key == Qt.Key_G else "BARLINE")
            return
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
    def paintEvent(self, event):
        super().paintEvent(event)
        wh, note_top, note_strip, note_bottom, cmd_strip = self._strip_rects()
        if note_strip <= 0:
            # 譜面帯が無い(音符データ未着)。グリッドだけ波形域に出す。
            note_top, note_strip = 0, wh
        p = QPainter(self)
        try:
            self._draw_range(p, note_top, note_strip)
            self._draw_edit_grid(p, note_top, note_strip)
            self._draw_long_preview(p, note_top, note_strip)
            self._draw_pending(p, note_top, note_strip)
            if not self._playing:
                # 再生中は赤い再生位置の線だけで足りる(利用者の指定)。
                self._draw_cursor(p, note_top, note_strip)
            if self._show_legend:
                self._draw_legend(p)
        finally:
            p.end()

    def _slot_style(self, k):
        """スロット k の線の色と長さ。

        4分(拍)にあたる位置は白い長い線、それ以外は今の分割の色で短い線。
        定規の「大きい目盛りと小さい目盛り」と同じ考え方。"""
        if self._grid > 0 and (k * 4) % self._grid == 0:
            return (BEAT_COLOR, BEAT_FRAC)
        return (GRID_COLORS.get(self._grid, GRID_COLORS[64]), SUB_FRAC)

    def _draw_edit_grid(self, p, top, strip):
        """小節をグリッド分割で割る線。小節線そのものは親が描く。

        定規と同じで、4分(拍)にだけ長い白線、その間は今の分割の色で短い線。
        帯の下端から生やす。全部同じ長さにすると細かいグリッドで画面が
        埋まって音符が読めなくなるため。"""
        if strip <= 0:
            return
        t0 = self.view_start
        t1 = t0 + self._visible_span()
        known = self._known_measures()
        # 譜面の末尾より先の小節線は親が描かないので、ここで描く
        # (どこまでが既存の譜面かが分かるように色と線種を変える)。
        pen_virtual = QPen(QColor(255, 210, 60, 110), 1, Qt.DashLine)
        bottom = top + strip
        # グリッドの目盛りは「いま編集している小節」と「次の小節」だけに引く
        # (利用者の指定)。画面いっぱいに引くと、どこを編集しているのかが
        # かえって読みにくい。末尾より先の小節線(破線)は範囲外でも引く。
        grid_measures = (self._cur_measure, self._cur_measure + 1)
        for m in range(self._measure_count()):
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
            if span / self._grid * (self.width() / max(1e-6, t1 - t0)) < 4:
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

    def _draw_range(self, p, top, strip):
        """範囲選択。終わりが決まるまでは始まりに細い帯だけを出す。"""
        if self._range_start is None:
            return
        t0 = self._key_time(self._range_start)
        if t0 is None:
            return
        x0 = self._sec_to_x(t0)
        if self._range_end is None:
            p.fillRect(x0 - 1, top, 3, strip, QColor(120, 190, 255, 200))
            return
        t1 = self._key_time(self._range_end)
        if t1 is None:
            return
        x1 = self._sec_to_x(t1)
        lo, hi = min(x0, x1), max(x0, x1)
        p.fillRect(lo, top, max(2, hi - lo), strip, QColor(120, 190, 255, 55))
        p.setPen(QPen(QColor(120, 190, 255, 200), 1))
        p.drawLine(lo, top, lo, top + strip)
        p.drawLine(hi, top, hi, top + strip)

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

    def _draw_cursor(self, p, top, strip):
        """編集カーソル。音符はグリッド線の上に置かれるので、枠はその線を
        中心に左右半グリッドずつ取り、縦線も中心に引く(音符が枠の真ん中に
        来る)。以前は線から右へ1グリッドぶん塗っていて、音符が枠の左端に
        乗って見えた。"""
        t = self.cursor_time()
        x = self._sec_to_x(t)
        m_end_t = self._address_time(self._cur_measure, self._cur_slot + 1,
                                     self._grid)
        if m_end_t is not None:
            half = max(1, (self._sec_to_x(m_end_t) - x) // 2)
            p.fillRect(x - half, top, half * 2, strip, QColor(255, 210, 60, 45))
        p.setPen(QPen(QColor(255, 210, 60), self.CURSOR_W))
        p.drawLine(x, top, x, top + strip)

    def _draw_legend(self, p):
        """どのキーがどの音符かを常に出しておく。ゲーム窓は固定サイズで縦の
        余裕が無いので、行を増やさずウィジェット内へ半透明で重ねる。"""
        h = self.LEGEND_H
        p.fillRect(0, 0, 52, h, QColor(0, 0, 0, 150))
        f = self.font()
        f.setPixelSize(11)
        p.setFont(f)
        x = 6
        # 現在のグリッド。
        # 現在の分割の色でラベルを出す(グリッド線の色と対応が付くように)。
        lr, lg, lb = GRID_COLORS.get(self._grid, (255, 210, 60))
        p.setPen(QColor(lr, lg, lb))
        label = "1/%d" % self._grid
        p.drawText(x, 0, 44, h, Qt.AlignVCenter | Qt.AlignLeft, label)
        # キーの一覧は出さない(利用者の指定)。グリッドの間隔だけ。
