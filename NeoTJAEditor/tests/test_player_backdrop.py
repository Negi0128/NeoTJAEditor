"""再生画面の背景・ジャケット(player/backdrop.py)。

- ゲーム画面の「下に敷くもの」は既定で無し(Editor は変わらない)。敷けば
  背景を透かした下地の下に描かれ、背景が無いときのジャケットは上に描かれる。
- 置き場所は録画と同じ(背景は上下を合わせて左右は黒、ジャケットは
  recorder.art_xy)。
- 動画の時刻は「曲の秒 + offset」。シーク(戻る・飛ぶ)で読み直し、
  止めている間は読み進めない。頭より前は黒、終わったら最後のコマ。
- 表示の設定(背景・ジャケット)と曲別の「表示しない」を守る。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_backdrop.py`
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-backdrop-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402

#: 試験の動画: 10fps・10秒・64x36。コマ n は赤 = 20*(n%10)、緑 = 20*(n//10)。
_VIDEO = None


def _video():
    global _VIDEO
    if _VIDEO is None:
        import imageio_ffmpeg
        path = os.path.join(_TMP, 'frames.mp4')
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-y', '-f', 'lavfi',
             '-i', "color=c=black:s=64x36:r=10:d=10,format=rgb24,"
                   "geq=r='20*mod(N,10)':g='20*floor(N/10)':b=0",
             '-c:v', 'libx264rgb', '-qp', '0', '-g', '10', '-pix_fmt', 'rgb24', path],
            check=True, capture_output=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        _VIDEO = path
    return _VIDEO


def _frame_no(img):
    c = img.pixelColor(img.width() // 2, img.height() // 2)
    return int(round(c.green() / 20.0)) * 10 + int(round(c.red() / 20.0))


def _wait(ms):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(5)


def _image(path, w, h, color):
    from PySide6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(color))
    img.save(path)
    return path


class Clock:
    def __init__(self, t=0.0, playing=False):
        self.t, self.playing = t, playing

    def __call__(self):
        return self.t, self.playing


def _bg(**kw):
    from neotja.recorder import RecordBackground
    return RecordBackground(**kw)


class UnderlayHookTests(unittest.TestCase):
    @real_qt_test
    def test_default_off_and_hooks_order(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QImage, QPainter
        from neotja.chart_preview_widget import ChartPreviewWidget
        from neotja.game_screen import GameScreenWidget, _GameScreenBase
        self.assertIsNone(_GameScreenBase._underlay)
        cp = ChartPreviewWidget()
        cp.game_state = lambda: (0.0, 0, None)
        gs = GameScreenWidget(cp)
        self.assertIsNone(gs.underlay())

        def shot():
            img = QImage(gs.logical_size(), QImage.Format_RGB32)
            img.fill(0xff808080)
            p = QPainter(img)
            gs.paint_screen(p)
            p.end()
            return img
        plain = shot()
        calls = []

        class Fake:
            def paint_under(self, screen, p):
                calls.append('under')
                p.fillRect(QRectF(0, 0, 1280, 720), QColor(255, 0, 0))

            def paint_over(self, screen, p):
                calls.append('over')
                p.fillRect(QRectF(0, 0, 20, 20), QColor(0, 0, 255))
        gs.set_underlay(Fake())
        # 背景を透かさないと、下に敷いたものは不透明な下地に隠れる。
        img = shot()
        self.assertEqual(calls, ['under', 'over'])
        self.assertNotEqual(img.pixelColor(640, 650).red(), 255)
        self.assertEqual(img.pixelColor(5, 5), QColor(0, 0, 255))
        gs.set_background_passthrough(True)
        img = shot()
        c = img.pixelColor(640, 650)            # 下背景の真ん中
        self.assertEqual((c.red(), c.green(), c.blue()), (255, 0, 0))
        gs.set_background_passthrough(False)
        gs.set_underlay(None)
        self.assertEqual(shot(), plain)         # 外せば元どおり
        gs.deleteLater()

    @real_qt_test
    def test_broken_underlay_does_not_break_screen(self):
        from PySide6.QtGui import QImage, QPainter
        from neotja.chart_preview_widget import ChartPreviewWidget
        from neotja.game_screen import GameScreenWidget
        cp = ChartPreviewWidget()
        cp.game_state = lambda: (0.0, 0, None)
        gs = GameScreenWidget(cp)

        class Bad:
            def paint_under(self, screen, p):
                raise RuntimeError('x')
        gs.set_underlay(Bad())
        img = QImage(gs.logical_size(), QImage.Format_RGB32)
        p = QPainter(img)
        gs.paint_screen(p)
        p.end()
        gs.deleteLater()


class GeometryTests(unittest.TestCase):
    def test_bg_rect_fits_height_and_centers(self):
        from neotja.player.backdrop import bg_draw_rect, decode_size
        r = bg_draw_rect(1920, 1080)
        self.assertEqual((r.x(), r.y(), r.width(), r.height()), (0, 0, 1280, 720))
        r = bg_draw_rect(640, 480)                # 4:3 → 左右が黒
        self.assertEqual((r.x(), r.width(), r.height()), (160, 960, 720))
        r = bg_draw_rect(2560, 720)               # 横長すぎ → はみ出して真ん中
        self.assertAlmostEqual(r.x(), (1280 - 2560) / 2)
        self.assertEqual(decode_size(1920, 1080, 1000), (1778, 1000))
        self.assertEqual(decode_size(1920, 1080, 1500), (1920, 1080))
        self.assertEqual(decode_size(640, 360, 1000), (640, 360))   # 元より大きくしない
        self.assertEqual(decode_size(1920, 1080, 1000, cap=720), (1280, 720))

    @real_qt_test
    def test_jacket_rect_matches_recorder(self):
        from neotja import recorder
        from neotja.player.backdrop import ChartBackdrop
        jk = _image(os.path.join(_TMP, 'jk.png'), 300, 200, '#30a0ff')
        for x, y, pct in ((0, 0, 30.0), (-500, 120, 20.0)):
            bg = _bg(art=jk, art_x=x, art_y=y, art_pct=pct, art_edge=6)
            bd = ChartBackdrop(Clock())
            bd.configure(bg)
            for k in (1.0, 1.5):
                rect, img = bd.art_rect(k)
                side = img.width()
                self.assertEqual(side, int(round(720 * k * pct / 100)) + 12)
                ex, ey = recorder.art_xy(bg, int(1280 * k), int(720 * k), side)
                self.assertAlmostEqual(rect.x() * k, ex, places=3)
                self.assertAlmostEqual(rect.y() * k, ey, places=3)
                self.assertAlmostEqual(rect.width() * k, side, places=3)

    @real_qt_test
    def test_paint_image_dim_jacket_and_compact(self):
        from PySide6.QtGui import QImage, QPainter
        from neotja.player.backdrop import ChartBackdrop
        bgp = _image(os.path.join(_TMP, 'bg43.png'), 400, 300, '#ffffff')   # 4:3
        jk = _image(os.path.join(_TMP, 'jk2.png'), 100, 100, '#00ff00')
        bd = ChartBackdrop(Clock())
        bd.configure(_bg(path=bgp, art=jk, dim=0.5, art_edge=0))

        class Scr:
            h = 720

            def _screen_height(self):
                return self.h
        scr = Scr()

        def shot():
            img = QImage(1280, 720, QImage.Format_RGB32)
            img.fill(0xff808080)
            p = QPainter(img)
            bd.paint_under(scr, p)
            p.end()
            return img
        img = shot()
        self.assertEqual(img.pixelColor(50, 100).name(), '#000000')     # 左右は黒
        mid = img.pixelColor(640, 100)
        self.assertAlmostEqual(mid.red(), 128, delta=2)                 # 暗さ 50%
        jc = img.pixelColor(640, 542)                                   # 下背景の真ん中
        self.assertGreater(jc.green(), 100)
        self.assertLess(jc.red(), 10)
        scr.h = 360                                                     # 詳細モード
        img = shot()
        self.assertEqual(img.pixelColor(640, 542).name(), '#808080')   # 下は描かない
        self.assertAlmostEqual(img.pixelColor(640, 100).red(), 128, delta=2)


class VideoTimeTests(unittest.TestCase):
    def _backdrop(self, clock, offset=0.0):
        from neotja.player.backdrop import ChartBackdrop, probe_video
        bd = ChartBackdrop(clock)
        bd.configure(_bg(path=_video(), offset=offset))
        bd._info = probe_video(_video())         # 調べ終わりを待たずに
        self.assertEqual(bd._info[:3], (64, 36, 10.0))
        return bd

    def _frame(self, bd, timeout=5.0):
        """いまの時計で出すべきコマが届くまで待って、その絵を返す。"""
        info = bd._info
        want = min(bd.video_now(), info[3] - 0.5 / info[2])
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            img = bd._video_frame(36)
            t = bd._frame[0] if bd._frame is not None else None
            if img is not None and t is not None and abs(t - want) <= 0.051:
                return img
            _wait(10)
        self.fail('コマが届かない (want %.2f, have %r)' % (
            want, bd._frame and bd._frame[0]))

    @real_qt_test
    def test_offset_seek_pause_and_ends(self):
        from neotja.player.backdrop import video_time
        self.assertAlmostEqual(video_time(3.0, 1.5), 4.5)
        self.assertAlmostEqual(video_time(-1.0, 0.25), -0.75)
        clock = Clock(3.0)
        bd = self._backdrop(clock, offset=1.5)
        try:
            self.assertAlmostEqual(bd.video_now(), 4.5)
            self.assertEqual(_frame_no(self._frame(bd)), 45)
            # 止めている間(時計が進まない)は読み進めない。
            frames = bd._reader.frames
            for _ in range(20):
                bd._video_frame(36)
                _wait(5)
            self.assertEqual(bd._reader.frames, frames)
            self.assertEqual(_frame_no(bd._frame[1]), 45)
            # 流す: 少し進めると次のコマ(読み直さない)。
            n = len(bd.restarts)
            clock.t = 3.3
            self.assertEqual(_frame_no(self._frame(bd)), 48)
            self.assertEqual(len(bd.restarts), n)
            # 戻るシーク・先へのシーク(詳細モード)は読み直してその位置。
            clock.t = 0.5
            self.assertEqual(_frame_no(self._frame(bd)), 20)
            clock.t = 6.0                     # 3秒より先へ飛ぶ → 読み直す
            self.assertEqual(_frame_no(self._frame(bd)), 75)
            self.assertEqual(len(bd.restarts), n + 2)
            # 小さな遅れ(3秒未満)は読み直さずに読み進めて追いつく。
            clock.t = 7.5
            self.assertEqual(_frame_no(self._frame(bd)), 90)
            self.assertEqual(len(bd.restarts), n + 2)
            # 頭より前は黒(None)。
            clock.t = -3.0
            self.assertIsNone(bd._video_frame(36))
            # 終わりを過ぎたら最後のコマで止める。
            clock.t = 30.0
            self.assertEqual(_frame_no(self._frame(bd)), 99)
            clock.t = 40.0
            self.assertEqual(_frame_no(self._frame(bd)), 99)
        finally:
            bd.stop()
        self.assertIsNone(bd._reader)

    @real_qt_test
    def test_resume_after_pause_continues_without_restart(self):
        clock = Clock(1.0)
        bd = self._backdrop(clock)
        try:
            self.assertEqual(_frame_no(self._frame(bd)), 10)
            n = len(bd.restarts)
            # 一時停止(途中メニュー)で時計が止まり、続けると同じ所から進む。
            for t in (1.0, 1.0, 1.1, 1.2, 1.3):
                clock.t = t
                img = self._frame(bd)
            self.assertEqual(_frame_no(img), 13)
            self.assertEqual(len(bd.restarts), n)
        finally:
            bd.stop()

    @real_qt_test
    def test_reader_command_is_scaled_and_fps_capped(self):
        from neotja.player.backdrop import FrameReader
        r = FrameReader(_video(), 2.0, 64, 36, 120.0, ffmpeg='ffmpeg-not-run', gpu=True)
        try:
            cmd = r.command()
        finally:
            r.close()
        self.assertIn('-ss', cmd)
        self.assertEqual(cmd[cmd.index('-ss') + 1], '2.000')
        vf = cmd[cmd.index('-vf') + 1]
        self.assertTrue(vf.startswith('fps=30,scale=64:36'), vf)
        self.assertEqual(cmd[cmd.index('-pix_fmt') + 1], 'rgb0')


class SettingsTests(unittest.TestCase):
    def _cfg(self, chart, bg, jk, hidden=False):
        from neotja.chart_art import path_key
        cfg = settings.default_settings()
        entry = {'path': chart, 'jacket': jk, 'background': bg,
                 'record_background': {'path': bg, 'art': jk, 'offset': 1.25,
                                       'dim': 0.3, 'lane': 0.5}}
        if hidden:
            entry['jacket_hidden'] = True
        cfg['player_chart_art'] = {path_key(chart): entry}
        return cfg

    @real_qt_test
    def test_player_background_and_show_flags(self):
        from neotja.player.backdrop import ChartBackdrop, player_background
        d = Path(_TMP) / 'song'
        d.mkdir(exist_ok=True)
        chart = str(d / 'a.tja')
        Path(chart).write_text('TITLE:a\n', encoding='cp932')
        bgp = _image(str(d / 'bg.png'), 32, 18, '#112233')
        jk = _image(str(d / 'jk.png'), 32, 32, '#445566')
        bg = player_background(self._cfg(chart, bgp, jk), chart)
        self.assertEqual((bg.path, bg.art, bg.offset, bg.dim, bg.lane),
                         (bgp, jk, 1.25, 0.3, 0.5))
        bg2 = player_background(self._cfg(chart, bgp, jk, hidden=True), chart)
        self.assertEqual(bg2.art, '')                                # 表示しない
        bd = ChartBackdrop(Clock())
        bd.configure(bg, show_bg=False, show_jacket=True)
        self.assertEqual((bd.bg_path, bd.art_path), ('', jk))
        bd.configure(bg, show_bg=True, show_jacket=False)
        self.assertEqual((bd.bg_path, bd.art_path), (bgp, ''))
        bd.configure(bg, show_bg=False, show_jacket=False)
        self.assertFalse(bd.active())

    @real_qt_test
    def test_attach_sets_passthrough_with_lane_and_detach_restores(self):
        from neotja.chart_preview_widget import ChartPreviewWidget
        from neotja.game_screen import GameScreenWidget
        from neotja.player.backdrop import ChartBackdrop
        bgp = _image(os.path.join(_TMP, 'bg2.png'), 32, 18, '#112233')
        cp = ChartPreviewWidget()
        cp.game_state = lambda: (0.0, 0, None)
        gs = GameScreenWidget(cp)
        lanes = []
        orig = cp.set_background_passthrough
        cp.set_background_passthrough = lambda on, lane=1.0: (lanes.append((on, lane)), orig(on, lane))
        bd = ChartBackdrop(Clock())
        bd.configure(_bg(path=bgp, lane=0.4))
        bd.attach(gs)
        self.assertIs(gs.underlay(), bd)
        self.assertTrue(gs._bg_passthrough)
        self.assertIn((True, 0.4), lanes)
        bd.stop()
        self.assertIsNone(gs.underlay())
        self.assertFalse(gs._bg_passthrough)
        # ジャケットだけなら背景は透かさない(画面の上に重ねる)。
        jk = _image(os.path.join(_TMP, 'jk3.png'), 32, 32, '#445566')
        bd2 = ChartBackdrop(Clock())
        bd2.configure(_bg(art=jk))
        bd2.attach(gs)
        self.assertIs(gs.underlay(), bd2)
        self.assertFalse(gs._bg_passthrough)
        bd2.stop()
        gs.deleteLater()


class HostTests(unittest.TestCase):
    @real_qt_test
    def test_start_and_end_play_attach_and_release(self):
        from neotja.chart_preview_widget import ChartPreviewWidget
        from neotja.game_screen import GameScreenWidget
        from neotja.player.host import PlayerHost
        from neotja.player.backdrop import ChartBackdrop
        d = Path(_TMP) / 'host'
        d.mkdir(exist_ok=True)
        chart = str(d / 'b.tja')
        Path(chart).write_text('TITLE:b\n', encoding='cp932')
        bgp = _image(str(d / 'bg.png'), 32, 18, '#112233')
        cp = ChartPreviewWidget()
        cp.game_state = lambda: (0.0, 0, None)
        gs = GameScreenWidget(cp)
        dock = types.SimpleNamespace(game_screen=gs, chart_preview=cp, _lead_sec=3.0)
        cfg = SettingsTests()._cfg(chart, bgp, '')
        fake = types.SimpleNamespace(cfg=cfg, _core=types.SimpleNamespace(dock=dock),
                                     _backdrop=None,
                                     backdrop_factory=lambda clock, _p: ChartBackdrop(clock))
        fake._stop_backdrop = lambda: PlayerHost._stop_backdrop(fake)
        PlayerHost._start_backdrop(fake, chart)
        bd = fake._backdrop
        self.assertIsNotNone(bd)
        self.assertIs(gs.underlay(), bd)
        # 時計: レーンの時刻(助走込み) − 助走 = 曲の秒。
        cp.set_playback(10.0, False)
        self.assertAlmostEqual(bd.song_time()[0], 7.0, places=3)
        self.assertAlmostEqual(bd.video_now(), 8.25, places=3)      # offset 1.25
        PlayerHost._stop_backdrop(fake)
        self.assertIsNone(gs.underlay())
        # 背景を出さない設定でジャケットも無いなら何も敷かない。
        cfg['player_show_bg'] = False
        PlayerHost._start_backdrop(fake, chart)
        self.assertIsNone(fake._backdrop)
        self.assertIsNone(gs.underlay())
        gs.deleteLater()


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
