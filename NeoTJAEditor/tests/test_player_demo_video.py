"""背景動画の追従・古い通知破棄・Player画面退出時の停止。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from neotja import settings
_TMP = tempfile.mkdtemp(prefix="neotja-video-test-")
_CFG = Path(_TMP) / "settings.json"
_CFG.write_text('{"master_volume":0.0}', encoding="utf-8")
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG
from gui_support import real_qt_test


class VideoSyncTests(unittest.TestCase):
    @real_qt_test
    def test_clock_wrap_pause_and_stale_duration(self):
        from neotja.player.demo_video import DemoVideo
        from PySide6.QtMultimedia import QMediaPlayer
        class Core:
            ready, playing, pos, duration = True, True, 2.0, None
            def demo_ready(self): return self.ready
            def demo_playing(self): return self.playing
            def demo_position(self): return self.pos
            def set_demo_video_duration(self, duration): self.duration = duration
        class Player:
            pos, state = 0, QMediaPlayer.PausedState
            calls = []
            def position(self): return self.pos
            def setPosition(self, ms): self.pos=ms; self.calls.append(ms)
            def playbackState(self): return self.state
            def play(self): self.state=QMediaPlayer.PlayingState
            def pause(self): self.state=QMediaPlayer.PausedState
        c=Core(); v=DemoVideo(c, lambda image:None, lambda:None)
        p=Player(); v.player=p; v._loaded=True
        v._sync()
        self.assertEqual(p.calls, [2000])
        self.assertEqual(p.state, QMediaPlayer.PlayingState)
        # 末尾からDEMOSTARTへ戻ったら追従誤差閾値より小さくてもseek。
        v._last_audio=2.1; c.pos=2.0; p.pos=2100
        v._sync(); self.assertEqual(p.calls, [2000,2000])
        c.ready=False; v._sync()
        self.assertEqual(p.state,QMediaPlayer.PausedState)
        v._duration(v._generation-1,9000); self.assertIsNone(c.duration)
        v._duration(v._generation,4000); self.assertEqual(c.duration,4.0)
        v.player=None; v.deleteLater()

    @real_qt_test
    def test_video_released_on_library_and_setting_off(self):
        from unittest.mock import patch
        from PySide6.QtCore import QObject
        from neotja.player.host import PlayerHost
        from neotja.player import demo_video, song_lists
        from test_player_preview import FakeCore, _chart
        class Video(QObject):
            made=[]
            def __init__(self,core,frame,clear,parent=None):
                super().__init__(parent);self.stopped=False;self.made.append(self)
                self._clear_callback=clear;self.frame=frame;self.free=False;self.path=''
            def start(self,path,free=False): self.path=path;self.free=free
            def stop(self): self.stopped=True
            def set_targets(self,frame,clear): self.frame=frame;self._clear_callback=clear
            def free_run(self): self.free=True
        with tempfile.TemporaryDirectory() as folder:
            path=_chart(Path(folder),'song',1)
            movie=Path(folder)/'bg.mp4';movie.write_bytes(b'fake')
            cfg={'master_volume':0.0}
            song_lists.set_chart_art(cfg,path,background=str(movie))
            with patch.object(PlayerHost,'core_factory',FakeCore), patch.object(demo_video,'DemoVideo',Video):
                host=PlayerHost(cfg,lambda:None);host.show();host.on_shown()
                try:
                    host.show_difficulty(path)
                    video=host._demo_video;self.assertIsNotNone(video)
                    host.show_library()
                    self.assertTrue(video.stopped);self.assertIsNone(host._demo_video)
                    host.show_difficulty(path)
                    video=host._demo_video
                    host._on_demo_setting('player_show_bg',False)
                    self.assertTrue(video.stopped);self.assertIsNone(host._demo_video)
                    host._on_demo_setting('player_show_bg',True)
                    video=host._demo_video
                    # 曲名表示へは止めずに引き継ぐ(test_intro_keeps_video_running)。
                    host.start_intro(path, 'Oni')
                    self.assertFalse(video.stopped);self.assertIs(host._demo_video,video)
                    self.assertEqual(video.frame,host.intro.set_video_frame)
                    self.assertFalse(video.free)
                    host._seq._stop_demo()            # 5秒目: 試聴を止める
                    self.assertTrue(video.free)       # 絵は自分の時計で流し続ける
                    host.show_difficulty(path)
                    self.assertTrue(video.stopped)
                    video=host._demo_video
                    host.shutdown()
                    self.assertTrue(video.stopped);self.assertIsNone(host._demo_video)
                finally:
                    host.shutdown();host.close();host.deleteLater()

class FreeRunTests(unittest.TestCase):
    @real_qt_test
    def test_free_run_loops_without_audio_clock(self):
        from PySide6.QtGui import QImage
        from PySide6.QtMultimedia import QMediaPlayer
        from neotja.player.demo_video import DemoVideo

        class Core:
            durations = []
            def demo_ready(self): return False
            def set_demo_video_duration(self, d): self.durations.append(d)

        class Player:
            def __init__(self):
                self.loops = 1
                self.state = QMediaPlayer.PausedState
                self.calls = []
            def setLoops(self, n): self.loops = n
            def playbackState(self): return self.state
            def play(self): self.state = QMediaPlayer.PlayingState; self.calls.append('play')
            def pause(self): self.state = QMediaPlayer.PausedState; self.calls.append('pause')
            def position(self): return 0
            def setPosition(self, ms): self.calls.append(('seek', ms))
            def stop(self): pass
            def setSource(self, url): pass
            def deleteLater(self): pass
        core = Core()
        got_a, got_b, cleared = [], [], []
        v = DemoVideo(core, got_a.append, lambda: cleared.append('a'))
        p = Player()
        v.player = p
        v._loaded = True
        img = QImage(4, 4, QImage.Format_RGB32)
        img.fill(0xff0000)
        v._frame(v._generation, type('F', (), {'isValid': lambda s: True,
                                                'toImage': lambda s: img})())
        self.assertEqual(len(got_a), 1)
        # 引き継ぎ: 最後の絵をすぐ新しい先へ。
        v.set_targets(got_b.append, lambda: cleared.append('b'))
        self.assertEqual(len(got_b), 1)
        # 試聴が止まった(音源の時計が無い)→ 止めずに自分でループ再生。
        v.free_run()
        self.assertTrue(v.free)
        self.assertEqual(p.loops, QMediaPlayer.Loops.Infinite)
        self.assertEqual(p.state, QMediaPlayer.PlayingState)
        p.calls.clear()
        v._sync()                     # 音源の時計は見ない(止めない)
        self.assertEqual(p.calls, [])
        v._duration(v._generation, 5000)
        self.assertEqual(core.durations, [])
        v.stop()
        self.assertEqual(cleared, ['b'])
        self.assertEqual(core.durations, [])      # 自分の時計のときは試聴へ触らない
        self.assertIsNone(v.last_image)
        # 音源の時計が無い使い方(結果画面): core=None でも落ちない。
        w = DemoVideo(None, got_a.append, lambda: None)
        w.free = True
        w.stop()
        w.deleteLater()
        v.deleteLater()

    @real_qt_test
    def test_real_video_free_mode_plays_muted_and_loops(self):
        """本物の動画(短い H264 を作れるときだけ)を free で流す。"""
        import shutil
        import subprocess
        from neotja.player.demo_video import DemoVideo
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # noqa: BLE001
            self.skipTest('ffmpeg が無い')
        with tempfile.TemporaryDirectory() as d:
            movie = os.path.join(d, 'bg.mp4')
            r = subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                                'testsrc=size=160x90:rate=30:duration=1', '-pix_fmt', 'yuv420p',
                                movie], capture_output=True)
            if r.returncode != 0 or not os.path.exists(movie):
                self.skipTest('動画を作れない')
            frames = []
            v = DemoVideo(None, frames.append, lambda: None)
            v.start(movie, free=True)
            from PySide6.QtTest import QTest
            for _ in range(250):
                QTest.qWait(10)
                if len(frames) > 45:
                    break
            self.assertGreater(len(frames), 45)        # 1秒の動画が止まらずに回る
            self.assertTrue(v.audio.isMuted())
            self.assertEqual(v.audio.volume(), 0.0)
            v.stop()
            v.deleteLater()
            QTest.qWait(50)


if __name__=='__main__': unittest.main()
