import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from neotja.audio_engine import BpmOffsetDetectWorker
from neotja.theme import COLORS
from neotja.worker_util import detach_worker as _detach_worker
from neotja.ytdlp_worker import ThumbnailFetchWorker, YtDlpDownloadWorker

THUMB_W, THUMB_H = 200, 113

# If the download makes no progress at all for this long we treat it as stuck
# and surface an error instead of leaving the user staring at a frozen bar.
# Generous enough to cover slow metadata extraction and OGG conversion, which
# legitimately report no updates for a while.
STALL_TIMEOUT_SEC = 90

# The process-level holding pen for still-running worker threads (and the
# detach helper that moves them into it) now lives in neotja/worker_util.py,
# since preview_dock and the AI chart dialog hit exactly the same
# "QThread: Destroyed while thread is still running" hazard. Imported here
# under the original private name so the rest of this module reads unchanged.


class NewProjectDialog(QDialog):
    """「新規」から開くダイアログ。空のファイルを作るか、YouTubeのURLから
    音声をOGGとして取得してすぐにBPM/OFFSET調整に入れる状態を作るかを選べる。
    YouTubeモードで成功した場合、呼び出し側は `mode`/`result_title`/
    `result_wave_path`/`result_folder` を見て新規ファイルを組み立てる。"""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.setWindowTitle("新規作成")
        self.resize(760, 620)

        self.mode = "youtube"
        self.result_title = ""
        self.result_subtitle = ""
        self.result_wave_path = None
        self.result_folder = None
        self.result_bpm = None
        self.result_offset = None
        self.enable_auto_detect = False
        self.enable_ai_gen = False
        self._worker = None
        self._thumb_worker = None
        self._detect_worker = None
        self._last_activity = 0.0
        self._download_done = False
        # Workers kept alive after they've been superseded/cancelled but haven't
        # finished their thread yet, so a still-running QThread is never garbage
        # collected out from under itself ("QThread: Destroyed while running").
        self._retired_workers = []

        # Watchdog that trips if a running download goes silent for too long
        # (see STALL_TIMEOUT_SEC). Only active while a download is in flight.
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(1000)
        self._watchdog.timeout.connect(self._check_stall)

        layout = QVBoxLayout(self)

        mode_row = QHBoxLayout()
        self.btn_blank = QPushButton("空のファイル")
        self.btn_blank.setCheckable(True)
        self.btn_youtube = QPushButton("YouTubeから作成")
        self.btn_youtube.setCheckable(True)
        self.btn_youtube.setChecked(True)
        self.btn_blank.clicked.connect(lambda: self._set_mode("blank"))
        self.btn_youtube.clicked.connect(lambda: self._set_mode("youtube"))
        mode_row.addWidget(self.btn_blank)
        mode_row.addWidget(self.btn_youtube)
        layout.addLayout(mode_row)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_blank_page())
        self.stack.addWidget(self._build_youtube_page())
        self.stack.setCurrentIndex(1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        self.btn_ok = QPushButton("作成")
        self.btn_ok.setObjectName("accentButton")
        self.btn_ok.clicked.connect(self._on_ok)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_ok)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------
    def _set_mode(self, mode):
        if self._worker is not None:
            return  # don't allow switching mid-download
        self.mode = mode
        self.btn_blank.setChecked(mode == "blank")
        self.btn_youtube.setChecked(mode == "youtube")
        self.stack.setCurrentIndex(0 if mode == "blank" else 1)

    def _build_blank_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("空のTJAテンプレートを新規作成します。"))
        v.addStretch()
        return w

    def _build_youtube_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)

        v.addWidget(QLabel("YouTubeのURLから音声を取得し、OGGに変換します。"))

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL"))
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText("https://www.youtube.com/watch?v=...")
        url_row.addWidget(self.ed_url, 1)
        v.addLayout(url_row)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("保存先フォルダ"))
        self.ed_folder = QLineEdit(self.main_window.config_data.get("last_project_folder", ""))
        folder_row.addWidget(self.ed_folder, 1)
        btn_browse = QPushButton("参照")
        btn_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(btn_browse)
        v.addLayout(folder_row)

        self.btn_download = QPushButton("ダウンロード開始")
        self.btn_download.clicked.connect(self._start_download)
        v.addWidget(self.btn_download)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet(
            f"QProgressBar {{ border: 1px solid {COLORS['border']}; border-radius: 5px; "
            f"background-color: {COLORS['surface']}; height: 18px; text-align: center; "
            f"color: {COLORS['fg_bright']}; }} "
            f"QProgressBar::chunk {{ background-color: {COLORS['accent']}; border-radius: 4px; }}"
        )
        v.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        v.addWidget(self.status_label)

        self.lbl_detect_status = QLabel("")
        self.lbl_detect_status.setStyleSheet(f"color: {COLORS['fg_dim']};")
        self.lbl_detect_status.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        v.addWidget(self.lbl_detect_status)

        preview_frame = QFrame()
        preview_frame.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['surface']}; "
            f"border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
        )
        preview_row = QHBoxLayout(preview_frame)
        preview_row.setContentsMargins(12, 12, 12, 12)
        preview_row.setSpacing(14)

        self.lbl_thumbnail = QLabel("(サムネイル)")
        self.lbl_thumbnail.setFixedSize(THUMB_W, THUMB_H)
        self.lbl_thumbnail.setAlignment(Qt.AlignCenter)
        self.lbl_thumbnail.setStyleSheet(
            f"background-color: #000; color: {COLORS['fg_dim']}; "
            f"border: 1px solid {COLORS['border']}; border-radius: 6px;"
        )
        preview_row.addWidget(self.lbl_thumbnail)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        self.lbl_video_title = QLabel("")
        self.lbl_video_title.setWordWrap(True)
        self.lbl_video_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {COLORS['fg_bright']};")
        self.lbl_video_title.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.lbl_channel = QLabel("")
        self.lbl_channel.setStyleSheet(f"color: {COLORS['fg_dim']};")
        self.lbl_channel.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        info_col.addWidget(self.lbl_video_title)
        info_col.addWidget(self.lbl_channel)
        info_col.addStretch()
        preview_row.addLayout(info_col, 1)
        v.addWidget(preview_frame)

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("TITLE"))
        self.ed_title = QLineEdit()
        self.ed_title.setPlaceholderText("ダウンロード後に動画タイトルが自動入力されます(編集可)")
        title_row.addWidget(self.ed_title, 1)
        v.addLayout(title_row)

        subtitle_row = QHBoxLayout()
        subtitle_row.addWidget(QLabel("SUBTITLE"))
        self.ed_subtitle = QLineEdit()
        self.ed_subtitle.setPlaceholderText("空欄可(未入力時は「--」になります)")
        subtitle_row.addWidget(self.ed_subtitle, 1)
        v.addLayout(subtitle_row)

        # Both the BPM/OFFSET auto-detect and AI chart generation are opt-in
        # here, off by default, and collapsed out of sight unless the user
        # explicitly wants to try them - keeping the normal/reliable
        # creation flow completely separate from the experimental one
        # (a past unconditional-auto-detect regression is what prompted
        # gating it behind this toggle instead of running it by default).
        self.chk_experimental = QCheckBox("実験的機能を使用する")
        self.chk_experimental.toggled.connect(self._on_experimental_toggled)
        v.addWidget(self.chk_experimental)

        self.experimental_panel = QWidget()
        exp_layout = QVBoxLayout(self.experimental_panel)
        exp_layout.setContentsMargins(20, 4, 0, 0)
        self.chk_auto_detect = QCheckBox("自動OFFSET/BPM検出を使用")
        self.chk_ai_gen = QCheckBox("AI譜面生成を使用(実験的)")
        exp_layout.addWidget(self.chk_auto_detect)
        exp_layout.addWidget(self.chk_ai_gen)
        self.experimental_panel.setVisible(False)
        v.addWidget(self.experimental_panel)

        v.addStretch()
        return w

    def _on_experimental_toggled(self, checked):
        self.experimental_panel.setVisible(checked)
        if not checked:
            self.chk_auto_detect.setChecked(False)
            self.chk_ai_gen.setChecked(False)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "保存先フォルダを選択", self.ed_folder.text())
        if folder:
            self.ed_folder.setText(folder)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------
    def _start_download(self):
        url = self.ed_url.text().strip()
        folder = self.ed_folder.text().strip()
        if not url:
            QMessageBox.warning(self, "確認", "YouTubeのURLを入力してください。")
            return
        if not folder:
            QMessageBox.warning(self, "確認", "保存先フォルダを選択してください。")
            return
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                QMessageBox.critical(self, "エラー", f"フォルダを作成できませんでした: {e}")
                return

        self.result_wave_path = None
        self._download_done = False
        self.btn_download.setText("中止")
        self.btn_download.clicked.disconnect()
        self.btn_download.clicked.connect(self._cancel_download)
        self.ed_url.setEnabled(False)
        self.ed_folder.setEnabled(False)
        self.status_label.setText("ダウンロードを開始しています...")

        # Start indeterminate ("busy") until yt-dlp reports a real percentage,
        # so the user can tell work is happening even before numbers arrive.
        self.progress_bar.setVisible(True)
        self._set_progress_busy()
        self._last_activity = time.monotonic()
        self._watchdog.start()

        self._worker = YtDlpDownloadWorker(url, folder)
        self._worker.progress.connect(self._on_progress_text)
        self._worker.progress_pct.connect(self._on_progress_pct)
        self._worker.finished_ok.connect(self._on_download_ok)
        self._worker.failed.connect(self._on_download_failed)
        self._worker.start()

    def _cancel_download(self):
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("キャンセル中...")

    # ------------------------------------------------------------------
    # Progress bar + stall watchdog
    # ------------------------------------------------------------------
    def _set_progress_busy(self):
        """Indeterminate/marquee mode (range 0..0) for phases without a %."""
        self.progress_bar.setRange(0, 0)

    def _on_progress_text(self, text):
        self._last_activity = time.monotonic()
        self.status_label.setText(text)

    def _on_progress_pct(self, pct):
        self._last_activity = time.monotonic()
        if pct < 0:
            self._set_progress_busy()
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(round(pct)))

    def _check_stall(self):
        if self._worker is None:
            self._watchdog.stop()
            return
        if time.monotonic() - self._last_activity > STALL_TIMEOUT_SEC:
            # Stuck: stop the worker and report it the same way as a failure.
            self._worker.cancel()
            self._on_download_failed(
                f"{STALL_TIMEOUT_SEC}秒間応答がありませんでした。"
                "処理が停止した可能性があります。\n\n"
                "ネットワーク状況を確認するか、時間を置いて再度お試しください。"
            )

    def _retire_worker(self):
        """Drop our active reference to the current worker but keep it alive
        until its thread actually finishes, so it can't be GC'd mid-run."""
        w = self._worker
        self._worker = None
        if w is not None:
            self._retired_workers.append(w)
            w.finished.connect(lambda: self._retired_workers.remove(w)
                               if w in self._retired_workers else None)

    def _reset_download_ui(self):
        self._watchdog.stop()
        self._retire_worker()
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.btn_download.setText("ダウンロード開始")
        self.btn_download.clicked.disconnect()
        self.btn_download.clicked.connect(self._start_download)
        self.ed_url.setEnabled(True)
        self.ed_folder.setEnabled(True)

    def _on_download_ok(self, ogg_path, title, uploader, thumbnail_url):
        if self._download_done:
            return
        self._download_done = True
        self._reset_download_ui()
        self.result_wave_path = ogg_path
        self.result_folder = self.ed_folder.text().strip()
        if not self.ed_title.text().strip():
            self.ed_title.setText(title)
        self.status_label.setText(f"完了: {os.path.basename(ogg_path)}")

        self.lbl_video_title.setText(title)
        self.lbl_channel.setText(f"チャンネル: {uploader}" if uploader else "")
        if thumbnail_url:
            self._thumb_worker = ThumbnailFetchWorker(thumbnail_url, self)
            self._thumb_worker.fetched.connect(self._on_thumbnail_fetched)
            self._thumb_worker.start()

        # Runs once, right after the download, so BPM:/OFFSET: come
        # pre-filled by the time the file is created - btn_ok stays disabled
        # meanwhile so the result can't be accepted with a half-finished
        # analysis. The button's own text changes to "読み込み中..." (not
        # just the small status label below) since that's what the user is
        # actually looking at/clicking - a merely-grayed-out "作成" button
        # with no other obvious feedback reads as broken/stuck rather than
        # "please wait". BpmOffsetDetectWorker also carries its own timeout
        # so this can never get stuck disabled forever.
        #
        # Opt-in only (see the 実験的機能を使用する section above) - this
        # used to run unconditionally, which is what caused the file-lock
        # crash a past project creation hit.
        self.result_bpm = None
        self.result_offset = None
        if not self.chk_auto_detect.isChecked():
            self.lbl_detect_status.setText("")
            return
        self.btn_ok.setEnabled(False)
        self.btn_ok.setText("読み込み中...")
        self.lbl_detect_status.setText("BPM/OFFSETを自動検出中(実験的)...")
        self._detect_worker = BpmOffsetDetectWorker(ogg_path, self)
        self._detect_worker.detected.connect(self._on_detect_ok)
        self._detect_worker.failed.connect(self._on_detect_failed)
        self._detect_worker.start()

    def _on_thumbnail_fetched(self, data: bytes):
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.lbl_thumbnail.setPixmap(
                pixmap.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def _on_download_failed(self, msg):
        if self._download_done:
            return
        self._download_done = True
        cancelled = msg.strip().startswith("キャンセル")
        self._reset_download_ui()
        self.status_label.setText(f"失敗: {msg}")
        # A user-initiated cancel isn't an error worth a modal; anything else
        # (network failure, bot-detection, stall) gets an explicit dialog so it
        # can't be missed.
        if not cancelled:
            QMessageBox.critical(self, "ダウンロードエラー", msg)

    def _on_detect_ok(self, bpm, offset):
        self.result_bpm = bpm
        self.result_offset = offset
        self.lbl_detect_status.setText(f"BPM/OFFSET自動検出(実験的): BPM {bpm:g} / OFFSET {offset:.3f}")
        self.btn_ok.setText("作成")
        self.btn_ok.setEnabled(True)

    def _on_detect_failed(self, msg):
        self.lbl_detect_status.setText(f"BPM/OFFSET自動検出に失敗しました(実験的): {msg}")
        self.btn_ok.setText("作成")
        self.btn_ok.setEnabled(True)

    # ------------------------------------------------------------------
    # Confirm / cancel
    # ------------------------------------------------------------------
    def _on_ok(self):
        if self.mode == "blank":
            self.accept()
            return

        if not self.result_wave_path:
            QMessageBox.warning(self, "確認", "先に音声のダウンロードを完了してください。")
            return
        title = self.ed_title.text().strip()
        if not title:
            QMessageBox.warning(self, "確認", "TITLEを入力してください。")
            return
        self.result_title = title
        self.result_subtitle = self.ed_subtitle.text().strip()
        self.enable_auto_detect = self.chk_auto_detect.isChecked()
        self.enable_ai_gen = self.chk_ai_gen.isChecked()
        self.main_window.config_data["last_project_folder"] = self.result_folder
        self.accept()

    def _on_cancel_clicked(self):
        self.reject()

    # ------------------------------------------------------------------
    # Teardown - make sure no worker thread outlives (or is destroyed by) the
    # dialog while still running.
    # ------------------------------------------------------------------
    def _shutdown_workers(self):
        self._watchdog.stop()
        for w in [self._worker, self._thumb_worker, self._detect_worker,
                  *self._retired_workers]:
            _detach_worker(w)
        self._worker = None
        self._thumb_worker = None
        self._detect_worker = None
        self._retired_workers.clear()

    def accept(self):
        # The download worker is already retired by the time OK is reachable;
        # the thumbnail/detect workers may still be in flight and must not be
        # destroyed with the dialog while running.
        self._watchdog.stop()
        _detach_worker(self._thumb_worker)
        _detach_worker(self._detect_worker)
        super().accept()

    def reject(self):
        self._shutdown_workers()
        super().reject()

    def closeEvent(self, event):
        self._shutdown_workers()
        super().closeEvent(event)
