import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout,
    QWidget,
)

from neotja.audio_guess import AudioGuessWorker
from neotja.dialogs.glass import apply_glass, field_label, make_card
from neotja.theme import COLORS, glass_colors
from neotja.theme import rgba as _rgba
from neotja.worker_util import detach_worker as _detach_worker
from neotja.ytdlp_worker import ThumbnailFetchWorker, YtDlpDownloadWorker

THUMB_W, THUMB_H = 200, 113

# If the download makes no progress at all for this long we treat it as stuck
# and surface an error instead of leaving the user staring at a frozen bar.
# Generous enough to cover slow metadata extraction and OGG conversion, which
# legitimately report no updates for a while.
STALL_TIMEOUT_SEC = 90

# URLがまだ無い(か、YouTubeのものと判断できない)ときにブラウザで開く行き先。
# 「とりあえずYouTubeを開いて動画を探す」という導線のための既定値。
YOUTUBE_HOME_URL = "https://www.youtube.com/"

# 動画IDだけを貼り付ける人が実際に多いので、11文字のID単体も受け付ける。
_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# ホスト名だけで判定する。watch/shorts/embed/live などパスの形は種類が多く、
# 全部を列挙して弾くと正しいURLまで蹴ってしまうため、あえて緩くしている
# (どのみち開くだけで、間違っていればブラウザ側で分かる)。
_YOUTUBE_URL_RE = re.compile(
    r"^(?:https?://)?(?:[\w-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com)(?:/|$)",
    re.IGNORECASE,
)


def resolve_youtube_url(text):
    """入力欄の文字列を、ブラウザで開けるYouTubeのURLに正規化する。
    YouTubeのURLでも動画IDでもないと判断したら None を返す(呼び出し側は
    そのときYouTubeのトップを開く)。"""
    text = (text or "").strip()
    if not text:
        return None
    if _YOUTUBE_ID_RE.match(text):
        return "https://www.youtube.com/watch?v=" + text
    if _YOUTUBE_URL_RE.match(text):
        # スキームを省いて貼られた場合、そのまま webbrowser に渡すと
        # ローカルのパス扱いになりかねないので必ず https:// を補う。
        if not text.lower().startswith(("http://", "https://")):
            return "https://" + text
        return text
    return None


# 利用者の指定 2026-10-05: 中身の yt-dlp は SoundCloud にも対応しているので、
# 画面の側もそう見えるようにする。URLが無い(かSoundCloudのものと判断できない)
# ときの行き先。
SOUNDCLOUD_HOME_URL = "https://soundcloud.com/"

# YouTube と同じくホスト名だけで判定する。SoundCloud は /user/track のほかに
# /user/sets/... や短縮の on.snd.sc があり、パスの形を数え上げると取りこぼすため。
# サブドメインは m.(モバイル) や on.(共有リンク) が実際に出てくるので許す。
_SOUNDCLOUD_URL_RE = re.compile(
    r"^(?:https?://)?(?:[\w-]+\.)*(?:soundcloud\.com|snd\.sc)(?:/|$)",
    re.IGNORECASE,
)


def resolve_soundcloud_url(text):
    """入力欄の文字列を、ブラウザで開けるSoundCloudのURLに正規化する。
    SoundCloudのURLでないと判断したら None を返す(呼び出し側はそのとき
    SoundCloudのトップを開く)。
    YouTubeの「動画IDだけ貼る」ような救済は入れていない。SoundCloudには
    固定長のIDが無く、何でもIDに見えてしまうため。"""
    text = (text or "").strip()
    if not text:
        return None
    if _SOUNDCLOUD_URL_RE.match(text):
        # スキーム省略時に https:// を補うのはYouTube側と同じ理由
        # (ローカルパス扱いを避ける)。
        if not text.lower().startswith(("http://", "https://")):
            return "https://" + text
        return text
    return None

# 「参照」で選べる音源の拡張子。ffmpeg が読める範囲で、実際に手元にありがち
# なものだけ並べる(「すべて」も付けるので漏れても困らない)。
AUDIO_FILE_FILTER = (
    "音声ファイル (*.ogg *.mp3 *.wav *.flac *.m4a *.aac *.opus *.wma);;すべてのファイル (*)"
)


class LocalAudioImportWorker(QThread):
    """手元の音源を保存先フォルダへ OGG として取り込む(利用者の指定 2026-11-12)。

    なぜ元ファイルをそのまま使わないか: 新規作成の仕上げ(main_window の
    _apply_youtube_project)は「渡された音源を曲フォルダへ複製してから元を
    消す」+「名前は必ず <TITLE>.ogg」という、yt-dlp の落としたファイル向けの
    動きをする。利用者の元ファイルを渡すと消されてしまうし、wav を .ogg と
    名乗らせることになる。そこで先に保存先フォルダへ OGG の複製を作り、
    以後はダウンロード完了とまったく同じ道を通す。

    finished_ok の引数は YtDlpDownloadWorker.finished_ok と同じ並び
    (ogg_path, title, uploader, thumbnail_url)。受け手を共有するため。"""

    finished_ok = Signal(str, str, str, str)
    failed = Signal(str)

    def __init__(self, src: str, out_dir: str, parent=None):
        super().__init__(parent)
        self.src = src
        self.out_dir = out_dir
        self._cancelled = False
        self._proc = None

    def cancel(self):
        self._cancelled = True
        p = self._proc
        if p is not None:
            try:
                p.kill()
            except OSError:
                pass

    def _dest_path(self) -> str:
        stem = os.path.splitext(os.path.basename(self.src))[0] or "song"
        dest = os.path.join(self.out_dir, stem + ".ogg")
        # 元ファイルそのものを上書き/削除してしまわないよう、同じ場所なら
        # 別名にする(あとで main_window が元を消すため)。
        if os.path.abspath(dest) == os.path.abspath(self.src):
            dest = os.path.join(self.out_dir, stem + "_import.ogg")
        return dest

    def run(self):
        try:
            dest = self._dest_path()
            os.makedirs(self.out_dir, exist_ok=True)
            if self.src.lower().endswith(".ogg"):
                shutil.copy2(self.src, dest)
            else:
                import imageio_ffmpeg
                exe = imageio_ffmpeg.get_ffmpeg_exe()
                cmd = [exe, "-y", "-v", "error", "-nostdin", "-i", self.src,
                       "-vn", "-c:a", "libvorbis", "-q:a", "6", dest]
                kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.PIPE, **kw)
                _, err = self._proc.communicate()
                code = self._proc.returncode
                self._proc = None
                if self._cancelled:
                    self.failed.emit("キャンセルされました。")
                    return
                if code != 0 or not os.path.exists(dest):
                    lines = (err or b"").decode("utf-8", "replace").strip().splitlines()
                    self.failed.emit("OGGへの変換に失敗しました: " + (lines[-1] if lines else "原因不明"))
                    return
        except Exception as e:  # noqa: BLE001 - 取り込みの失敗で窓を落とさない
            self.failed.emit(f"音源を取り込めませんでした: {e}")
            return
        if self._cancelled:
            self.failed.emit("キャンセルされました。")
            return
        title = os.path.splitext(os.path.basename(self.src))[0]
        self.finished_ok.emit(dest, title, "", "")


# The process-level holding pen for still-running worker threads (and the
# detach helper that moves them into it) now lives in neotja/worker_util.py,
# since preview_dock and the AI chart dialog hit exactly the same
# "QThread: Destroyed while thread is still running" hazard. Imported here
# under the original private name so the rest of this module reads unchanged.


class NewProjectDialog(QDialog):
    """「新規」から開くダイアログ。空のファイルを作るか、YouTube/SoundCloudの
    URLから音声をOGGとして取得してすぐにBPM/OFFSET調整に入れる状態を作るかを
    選べる(中身の yt-dlp がどちらにも対応しているため、画面もそう見せる)。
    モード名は歴史的に "youtube" のままで、設定の鍵として使われている。
    このモードで成功した場合、呼び出し側は `mode`/`result_title`/
    `result_wave_path`/`result_folder` を見て新規ファイルを組み立てる。"""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.setWindowTitle("新規作成")
        self.resize(800, 640)

        self.mode = "youtube"
        self.result_title = ""
        self.result_subtitle = ""
        self.result_wave_path = None
        self.result_folder = None
        self.result_bpm = None
        self.result_offset = None
        # 「実験的機能」「AI譜面生成」のチェックは画面から外した(利用者の
        # 指定 2026-11-13)。main_window は enable_ai_gen を読むので、属性
        # だけは常に False のまま残す。
        self.enable_auto_detect = False
        self.enable_ai_gen = False
        self._worker = None
        self._thumb_worker = None
        self._guess_worker = None
        # 最後の解析結果 (bpm, offset)。自動入力のチェックを後から入れた
        # ときに、解析をやり直さずここから埋めるため。
        self._guess_result = None
        # 解析にかける音源。両方のチェックが外れていて解析を飛ばしたあとで
        # チェックを入れたとき、ここから解析を始める。
        self._guess_path = None
        # 解析が自動で入れた文字。欄がこれのままなら次の解析で上書きして
        # よく、違っていれば利用者が打ったので触らない。
        self._auto_bpm_text = ""
        self._auto_offset_text = ""
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
        # 見た目は起動画面と同じ「ガラスと光」(dialogs/glass.py、利用者の
        # 承認 2026-11-12)。中身は「音源」「譜面の情報」の 2 枚のカードに
        # まとめ、主役のボタン(Enter の行き先)だけアクセント色にする。
        apply_glass(self)

        mode_row = QHBoxLayout()
        self.btn_blank = QPushButton("空のファイル")
        self.btn_blank.setCheckable(True)
        # 見える文字だけ変える。モード名 "youtube" は設定や他の箇所が
        # 参照しうるので内部の鍵は据え置き(利用者の指定 2026-10-05)。
        self.btn_youtube = QPushButton("YouTube/SoundCloudから作成")
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
        # Enter を受けるボタンを決める(利用者の要望 2026-11-07)。開いた
        # 直後は「ダウンロード開始」。ボタンが出そろったここで1回呼ぶ。
        self._sync_default_button()

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------
    def _sync_default_button(self):
        """Enter で押されるボタンを、いまの状態に合わせて決める。

        利用者の要望 2026-11-07「URL張る→enterでDL→DL後enterでそのまま
        新規作成できるようにしてほしい」。まだ落としていないあいだは
        「ダウンロード開始」、落とし終わったら「作成」。
        """
        from neotja.dialogs import set_default_button
        want = self.btn_ok
        if (self.mode == "youtube" and not self.result_wave_path
                and self._worker is None):
            want = self.btn_download
        set_default_button(self, want)
        # 主役の見た目(アクセント色)も同じ考えで付け替える: 光っている
        # ボタンが「次に押すもの」。ただし「作成」は押して通るときだけ光らせる
        # (落としている最中に光らせると、押しても警告が出るだけで紛らわしい)。
        ok_ready = self.mode == "blank" or bool(self.result_wave_path)
        self._set_accent(self.btn_download, want is self.btn_download)
        self._set_accent(self.btn_ok, want is self.btn_ok and ok_ready)

    @staticmethod
    def _set_accent(button, on):
        """主役の見た目(QSS の #accentButton)を付け外しする。objectName を
        変えただけでは QSS が当て直されないので、polish し直す。"""
        name = "accentButton" if on else ""
        if button.objectName() == name:
            return
        button.setObjectName(name)
        st = button.style()
        st.unpolish(button)
        st.polish(button)
        button.update()

    def _set_mode(self, mode):
        if self._worker is not None:
            return  # don't allow switching mid-download
        self.mode = mode
        self.btn_blank.setChecked(mode == "blank")
        self.btn_youtube.setChecked(mode == "youtube")
        self.stack.setCurrentIndex(0 if mode == "blank" else 1)
        self._sync_default_button()

    def _build_blank_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        card, g = make_card("空のファイル")
        g.addWidget(QLabel("空のTJAテンプレートを新規作成します。"), 1, 0, 1, 2)
        v.addWidget(card)
        v.addStretch()
        return w

    def _build_youtube_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # 「音源」のカード: 音をどこから持ってくるか(URL か手元のファイル)と
        # どこへ置くか。説明は見出しの右に添える(行を別に取ると窓が伸びる)。
        src_card, src = make_card(
            "音源", "YouTube / SoundCloud のURLから音声を取得し、OGGに変換します。")

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText(
            "https://www.youtube.com/watch?v=... / https://soundcloud.com/..."
        )
        url_row.addWidget(self.ed_url, 1)
        # URLを手元に持っていない人がここで手が止まってしまうので、
        # 「参照」ボタンと同じ位置(入力欄の右端)から配信元へ飛べるようにする。
        # URLが入っているときはその曲/動画を開く動作に変わり、貼ったURLが
        # 目的のものかどうかをダウンロード前に確認できる。
        self.btn_open_youtube = QPushButton()
        self.btn_open_youtube.clicked.connect(self._open_youtube)
        url_row.addWidget(self.btn_open_youtube)
        self.btn_open_soundcloud = QPushButton()
        self.btn_open_soundcloud.clicked.connect(self._open_soundcloud)
        url_row.addWidget(self.btn_open_soundcloud)
        # 入力の変化に合わせて両方のラベル/ツールチップを切り替える。
        self.ed_url.textChanged.connect(self._update_open_buttons)
        # URL を貼って Enter でそのまま落とし始める(利用者の要望
        # 2026-11-07)。
        self.ed_url.returnPressed.connect(self._start_download)
        self._update_open_buttons()
        src.addWidget(field_label("URL"), 1, 0)
        src.addLayout(url_row, 1, 1)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self.ed_folder = QLineEdit(self.main_window.config_data.get("last_project_folder", ""))
        folder_row.addWidget(self.ed_folder, 1)
        btn_browse = QPushButton("参照")
        btn_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(btn_browse)
        src.addWidget(field_label("保存先フォルダ"), 2, 0)
        src.addLayout(folder_row, 2, 1)

        # ダウンロードの行: ボタンの右に進み具合の棒。ボタンは中身なりの幅に
        # 固定する(棒が隠れているあいだに横いっぱいへ伸びないように)。幅の
        # 下限は「中止」に変わったときに縮んで棒が跳ねないため。
        dl_row = QHBoxLayout()
        dl_row.setSpacing(10)
        self.btn_download = QPushButton("ダウンロード開始")
        self.btn_download.clicked.connect(self._start_download)
        self.btn_download.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_download.setMinimumWidth(150)
        dl_row.addWidget(self.btn_download, 0, Qt.AlignLeft)

        # URL の代わりに手元の音源を使う道(利用者の指定 2026-11-12)。
        # 選んだ時点で保存先フォルダへ OGG として取り込み、ダウンロード完了と
        # 同じ扱いに乗せる(LocalAudioImportWorker の説明を参照)。
        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        self.ed_file = QLineEdit()
        self.ed_file.setReadOnly(True)
        self.ed_file.setPlaceholderText("URLの代わりに手元の音声ファイルを使う場合は「参照」から選択")
        file_row.addWidget(self.ed_file, 1)
        self.btn_browse_file = QPushButton("参照")
        self.btn_browse_file.clicked.connect(self._browse_audio_file)
        file_row.addWidget(self.btn_browse_file)

        # 進み具合の棒は窓の QSS(glass.py)の色で描く — 主役のボタンと同じ
        # グラデーションの帯。ここで個別に色を当てると見た目が揃わない。
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        dl_row.addWidget(self.progress_bar, 1)
        # ダウンロードの行は URL と保存先のすぐ下(落とす操作の続き)、手元の
        # ファイルの行はその下に「もう一つの道」として置く。
        src.addLayout(dl_row, 3, 1)
        src.addWidget(field_label("音源ファイル"), 4, 0)
        src.addLayout(file_row, 4, 1)

        self.status_label = QLabel("")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.lbl_detect_status = QLabel("")
        self.lbl_detect_status.setObjectName("dim")
        self.lbl_detect_status.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        status_col = QVBoxLayout()
        status_col.setSpacing(2)
        status_col.addWidget(self.status_label)
        status_col.addWidget(self.lbl_detect_status)
        src.addLayout(status_col, 5, 1)
        v.addWidget(src_card)

        # 「譜面の情報」のカード: 左にサムネイル、右に TITLE などの欄。
        # サムネイルを欄の横に並べるのは、縦に積むと窓が画面からはみ出す
        # 高さになるため(サムネイルの高さと欄 3 行がほぼ同じ)。取得した
        # 曲名と投稿者は見出しの行の右へ — 見出しの行は横が空いているので、
        # 行を別に取らずに済む(まだ何も取得していないときに空行が残らない)。
        info_card, info = make_card("譜面の情報")
        self.lbl_thumbnail = QLabel("(サムネイル)")
        self.lbl_thumbnail.setFixedSize(THUMB_W, THUMB_H)
        self.lbl_thumbnail.setAlignment(Qt.AlignCenter)
        # 地は欄と同じ半透明(真っ黒だと明るいテーマで穴に見える)。
        g = glass_colors()
        self.lbl_thumbnail.setStyleSheet(
            f"background-color: {_rgba(g['field_fill'])}; color: {COLORS['fg_dim']}; "
            f"border: 1px solid {_rgba(g['field_border'])}; border-radius: 8px;"
        )
        self.lbl_video_title = QLabel("")
        self.lbl_video_title.setWordWrap(True)
        self.lbl_video_title.setStyleSheet(f"font-weight: bold; color: {COLORS['fg_bright']};")
        self.lbl_video_title.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.lbl_channel = QLabel("")
        self.lbl_channel.setObjectName("dim")
        self.lbl_channel.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        # 見出しの後ろ(伸び縮みの手前)へ。曲名は長いと折り返す。
        info_card.header.insertWidget(1, self.lbl_video_title, 1)
        info_card.header.insertWidget(2, self.lbl_channel)

        fields = QGridLayout()
        fields.setHorizontalSpacing(10)
        fields.setVerticalSpacing(10)
        fields.setColumnStretch(1, 1)
        fields.setColumnStretch(4, 1)
        self.ed_title = QLineEdit()
        self.ed_title.setPlaceholderText("ダウンロード後に曲/動画のタイトルが自動入力されます(編集可)")
        # このカードの欄の名前は、左端そろえ用の幅を取らない(サムネイルの
        # 横で欄が狭いので、BPM/OFFSET の説明の字が切れないように)。
        def short_label(text):
            lbl = field_label(text)
            lbl.setMinimumWidth(0)
            return lbl
        fields.addWidget(short_label("TITLE"), 0, 0)
        fields.addWidget(self.ed_title, 0, 1, 1, 4)

        self.ed_subtitle = QLineEdit()
        self.ed_subtitle.setPlaceholderText("空欄可(未入力時は「--」になります)")
        fields.addWidget(short_label("SUBTITLE"), 1, 0)
        fields.addWidget(self.ed_subtitle, 1, 1, 1, 4)

        # BPM と OFFSET(利用者の指定 2026-11-12)。音源が決まると裏で解析して
        # 自動で埋めるが、欄はふつうの入力欄のままで、打ち直せる。解析中は
        # 空欄のプレースホルダに「解析中…」を出す(文字を入れると利用者の
        # 入力と見分けがつかなくなるので、プレースホルダで知らせる)。
        self.ed_bpm = QLineEdit()
        self.ed_bpm.setPlaceholderText("音源から自動入力(編集可)")
        fields.addWidget(short_label("BPM"), 2, 0)
        fields.addWidget(self.ed_bpm, 2, 1)
        fields.setColumnMinimumWidth(2, 6)
        fields.addWidget(short_label("OFFSET"), 2, 3)
        self.ed_offset = QLineEdit()
        self.ed_offset.setPlaceholderText("音源から自動入力(編集可)")
        fields.addWidget(self.ed_offset, 2, 4)
        fields.setRowStretch(3, 1)

        info.addWidget(self.lbl_thumbnail, 1, 0, Qt.AlignTop)
        info.addLayout(fields, 1, 1)
        v.addWidget(info_card)

        # BPM / OFFSET を音源から自動で入れるかどうか(利用者の指定
        # 2026-11-13、「実験的機能」「AI譜面生成」のチェックと入れ替え)。
        # 曲によっては解析が外れて毎回消す手間になるので、欄ごとに切れる
        # ようにした。置き場所は前のチェックと同じカードの外、地の上。
        # 前回の選択は config_data に覚える(次に開いたときも同じ状態)。
        cfg = self.main_window.config_data
        auto_row = QHBoxLayout()
        auto_row.setContentsMargins(4, 0, 0, 0)
        auto_row.setSpacing(16)
        self.chk_auto_bpm = QCheckBox("BPM自動入力")
        self.chk_auto_bpm.setChecked(bool(cfg.get("new_project_auto_bpm", True)))
        self.chk_auto_offset = QCheckBox("OFFSET自動入力")
        self.chk_auto_offset.setChecked(bool(cfg.get("new_project_auto_offset", True)))
        self.chk_auto_bpm.toggled.connect(lambda on: self._on_auto_toggled("bpm", on))
        self.chk_auto_offset.toggled.connect(lambda on: self._on_auto_toggled("offset", on))
        auto_row.addWidget(self.chk_auto_bpm)
        auto_row.addWidget(self.chk_auto_offset)
        auto_row.addStretch(1)
        v.addLayout(auto_row)
        self._restore_placeholders()

        v.addStretch()
        return w

    def _auto_fields(self):
        """(欄, 自動入力のチェック, 前回自動で入れた文字の属性名) の組。"""
        return (("bpm", self.ed_bpm, self.chk_auto_bpm, "_auto_bpm_text"),
                ("offset", self.ed_offset, self.chk_auto_offset, "_auto_offset_text"))

    @staticmethod
    def _format_guess(which, value):
        """解析の値を欄に入れる文字へ。BPM は整数、OFFSET は小数3桁。"""
        if value is None:
            return None
        return str(int(value)) if which == "bpm" else f"{value:.3f}"

    def _on_auto_toggled(self, which, checked):
        cfg = self.main_window.config_data
        cfg["new_project_auto_" + which] = bool(checked)
        # 窓をキャンセルで閉じても選択は覚えておきたいので、その場で保存する。
        # 保存口が無い呼び出し元(試験用の仮の親など)では config_data に
        # 入れるだけにする。
        save = getattr(self.main_window, "_save_config", None)
        if callable(save):
            save()

        for key, edit, chk, auto_attr in self._auto_fields():
            if key != which:
                continue
            auto_text = getattr(self, auto_attr)
            if not checked:
                # 自動で入れたままの値は消す(チェックを外した = 自動の値は
                # 要らない、という意味に取る)。利用者が打った値は残す。
                if auto_text and edit.text().strip() == auto_text:
                    edit.clear()
                setattr(self, auto_attr, "")
            elif self._guess_result is not None:
                # 解析はもう済んでいる。やり直さず、覚えておいた結果で埋める。
                value = self._guess_result[0 if key == "bpm" else 1]
                text = self._format_guess(key, value)
                if text is not None and not self._field_is_user_typed(edit, auto_text):
                    edit.setText(text)
                    setattr(self, auto_attr, text)
            elif self._guess_worker is None and self._guess_path:
                # 両方切れていて解析を飛ばしていた。ここで初めて回す。
                self._start_guess(self._guess_path)
        self._restore_placeholders()

    def _update_open_buttons(self, _text=None):
        """入力欄の中身に応じてボタンの意味が変わるので、押す前に
        どちらの動作になるかが分かるようラベルとツールチップを更新する。
        YouTube用とSoundCloud用の2つあり、貼られたURLに該当する側だけが
        「この動画/曲を開く」に変わる(どちらに貼ったのかが一目で分かる)。"""
        yt_url = resolve_youtube_url(self.ed_url.text())
        if yt_url:
            self.btn_open_youtube.setText("この動画を開く")
            self.btn_open_youtube.setToolTip(
                "入力されたURLの動画をブラウザで開きます。\n" + yt_url
            )
        else:
            self.btn_open_youtube.setText("YouTubeを開く")
            self.btn_open_youtube.setToolTip(
                "YouTubeをブラウザで開きます。動画のURLをここにコピーしてください。"
            )

        sc_url = resolve_soundcloud_url(self.ed_url.text())
        if sc_url:
            self.btn_open_soundcloud.setText("この曲を開く")
            self.btn_open_soundcloud.setToolTip(
                "入力されたURLの曲をブラウザで開きます。\n" + sc_url
            )
        else:
            self.btn_open_soundcloud.setText("SoundCloudを開く")
            self.btn_open_soundcloud.setToolTip(
                "SoundCloudをブラウザで開きます。曲のURLをここにコピーしてください。"
            )

    def _open_browser(self, url):
        try:
            webbrowser.open(url)
        except Exception as e:
            # ブラウザが見つからない環境などで落とす必要はないので、
            # 状態表示だけに留めてダイアログの操作は続行できるようにする。
            self.status_label.setText(f"ブラウザを開けませんでした: {e}")

    def _open_youtube(self):
        # URLが未入力/YouTubeのものと判断できないときはトップページへ。
        self._open_browser(resolve_youtube_url(self.ed_url.text()) or YOUTUBE_HOME_URL)

    def _open_soundcloud(self):
        # URLが未入力/SoundCloudのものと判断できないときはトップページへ。
        self._open_browser(
            resolve_soundcloud_url(self.ed_url.text()) or SOUNDCLOUD_HOME_URL
        )

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "保存先フォルダを選択", self.ed_folder.text())
        if folder:
            self.ed_folder.setText(folder)

    # ------------------------------------------------------------------
    # Local audio file
    # ------------------------------------------------------------------
    def _browse_audio_file(self):
        if self._worker is not None:
            return  # ダウンロード/取り込み中は選び直させない
        start_dir = os.path.dirname(self.ed_file.text()) or self.ed_folder.text()
        path, _ = QFileDialog.getOpenFileName(self, "音源ファイルを選択", start_dir, AUDIO_FILE_FILTER)
        if path:
            self.import_local_file(path)

    def import_local_file(self, path):
        """手元の音源を取り込む。保存先フォルダが要るのはダウンロードと同じ
        理由(OGG の複製をそこへ作る)。進み具合の見せ方もダウンロードと
        そろえ、終わったら _on_download_ok に合流する。"""
        folder = self.ed_folder.text().strip()
        if not folder:
            QMessageBox.warning(self, "警告", "保存先フォルダを選択してください。")
            return
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                QMessageBox.critical(self, "エラー", f"フォルダを作成できませんでした: {e}")
                return
        self.ed_file.setText(path)
        self.result_wave_path = None
        self._download_done = False
        self.btn_download.setText("中止")
        self.btn_download.clicked.disconnect()
        self.btn_download.clicked.connect(self._cancel_download)
        self.btn_browse_file.setEnabled(False)
        self.ed_url.setEnabled(False)
        self.ed_folder.setEnabled(False)
        self.status_label.setText(f"取り込み中: {os.path.basename(path)}")
        self._sync_default_button()

        self.progress_bar.setVisible(True)
        self._set_progress_busy()
        self._last_activity = time.monotonic()
        self._watchdog.start()

        self._worker = LocalAudioImportWorker(path, folder)
        self._worker.finished_ok.connect(self._on_download_ok)
        self._worker.failed.connect(self._on_download_failed)
        self._worker.start()

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------
    def _start_download(self):
        url = self.ed_url.text().strip()
        folder = self.ed_folder.text().strip()
        if not url:
            QMessageBox.warning(self, "警告", "YouTube または SoundCloud の URL を入力してください。")
            return
        if not folder:
            QMessageBox.warning(self, "警告", "保存先フォルダを選択してください。")
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
        self._sync_default_button()

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
        self.btn_browse_file.setEnabled(True)
        self._sync_default_button()

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
        # 落とし終わったら Enter は「作成」へ(利用者の要望 2026-11-07)。
        self._sync_default_button()

        self.lbl_video_title.setText(title)
        # SoundCloudだと「チャンネル」ではなく投稿者/アーティストなので、
        # どちらでも嘘にならない言い方にする(利用者の指定 2026-10-05)。
        self.lbl_channel.setText(f"投稿者: {uploader}" if uploader else "")
        if thumbnail_url:
            self._thumb_worker = ThumbnailFetchWorker(thumbnail_url, self)
            self._thumb_worker.fetched.connect(self._on_thumbnail_fetched)
            self._thumb_worker.start()

        # 音源が決まったので BPM/OFFSET の解析を裏で回す(利用者の指定
        # 2026-11-12)。「作成」は止めない: 解析を待たずに作ってもよく、その
        # ときは欄に入っている値(空なら既定)が使われる。
        self._start_guess(ogg_path)

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

    # ------------------------------------------------------------------
    # BPM / OFFSET auto-fill (neotja/audio_guess.py)
    # ------------------------------------------------------------------
    def _field_is_user_typed(self, edit, auto_text):
        """欄の中身が利用者の手によるものか。空か、前回の解析が入れた文字の
        ままなら「触っていない」と見なして上書きしてよい。"""
        text = edit.text().strip()
        return bool(text) and text != auto_text

    def _start_guess(self, path):
        # 新しい音源なので、前の音源の解析結果は使えない。
        self._guess_path = path
        self._guess_result = None
        # 前の解析がまだ走っていたら切り離す(結果は捨てる)。
        old = self._guess_worker
        if old is not None:
            try:
                old.guessed.disconnect(self._on_guess_ok)
                old.failed.disconnect(self._on_guess_failed)
            except (RuntimeError, TypeError):
                pass
            _detach_worker(old)
        self._guess_worker = None

        wanted = [key for key, _e, chk, _a in self._auto_fields() if chk.isChecked()]
        if not wanted:
            # 両方とも自動入力しない。解析は重い(数秒かかる)ので回さない。
            # 後からチェックを入れたら _on_auto_toggled がここへ戻ってくる。
            self.lbl_detect_status.setText("")
            self._restore_placeholders()
            return

        for key, edit, chk, auto_attr in self._auto_fields():
            if not chk.isChecked():
                continue
            if not self._field_is_user_typed(edit, getattr(self, auto_attr)):
                edit.clear()
                edit.setPlaceholderText("解析中…")
        names = " と ".join("BPM" if k == "bpm" else "OFFSET" for k in wanted)
        self.lbl_detect_status.setText(f"音源から {names} を解析中…")

        self._guess_worker = AudioGuessWorker(path, self)
        self._guess_worker.guessed.connect(self._on_guess_ok)
        self._guess_worker.failed.connect(self._on_guess_failed)
        self._guess_worker.start()

    def _restore_placeholders(self):
        # 自動入力を切った欄に「自動入力」と出すと嘘になるので、案内を変える。
        # 解析中は「解析中…」を出したままにする(途中でチェックを入れた欄も)。
        for _k, edit, chk, _a in self._auto_fields():
            if not chk.isChecked():
                edit.setPlaceholderText("空欄可(未入力時は既定値)")
            elif self._guess_worker is not None:
                edit.setPlaceholderText("解析中…")
            else:
                edit.setPlaceholderText("音源から自動入力(編集可)")

    def _release_guess_worker(self):
        """終わった解析を「走っていない」扱いにする。参照は _retired_workers
        へ移し、スレッドが本当に抜けるまで GC されないようにする(ダウンロード
        の _retire_worker と同じ理由)。"""
        done = self._guess_worker
        self._guess_worker = None
        if done is not None:
            self._retired_workers.append(done)
            done.finished.connect(lambda: self._retired_workers.remove(done)
                                  if done in self._retired_workers else None)

    def _on_guess_ok(self, bpm, offset):
        bpm, offset = AudioGuessWorker.unpack(bpm, offset)
        self._guess_result = (bpm, offset)
        self._release_guess_worker()
        self._restore_placeholders()
        parts = []
        for key, edit, chk, auto_attr in self._auto_fields():
            text = self._format_guess(key, bpm if key == "bpm" else offset)
            if text is None or not chk.isChecked():
                # 自動入力を切った欄は触らない(結果は _guess_result に
                # 覚えてあり、後でチェックを入れたら埋まる)。
                continue
            name = "BPM" if key == "bpm" else "OFFSET"
            if self._field_is_user_typed(edit, getattr(self, auto_attr)):
                # 解析が終わる前に打ち込まれていた。消さずに、推定値だけ添える。
                parts.append(f"{name} {text}(入力済みのため未反映)")
            else:
                edit.setText(text)
                parts.append(f"{name} {text}")
            setattr(self, auto_attr, text)
        self.lbl_detect_status.setText(
            ("音源から推定: " + " / ".join(parts) + "(手直しできます)") if parts else "")

    def _on_guess_failed(self, msg):
        # 欄は触らない(入っていた値はそのまま)。短く知らせるだけ。
        # 結果は「何も取れなかった」として覚える — チェックを入れ直すたびに
        # 同じ音源で失敗する解析を回し直さないため。
        self._guess_result = (None, None)
        self._release_guess_worker()
        self._restore_placeholders()
        self.lbl_detect_status.setText(f"BPM/OFFSET を推定できませんでした: {msg}")

    @staticmethod
    def _parse_number(text):
        """欄の文字を数に。空や読めないものは None(ひな形の既定値のまま)。"""
        text = (text or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Confirm / cancel
    # ------------------------------------------------------------------
    def _on_ok(self):
        if self.mode == "blank":
            self.accept()
            return

        if not self.result_wave_path:
            QMessageBox.warning(self, "警告", "先に音声のダウンロードを完了してください。")
            return
        title = self.ed_title.text().strip()
        if not title:
            QMessageBox.warning(self, "警告", "TITLEを入力してください。")
            return
        self.result_title = title
        self.result_subtitle = self.ed_subtitle.text().strip()
        # 欄の値(自動入力でも手直し後でも)をそのまま渡す。main_window 側は
        # bpm が偽なら BPM:、offset が None なら OFFSET: をひな形のままにする。
        bpm = self._parse_number(self.ed_bpm.text())
        self.result_bpm = bpm if bpm and bpm > 0 else None
        self.result_offset = self._parse_number(self.ed_offset.text())
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
        for w in [self._worker, self._thumb_worker, self._guess_worker,
                  *self._retired_workers]:
            _detach_worker(w)
        self._worker = None
        self._thumb_worker = None
        self._guess_worker = None
        self._retired_workers.clear()

    def accept(self):
        # The download worker is already retired by the time OK is reachable;
        # the thumbnail/detect workers may still be in flight and must not be
        # destroyed with the dialog while running.
        self._watchdog.stop()
        _detach_worker(self._thumb_worker)
        _detach_worker(self._guess_worker)
        super().accept()

    def reject(self):
        self._shutdown_workers()
        super().reject()

    def closeEvent(self, event):
        self._shutdown_workers()
        super().closeEvent(event)
