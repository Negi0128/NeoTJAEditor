import os

from PySide6.QtCore import QThread, Signal


class DownloadCancelled(Exception):
    pass


class YtDlpDownloadWorker(QThread):
    """Downloads a YouTube URL's audio and converts it to OGG (via yt-dlp +
    a bundled ffmpeg from imageio-ffmpeg) in a background thread, so the
    dialog stays responsive while it runs."""

    progress = Signal(str)                    # status text
    finished_ok = Signal(str, str, str, str)  # ogg_path, video_title, uploader, thumbnail_url
    failed = Signal(str)

    def __init__(self, url: str, out_dir: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.out_dir = out_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # YouTube periodically tightens bot-detection on its default ("web")
    # player client, which surfaces to yt-dlp users as 403 Forbidden even
    # though the video itself is public. Falling back to other official
    # clients is the standard workaround and doesn't need extra dependencies
    # (unlike cookies or a JS runtime, which the "web" client needs for its
    # challenge-solving).
    PLAYER_CLIENTS = [None, "android", "tv", "ios"]

    def run(self):
        import imageio_ffmpeg
        import yt_dlp

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        last_error = None

        for client in self.PLAYER_CLIENTS:
            if self._cancelled:
                self.failed.emit("キャンセルされました。")
                return
            try:
                ogg_path, title, uploader, thumbnail_url = self._attempt_download(yt_dlp, ffmpeg_path, client)
                self.finished_ok.emit(ogg_path, title, uploader, thumbnail_url)
                return
            except DownloadCancelled:
                self.failed.emit("キャンセルされました。")
                return
            except Exception as e:
                last_error = e
                continue

        if self._cancelled:
            self.failed.emit("キャンセルされました。")
        else:
            self.failed.emit(
                f"{last_error}\n\n"
                "YouTube側のダウンロード制限(BOT対策)が原因の可能性があります。"
                "時間を置くか、別の動画・別のURLで試してみてください。"
            )

    def _attempt_download(self, yt_dlp, ffmpeg_path, player_client):
        def hook(d):
            if self._cancelled:
                raise DownloadCancelled("cancelled")
            if d.get('status') == 'downloading':
                pct = (d.get('_percent_str') or '').strip()
                self.progress.emit(f"ダウンロード中... {pct}")
            elif d.get('status') == 'finished':
                self.progress.emit("音声をOGGに変換中...")

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
