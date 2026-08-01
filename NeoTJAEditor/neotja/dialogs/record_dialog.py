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
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QVBoxLayout,
)

from neotja import recorder
from neotja import settings as settings_mod
from neotja.theme import COLORS

# step() 1回あたりに使う時間の目安。絵を描くのは GUI スレッドでしかできないので
# (recorder.VideoRecording の説明を参照)、この長さだけ描いてはイベントループへ
# 戻る。16ms = 60fps 1コマぶんで、これなら操作の引っかかりは体感できない。
_SLICE_SEC = 0.016

# 画質は2択。範囲は常に「曲の頭から終わりまで」で固定。
#   supersample: レーンは 908px 幅しかないので、そのまま引き伸ばすとぼやける。
#     この倍率で細かく描いてから縮小するため、輪郭が立つ。
#   所要目安: 曲の長さに対する倍率(実測から)。ダイアログの案内に使う。
# supersample は「出力解像度と同じ画素数で描く」値にしてある(720p は等倍、
# 1080p は 1.5 倍 = 1920x1080)。以前は両方 2 倍で描いてから ffmpeg 側で
# lanczos 縮小しており、出力の 4 倍(720p)/1.8 倍(1080p)の画素を毎コマ描いて
# 捨てていた。等倍にすると縮小フィルタ自体が不要になり、パイプに流す量も減る。
QUALITY_PRESETS = [
    ("60 fps / 720p",  {"fps": 60,  "canvas": "720p",  "supersample": 1.0,
                        "preset": "veryfast", "time_factor": 0.25}),
    ("60 fps / 1080p", {"fps": 60, "canvas": "1080p", "supersample": 1.5,
                        "preset": "veryfast", "time_factor": 0.45}),
    ("120 fps / 1080p", {"fps": 120, "canvas": "1080p", "supersample": 1.5,
                         "preset": "veryfast", "time_factor": 0.9}),
]
DEFAULT_QUALITY = 1        # 既定は 60fps/1080p(速度と画質のつり合いが良い)


def _estimate_text(song_seconds, factor):
    """その画質での所要時間の目安。速い/きれいといった言い回しではなく、
    実際にどれだけ待つのかを分で出す(曲の長さで変わるので実行時に作る)。"""
    est = max(0.0, song_seconds) * factor
    if est < 60.0:
        return "1分未満"
    return "%d分" % round(est / 60.0)


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

        # 範囲は常に曲全体(選ばせない)。画質だけ選べる。
        rng = QLabel(f"曲全体（0 〜 {song_seconds:.1f} 秒）")
        rng.setStyleSheet("font-weight: bold;")
        form.addRow("範囲", rng)

        self.cb_quality = QComboBox()
        for label, cfgq in QUALITY_PRESETS:
            est = _estimate_text(song_seconds, cfgq.get("time_factor", 1.0))
            self.cb_quality.addItem(f"{label}（出力 約{est}）", cfgq)
        self.cb_quality.setCurrentIndex(DEFAULT_QUALITY)
        self.cb_quality.currentIndexChanged.connect(self._update_estimate)
        form.addRow("画質", self.cb_quality)

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

        self.lbl_status = QLabel()
        self._update_estimate()
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

    def _update_estimate(self):
        """選んだ画質での所要目安を出す。実測(曲の長さ×係数)からのざっくり値で、
        書き出しが始まれば進捗側に実測の残り時間が出る。"""
        factor = self.cb_quality.currentData().get("time_factor", 1.0)
        est_text = _estimate_text(self._song_seconds, factor)
        self.lbl_status.setText(
            "画面を録画するのではなく1コマずつ描き直すので、書き出し中に\n"
            "アプリを操作しても出来上がりには影響しません。\n"
            f"この画質だと目安で 約{est_text} ほどかかります。")

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
        q = self.cb_quality.currentData()
        # 打音は再生と同じものを使う。設定の欄だけを見るとスキン同梱の打音が
        # 拾えず、動画だけ内蔵音になってしまう。
        rec_don, rec_ka = settings_mod.effective_hit_sound_paths(cfg)
        # 次に開いたときも同じ場所が出るよう、保存先のフォルダを覚える。
        cfg["record_output_dir"] = os.path.dirname(out)
        settings_mod.save_settings(cfg)
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
                fps=q["fps"], canvas=q["canvas"],
                supersample=q["supersample"], preset=q["preset"],
                don_path=rec_don,
                ka_path=rec_ka,
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
