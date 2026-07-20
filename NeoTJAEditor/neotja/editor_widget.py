from PySide6.QtCore import Qt, QRect, QSize, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QTextCursor, QTextFormat
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget, QToolTip

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

        self._update_gutter_width(0)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    # Font / metrics
    # ------------------------------------------------------------------
    def set_mono_font(self, family: str, size: int):
        f = QFont(family, size)
        f.setFixedPitch(True)
        self.setFont(f)
        self.gutter.setFont(f)
        self._update_gutter_width(0)

    def char_width(self) -> float:
        return QFontMetrics(self.font()).horizontalAdvance("0")

    # ------------------------------------------------------------------
    # Gutter (line numbers + checkpoint/invalid markers + dirty highlight)
    # ------------------------------------------------------------------
    def gutter_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 10 + self.fontMetrics().horizontalAdvance("9") * (digits + 2)

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
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_no = block_number + 1
                if line_no in self.modified_lines:
                    painter.fillRect(0, int(top), w, int(bottom - top), dirty_bg)

                mark = ""
                if line_no in self.checkpoints:
                    mark += "▶"
                if line_no in self.invalid_lines:
                    mark += "!"

                painter.setPen(QColor(COLORS["err"]) if line_no in self.invalid_lines else QColor(COLORS["fg_dim"]))
                text = f"{line_no:>3}{mark}"
                painter.drawText(0, int(top), w - 6, int(bottom - top), Qt.AlignRight, text)

            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1

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
        if event.key() not in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt,
                                Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            li = self.textCursor().blockNumber() + 1
            self.modified_lines.add(li)
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
