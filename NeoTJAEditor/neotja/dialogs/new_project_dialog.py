import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from neotja.theme import COLORS
from neotja.ytdlp_worker import ThumbnailFetchWorker, YtDlpDownloadWorker

THUMB_W, THUMB_H = 200, 113


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
        self._worker = None
        self._thumb_worker = None

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

        self.status_label = QLabel("")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        v.addWidget(self.status_label)

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

        v.addStretch()
        return w

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
        self.btn_download.setText("中止")
        self.btn_download.clicked.disconnect()
        self.btn_download.clicked.connect(self._cancel_download)
        self.ed_url.setEnabled(False)
        self.ed_folder.setEnabled(False)
        self.status_label.setText("ダウンロードを開始しています...")

        self._worker = YtDlpDownloadWorker(url, folder)
        self._worker.progress.connect(self.status_label.setText)
        self._worker.finished_ok.connect(self._on_download_ok)
        self._worker.failed.connect(self._on_download_failed)
        self._worker.start()

    def _cancel_download(self):
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("キャンセル中...")

    def _reset_download_ui(self):
        self._worker = None
        self.btn_download.setText("ダウンロード開始")
        self.btn_download.clicked.disconnect()
        self.btn_download.clicked.connect(self._start_download)
        self.ed_url.setEnabled(True)
        self.ed_folder.setEnabled(True)

    def _on_download_ok(self, ogg_path, title, uploader, thumbnail_url):
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

    def _on_thumbnail_fetched(self, data: bytes):
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.lbl_thumbnail.setPixmap(
                pixmap.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def _on_download_failed(self, msg):
        self._reset_download_ui()
        self.status_label.setText(f"失敗: {msg}")

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
        self.main_window.config_data["last_project_folder"] = self.result_folder
        self.accept()

    def _on_cancel_clicked(self):
        if self._worker is not None:
            self._worker.cancel()
        self.reject()
