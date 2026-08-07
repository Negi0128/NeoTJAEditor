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

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget  # noqa: F401  (型注釈用)

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

# キー → 音符文字。数字キーはエディタに打つのと同じ対応にしてある(覚え直しが
# 要らない)。ホームポジション側は PeepoDrumKit と同じ並び。
_PLAIN_KEYS = {
    Qt.Key_1: "1", Qt.Key_2: "2", Qt.Key_3: "3", Qt.Key_4: "4", Qt.Key_5: "5",
    Qt.Key_6: "6", Qt.Key_7: "7", Qt.Key_8: "8", Qt.Key_9: "9", Qt.Key_0: "0",
    Qt.Key_F: "1", Qt.Key_J: "1",
    Qt.Key_D: "2", Qt.Key_K: "2",
    Qt.Key_R: "5", Qt.Key_U: "5",
    Qt.Key_E: "7", Qt.Key_I: "7",
    Qt.Key_T: "8", Qt.Key_Y: "8",
}
# Alt 併用で大きい方/くす玉へ。
_ALT_KEYS = {
    Qt.Key_F: "3", Qt.Key_J: "3",
    Qt.Key_D: "4", Qt.Key_K: "4",
    Qt.Key_R: "6", Qt.Key_U: "6",
    Qt.Key_E: "9", Qt.Key_I: "9",
}


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

    def set_default_measure_len(self, sec):
        """小節が無い/末尾より先へ出たときに使う1小節の長さ(秒)。"""
        try:
            sec = float(sec)
        except (TypeError, ValueError):
            return
        if sec > 0:
            self._default_measure_len = sec
            self.update()

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
        self._ensure_cursor_visible()
        self.cursorMoved.emit(self.cursor_time())
        self.update()

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

    def _address_from_time(self, t):
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
        slot = int(round((t - t0) / span * self._grid))
        if slot < 0:
            slot = 0
        elif slot >= self._grid:
            if i + 1 < total:
                return (i + 1, 0)
            slot = self._grid - 1
        return (i, slot)

    def mousePressEvent(self, event):
        """クリックした位置へ編集カーソルを置く。再生位置のシークは親の担当
        (seekRequested) なので、そのまま super() へ流す。"""
        if event.button() == Qt.LeftButton and not self.offset_mode:
            self._move_cursor_to_x(event.position().x())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # ドラッグでシークするあいだ、編集カーソルも一緒に付いていく。
        if self._dragging and not self.offset_mode:
            self._move_cursor_to_x(event.position().x())
        super().mouseMoveEvent(event)

    def _move_cursor_to_x(self, x):
        addr = self._address_from_time(max(0.0, self._x_to_sec(x)))
        if addr is None:
            return
        self._cur_measure, self._cur_slot = addr
        self._clamp_cursor()
        self.cursorMoved.emit(self.cursor_time())
        self.update()

    def set_cursor_from_time(self, t):
        """再生位置などからカーソルを合わせる。"""
        addr = note_edit.time_to_address(self._bar_times, t, self._grid)
        if addr is None:
            return
        self._cur_measure, self._cur_slot = addr
        self._clamp_cursor()
        self.update()

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
    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

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
            self._place("0")
            return
        if key == Qt.Key_Backspace:
            # 文字入力の BackSpace と同じ気持ち: 1つ戻ってから消す。
            # 配置で自動的に前へ進むので、直前に置いた音符がここに来る。
            self.move_cursor(-1)
            self._place("0")
            return

        char = None
        if mods & Qt.AltModifier:
            char = _ALT_KEYS.get(key)
        if char is None and not (mods & (Qt.ControlModifier | Qt.MetaModifier)):
            char = _PLAIN_KEYS.get(key)
        if char is not None:
            self._place(char)
            return

        # ここで拾わなかったキー(Space の再生など)は親へ。
        super().keyPressEvent(event)

    def _place(self, char):
        """カーソル位置に音符を置き、1グリッド先へ進む。

        以前は「同じ音符をもう一度でトグル」だったが、判定に使えるのが
        暫定表示(_pending)だけで、再解析が届いて暫定表示が消えると同じキーが
        配置になったり削除になったりして安定しなかった。消すのは 0 /
        Delete / BackSpace に一本化してある。"""
        addr = (self._cur_measure, self._cur_slot, self._grid)
        self._pending[addr] = char
        if char == "0":
            self._hide_note_at(*addr)
        self.noteEdited.emit(self._cur_measure, self._cur_slot, self._grid, char)
        if char != "0":
            # 消すときは進まない(その場を見ながら消せるように)。
            self.move_cursor(1)
        self.update()

    def _hide_note_at(self, m, slot, grid):
        """消したばかりの音符を、再解析を待たずに見た目から消す。

        置くときは暫定表示を上に描けばよいが、消すときは親が描いている
        権威データの音符が残ってしまい、再解析(600ms)まで消えたように
        見えなかった。手元の音符列からそのスロットぶんを抜いて描き直す。
        正式な結果が届けば set_notes で丸ごと置き換わる。

        連打・風船の帯(set_spans の側)はここでは触らない。"""
        raw = getattr(self, "_notes_raw", None)
        if not raw:
            return
        t = self._address_time(m, slot, grid)
        if t is None:
            return
        t_next = self._address_time(m, slot + 1, grid)
        half = abs(t_next - t) * 0.5 if t_next is not None else 0.01
        half = max(1e-3, half)
        # _notes_raw は譜面時刻。chart_time = audio_time + OFFSET。
        center = t + self.offset
        kept = [n for n in raw if not (center - half <= n[0] < center + half)]
        if len(kept) != len(raw):
            self._notes_raw = kept
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
            self._draw_edit_grid(p, note_top, note_strip)
            self._draw_pending(p, note_top, note_strip)
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
        for m in range(self._measure_count()):
            m_start = self._bar_time(m)
            m_end = self._bar_time(m + 1)
            if m_start is None or m_end is None or m_end < t0 or m_start > t1:
                continue
            if m >= known and t0 <= m_start <= t1:
                p.setPen(pen_virtual)
                bx = self._sec_to_x(m_start)
                p.drawLine(bx, top, bx, bottom)
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

    def _draw_cursor(self, p, top, strip):
        t = self.cursor_time()
        x = self._sec_to_x(t)
        # スロットの幅ぶんを薄く塗ってから、頭に縦線。
        m_end_t = self._address_time(self._cur_measure, self._cur_slot + 1,
                                     self._grid)
        if m_end_t is not None:
            x2 = self._sec_to_x(m_end_t)
            if x2 > x:
                p.fillRect(x, top, max(2, x2 - x), strip, QColor(255, 210, 60, 45))
        p.setPen(QPen(QColor(255, 210, 60), self.CURSOR_W))
        p.drawLine(x, top, x, top + strip)

    def _draw_legend(self, p):
        """どのキーがどの音符かを常に出しておく。ゲーム窓は固定サイズで縦の
        余裕が無いので、行を増やさずウィジェット内へ半透明で重ねる。"""
        w = self.width()
        h = self.LEGEND_H
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, 150))
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
        x += 46
        for ch in "1234567890":
            name, colkey = NOTE_INFO[ch]
            p.setPen(QColor(self._pal.get(colkey, self._pal["fg"])))
            p.drawText(x, 0, 12, h, Qt.AlignVCenter | Qt.AlignLeft, ch)
            x += 11
            p.setPen(QColor(self._pal["fg_dim"]))
            p.drawText(x, 0, 30, h, Qt.AlignVCenter | Qt.AlignLeft, name)
            x += 32
        p.setPen(QColor(self._pal["fg_dim"]))
        p.drawText(x + 6, 0, 220, h, Qt.AlignVCenter | Qt.AlignLeft,
                   "←→移動  ↑↓分割  H凡例")
