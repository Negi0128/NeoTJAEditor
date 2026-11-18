"""Player の無音背景動画。

- 難易度選択: 試聴の音源の時計に追従する(DEMOSTART・折り返しも音源に合わせる)。
- 曲名表示: 難易度選択の動画をそのまま引き継ぎ(set_targets)、試聴が止まったら
  自分の時計で流し続ける(free_run。無音・ループ)。
- 結果画面: 最初から自分の時計で流す(start(..., free=True))。
"""
import os
from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink

VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.mkv', '.avi', '.m4v'}

def is_video_path(path):
    return os.path.splitext(str(path or ''))[1].lower() in VIDEO_EXTENSIONS


class DemoVideo(QObject):
    def __init__(self, core, frame_callback, clear_callback, parent=None):
        super().__init__(parent)
        self.core = core
        self._frame_callback = frame_callback
        self._clear_callback = clear_callback
        self.player = self.sink = self.audio = None
        self.path = ""
        #: 音源の時計を見ずに、自分で流し続けているか(無音・ループ)。
        self.free = False
        #: 最後に届いた絵(引き継ぎ先へすぐ渡すため)。
        self.last_image = None
        self._generation = 0
        self._last_audio = None
        self._loaded = False
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._sync)

    def start(self, path, free=False):
        """path を読み込んで流す。free=True なら音源の時計を見ずに、
        読めたら頭から無音でループ再生する(結果画面・曲名表示のやり直し)。"""
        self.stop()
        if not is_video_path(path) or not os.path.isfile(path):
            return
        self.path = path
        self.free = bool(free)
        self._generation += 1
        generation = self._generation
        self.player = QMediaPlayer(self)
        self.sink = QVideoSink(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.0)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(
            lambda frame: self._frame(generation, frame))
        self.player.durationChanged.connect(
            lambda ms: self._duration(generation, ms))
        self.player.mediaStatusChanged.connect(
            lambda status: self._status(generation, status))
        self.player.errorOccurred.connect(
            lambda *_: self._failed(generation))
        if self.free:
            self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        if self.free:
            self.player.play()
        else:
            self._timer.start()

    def set_targets(self, frame_callback, clear_callback):
        """絵を渡す先を替える(難易度選択 → 曲名表示など)。最後の絵はすぐ渡す。"""
        self._frame_callback = frame_callback
        self._clear_callback = clear_callback
        if self.last_image is not None:
            frame_callback(self.last_image)

    def free_run(self):
        """音源の時計から離れ、今の位置から自分で流し続ける(無音・ループ)。
        試聴が止まった・下がりきったあとも絵を止めないため。"""
        if self.free:
            return
        self.free = True
        self._timer.stop()
        player = self.player
        if player is None:
            return
        player.setLoops(QMediaPlayer.Loops.Infinite)
        if player.playbackState() != QMediaPlayer.PlayingState:
            player.play()

    def stop(self):
        self._generation += 1
        self._timer.stop()
        self._loaded = False
        self._last_audio = None
        self.last_image = None
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
            self.player.deleteLater()
        for obj in (self.sink, self.audio):
            if obj is not None:
                obj.deleteLater()
        self.player = self.sink = self.audio = None
        if self.core is not None and not self.free:
            self.core.set_demo_video_duration(None)
        self._clear_callback()

    def _duration(self, generation, ms):
        if (generation == self._generation and ms > 0 and not self.free
                and self.core is not None):
            self.core.set_demo_video_duration(ms / 1000.0)

    def _status(self, generation, status):
        if generation != self._generation or self.player is None:
            return
        if self.free:
            if status == QMediaPlayer.InvalidMedia:
                self._failed(generation)
            elif (status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia)
                  and self.player.playbackState() != QMediaPlayer.PlayingState):
                self.player.play()
            return
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self._loaded = True
            self._sync()
        elif status == QMediaPlayer.EndOfMedia and self.core.demo_ready():
            self.core.restart_demo()
            self._last_audio = None
            self._sync()
        elif status == QMediaPlayer.InvalidMedia:
            self._failed(generation)

    def _failed(self, generation):
        if generation == self._generation:
            self.stop()

    def _frame(self, generation, frame):
        if generation != self._generation or not frame.isValid():
            return
        image = frame.toImage()
        if not image.isNull():
            self.last_image = image
            self._frame_callback(image)

    def _sync(self):
        player = self.player
        if player is None or not self._loaded or self.free:
            return
        if not self.core.demo_ready():
            player.pause()
            return
        position = max(0.0, self.core.demo_position())
        wrapped = self._last_audio is not None and position < self._last_audio - .05
        if self._last_audio is None or wrapped or abs(player.position()/1000.0-position) > .25:
            player.setPosition(round(position * 1000))
        self._last_audio = position
        if self.core.demo_playing():
            if player.playbackState() != QMediaPlayer.PlayingState:
                player.play()
        elif player.playbackState() == QMediaPlayer.PlayingState:
            player.pause()
