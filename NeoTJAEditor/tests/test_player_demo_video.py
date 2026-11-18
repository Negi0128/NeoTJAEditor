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
            def start(self,path): self.path=path
            def stop(self): self.stopped=True
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
                    host.start_intro(path, 'Oni')
                    self.assertTrue(video.stopped);self.assertIsNone(host._demo_video)
                    host.show_difficulty(path)
                    video=host._demo_video
                    host.shutdown()
                    self.assertTrue(video.stopped);self.assertIsNone(host._demo_video)
                finally:
                    host.shutdown();host.close();host.deleteLater()

if __name__=='__main__': unittest.main()
