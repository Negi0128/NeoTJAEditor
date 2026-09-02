"""Player のランチャー窓。

**再生そのものはここには無い。** 再生は PreviewDock が持っている再生
ウィンドウ(NeoTJAPlayer の窓)がそのまま担当する — 4つのモードも速度も
表示倍率もコース切替も録画も、あの窓が既に持っているので、作り直さない。

この窓がするのは「どれを再生するか」「何をまとめて録るか」を決めることだけ。
中身は2枚:

  * 曲を選ぶ(library.LibraryPage) … 覚えたフォルダの譜面を並べる
  * まとめて録画(batch.BatchPage) … 待ち行列に積んで順番に書き出す

譜面を1つ指定して起動されたとき(Editor からの受け渡し)は、この窓を出さずに
いきなり再生画面へ行く。鑑賞会でも録画でもなく「この譜面を見たい」だけなので、
選ぶ画面を挟む意味が無い。
"""

import os

from PySide6.QtWidgets import (
    QFileDialog, QMainWindow, QMessageBox, QTabWidget,
)

from neotja.player.batch import BatchPage
from neotja.player.core import PlayerCore, save_shared_settings
from neotja.player.library import LibraryPage


class PlayerWindow(QMainWindow):
    def __init__(self, config_data, parent=None):
        super().__init__(parent)
        self.cfg = config_data
        self.setWindowTitle("NeoTJAPlayer")
        self.resize(940, 620)

        self.core = PlayerCore(self.cfg)

        self.tabs = QTabWidget()
        self.library = LibraryPage(self.cfg, self._save)
        self.library.chartChosen.connect(self._on_chart_chosen)
        self.tabs.addTab(self.library, "曲を選ぶ")
        self.batch = BatchPage(self.cfg, self._save)
        self.tabs.addTab(self.batch, "まとめて録画")
        self.setCentralWidget(self.tabs)

        self._build_menu()
        self.setAcceptDrops(True)

    def _save(self):
        save_shared_settings(self.cfg)

    def _build_menu(self):
        m = self.menuBar().addMenu("ファイル")
        a = m.addAction("譜面を開く...")
        a.setShortcut("Ctrl+O")
        a.triggered.connect(self.pick_chart)
        m.addSeparator()
        a = m.addAction("再生画面を開く")
        a.setShortcut("F5")
        a.triggered.connect(self.show_player)
        m.addSeparator()
        a = m.addAction("終了")
        a.triggered.connect(self.close)

        v = self.menuBar().addMenu("表示")
        a = v.addAction("全画面（再生画面）")
        a.setShortcut("F11")
        a.triggered.connect(self._toggle_fullscreen)

    def _toggle_fullscreen(self):
        """再生画面を全画面にする。窓が出ていなければ先に出す
        (メニューから押したのに何も起きない、を避ける)。"""
        self.core.show()
        self.core.window.toggle_fullscreen()

    # ------------------------------------------------------------------
    def pick_chart(self):
        start = os.path.dirname(self.cfg.get("player_last_file", "")) or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "譜面を開く", start, "TJA Files (*.tja);;All Files (*.*)")
        if path:
            self.open_chart(path)

    def _on_chart_chosen(self, path, course_key):
        self.open_chart(path, course_key=course_key or None)

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
        self.show_player()
        if at_seconds:
            self.core.dock.audio.seek(int(at_seconds * 1000))
        self._save()
        return True

    def show_player(self):
        self.core.show()

    # ---- ドラッグ&ドロップ ----
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """.tja を落としたら再生、フォルダを落としたら一覧へ足す。"""
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        charts = [p for p in paths if p.lower().endswith(".tja")]
        folders = [p for p in paths if os.path.isdir(p)]
        if folders:
            self.library.add_folders(folders)
        if charts:
            self.open_chart(charts[0])

    def closeEvent(self, event):
        # 再生画面は別のトップレベル窓なので、ここで閉じないと残る。
        try:
            self.core.window.close()
        except Exception:  # noqa: BLE001
            pass
        self.core.shutdown()
        self._save()
        super().closeEvent(event)
