"""Player の無音背景動画。

- 難易度選択: 試聴の音源の時計に追従する(DEMOSTART・折り返しも音源に合わせる)。
- 曲名表示: 難易度選択の動画をそのまま引き継ぎ(set_targets)、試聴が止まったら
  自分の時計で流し続ける(free_run。無音・ループ)。
- 結果画面: 最初から自分の時計で流す(start(..., free=True))。

音源への追従(2026-11-18 測り直し): QMediaPlayer は自分の時計で絵を出すので、
頭出し(シーク)のあとに音源の時計から一定だけずれたまま走る。実測で、1080p・
キーフレーム間隔 250 の H264 だと絵が常に 40〜45ms 先に出ていた(以前の
「0.25 秒を超えたらシーク」では一度も直らない幅)。そこで:

- ずれは**届いたフレームの時刻(startTime)と、その瞬間の音源の時計**の差で
  測る(player.position() は粗く、出している絵と合わない)。音源の時計は
  PlayerCore.demo_position() = 出力の遅れ(PortAudio の latency とワイヤレス調整)を
  引いた「耳に届いている位置」。
- 小さなずれ(DRIFT_START 以上)は再生速度を ±3% まで少し変えて寄せる
  (シークすると絵が止まって見えるため)。DRIFT_STOP 未満で 1 倍へ戻す。
- 大きなずれ(SEEK_SEC 超え)・折り返し・始めだけシークする。シークした直後は
  追いつくまでフレームが続けて届くので、SETTLE_SEC のあいだは測らない。
"""
import os
import time

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink

VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.mkv', '.avi', '.m4v'}

def is_video_path(path):
    return os.path.splitext(str(path or ''))[1].lower() in VIDEO_EXTENSIONS


class DemoVideo(QObject):
    #: これを超えたらシークし直す(秒)。
    SEEK_SEC = 0.4
    #: 速度で寄せ始めるずれ / 1 倍へ戻すずれ(秒)。
    DRIFT_START = 0.02
    DRIFT_STOP = 0.008
    #: 速度を変える上限(±)と、ずれ 1 秒あたりの変え方。
    MAX_NUDGE = 0.03
    GAIN = 0.6
    #: シーク直後に測らない時間(秒)。
    SETTLE_SEC = 0.35
    #: ずれの平滑化(フレームごと)。
    EMA = 0.2

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
        #: 測ったずれ(動画 - 音源, 秒)。None はまだ測っていない。
        self.drift = None
        self._settle_until = 0.0
        self._rate = 1.0
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
        self._set_rate(1.0)
        if player.playbackState() != QMediaPlayer.PlayingState:
            player.play()

    def stop(self):
        self._generation += 1
        self._timer.stop()
        self._loaded = False
        self._last_audio = None
        self.last_image = None
        self.drift = None
        self._rate = 1.0
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
        self._measure(frame)
        # 絵にする(toImage は 1080p で 1コマ約 10ms)のは、描けるときに
        # いちばん新しいコマだけ。届くたびに直すと GUI がそれで埋まり、
        # 画面を描けるのが毎秒 4〜7 コマに落ちていた。
        self._pending_frame = frame
        if not self._convert_queued:
            self._convert_queued = True
            QTimer.singleShot(0, lambda g=generation: self._convert(g))

    _pending_frame = None
    _convert_queued = False

    def _convert(self, generation):
        self._convert_queued = False
        frame, self._pending_frame = self._pending_frame, None
        if frame is None or generation != self._generation:
            return
        image = frame.toImage()
        if not image.isNull():
            self.last_image = image
            self._frame_callback(image)

    def _measure(self, frame):
        """届いたフレームの時刻と音源の時計の差を、平らにして持つ。"""
        if self.free or self.core is None or self.player is None:
            return
        if time.monotonic() < self._settle_until:
            return
        try:
            if not self.core.demo_playing():
                return
            pts = frame.startTime()
            if pts is None or pts < 0:
                return
            err = pts / 1e6 - float(self.core.demo_position())
        except Exception:  # noqa: BLE001
            return
        if abs(err) > 5.0:
            return                     # 折り返しの境目など。_sync に任せる
        self.drift = err if self.drift is None else (
            self.drift + (err - self.drift) * self.EMA)

    def _seek(self, position):
        self.player.setPosition(round(position * 1000))
        self.drift = None
        self._settle_until = time.monotonic() + self.SETTLE_SEC
        self._set_rate(1.0)

    def _set_rate(self, rate):
        rate = round(float(rate) / 0.005) * 0.005
        if abs(rate - self._rate) < 1e-6:
            return
        self._rate = rate
        player = self.player
        if player is not None:
            try:
                player.setPlaybackRate(rate)
            except Exception:  # noqa: BLE001
                pass

    def _nudge(self):
        """測ったずれを速度で寄せる(先なら少し遅く、後なら少し速く)。"""
        d = self.drift
        if d is None:
            return
        if abs(d) < self.DRIFT_STOP:
            self._set_rate(1.0)
        elif abs(d) >= self.DRIFT_START or self._rate != 1.0:
            k = max(-self.MAX_NUDGE, min(self.MAX_NUDGE, d * self.GAIN))
            self._set_rate(1.0 - k)

    def _sync(self):
        player = self.player
        if player is None or not self._loaded or self.free:
            return
        if not self.core.demo_ready():
            player.pause()
            return
        position = max(0.0, self.core.demo_position())
        wrapped = self._last_audio is not None and position < self._last_audio - .05
        coarse = abs(player.position() / 1000.0 - position)
        far = (self.drift is not None and abs(self.drift) > self.SEEK_SEC) or (
            self.drift is None and coarse > self.SEEK_SEC
            and time.monotonic() >= self._settle_until)
        if self._last_audio is None or wrapped or far:
            self._seek(position)
        else:
            self._nudge()
        self._last_audio = position
        if self.core.demo_playing():
            if player.playbackState() != QMediaPlayer.PlayingState:
                player.play()
        elif player.playbackState() == QMediaPlayer.PlayingState:
            player.pause()
