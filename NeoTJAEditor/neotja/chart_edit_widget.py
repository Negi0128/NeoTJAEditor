"""作譜モード: 波形の譜面帯にグリッドとカーソルを出し、キーで音符を置く。

波形・音符・命令帯の描画は WaveformWidget のものをそのまま使い、この派生
クラスは「グリッド線」「編集カーソル」「キー入力」「凡例」だけを足す。

カーソルは時刻ではなく **(小節番号, スロット番号)** で持つ。TJA の音符は
「ある小節のあるスロットの1文字」なので、この住所のままテキストへ書き戻せる
(neotja/note_edit.py)。時刻との変換は小節の開始/終了時刻からの比例計算。

音符を置いた直後は、正式な再解析(600ms デバウンス)を待たずに手元の音符列へ
同じ変更を当てて描く。待っていると打ち込みのたびに引っかかるため。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget  # noqa: F401  (型注釈用)

from neotja import note_edit
from neotja.waveform_widget import WaveformWidget

# 分割数の候補。TJA でよく使う値(constants.VALID_MEASURE_COUNTS の部分集合)。
GRID_CHOICES = [4, 8, 12, 16, 24, 32, 48, 64]

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

    def __init__(self, parent=None, toggle_play_cb=None):
        super().__init__(parent, toggle_play_cb=toggle_play_cb, force_dark=True)
        self._grid = 16
        self._cur_measure = 0
        self._cur_slot = 0
        self._bar_times = []       # 小節の開始時刻(音源時間)
        self._show_legend = True
        # 楽観的表示用。置いた直後、再解析が届くまでのあいだ描く音符。
        # {(小節, スロット, 分割数): 文字}
        self._pending = {}

    # ------------------------------------------------------------------
    # 外から入れるもの
    # ------------------------------------------------------------------
    def set_bar_times(self, times, offset):
        """小節の開始時刻(譜面時間)を音源時間へ直して持つ。

        build_preview_timeline の "bar_times" は (時刻, BPM, SCROLL, 表示) の
        タプル列。時刻だけあればよいので取り出す(素の float が来ても通す)。"""
        out = []
        for item in (times or []):
            t = item[0] if isinstance(item, (tuple, list)) else item
            out.append(max(0.0, float(t) - offset))
        self._bar_times = out
        self._clamp_cursor()
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
    def _measure_count(self):
        return max(0, len(self._bar_times))

    def _clamp_cursor(self):
        n = self._measure_count()
        if n <= 0:
            self._cur_measure = 0
            self._cur_slot = 0
            return
        self._cur_measure = max(0, min(self._cur_measure, n - 1))
        self._cur_slot = max(0, min(self._cur_slot, self._grid - 1))

    def cursor_time(self):
        t = note_edit.address_to_time(self._bar_times, self._cur_measure,
                                      self._cur_slot, self._grid)
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
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
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
        if self._measure_count() <= 0:
            return
        addr = (self._cur_measure, self._cur_slot, self._grid)
        # 同じ音符をもう一度置いたら消す(トグル)。今そこに何があるかは
        # 暫定表示を優先して見る。
        if self._pending.get(addr) == char and char != "0":
            char = "0"
        self._pending[addr] = char
        self.noteEdited.emit(self._cur_measure, self._cur_slot, self._grid, char)
        self.update()

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

    def _draw_edit_grid(self, p, top, strip):
        """小節内のグリッド線。小節線そのものは親が描くので、その間を割る線だけ。"""
        if not self._bar_times or strip <= 0:
            return
        t0 = self.view_start
        t1 = t0 + self._visible_span()
        pen_fine = QPen(QColor(255, 255, 255, 40), 1)
        pen_beat = QPen(QColor(255, 255, 255, 80), 1)
        for m in range(len(self._bar_times)):
            m_start = self._bar_times[m]
            m_end = (self._bar_times[m + 1] if m + 1 < len(self._bar_times)
                     else m_start + (self._bar_times[-1] - self._bar_times[-2]
                                     if len(self._bar_times) >= 2 else 2.0))
            if m_end < t0 or m_start > t1:
                continue
            span = m_end - m_start
            if span <= 0:
                continue
            # 線が潰れるほど細かいときは描かない(見づらいだけなので)。
            if span / self._grid * (self.width() / max(1e-6, t1 - t0)) < 4:
                continue
            for k in range(1, self._grid):
                t = m_start + span * (k / self._grid)
                if t < t0 or t > t1:
                    continue
                # 4分の位置は少し濃く(拍が分かるように)。
                is_beat = (k * 4) % self._grid == 0
                p.setPen(pen_beat if is_beat else pen_fine)
                x = self._sec_to_x(t)
                p.drawLine(x, top, x, top + strip)

    def _draw_pending(self, p, top, strip):
        """再解析が届くまでのあいだ、置いたばかりの音符を描く。"""
        if not self._pending or not self._bar_times:
            return
        cy = top + strip // 2
        for (m, slot, grid), char in self._pending.items():
            if char == "0":
                continue
            t = note_edit.address_to_time(self._bar_times, m, slot, grid)
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
        if not self._bar_times:
            return
        t = self.cursor_time()
        x = self._sec_to_x(t)
        # スロットの幅ぶんを薄く塗ってから、頭に縦線。
        m_end_t = note_edit.address_to_time(self._bar_times, self._cur_measure,
                                            self._cur_slot + 1, self._grid)
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
        p.setPen(QColor(255, 210, 60))
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
