import math

from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from neotja.measure_math import (
    BASE, EXTEND, min_len, parse_measure_lines, render_converted, valid_targets, wrap_options,
)


class MeasureConvertDialog(QDialog):
    def __init__(self, main_window, initial_text, apply_cb, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.apply_cb = apply_cb
        self.setWindowTitle("ノーツ間隔リサイズ")
        self.resize(580, 680)

        self.parsed = parse_measure_lines(initial_text)
        self.has_targets = False   # _update_targets で確定する(変換候補があるか)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("▸ 変換前"))
        txt_before = QPlainTextEdit(initial_text)
        txt_before.setReadOnly(True)
        txt_before.setFixedHeight(120)
        layout.addWidget(txt_before)

        form = QFormLayout()
        self.cb_target = QComboBox()
        form.addRow("変換後の桁数", self.cb_target)
        self.cb_wrap = QComboBox()
        form.addRow("折り返し文字数", self.cb_wrap)
        layout.addLayout(form)

        layout.addWidget(QLabel("▸ 変換後プレビュー"))
        self.txt_after = QPlainTextEdit()
        layout.addWidget(self.txt_after, 1)

        # 変換できないときの理由を出す行。何も問題が無いときは邪魔なので隠す。
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.hide()
        layout.addWidget(self.lbl_status)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.clicked.connect(self.reject)
        self.btn_apply = QPushButton("エディタに適用")
        self.btn_apply.setObjectName("accentButton")
        self.btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_apply)
        layout.addLayout(btn_row)

        self.cb_target.currentTextChanged.connect(self._update_wrap_options)
        self.cb_wrap.currentTextChanged.connect(self._preview)

        self._update_targets()
        self._update_wrap_options()
        self._preview()

    def _required_div(self):
        """すべての小節を情報を落とさず表すのに必要な分割数(各小節の最小分割数の最小公倍数)。

        音符がまったく無い選択では 1(=どの分割数でもよい)を返す。
        """
        base = 1
        for p in self.parsed:
            if p["type"] == "measure" and p["notes"]:
                m = min_len(p["notes"])
                if m > 0:
                    base = base * m // math.gcd(base, m)
        return base

    def _no_target_reason(self):
        """変換候補が無い理由の文言。なぜ無理なのかまで書く。"""
        use_ext = self.main_window.config_data.get("resize_ext", False)
        need = self._required_div()
        msg = ("この選択範囲は変換できません。\n"
               "選択した小節をそのまま表すには {} 分割が必要ですが、"
               "用意されている分割数のどれもその倍数になりません。".format(need))
        # 拡張分割数を有効にすれば通る場合があるので、そこまで案内する。
        if not use_ext and any(t % need == 0 for t in EXTEND):
            msg += "\n設定で拡張分割数({}〜{})を有効にすると変換できるようになります。".format(
                EXTEND[0], EXTEND[-1])
        else:
            msg += "\n({} 分割までの選択肢では表せません。選択範囲を見直してください)".format(
                (BASE + EXTEND if use_ext else BASE)[-1])
        return msg

    def _update_targets(self):
        use_ext = self.main_window.config_data.get("resize_ext", False)
        valid = [str(t) for t in valid_targets(self.parsed, use_ext)]
        # 候補が無い状態を「文字列 "変換不可" を候補に入れる」で表すと、後段で
        # int() に失敗して黙って何もしないだけになるので、フラグで明示的に持つ。
        self.has_targets = bool(valid)
        self.cb_target.blockSignals(True)
        self.cb_target.clear()
        self.cb_target.addItems(valid)
        self.cb_target.blockSignals(False)
        self.cb_target.setEnabled(self.has_targets)
        self.cb_wrap.setEnabled(self.has_targets)

    def _update_wrap_options(self, *_):
        if not self.has_targets:
            self._preview()
            return
        tgt = int(self.cb_target.currentText())
        cfg = self.main_window.config_data
        vals = wrap_options(tgt)
        if tgt % 12 == 0:
            dflt = str(cfg.get("resize_wrap_12", 24))
        elif tgt % 16 == 0:
            dflt = str(cfg.get("resize_wrap_16", 16))
        else:
            dflt = "改行なし"

        self.cb_wrap.blockSignals(True)
        self.cb_wrap.clear()
        self.cb_wrap.addItems(vals)
        self.cb_wrap.setCurrentText(dflt if dflt in vals else vals[0])
        self.cb_wrap.blockSignals(False)
        self._preview()

    def _preview(self, *_):
        if not self.has_targets:
            # 無反応にならないよう、理由を出して「適用」も押せなくしておく。
            self.txt_after.setPlainText("")
            self.lbl_status.setText(self._no_target_reason())
            self.lbl_status.show()
            self.btn_apply.setEnabled(False)
            return
        self.lbl_status.hide()
        self.btn_apply.setEnabled(True)
        tgt = int(self.cb_target.currentText())
        wrap_val = 0
        if self.cb_wrap.currentText() != "改行なし":
            try:
                wrap_val = int(self.cb_wrap.currentText())
            except ValueError:
                pass
        self.txt_after.setPlainText(render_converted(self.parsed, tgt, wrap_val))

    def _apply(self):
        if not self.has_targets:
            return             # ボタンは無効化済み。念のための保険。
        t = self.txt_after.toPlainText().strip()
        if t:
            self.apply_cb(t)
            self.accept()
