"""YouTube から背景の動画を落とすときの画質: クライアントの並び、形式の指定、
720p 未満なら次のクライアントで取り直す判断。ネットには出ない(落とし屋は偽物)。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_ytdlp_video_quality.py`
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import ytdlp_worker  # noqa: E402
from neotja.ytdlp_worker import (YtDlpDownloadWorker, should_retry_video,  # noqa: E402
                                 video_format_options)

URL = "https://www.youtube.com/watch?v=xxxxxxxxxxx"


class FakeWorker(YtDlpDownloadWorker):
    """_attempt_download を差し替え、クライアントごとの結果(高さ or 例外)を返す。"""

    def __init__(self, out_dir, plan, want="video", url=URL):
        super().__init__(url, out_dir, want=want)
        self.plan = dict(plan)
        self.tried = []
        self.done = []
        self.errors = []
        self.messages = []
        self.finished_ok.connect(lambda *a: self.done.append(a))
        self.failed.connect(self.errors.append)
        self.progress.connect(self.messages.append)

    def _attempt_download(self, yt_dlp, ffmpeg_path, client):
        self.tried.append(client)
        got = self.plan.get(client, RuntimeError("403"))
        if isinstance(got, Exception):
            raise got
        path = os.path.join(self.out_dir, "song.mp4")
        with open(path, "w", encoding="utf-8") as f:
            f.write("%s:%s" % (client, got))   # 中身で、どの回のものか分かる
        self._attempt_height = got
        return path, "song", "up", "thumb"


class ClientOrderTests(unittest.TestCase):
    def test_video_prefers_high_resolution_clients_and_android_last(self):
        w = YtDlpDownloadWorker(URL, ".", want="video")
        order = w.player_clients()
        self.assertEqual(order[0], None)                 # まず yt-dlp の既定
        self.assertEqual(order[-1], "android")           # 360p までなので最後
        for c in ("tv", "web_safari"):
            self.assertLess(order.index(c), order.index("android"))
        self.assertNotIn("android_vr", order)            # 403 で弾かれる

    def test_audio_order_unchanged_and_non_youtube_single_try(self):
        self.assertEqual(YtDlpDownloadWorker(URL, ".").player_clients(),
                         [None, "android", "tv", "ios"])
        self.assertEqual(YtDlpDownloadWorker("https://soundcloud.com/a/b", ".",
                                             want="video").player_clients(), [None])

    def test_clients_exist_in_bundled_ytdlp(self):
        from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS
        for c in YtDlpDownloadWorker.VIDEO_PLAYER_CLIENTS:
            if c:
                self.assertIn(c, INNERTUBE_CLIENTS)

    def test_format_options_prefer_1080_h264(self):
        o = video_format_options()
        self.assertIn("bestvideo[height<=1080]+bestaudio", o["format"])
        self.assertEqual(o["format_sort"], ["res:1080", "vcodec:h264", "acodec:m4a"])
        self.assertEqual(o["merge_output_format"], "mp4")
        w = YtDlpDownloadWorker(URL, "out", want="video")
        opts = w.video_options("tv")
        self.assertEqual(opts["format_sort"][0], "res:1080")
        self.assertEqual(opts["extractor_args"], {"youtube": {"player_client": ["tv"]}})
        self.assertNotIn("extractor_args", w.video_options(None))

    def test_format_sort_is_accepted_by_ytdlp(self):
        import yt_dlp
        with yt_dlp.YoutubeDL(dict(video_format_options(), quiet=True)) as ydl:
            self.assertEqual(ydl.params["format_sort"][1], "vcodec:h264")

    def test_retry_decision(self):
        self.assertTrue(should_retry_video(360, ["tv"]))
        self.assertFalse(should_retry_video(360, []))
        self.assertFalse(should_retry_video(720, ["tv"]))
        self.assertFalse(should_retry_video(1080, ["tv"]))
        self.assertFalse(should_retry_video(None, ["tv"]))


class RetryRunTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="neotja-yt-test-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _content(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_low_then_high_returns_high(self):
        w = FakeWorker(self.dir, {None: 360, "tv": 1080})
        w.run()
        self.assertEqual(w.tried, [None, "tv"])
        self.assertEqual(len(w.done), 1)
        self.assertEqual(self._content(w.done[0][0]), "tv:1080")
        self.assertEqual(w.video_height, 1080)
        self.assertEqual(sorted(os.listdir(self.dir)), ["song.mp4"])   # 退避は消える
        self.assertTrue(any("360p" in m for m in w.messages))

    def test_first_good_does_not_retry(self):
        w = FakeWorker(self.dir, {None: 1080})
        w.run()
        self.assertEqual(w.tried, [None])
        self.assertEqual(w.video_height, 1080)

    def test_all_low_keeps_best_and_reports_height(self):
        plan = {None: 480, "tv": RuntimeError("x"), "web_safari": 360,
                "mweb": RuntimeError("x"), "ios": RuntimeError("x"),
                "android": 360}
        w = FakeWorker(self.dir, plan)
        w.run()
        self.assertEqual(w.errors, [])
        self.assertEqual(len(w.done), 1)
        self.assertEqual(self._content(w.done[0][0]), "None:480")
        self.assertEqual(w.done[0][0], os.path.join(self.dir, "song.mp4"))
        self.assertEqual(w.video_height, 480)
        self.assertEqual(sorted(os.listdir(self.dir)), ["song.mp4"])

    def test_low_then_all_fail_returns_low(self):
        w = FakeWorker(self.dir, {None: 360})
        w.run()
        self.assertEqual(w.tried, YtDlpDownloadWorker.VIDEO_PLAYER_CLIENTS)
        self.assertEqual(len(w.done), 1)
        self.assertEqual(w.video_height, 360)
        self.assertEqual(self._content(w.done[0][0]), "None:360")

    def test_audio_never_checks_height(self):
        w = FakeWorker(self.dir, {None: 144}, want="audio")
        w.run()
        self.assertEqual(w.tried, [None])
        self.assertIsNone(w.video_height)

    def test_unknown_height_falls_back_to_probe(self):
        calls = []
        orig = ytdlp_worker.probe_video_height
        ytdlp_worker.probe_video_height = lambda p, f: calls.append(p) or 1080
        try:
            w = FakeWorker(self.dir, {None: None})
            w.run()
        finally:
            ytdlp_worker.probe_video_height = orig
        self.assertEqual(len(calls), 1)
        self.assertEqual(w.video_height, 1080)


class ProbeTests(unittest.TestCase):
    def test_probe_reads_height_from_generated_clip(self):
        import subprocess
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        d = tempfile.mkdtemp(prefix="neotja-yt-probe-")
        try:
            out = os.path.join(d, "c.mp4")
            subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "color=c=red:s=640x360:d=0.2", "-y", out],
                           check=True, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(ytdlp_worker.probe_video_height(out, ff), 360)
            self.assertIsNone(ytdlp_worker.probe_video_height(
                os.path.join(d, "none.mp4"), ff))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class DialogLabelTests(unittest.TestCase):
    def test_low_height_label_only_below_720(self):
        from PySide6.QtWidgets import QApplication, QLabel, QLineEdit
        from neotja.dialogs.record_bg_dialog import RecordBackgroundDialog
        QApplication.instance() or QApplication([])

        class Dummy:
            pass
        d = Dummy()
        d.lb_yt_res = QLabel()
        RecordBackgroundDialog.show_youtube_height(d, 360)
        self.assertEqual(d.lb_yt_res.text(), "360p")
        self.assertFalse(d.lb_yt_res.isHidden())
        RecordBackgroundDialog.show_youtube_height(d, 1080)
        self.assertTrue(d.lb_yt_res.isHidden())
        RecordBackgroundDialog.show_youtube_height(d, None)
        self.assertTrue(d.lb_yt_res.isHidden())


if __name__ == "__main__":
    unittest.main()
