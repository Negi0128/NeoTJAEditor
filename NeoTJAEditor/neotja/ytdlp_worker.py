import os
import re

from PySide6.QtCore import QThread, Signal

# ホスト名だけでYouTubeかどうかを見る。下のプレイヤー総当たりとBOT対策の案内は
# YouTube限定の話なので、SoundCloudなど他サイトのときに巻き込まないための判定。
# ダイアログ側にも似た正規表現があるが、ここから neotja.dialogs を import すると
# 依存が逆向き(下位→上位)になるので、あえて小さい判定を手元に持つ。
_YOUTUBE_HOST_RE = re.compile(
    r"^(?:https?://)?(?:[\w-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com)(?:/|$)",
    re.IGNORECASE,
)


def is_youtube_url(url):
    """URLがYouTubeのものなら True。判断できないときは False
    (= 余計な総当たりも、的外れなBOT対策の案内もしない)。"""
    return bool(_YOUTUBE_HOST_RE.match((url or "").strip()))


def _strip_pyinstaller_env():
    """Remove PyInstaller's onefile bootloader environment variables from the
    current process environment so any child process we (or yt-dlp) spawn does
    NOT inherit them.

    In a onefile build the app is unpacked into a temp dir named ``_MEIxxxxxx``.
    The bootloader passes markers like ``_MEIPASS2`` / ``_PYI_ARCHIVE_FILE`` /
    ``_PYI_APPLICATION_HOME_DIR`` to children via the environment. If a child
    that is itself a PyInstaller onefile exe inherits these, it latches onto the
    parent's ``_MEI`` dir and, on exit, tries to delete it — producing the
    "Failed to remove temporary directory: ..._MEIxxxxxx" warning box the user
    sees during a YouTube download. These markers are only read by the
    bootloader at startup, so clearing them now is safe and is the practice
    PyInstaller documents for launching external programs."""
    for key in list(os.environ):
        if key == "_MEIPASS2" or key.startswith("_PYI_"):
            os.environ.pop(key, None)


class DownloadCancelled(Exception):
    pass


#: 背景の動画はこの高さ未満なら、残っている別のクライアントで取り直す。
MIN_VIDEO_HEIGHT = 720

_VIDEO_SIZE_RE = re.compile(r"Stream #\d+:\d+.*?: Video: .*?(\d{2,5})x(\d{2,5})")


def probe_video_height(path, ffmpeg_path):
    """動画ファイルの映像の高さ(px)。分からなければ None。
    ffprobe は同梱されていないので ffmpeg -i の表示から読む。"""
    import subprocess
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run([ffmpeg_path, "-hide_banner", "-i", path],
                             capture_output=True, timeout=20,
                             creationflags=flags)
        text = (out.stderr or b"").decode("utf-8", "replace")
    except Exception:
        return None
    m = _VIDEO_SIZE_RE.search(text)
    return int(m.group(2)) if m else None


def should_retry_video(height, remaining_clients):
    """落とした動画の高さを見て、次のクライアントで取り直すか。
    高さが分からないときは取り直さない(良し悪しを判断できないため)。"""
    if not remaining_clients or height is None:
        return False
    return int(height) < MIN_VIDEO_HEIGHT


def video_format_options():
    """背景動画の形式の指定。1080p を上限に解像度を最優先し、同じ高さなら
    H.264 / AAC(m4a) を選ぶ(Qt のデコーダーで確実に再生できるため)。"""
    return {
        'format': ('bestvideo[height<=1080]+bestaudio'
                   '/best[height<=1080]/best'),
        'format_sort': ['res:1080', 'vcodec:h264', 'acodec:m4a'],
        'merge_output_format': 'mp4',
    }


class YtDlpDownloadWorker(QThread):
    """Downloads a URL's audio (YouTube, SoundCloud, or anything else yt-dlp
    supports) and converts it to OGG (via yt-dlp +
    a bundled ffmpeg from imageio-ffmpeg) in a background thread, so the
    dialog stays responsive while it runs."""

    progress = Signal(str)                    # status text
    progress_pct = Signal(float)              # 0-100, or -1 when indeterminate
    finished_ok = Signal(str, str, str, str)  # ogg_path, video_title, uploader, thumbnail_url
    failed = Signal(str)

    def __init__(self, url: str, out_dir: str, parent=None, want="audio"):
        super().__init__(parent)
        self.url = url
        self.out_dir = out_dir
        #: "audio" なら今までどおり OGG。"video" は録画の背景に敷く mp4
        #: (利用者の指定 2026-10-25)。音の道は何も変えていない。
        self.want = "video" if str(want) == "video" else "audio"
        self._cancelled = False
        #: 背景の動画を落としたときの映像の高さ(px)。分からなければ None。
        self.video_height = None
        self._attempt_height = None

    def cancel(self):
        self._cancelled = True

    # YouTube periodically tightens bot-detection on its default ("web")
    # player client, which surfaces to yt-dlp users as 403 Forbidden even
    # though the video itself is public. Falling back to other official
    # clients is the standard workaround and doesn't need extra dependencies
    # (unlike cookies or a JS runtime, which the "web" client needs for its
    # challenge-solving).
    PLAYER_CLIENTS = [None, "android", "tv", "ios"]

    # 背景の動画は高い解像度を出すクライアントを先に試す(2026-11-18
    # 「YouTube から落とすと画質が低い」)。android は 360p までしか
    # 出さないので最後。yt-dlp 2025.10.14 の既定は tv / web_safari / web。
    # tv は PO トークン不要で DASH の 1080p、web_safari は HLS の 1080p、
    # mweb / ios は PO トークンが要る形式が多いので後ろ。
    # android_vr は 1080p を一覧に出すが、いざ取ると 403 で弾かれた
    # (実測 2026-10-25)ので入れない。
    VIDEO_PLAYER_CLIENTS = [None, "tv", "web_safari", "mweb", "ios", "android"]

    def player_clients(self):
        """今回のURLで試すプレイヤークライアントの並びを返す。
        player_client はYouTubeの抽出器にしか効かないので、他サイト
        (SoundCloudなど)で総当たりしても同じ失敗を4回繰り返すだけ。
        そこで非YouTubeは1回で諦める(利用者の指定 2026-10-05)。"""
        if is_youtube_url(self.url):
            if self.want == "video":
                return list(self.VIDEO_PLAYER_CLIENTS)
            return list(self.PLAYER_CLIENTS)
        return [None]

    def run(self):
        _strip_pyinstaller_env()

        import imageio_ffmpeg
        import yt_dlp

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        last_error = None
        clients = self.player_clients()
        # 低い解像度で取れた動画は捨てずに一番良いものを残しておき、
        # 取り直しがすべて失敗・もっと低いときはそれを返す。
        low_best = None   # (height, 退避した道, 元の道, title, uploader, thumb)

        for i, client in enumerate(clients):
            if self._cancelled:
                self._drop_low(low_best)
                self.failed.emit("キャンセルされました。")
                return
            try:
                self._attempt_height = None
                ogg_path, title, uploader, thumbnail_url = self._attempt_download(yt_dlp, ffmpeg_path, client)
            except DownloadCancelled:
                self._drop_low(low_best)
                self.failed.emit("キャンセルされました。")
                return
            except Exception as e:
                last_error = e
                continue
            if self.want != "video":
                self.finished_ok.emit(ogg_path, title, uploader, thumbnail_url)
                return
            height = self._attempt_height
            if height is None:
                height = probe_video_height(ogg_path, ffmpeg_path)
            if low_best is not None and (height is None
                                         or height < low_best[0]):
                # 前に取れたものの方が良い: 今回のを捨てて前のを戻す。
                self._remove(ogg_path)
                self._finish_low(low_best)
                return
            if should_retry_video(height, clients[i + 1:]):
                self._drop_low(low_best)
                stash = self._stash(ogg_path)
                low_best = (height, stash, ogg_path, title, uploader,
                            thumbnail_url)
                self.progress_pct.emit(-1.0)
                self.progress.emit("取り直しています... (%dp)" % height)
                continue
            self._drop_low(low_best)
            self.video_height = height
            self.finished_ok.emit(ogg_path, title, uploader, thumbnail_url)
            return

        if low_best is not None and not self._cancelled:
            self._finish_low(low_best)
            return
        self._drop_low(low_best)
        if self._cancelled:
            self.failed.emit("キャンセルされました。")
        elif is_youtube_url(self.url):
            self.failed.emit(
                f"{last_error}\n\n"
                "YouTube側のダウンロード制限(BOT対策)が原因の可能性があります。"
                "時間を置くか、別の動画・別のURLで試してみてください。"
            )
        else:
            # 他サイトでBOT対策の話をされても的外れなので、素のエラーだけ出す
            # (利用者の指定 2026-10-05)。
            self.failed.emit(f"{last_error}")

    @staticmethod
    def _remove(path):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    @staticmethod
    def _stash(path):
        """取り直しで同じ名前に書けるよう、低い方を別名へ退避する
        (同じ名前があると yt-dlp は落とし直さずに済ませてしまう)。"""
        stash = path + ".low"
        try:
            if os.path.exists(stash):
                os.remove(stash)
            os.replace(path, stash)
            return stash
        except OSError:
            return path

    def _drop_low(self, low_best):
        if low_best is not None and low_best[1] != low_best[2]:
            self._remove(low_best[1])

    def _finish_low(self, low_best):
        height, stash, path, title, uploader, thumb = low_best
        if stash != path:
            try:
                if os.path.exists(path):
                    os.remove(path)
                os.replace(stash, path)
            except OSError:
                path = stash
        self.video_height = height
        self.finished_ok.emit(path, title, uploader, thumb)

    def video_options(self, player_client, hook=None, ffmpeg_path=None):
        """背景の動画を落とすときの yt-dlp の設定。"""
        ydl_opts = dict(video_format_options())
        ydl_opts.update({
            'outtmpl': os.path.join(self.out_dir, '%(title)s.%(ext)s'),
            'ffmpeg_location': ffmpeg_path,
            'progress_hooks': [hook] if hook is not None else [],
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'windowsfilenames': True,
            'socket_timeout': 30,
        })
        if player_client:
            ydl_opts['extractor_args'] = {
                'youtube': {'player_client': [player_client]}}
        return ydl_opts

    def _attempt_download(self, yt_dlp, ffmpeg_path, player_client):
        def hook(d):
            if self._cancelled:
                raise DownloadCancelled("cancelled")
            status = d.get('status')
            if status == 'downloading':
                downloaded = d.get('downloaded_bytes') or 0
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                pct_val = -1.0
                if total > 0:
                    pct_val = max(0.0, min(100.0, downloaded * 100.0 / total))
                    pct_text = f"{pct_val:.1f}%"
                else:
                    pct_text = (d.get('_percent_str') or '').strip()
                    try:
                        pct_val = float(pct_text.rstrip('%'))
                    except (ValueError, AttributeError):
                        pct_val = -1.0
                self.progress_pct.emit(pct_val)
                self.progress.emit(f"ダウンロード中... {pct_text}".rstrip())
            elif status == 'finished':
                # Download done; ffmpeg is now converting to OGG. There is no
                # reliable percentage for this phase, so switch to indeterminate.
                self.progress_pct.emit(-1.0)
                self.progress.emit("映像をまとめています..." if
                                   self.want == "video"
                                   else "音声をOGGに変換中...")

        if self.want == "video":
            # 背景に敷く動画。映像だけの形式は扱いにくいので mp4 へまとめる。
            # 1080p までに抑える — 録画の画面はそれより小さく、大きくしても
            # 落とす時間とファイルが増えるだけ。
            # 入れ物(mp4/webm)は選り好みしない — ext を縛ると、まとめ
            # 済みの 360p しか残らないことがある(実測 2026-10-25:
            # android クライアントで 640x360 になった)。まとめるのは
            # merge_output_format が mp4 でやってくれる。
            ydl_opts = self.video_options(player_client, hook, ffmpeg_path)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=True)
                try:
                    self._attempt_height = int(info.get('height') or 0) or None
                except (TypeError, ValueError):
                    self._attempt_height = None
                title = info.get('title') or 'untitled'
                uploader = info.get('uploader') or info.get('channel') or ''
                thumbnail_url = info.get('thumbnail') or ''
                got = ydl.prepare_filename(info)
                if not os.path.exists(got):
                    got = os.path.splitext(got)[0] + '.mp4'
            if not os.path.exists(got):
                raise RuntimeError("落とした動画が見つかりません: %s" % got)
            return got, title, uploader, thumbnail_url

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(self.out_dir, '%(title)s.%(ext)s'),
            'ffmpeg_location': ffmpeg_path,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'vorbis',
                'preferredquality': '5',
            }],
            'progress_hooks': [hook],
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'windowsfilenames': True,
            # Bound network stalls so a dead connection raises instead of
            # hanging forever (which would otherwise only be caught by the
            # dialog's much slower stall watchdog).
            'socket_timeout': 30,
        }
        if player_client:
            ydl_opts['extractor_args'] = {'youtube': {'player_client': [player_client]}}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.url, download=True)
            title = info.get('title') or 'untitled'
            uploader = info.get('uploader') or info.get('channel') or ''
            thumbnail_url = info.get('thumbnail') or ''
            base = ydl.prepare_filename(info)
            ogg_path = os.path.splitext(base)[0] + '.ogg'

        if not os.path.exists(ogg_path):
            raise RuntimeError(f"変換後のOGGファイルが見つかりません: {ogg_path}")
        return ogg_path, title, uploader, thumbnail_url


class ThumbnailFetchWorker(QThread):
    """Downloads a video thumbnail's raw image bytes in the background so
    the dialog can show it without blocking the UI on network I/O."""

    fetched = Signal(bytes)
    failed = Signal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            import urllib.request
            req = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            self.fetched.emit(data)
        except Exception as e:
            self.failed.emit(str(e))
