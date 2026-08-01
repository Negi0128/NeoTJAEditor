from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPixmap, QTextCursor
from PySide6.QtWidgets import QWidget

from neotja.theme import COLORS

RULER_HEIGHT = 22


class RulerWidget(QWidget):
    """Character-column ruler shown above the editor (mirrors the original's
    Tk Canvas ruler: it marks text columns, not musical beats).

    目盛りそのものは「幅・文字幅・横スクロール量・テーマ」でしか変わらない
    のに、以前は1文字打つたびに全部を引き直していた(1回の描画で drawLine
    120本 + drawText 40個ほど、しかも打鍵ごとに数回呼ばれる)。目盛りは
    QPixmap に焼いておき、毎回の描画は「その1枚 + カーソル位置の四角」だけに
    する。"""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setFixedHeight(RULER_HEIGHT)
        self._tick_key = None
        self._tick_pm = None

        editor.horizontalScrollBar().valueChanged.connect(self.update)
        editor.verticalScrollBar().valueChanged.connect(self.update)
        editor.cursorPositionChanged.connect(self.update)
        editor.updateRequest.connect(lambda *_: self.update())

    def refresh_theme(self):
        self._tick_key = None
        self.update()

    def _ticks(self, w, h, char_w, offset, lnw):
        """目盛りだけを描いた QPixmap(キャッシュ)。"""
        key = (w, h, round(char_w, 3), round(offset, 1), lnw,
               COLORS["surface"], COLORS["border"], COLORS["fg_dim"])
        if key == self._tick_key and self._tick_pm is not None:
            return self._tick_pm
        pm = QPixmap(w, h)
        pm.fill(QColor(COLORS["surface"]))
        p = QPainter(pm)
        border = QColor(COLORS["border"])
        dim = QColor(COLORS["fg_dim"])
        start_col = int(max(0, -offset // char_w)) - 5
        end_col = int((w - offset) // char_w) + 15
        for i in range(start_col, end_col):
            x = offset + i * char_w
            if x < lnw:
                continue
            if i >= 0 and i % 4 == 0:
                p.setPen(border)
                p.drawLine(int(x), 12, int(x), 22)
                p.setPen(dim)
                p.drawText(QRectF(x, 0, 30, 12), Qt.AlignLeft | Qt.AlignVCenter, str(i))
            elif i >= 0:
                p.setPen(border)
                p.drawLine(int(x), 18, int(x), 22)
        p.end()
        self._tick_key = key
        self._tick_pm = pm
        return pm

    def paintEvent(self, event):
        editor = self.editor
        painter = QPainter(self)
        w, h = self.width(), self.height()

        char_w = editor.char_width()
        if char_w <= 0:
            painter.fillRect(0, 0, w, h, QColor(COLORS["surface"]))
            return

        lnw = editor.gutter_width() + 1
        first_block = editor.firstVisibleBlock()
        cursor0 = QTextCursor(editor.document())
        cursor0.setPosition(first_block.position())
        rect0 = editor.cursorRect(cursor0)
        offset = editor.gutter_width() + rect0.left()

        painter.drawPixmap(0, 0, self._ticks(w, h, char_w, offset, lnw))

        cur_rect = editor.cursorRect(editor.textCursor())
        cx = editor.gutter_width() + cur_rect.left()
        painter.fillRect(int(cx), 14, max(1, int(char_w)), 8, QColor(COLORS["cursor"]))
