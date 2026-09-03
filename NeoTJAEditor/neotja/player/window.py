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

from PySide6.QtCore import Qt, QTimer
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
        # 再生画面の「● 録画」ボタン。**PreviewDock は呼び先を渡されない限り
        # 何もしない**ので、ここを配線しないとボタンが黙って効かない
        # (Editor 側だけ配線してあり、Player では押しても無反応だった)。
        self.core.dock.record_cb = self.open_video_recorder
        self._record_dialog = None
        # 環境設定ダイアログ(SettingsDialog)は「渡された相手の config_data と
        # preview_dock を触る」だけの作りなので、その2つさえ持っていれば
        # Editor 用のものをそのまま開ける。専用の設定画面を作り直すと、
        # 同じ設定が2か所にある状態になって混乱するので避けた。
        self.config_data = self.cfg
        # RecordDialog は渡された相手の config_data と **current_file** を見る
        # (書き出すファイルの既定名をここから作る)。Editor の MainWindow に
        # 合わせて、同じ名前で出しておく。
        self._pending_path = ""

        self.select = SelectScreen()
        self.select.click_sound_cb = self.core.click_sound
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
        # 選曲画面(858x528)がちょうど収まる大きさ。
        self.resize(900, 620)

        self._build_menu()
        self.setAcceptDrops(True)
        # 譜面を開くまでは選曲画面の BGM。窓が出てから鳴らす(組み立ての
        # 途中で音を出すと、まだ何も見えていないのに音だけ始まる)。
        QTimer.singleShot(300, self.core.play_select_bgm)

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
        a = v.addAction("再生画面のボタンを隠す / 出す")
        a.setShortcut("F11")
        a.triggered.connect(self._toggle_overlay)

        s = self.menuBar().addMenu("設定")
        s.addAction("環境設定...", self.open_settings)

    def _toggle_overlay(self):
        """再生画面の上に浮いているボタン(モード切替・コース・録画・倍率・
        FPS)を隠す/出す。

        全画面ではなくこちらにしたのは、鑑賞会で見せたいのが「絵だけ」で
        あって、窓の大きさそのものは変えたくないことが多いため。"""
        self.core.show()
        w = self.core.window
        self._overlay_shown = not getattr(self, "_overlay_shown", True)
        w.set_overlay_visible(self._overlay_shown)

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
        # 選んだ譜面の音を DEMOSTART から流す(本家の選曲画面と同じ聞こえ方)。
        from neotja.player.core import demo_start_seconds, read_text
        try:
            self.core.play_demo(path, demo_start_seconds(read_text(path)))
        except Exception:  # noqa: BLE001
            pass
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
        # 試聴を止めてから本編へ。止めないと、譜面の頭出しと試聴の折り返しが
        # 取り合って再生位置が飛ぶ。
        self.core.stop_audio()
        if not self.core.load(path, course_key=course_key):
            QMessageBox.warning(self, "NeoTJAPlayer",
                                "譜面を読めませんでした:\n%s" % path)
            return False
        self.core.show()
        # 窓を前面にしてから予約する。再生ウィンドウは「前面でなくなったら
        # 止める」作りなので、出した直後のフォーカスの動きで、せっかく始めた
        # 再生がその場で止められてしまう(実際にそうなった)。
        w = self.core.window
        w.raise_()
        w.activateWindow()
        # 音源が読めたところで自動的に流す。Player は「見る」道具なので、
        # 開いてから再生ボタンを押させる理由が無い。
        QTimer.singleShot(120, lambda: self.core.play_chart(at_seconds))
        return True

    @property
    def current_file(self):
        """いま開いている譜面。RecordDialog が既定の出力名に使う。"""
        return self.core.current_file

    def _is_recording(self):
        """書き出しが進行中か(ダイアログが開いているだけは含まない)。"""
        dlg = self._record_dialog
        if dlg is None:
            return False
        try:
            return bool(dlg.is_busy())
        except RuntimeError:
            # C++ 側が既に破棄されていた。
            self._record_dialog = None
            return False

    def open_video_recorder(self):
        """再生画面の「● 録画」から、いま見ている譜面を mp4 に書き出す。

        中身は Editor の open_video_recorder と同じ RecordDialog。違うのは
        譜面がエディタの文字ではなくファイルから来ることだけ。ダイアログは
        モーダルにしない(書き出しは始めた時点の写しに対して行われるので、
        走らせたまま再生を続けられる)。"""
        path = self.core.current_file
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "動画を書き出す",
                                    "先に譜面を開いてください。")
            return
        if self._record_dialog is not None:
            if self._is_recording():
                # 2本同時には走らせない。同じ描画用ウィジェットと ffmpeg を
                # 取り合って、どちらの動画も壊れる。
                self._record_dialog.raise_()
                self._record_dialog.activateWindow()
                QMessageBox.information(
                    self, "動画を書き出す",
                    "すでに動画の書き出しが進行中です。" + "\n"
                    "終わるか、中止してからもう一度お試しください。")
                return
            # 開きっぱなしだが走ってはいない。押した時点の譜面で作り直す。
            self._record_dialog.close()
            self._record_dialog = None

        pd = self.core.dock
        wave = pd.wave_path()
        if not wave or not os.path.exists(wave):
            QMessageBox.warning(
                self, "動画を書き出す",
                "音源(WAVE)が見つかりません。TJA の WAVE: を確認してください。")
            return
        from neotja.player.core import read_text
        try:
            content = read_text(path)
        except OSError as exc:
            QMessageBox.warning(self, "動画を書き出す",
                                "譜面を読めませんでした:" + "\n%s" % exc)
            return
        preview = self.core.analyzer.build_preview_timeline(
            content, None, self.core.course_override,
            branch_level=self.core.branch_level)
        out_dir = os.path.dirname(path)
        if not out_dir or not os.path.isdir(out_dir):
            out_dir = os.path.expanduser("~")
        # 録画を始めたときのモードで、何を録るかが決まる(Editor と同じ)。
        layout = "wave" if pd.bottom_stack.currentIndex() == pd.MODE_WAVE else "game"
        from neotja.dialogs.record_dialog import RecordDialog
        dlg = RecordDialog(self, preview, pd.spin_offset.value(), wave,
                           pd.duration_seconds(), out_dir, layout=layout)
        dlg.finished.connect(self._on_record_dialog_finished)
        self._record_dialog = dlg
        dlg.show()

    def _on_record_dialog_finished(self, *_a):
        # 持ったままだと画面外の描画用ウィジェット(スキン一式)が居座る。
        self._record_dialog = None

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
