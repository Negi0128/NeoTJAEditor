"""難易度選択の無音背景動画。音源の時計を基準に追従する。"""
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
        self._generation = 0
        self._last_audio = None
        self._loaded = False
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._sync)

    def start(self, path):
        self.stop()
        if not is_video_path(path) or not os.path.isfile(path):
            return
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
        self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        self._timer.start()

    def stop(self):
        self._generation += 1
        self._timer.stop()
        self._loaded = False
        self._last_audio = None
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
            self.player.deleteLater()
        for obj in (self.sink, self.audio):
            if obj is not None:
                obj.deleteLater()
        self.player = self.sink = self.audio = None
        self.core.set_demo_video_duration(None)
        self._clear_callback()

    def _duration(self, generation, ms):
        if generation == self._generation and ms > 0:
            self.core.set_demo_video_duration(ms / 1000.0)

    def _status(self, generation, status):
        if generation != self._generation or self.player is None:
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
            self._frame_callback(image)

    def _sync(self):
        player = self.player
        if player is None or not self._loaded:
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
