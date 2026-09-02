"""NeoTJAPlayer の窓。

**流れは1本道。** 譜面を開く → 難易度を選ぶ → 再生。

  1. 「TJAを開く」でエクスプローラーが出る(単発のファイルを開くのが
     中心という使い方なので、曲の一覧を経由させない)
  2. 難易度選択画面(select_screen)でコースを選ぶ
  3. 再生ウィンドウ(PreviewDock が持っているもの)が開く

再生そのものは作り直していない。PreviewDock は MainWindow を一切参照して
おらず、結合は全部コールバック(必須は apply_offset_cb ひとつ)なので、
1つ作ってその再生ウィンドウを見せるだけで、4つのモードも速度も表示倍率も
コース切替も録画も音声波形もそのまま手に入る。

まとめて録画(batch)は別のタブ。こちらは30譜面を並べて順番に書き出す道具で、
1曲を見るのとは目的が違うので混ぜない。
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QMainWindow, QMessageBox, QTabWidget, QVBoxLayout, QWidget,
)

from neotja.player.batch import BatchPage
from neotja.player.core import PlayerCore, save_shared_settings
from neotja.player.select_screen import SelectScreen


class PlayerWindow(QMainWindow):
    def __init__(self, config_data, parent=None):
        super().__init__(parent)
        self.cfg = config_data
        self.setWindowTitle("NeoTJAPlayer")

        self.core = PlayerCore(self.cfg)
        # 環境設定ダイアログ(SettingsDialog)は「渡された相手の config_data と
        # preview_dock を触る」だけの作りなので、その2つさえ持っていれば
        # Editor 用のものをそのまま開ける。専用の設定画面を作り直すと、
        # 同じ設定が2か所にある状態になって混乱するので避けた。
        self.config_data = self.cfg
        self._pending_path = ""

        self.select = SelectScreen()
        self.select.courseChosen.connect(self._on_course_chosen)
        self.select.cancelled.connect(self.pick_chart)
        self.select.settingsRequested.connect(self.open_settings)

        # 選択画面は 1280x720 固定なので、入れ物に中央寄せで載せる。
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.select, 0, Qt.AlignCenter)

        self.tabs = QTabWidget()
        self.tabs.addTab(page, "譜面を見る")
        self.batch = BatchPage(self.cfg, self._save)
        self.tabs.addTab(self.batch, "まとめて録画")
        self.setCentralWidget(self.tabs)
        self.resize(1300, 800)

        self._build_menu()
        self.setAcceptDrops(True)

    def _save(self):
        save_shared_settings(self.cfg)

    def _build_menu(self):
        m = self.menuBar().addMenu("ファイル")
        a = m.addAction("TJAを開く...")
        a.setShortcut("Ctrl+O")
        a.triggered.connect(self.pick_chart)
        m.addSeparator()
        m.addAction("終了", self.close)

        v = self.menuBar().addMenu("表示")
        a = v.addAction("全画面（再生画面）")
        a.setShortcut("F11")
        a.triggered.connect(self._toggle_fullscreen)

        s = self.menuBar().addMenu("設定")
        s.addAction("環境設定...", self.open_settings)

    def _toggle_fullscreen(self):
        self.core.show()
        self.core.window.toggle_fullscreen()

    # ------------------------------------------------------------------
    def pick_chart(self):
        start = os.path.dirname(self.cfg.get("player_last_file", "")) or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "TJAを開く", start, "TJA Files (*.tja);;All Files (*.*)")
        if path:
            self.open_chart(path)

    def open_chart(self, path, course_key=None, at_seconds=0.0):
        """譜面を開く。

        コースが指定されていれば(Editor からの受け渡し)そのまま再生へ。
        指定が無ければ難易度選択画面を出す。"""
        if not os.path.exists(path):
            QMessageBox.warning(self, "NeoTJAPlayer",
                                "ファイルが見つかりません:\n%s" % path)
            return False
        info = self.core.peek(path)
        if info is None:
            QMessageBox.warning(self, "NeoTJAPlayer",
                                "譜面を読めませんでした:\n%s" % path)
            return False
        title, subtitle, courses = info
        if not courses:
            QMessageBox.warning(self, "NeoTJAPlayer",
                                "この譜面にはコースが見つかりませんでした:\n%s"
                                % os.path.basename(path))
            return False

        self._pending_path = path
        self.cfg["player_last_file"] = path
        self._save()
        if course_key:
            return self._play(path, course_key, at_seconds)
        self.select.set_song(title, subtitle, courses)
        self.tabs.setCurrentIndex(0)
        self.select.setFocus(Qt.OtherFocusReason)
        return True

    def _on_course_chosen(self, course_key):
        if self._pending_path:
            self._play(self._pending_path, course_key, 0.0)

    def _play(self, path, course_key, at_seconds):
        if not self.core.load(path, course_key=course_key):
            QMessageBox.warning(self, "NeoTJAPlayer",
                                "譜面を読めませんでした:\n%s" % path)
            return False
        self.core.show()
        if at_seconds:
            self.core.dock.audio.seek(int(at_seconds * 1000))
        return True

    @property
    def preview_dock(self):
        """環境設定ダイアログが「いますぐ音声出力を開き直す」で使う。"""
        return self.core.dock

    def open_settings(self):
        """NeoTJAPlayer の設定。Editor と同じ環境設定を開く。

        音量・打音・素材(System)・録画の保存先は両方に効くもので、Player
        専用に作り直すと同じ設定が2か所にあることになって混乱する。
        譜面の編集にしか関わらない項目は、押しても Player の動きには
        影響しない(設定ファイルは共有だが、書き戻すのは Player が触る
        キーだけ — core.PLAYER_KEYS 参照)。"""
        try:
            from neotja.dialogs.settings_dialog import SettingsDialog
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "NeoTJAPlayer",
                                "設定を開けませんでした:\n%s" % exc)
            return
        # 第1引数は「config_data と preview_dock を持つ相手」。この窓が
        # それを満たすので自分を渡す(辞書ではない)。
        dlg = SettingsDialog(self, self)
        if dlg.exec():
            self._save()

    # ---- ドラッグ&ドロップ ----
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for u in event.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".tja"):
                self.open_chart(p)
                return

    def closeEvent(self, event):
        try:
            self.core.window.close()
        except Exception:  # noqa: BLE001
            pass
        self.core.shutdown()
        self._save()
        super().closeEvent(event)
