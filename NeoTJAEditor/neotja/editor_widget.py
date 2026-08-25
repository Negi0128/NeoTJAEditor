from PySide6.QtCore import Qt, QRect, QSize, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget, QToolTip

from neotja.theme import COLORS

# 機能1(ノーツ入力音)が反応する文字。0 は常に無音(空白マス)。
_NOTE_SOUND_CHARS = "123456789"


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)


class TJAEditor(QPlainTextEdit):
    fontSizeChanged = Signal(int)
    checkpointsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabChangesFocus(False)

        self.checkpoints: set[int] = set()
        self.modified_lines: set[int] = set()
        self.invalid_lines: dict[int, int] = {}
        self.highlight_data = None  # set externally to a highlighter.HighlightData

        self.gutter = LineNumberArea(self)

        # 機能1(ノーツ入力音): 1文字ぶんの本物のキー入力(text() が単一の
        # ノーツ文字)だけに反応するコールバック。main_window から
        # set_note_typed_cb() で配線される。ペーストはこのイベントを経由
        # せず QPlainTextEdit::insertFromMimeData 側で処理されるので、
        # ブロック貼り付けで連打音が鳴ることはない。
        self._note_typed_cb = None

        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter_area)
        self.cursorPositionChanged.connect(self.viewport().update)

        # 変更行マークとチェックポイントは「絶対行番号」で持っているので、
        # 行が増減すると以降の印が全部ずれる。QTextDocument.contentsChange は
        # 実際に本文が変わったときだけ(変わった位置と文字数つきで)飛んでくる
        # ので、これを唯一の入口にして (a) 増減したぶん行番号をずらし、
        # (b) 変わった行だけを変更行として立てる。直前のブロック数を覚えて
        # おくのは、contentsChange が「何行増減したか」を教えてくれないため。
        self._prev_block_count = self.document().blockCount()
        self.document().contentsChange.connect(self._on_contents_change)

        self._update_gutter_width(0)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    # Font / metrics
    # ------------------------------------------------------------------
    # フォントが変わるたびに増やす通し番号。文字幅/行番号欄の幅はフォントと
    # 桁数でしか変わらないのに、打鍵のたびに QFontMetrics を作り直して測って
    # いたので(1打鍵あたり4〜5回)、この番号をキーにして覚えておく。
    _font_gen = 0

    def setFont(self, font):
        # 幅のキャッシュはここで必ず捨てる。Ctrl+ホイールの拡大縮小など
        # set_mono_font を通らない経路があるため、入口を1つにまとめる。
        self._font_gen += 1
        self._char_w_cache = None
        self._gutter_w_cache = None
        super().setFont(font)

    def set_mono_font(self, family: str, size: int):
        f = QFont(family, size)
        f.setFixedPitch(True)
        self.setFont(f)
        self.gutter.setFont(f)
        self._update_gutter_width(0)

    _char_w_cache = None
    _gutter_w_cache = None

    def char_width(self) -> float:
        got = self._char_w_cache
        if got is not None and got[0] == self._font_gen:
            return got[1]
        w = QFontMetrics(self.font()).horizontalAdvance("0")
        self._char_w_cache = (self._font_gen, w)
        return w

    # ------------------------------------------------------------------
    # Gutter (line numbers + checkpoint/invalid markers + dirty highlight)
    # ------------------------------------------------------------------
    def gutter_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        got = self._gutter_w_cache
        if got is not None and got[0] == (digits, self._font_gen):
            return got[1]
        w = 10 + self.fontMetrics().horizontalAdvance("9") * (digits + 2)
        self._gutter_w_cache = ((digits, self._font_gen), w)
        return w

    def _update_gutter_width(self, _):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter_area(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), QColor(COLORS["surface"]))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()
        w = self.gutter.width()

        dirty_bg = QColor("#2d3000")
        # 色は行ごとに作らない(可視行ぶん毎回 QColor を2つ作っていた)。
        col_err = QColor(COLORS["err"])
        col_dim = QColor(COLORS["fg_dim"])
        rect_top, rect_bottom = event.rect().top(), event.rect().bottom()
        while block.isValid() and top <= rect_bottom:
            if block.isVisible() and bottom >= rect_top:
                line_no = block_number + 1
                if line_no in self.modified_lines:
                    painter.fillRect(0, int(top), w, int(bottom - top), dirty_bg)

                mark = ""
                if line_no in self.checkpoints:
                    mark += "▶"
                if line_no in self.invalid_lines:
                    mark += "!"

                painter.setPen(col_err if line_no in self.invalid_lines else col_dim)
                text = f"{line_no:>3}{mark}"
                painter.drawText(0, int(top), w - 6, int(bottom - top), Qt.AlignRight, text)

            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1

    # ------------------------------------------------------------------
    # 行マークの追従(挿入/削除で行番号がずれないようにする)
    # ------------------------------------------------------------------
    def _clamp_pos(self, pos: int) -> int:
        # characterCount() は終端の番兵を含むので、有効な位置は -1 まで。
        return max(0, min(int(pos), self.document().characterCount() - 1))

    def mark_edited(self, start_pos: int, end_pos=None):
        """位置 start_pos〜end_pos にかかる行を「変更あり」として立てる。

        contentsChange 経由でも立つが、ツール類(全置換・あべこべ反転・
        ハイスピ変換など)は「閉じるときの確認ダイアログと自動保存が必ず
        効く」ことが要なので、適用側からも明示的に呼べるようにしてある。"""
        doc = self.document()
        a = self._clamp_pos(start_pos)
        b = self._clamp_pos(start_pos if end_pos is None else end_pos)
        lo = doc.findBlock(min(a, b)).blockNumber() + 1
        hi = doc.findBlock(max(a, b)).blockNumber() + 1
        for ln in range(lo, hi + 1):
            self.modified_lines.add(ln)
        self.gutter.update()

    def _shift_marks(self, base: int, delta: int):
        """base 行目以降の行マークを delta 行ぶんずらす。

        delta < 0(行が消えた)のときは、消えた行 base〜base-delta-1 に付いて
        いた印は行ごと無くなったものとして捨てる。"""
        def shifted(lines):
            out = set()
            if delta > 0:
                for ln in lines:
                    out.add(ln + delta if ln >= base else ln)
            else:
                k = -delta
                for ln in lines:
                    if ln < base:
                        out.add(ln)
                    elif ln >= base + k:
                        out.add(ln - k)
            return out

        before = self.checkpoints
        self.checkpoints = shifted(before)
        self.modified_lines = shifted(self.modified_lines)
        # invalid_lines は次の解析パスで作り直されるので、ここでは触らない。
        if self.checkpoints != before:
            # プレビュー側のチェックポイントも同じだけずらす必要がある。
            # main_window がこの signal を拾って押し込んでくれる。
            self.checkpointsChanged.emit()

    def _on_contents_change(self, position, chars_removed, chars_added):
        doc = self.document()
        delta = doc.blockCount() - self._prev_block_count
        self._prev_block_count = doc.blockCount()

        block = doc.findBlock(self._clamp_pos(position))
        line = block.blockNumber() + 1
        # 行頭ちょうどでの挿入/削除は、その行の中身ごと下(上)へ動く。行の
        # 途中なら、その行の前半は動かないので次の行から数える。
        base = line if position == block.position() else line + 1
        if delta:
            self._shift_marks(base, delta)

        if chars_removed or chars_added:
            self.mark_edited(position, position + max(0, int(chars_added)))

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------
    def toggle_checkpoint(self):
        li = self.textCursor().blockNumber() + 1
        if li in self.checkpoints:
            self.checkpoints.discard(li)
        else:
            self.checkpoints.add(li)
        self.gutter.update()
        self.checkpointsChanged.emit()

    def jump_checkpoint(self, direction: str):
        if not self.checkpoints:
            return
        cur = self.textCursor().blockNumber() + 1
        cps = sorted(self.checkpoints)
        if direction == "up":
            cands = [c for c in cps if c < cur]
            tgt = max(cands) if cands else cps[-1]
        else:
            cands = [c for c in cps if c > cur]
            tgt = min(cands) if cands else cps[0]
        block = self.document().findBlockByNumber(tgt - 1)
        cursor = QTextCursor(block)
        self.setTextCursor(cursor)
        self.centerCursor()

    # ------------------------------------------------------------------
    # Snippet insertion (used by main_window shortcut wiring)
    # ------------------------------------------------------------------
    def insert_at_cursor(self, text: str):
        self.textCursor().insertText(text)

    def set_note_typed_cb(self, cb):
        """cb(char, line_no) は、ユーザーが単一のノーツ文字(1〜9)を実際に
        キー入力した直後(挿入前の行番号)に呼ばれる(機能1)。main_window が
        設定/コース範囲/F1トグルをまとめてチェックしてから発音する。"""
        self._note_typed_cb = cb

    # ------------------------------------------------------------------
    # Dirty-line tracking / font zoom / hover tooltips
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        # ここで変更行を立てていた頃は「押されたキーの除外リスト」方式だった
        # ため、End や PageDown、Ctrl+C といった本文を変えない操作まで変更行に
        # 入り、開いて End を1回押して閉じるだけで「保存しますか？」が出て
        # いた。いまは _on_contents_change(本文が実際に変わったときだけ飛ぶ)
        # 一本に任せているので、ここでは何もしない。
        # 機能1(ノーツ入力音): text() が単一のノーツ文字そのものの、正真正銘
        # 1回ぶんのキー入力にだけ反応する。Ctrl/Alt/Meta 修飾つき(カスタム
        # ショートカット等)は除外。ペーストは QPlainTextEdit 側で
        # insertFromMimeData 経由に処理され、この keyPressEvent には来ない
        # ので、ブロック貼り付けで連打音が鳴ることはない。挿入前の行番号を
        # 使う(この1文字は改行を跨がないので、挿入前後で行番号は変わらない)。
        text = event.text()
        if (self._note_typed_cb is not None and len(text) == 1 and text in _NOTE_SOUND_CHARS
                and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))):
            li = self.textCursor().blockNumber() + 1
            self._note_typed_cb(text, li)
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            delta = 1 if event.angleDelta().y() > 0 else -1
            new_size = max(6, min(72, self.font().pointSize() + delta))
            f = self.font()
            f.setPointSize(new_size)
            self.setFont(f)
            self.gutter.setFont(f)
            self._update_gutter_width(0)
            self.fontSizeChanged.emit(new_size)
            event.accept()
            return
        super().wheelEvent(event)

    def mouseMoveEvent(self, event):
        data = self.highlight_data
        if data is not None:
            cursor = self.cursorForPosition(event.pos())
            line = cursor.blockNumber() + 1
            col = cursor.positionInBlock()
            shown = False
            for start, end, kind, idx in data.hover_spans.get(line, []):
                if start <= col < end:
                    if kind == "balloon":
                        info = data.balloon_hits.get(idx, {})
                        hits = info.get("hits", "?")
                        dur = info.get("duration", 0.0)
                        QToolTip.showText(event.globalPosition().toPoint(), f"風船 No.{idx + 1} → {dur:.2f}秒 ({hits}打)", self)
                    else:
                        info = data.roll_hits.get(idx, {})
                        hits = info.get("hits", "?")
                        dur = info.get("duration", 0.0)
                        QToolTip.showText(event.globalPosition().toPoint(), f"連打 No.{idx + 1} → {dur:.2f}秒 (想定{hits}打)", self)
                    shown = True
                    break
            if not shown:
                QToolTip.hideText()
        super().mouseMoveEvent(event)
