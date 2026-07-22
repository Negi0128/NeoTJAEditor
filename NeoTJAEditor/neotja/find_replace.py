"""エディタ上部に出す検索・置換バー。QPlainTextEdit には検索機能が無いので、
QTextDocument.find() を薄くラップして前方/後方検索・大小区別・正規表現・
置換/全置換・折り返し検索を提供する。Ctrl+F / Ctrl+H で開き、Esc で閉じる。"""
from PySide6.QtCore import Qt, QRegularExpression
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget,
)


class FindReplaceBar(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(4)

        row.addWidget(QLabel("検索:"))
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("検索する文字")
        self.find_edit.returnPressed.connect(self._find_next)
        self.find_edit.textChanged.connect(self._update_count)
        row.addWidget(self.find_edit, 2)

        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedWidth(30)
        self.btn_prev.setToolTip("前を検索 (Shift+Enter)")
        self.btn_prev.clicked.connect(lambda: self._find_next(backward=True))
        row.addWidget(self.btn_prev)

        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedWidth(30)
        self.btn_next.setToolTip("次を検索 (Enter)")
        self.btn_next.clicked.connect(self._find_next)
        row.addWidget(self.btn_next)

        self.chk_case = QCheckBox("Aa")
        self.chk_case.setToolTip("大文字/小文字を区別")
        row.addWidget(self.chk_case)
        self.chk_regex = QCheckBox(".*")
        self.chk_regex.setToolTip("正規表現")
        self.chk_regex.toggled.connect(self._update_count)
        row.addWidget(self.chk_regex)

        row.addWidget(QLabel("置換:"))
        self.repl_edit = QLineEdit()
        self.repl_edit.setPlaceholderText("置き換える文字")
        self.repl_edit.returnPressed.connect(self._replace_one)
        row.addWidget(self.repl_edit, 2)

        self.btn_replace = QPushButton("置換")
        self.btn_replace.clicked.connect(self._replace_one)
        row.addWidget(self.btn_replace)
        self.btn_replace_all = QPushButton("すべて")
        self.btn_replace_all.clicked.connect(self._replace_all)
        row.addWidget(self.btn_replace_all)

        self.lbl_count = QLabel("")
        self.lbl_count.setMinimumWidth(70)
        row.addWidget(self.lbl_count)

        btn_close = QPushButton("×")
        btn_close.setFixedWidth(26)
        btn_close.setToolTip("閉じる (Esc)")
        btn_close.clicked.connect(self.close_bar)
        row.addWidget(btn_close)

        self._replace_widgets = (self.repl_edit, self.btn_replace, self.btn_replace_all)
        self.hide()

    # ------------------------------------------------------------------
    # 開閉
    # ------------------------------------------------------------------
    def show_find(self):
        self._show(with_replace=False)

    def show_replace(self):
        self._show(with_replace=True)

    def _show(self, with_replace):
        sel = self.editor.textCursor().selectedText()
        if sel and " " not in sel:   # 改行を含まない選択だけ流用
            self.find_edit.setText(sel)
        for w in self._replace_widgets:
            w.setVisible(with_replace)
        for lbl in self.findChildren(QLabel):
            if lbl.text() == "置換:":
                lbl.setVisible(with_replace)
        self.show()
        self.find_edit.setFocus()
        self.find_edit.selectAll()
        self._update_count()

    def close_bar(self):
        self.hide()
        self.editor.setFocus()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close_bar()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (event.modifiers() & Qt.ShiftModifier):
            self._find_next(backward=True)
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 検索
    # ------------------------------------------------------------------
    def _flags(self, backward=False):
        flags = QTextDocument.FindFlags()
        if backward:
            flags |= QTextDocument.FindBackward
        if self.chk_case.isChecked():
            flags |= QTextDocument.FindCaseSensitively
        return flags

    def _needle(self):
        text = self.find_edit.text()
        if not text:
            return None
        if self.chk_regex.isChecked():
            rx = QRegularExpression(text)
            if not self.chk_case.isChecked():
                rx.setPatternOptions(QRegularExpression.CaseInsensitiveOption)
            if not rx.isValid():
                return False   # 不正な正規表現
            return rx
        return text

    def _find_next(self, backward=False):
        needle = self._needle()
        if needle is None:
            return
        if needle is False:
            self.lbl_count.setText("正規表現エラー")
            return
        doc = self.editor.document()
        flags = self._flags(backward)
        cur = doc.find(needle, self.editor.textCursor(), flags)
        if cur.isNull():
            # 折り返し: 末尾/先頭から一度だけやり直す。
            edge = QTextCursor(doc)
            edge.movePosition(QTextCursor.End if backward else QTextCursor.Start)
            cur = doc.find(needle, edge, flags)
        if cur.isNull():
            self.lbl_count.setText("見つかりません")
            return
        self.editor.setTextCursor(cur)
        self.editor.centerCursor()
        self._update_count()

    def _update_count(self):
        needle = self._needle()
        if needle is None:
            self.lbl_count.setText("")
            return
        if needle is False:
            self.lbl_count.setText("正規表現エラー")
            return
        doc = self.editor.document()
        flags = self._flags()
        count = 0
        cur = QTextCursor(doc)
        cur.movePosition(QTextCursor.Start)
        while True:
            cur = doc.find(needle, cur, flags)
            if cur.isNull():
                break
            count += 1
            if count > 9999:
                break
        self.lbl_count.setText(f"{count}件" if count else "0件")

    # ------------------------------------------------------------------
    # 置換
    # ------------------------------------------------------------------
    def _replace_one(self):
        needle = self._needle()
        if not needle:
            return
        # 現在の選択が検索語に一致していれば置換、していなければ次を探す。
        cur = self.editor.textCursor()
        sel = cur.selectedText().replace(" ", "\n")
        matches = False
        if sel:
            if self.chk_regex.isChecked():
                m = needle.match(sel)
                matches = m.hasMatch() and m.capturedLength() == len(sel)
            else:
                a, b = sel, self.find_edit.text()
                matches = (a == b) if self.chk_case.isChecked() else (a.lower() == b.lower())
        if matches:
            cur.insertText(self.repl_edit.text())
        self._find_next()

    def _replace_all(self):
        needle = self._needle()
        if not needle:
            return
        if needle is False:
            self.lbl_count.setText("正規表現エラー")
            return
        doc = self.editor.document()
        flags = self._flags()
        repl = self.repl_edit.text()
        editcur = QTextCursor(doc)
        editcur.beginEditBlock()
        find_from = QTextCursor(doc)
        find_from.movePosition(QTextCursor.Start)
        n = 0
        while True:
            found = doc.find(needle, find_from, flags)
            if found.isNull():
                break
            found.insertText(repl)
            find_from = found   # 置換後の位置から続行(無限ループ防止)
            n += 1
            if n > 99999:
                break
        editcur.endEditBlock()
        self.lbl_count.setText(f"{n}件置換")
