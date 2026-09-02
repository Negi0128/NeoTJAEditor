"""Player のランチャー窓。

**再生そのものはここには無い。** 再生は PreviewDock が持っている再生
ウィンドウ(えぬいーさん次郎の窓)がそのまま担当する — 4つのモードも速度も
表示倍率もコース切替も録画も、あの窓が既に持っているので、作り直さない。

この窓がするのは「どれを再生するか」を決めることだけ:
  * 曲の一覧(フォルダを覚える)
  * まとめて録画への入口
  * 全画面の切り替え

譜面を1つ指定して起動されたとき(Editor からの受け渡し)は、この窓は出さずに
いきなり再生画面へ行く。
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from neotja.player.core import PlayerCore, save_shared_settings


class PlayerWindow(QMainWindow):
    def __init__(self, config_data, parent=None):
        super().__init__(parent)
        self.cfg = config_data
        self.setWindowTitle("NeoTJAPlayer")
        self.resize(760, 520)

        self.core = PlayerCore(self.cfg)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        self.lbl = QLabel("譜面(.tja)を開いてください。")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)

        row = QHBoxLayout()
        b_open = QPushButton("譜面を開く...")
        b_open.clicked.connect(self.pick_chart)
        row.addWidget(b_open)
        self.b_play = QPushButton("再生画面を開く")
        self.b_play.setEnabled(False)
        self.b_play.clicked.connect(self.show_player)
        row.addWidget(self.b_play)
        row.addStretch()
        v.addLayout(row)
        v.addStretch()

        self.setCentralWidget(central)
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    def pick_chart(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "譜面を開く", self.cfg.get("player_last_file", ""),
            "TJA Files (*.tja);;All Files (*.*)")
        if path:
            self.open_chart(path)

    def open_chart(self, path, course_key=None, at_seconds=0.0):
        """譜面を読み込んで再生画面を出す。"""
        if not os.path.exists(path):
            QMessageBox.warning(self, "NeoTJAPlayer",
                                "ファイルが見つかりません:\n%s" % path)
            return False
        if not self.core.load(path, course_key=course_key):
            QMessageBox.warning(self, "NeoTJAPlayer",
                                "譜面を読めませんでした:\n%s" % path)
            return False
        self.lbl.setText(os.path.basename(path))
        self.b_play.setEnabled(True)
        self.show_player()
        if at_seconds:
            self.core.dock.audio.seek(int(at_seconds * 1000))
        save_shared_settings(self.cfg)
        return True

    def show_player(self):
        self.core.show()

    # ---- ドラッグ&ドロップ ----
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith(".tja"):
                self.open_chart(p)
                break

    def closeEvent(self, event):
        self.core.shutdown()
        save_shared_settings(self.cfg)
        super().closeEvent(event)
