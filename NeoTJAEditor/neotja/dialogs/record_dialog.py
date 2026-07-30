"""えぬいーさん次郎(ゲームプレビュー)の動画書き出しダイアログ。

書き出し本体は neotja/recorder.py。ここは範囲・fps・画面サイズを決めて、
別スレッドで走らせ、進捗と中止を面倒みるだけ。

画面を録画するのではなく1コマずつ描き直すので、書き出し中にアプリを
触っても、他のウィンドウを重ねても、出来上がりには一切影響しない。
"""

import os

import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout,
)

from neotja import recorder
from neotja.theme import COLORS

# step() 1回あたりに使う時間の目安。絵を描くのは GUI スレッドでしかできないので
# (recorder.VideoRecording の説明を参照)、この長さだけ描いてはイベントループへ
# 戻る。16ms = 60fps 1コマぶんで、これなら操作の引っかかりは体感できない。
_SLICE_SEC = 0.016

# 出力は固定。曲の頭から終わりまで / 1080p / 120fps。
FPS = 120
CANVAS = "1080p"
# レーンは 908px 幅しかないので、そのまま 1920px へ引き伸ばすとぼやける。
# 2倍の細かさで描いてから縮めることで 1080p でも輪郭が立つ。
SUPERSAMPLE = 2
# 1080p120 は枚数が多いので、エンコード側は medium より速い preset にする
# (crf は据え置きなので見た目の劣化はほぼ無く、待ち時間だけ縮む)。
X264_PRESET = "fast"


class RecordDialog(QDialog):
    """書き出す範囲と画質を決めて動画を作る。

    描画に使うウィジェットは、いま見えているプレビューとは別に画面外へ用意する
    (recorder.make_offline_widget)。そのため書き出し中も再生位置やコースは
    動かないし、途中で編集しても出来上がりは始めた時点の譜面のまま。"""

    def __init__(self, main_window, preview_data, offset, song_path,
                 song_seconds, default_dir, parent=None):
        super().__init__(parent or main_window)
        self._mw = main_window
        self._preview = preview_data or {}
        self._offset = offset
        self._song = song_path
        self._rec = None            # 進行中の VideoRecording
        self._widget = None         # 画面外の描画用ウィジェット
        self._cancel = False
        self._t0 = 0.0
        # step() 1回で描くコマ数。1コマの実測から毎回調整するので、最初は
        # 必ず 1 から始める(いきなり多めに描くと初回だけ引っかかる)。
        self._batch = 1
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._tick)
        self.setWindowTitle("動画を書き出す")
        self.resize(560, 340)

        # プレビュー側の長さは音源の読み込みが済むまで 0 のことがある。
        # そのままだと範囲が「0秒〜0秒」になって何も書き出せないので、
        # 音源そのものに聞き直す。
        if song_seconds <= 0.0:
            song_seconds = recorder.probe_song_seconds(song_path)
        self._song_seconds = song_seconds

        layout = QVBoxLayout(self)
        course = self._preview.get("course_label") or self._preview.get("course_key") or "-"
        head = QLabel(f"対象コース: {course}    曲の長さ: {song_seconds:.1f} 秒")
        head.setStyleSheet("font-weight: bold;")
        layout.addWidget(head)

        form = QFormLayout()

        # 出力は固定(曲全体 / 1080p / 120fps)。選ばせない代わりに、何が
        # 出てくるのかはここに書いておく。
        fixed = QLabel(f"曲全体（0 〜 {song_seconds:.1f} 秒）  1080p (1920×1080)  120 fps")
        fixed.setStyleSheet("font-weight: bold;")
        form.addRow("出力", fixed)

        self.chk_hit = QCheckBox("打音(ドン/カッ)を入れる")
        self.chk_hit.setChecked(True)
        form.addRow("", self.chk_hit)

        path_row = QHBoxLayout()
        self.ed_path = QLineEdit(os.path.join(default_dir, self._default_name()))
        btn_browse = QPushButton("参照...")
        btn_browse.clicked.connect(self._browse)
        path_row.addWidget(self.ed_path)
        path_row.addWidget(btn_browse)
        form.addRow("保存先", path_row)

        layout.addLayout(form)

        # 1080p120 は 1秒あたり 120枚。実測でおよそ曲の長さの2倍かかるので、
        # 「あと何分待つのか」を先に出しておく(進捗にも残り時間が出る)。
        self.lbl_status = QLabel(
            "画面を録画するのではなく1コマずつ描き直すので、書き出し中に\n"
            "アプリを操作しても出来上がりには影響しません。\n"
            f"1080p/120fps は枚数が多く、目安で {song_seconds * 2 / 60:.0f} 分ほどかかります。")
        self.lbl_status.setStyleSheet(f"color: {COLORS['fg_dim']};")
        layout.addWidget(self.lbl_status)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)

        self.buttons = QDialogButtonBox()
        self.btn_start = self.buttons.addButton("書き出す", QDialogButtonBox.AcceptRole)
        self.btn_close = self.buttons.addButton("閉じる", QDialogButtonBox.RejectRole)
        self.btn_start.clicked.connect(self._start)
        self.btn_close.clicked.connect(self.reject)
        layout.addWidget(self.buttons)

    def _default_name(self):
        base = os.path.splitext(os.path.basename(self._mw.current_file or "chart"))[0]
        course = self._preview.get("course_key") or ""
        return f"{base}{('_' + course) if course else ''}.mp4"

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "動画の保存先", self.ed_path.text(), "MP4 動画 (*.mp4)")
        if path:
            self.ed_path.setText(path)

    # ------------------------------------------------------------------
    def _start(self):
        out = self.ed_path.text().strip()
        if not out:
            QMessageBox.warning(self, "動画を書き出す", "保存先を指定してください。")
            return
        if not out.lower().endswith(".mp4"):
            out += ".mp4"
            self.ed_path.setText(out)
        if self._song_seconds < 1.0:
            QMessageBox.warning(self, "動画を書き出す", "音源の長さが取得できませんでした。")
            return
        if os.path.exists(out) and QMessageBox.question(
                self, "動画を書き出す",
                f"{os.path.basename(out)} は既にあります。上書きしますか？") != QMessageBox.Yes:
            return

        cfg = self._mw.config_data
        self._widget = recorder.make_offline_widget(
            self._preview, self._offset, cfg.get("se_text_enabled", True))

        # 曲のデコードと音声合成はここで済ませる(3分の曲でも 2秒ほど)。
        # そのあいだ画面が固まるので、先に「準備中」を出しておく。
        self._set_running(True)
        self.lbl_status.setText("音声を用意しています...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        # 最初の1コマだけはフォントの読み込みや各種キャッシュの用意で 100ms 前後
        # かかる。録画が始まってからだとそこだけ引っかかるので、「準備中」を
        # 出しているこの間に1枚捨て描きして済ませておく。
        from PySide6.QtGui import QImage as _QImage
        self._widget.render(_QImage(self._widget.width(), self._widget.height(),
                                    _QImage.Format_RGBA8888))
        try:
            self._rec = recorder.VideoRecording(
                self._widget, out,
                preview_data=self._preview, offset=self._offset, song_path=self._song,
                # 曲の頭から終わりまで。end_sec=None で音源そのものの長さになる。
                start_sec=0.0, end_sec=None,
                fps=FPS, canvas=CANVAS, supersample=SUPERSAMPLE, preset=X264_PRESET,
                don_path=cfg.get("hit_sound_don_path", "") or "",
                ka_path=cfg.get("hit_sound_ka_path", "") or "",
                sfx_volume=float(cfg.get("sfx_volume", 0.7)),
                hit_sounds=self.chk_hit.isChecked(),
            )
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self._finish()
            QMessageBox.warning(self, "動画を書き出す", f"書き出しを開始できませんでした:\n{e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._cancel = False
        self._t0 = time.perf_counter()
        self._timer.start()

    def _tick(self):
        """1コマ〜数コマ描いてはイベントループへ返す。詳しくは _SLICE_SEC。"""
        if self._rec is None:
            self._timer.stop()
            return
        if self._cancel:
            self._timer.stop()
            self._rec.abort()
            self._finish()
            self.lbl_status.setText("中止しました。")
            return

        t0 = time.perf_counter()
        more = self._rec.step(self._batch)
        spent = time.perf_counter() - t0
        # 1コマの実測から、次回のコマ数を _SLICE_SEC に収まるよう調整する。
        per = spent / max(1, self._batch)
        if per > 0:
            self._batch = max(1, min(60, int(_SLICE_SEC / per)))
        self._on_progress(self._rec.frame, self._rec.total_frames)

        if more:
            return
        self._timer.stop()
        rec, self._rec = self._rec, None
        # 最後に ffmpeg が動画を閉じ切るのを待つ数百 ms は、こちらからは
        # 縮められない。無言で固まったように見えないよう先に表示を出す。
        self.bar.setValue(100)
        self.lbl_status.setText("仕上げています...")
        QApplication.processEvents()
        try:
            path = rec.finish()
        except Exception as e:  # noqa: BLE001
            self._finish()
            self.lbl_status.setText("失敗しました。")
            QMessageBox.warning(self, "動画を書き出す", f"書き出しに失敗しました:\n{e}")
            return
        el = time.perf_counter() - self._t0
        self._finish()
        self.lbl_status.setText(f"書き出しました({el:.0f} 秒): {path}")
        if QMessageBox.question(
                self, "動画を書き出す",
                f"書き出しました。({el:.0f} 秒)\n{path}\n\n"
                "保存先のフォルダを開きますか？") == QMessageBox.Yes:
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    def _set_running(self, running):
        for wdg in (self.chk_hit, self.ed_path):
            wdg.setEnabled(not running)
        self.bar.setVisible(running)
        self.bar.setValue(0)
        self.btn_start.setText("中止" if running else "書き出す")
        try:
            self.btn_start.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_start.clicked.connect(self._request_cancel if running else self._start)
        self.btn_close.setEnabled(not running)
        if running:
            self.lbl_status.setText("書き出し中...")

    def _request_cancel(self):
        self._cancel = True
        self.btn_start.setEnabled(False)
        self.lbl_status.setText("中止しています...")

    def _on_progress(self, done, total):
        if total > 0:
            self.bar.setValue(int(done * 100 / total))
            el = time.perf_counter() - self._t0
            rest = (el / done * (total - done)) if done > 0 else 0.0
            self.lbl_status.setText(
                f"書き出し中... {done * 100 // total}%  ({done}/{total} コマ)"
                f"  残り約 {rest:.0f} 秒")

    def _finish(self):
        self._timer.stop()
        self._rec = None
        self._widget = None            # 画面外ウィジェットを解放
        self.btn_start.setEnabled(True)
        self._set_running(False)

    def closeEvent(self, event):
        # 書き出し中に閉じられたら、その場で畳んで書きかけを消す。すべて
        # GUI スレッド上なので、待たされることも取り残されることもない。
        if self._rec is not None:
            self._timer.stop()
            self._rec.abort()
            self._rec = None
            self._widget = None
        super().closeEvent(event)
