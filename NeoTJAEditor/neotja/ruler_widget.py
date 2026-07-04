from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QTextCursor
from PySide6.QtWidgets import QWidget

from neotja.theme import COLORS

RULER_HEIGHT = 22


class RulerWidget(QWidget):
    """Character-column ruler shown above the editor (mirrors the original's
    Tk Canvas ruler: it marks text columns, not musical beats)."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setFixedHeight(RULER_HEIGHT)

        editor.horizontalScrollBar().valueChanged.connect(self.update)
        editor.verticalScrollBar().valueChanged.connect(self.update)
        editor.cursorPositionChanged.connect(self.update)
        editor.updateRequest.connect(lambda *_: self.update())

    def paintEvent(self, event):
        editor = self.editor
        painter = QPainter(self)
        w = self.width()
        painter.fillRect(0, 0, w, self.height(), QColor(COLORS["surface"]))

        char_w = editor.char_width()
        if char_w <= 0:
            return

        lnw = editor.gutter_width() + 1

        first_block = editor.firstVisibleBlock()
        cursor0 = QTextCursor(editor.document())
        cursor0.setPosition(first_block.position())
        rect0 = editor.cursorRect(cursor0)
        offset = editor.gutter_width() + rect0.left()

        start_col = int(max(0, -offset // char_w)) - 5
        end_col = int((w - offset) // char_w) + 15

        border = QColor(COLORS["border"])
        dim = QColor(COLORS["fg_dim"])

        for i in range(start_col, end_col):
            x = offset + i * char_w
            if x < lnw:
                continue
            if i >= 0 and i % 4 == 0:
                painter.setPen(border)
                painter.drawLine(int(x), 12, int(x), 22)
                painter.setPen(dim)
                painter.drawText(QRectF(x, 0, 30, 12), Qt.AlignLeft | Qt.AlignVCenter, str(i))
            elif i >= 0:
                painter.setPen(border)
                painter.drawLine(int(x), 18, int(x), 22)

        cur_rect = editor.cursorRect(editor.textCursor())
        cx = editor.gutter_width() + cur_rect.left()
        painter.fillRect(int(cx), 14, max(1, int(char_w)), 8, QColor(COLORS["cursor"]))
