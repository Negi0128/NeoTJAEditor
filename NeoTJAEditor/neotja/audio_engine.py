import bisect
import math
import os
import struct
import tempfile
import wave

import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat, QAudioOutput, QMediaPlayer, QSoundEffect

_DTYPE_BY_SAMPLE_FORMAT = {
    QAudioFormat.SampleFormat.UInt8: np.uint8,
    QAudioFormat.SampleFormat.Int16: np.int16,
    QAudioFormat.SampleFormat.Int32: np.int32,
    QAudioFormat.SampleFormat.Float: np.float32,
}


def _to_float_mono(raw: bytes, fmt: QAudioFormat) -> np.ndarray:
    dtype = _DTYPE_BY_SAMPLE_FORMAT.get(fmt.sampleFormat())
    if dtype is None:
        return np.zeros(0, dtype=np.float32)
    samples = np.frombuffer(raw, dtype=dtype)
    channels = max(1, fmt.channelCount())
    frame_count = samples.size // channels
    samples = samples[: frame_count * channels].reshape(frame_count, channels)

    if dtype == np.uint8:
        floats = (samples.astype(np.float32) - 128.0) / 128.0
    elif dtype == np.int16:
        floats = samples.astype(np.float32) / 32768.0
    elif dtype == np.int32:
        floats = samples.astype(np.float32) / 2147483648.0
    else:
        floats = samples.astype(np.float32)

    return floats.mean(axis=1)


def downsample_peaks(mono: np.ndarray, target_columns: int) -> list:
    if mono.size == 0 or target_columns <= 0:
        return []
    bucket = max(1, mono.size // target_columns)
    usable = (mono.size // bucket) * bucket
    if usable == 0:
        peak = float(np.max(np.abs(mono))) if mono.size else 0.0
        return [(-peak, peak)]
    chunks = mono[:usable].reshape(-1, bucket)
    mins = chunks.min(axis=1)
    maxs = chunks.max(axis=1)
    return list(zip(mins.tolist(), maxs.tolist()))


class WaveformDecodeWorker(QThread):
    """Decodes an audio file to a peak (min, max) envelope in a background
    thread so a multi-minute song doesn't freeze the UI while loading."""

    decoded = Signal(list, float)  # peaks, duration_seconds
    failed = Signal(str)

    def __init__(self, path: str, target_columns: int = 2000, parent=None):
        super().__init__(parent)
        self.path = path
        self.target_columns = target_columns

    def run(self):
        loop = QEventLoop()
        decoder = QAudioDecoder()
        decoder.setSource(QUrl.fromLocalFile(self.path))

        chunks = []
        sample_rate = [44100]
        error_holder = []

        def on_buffer_ready():
            buf = decoder.read()
            fmt = buf.format()
            sample_rate[0] = fmt.sampleRate() or sample_rate[0]
            raw = bytes(buf.constData())
            chunks.append(_to_float_mono(raw, fmt))

        def on_finished():
            loop.quit()

        def on_error(_err):
            error_holder.append(decoder.errorString())
            loop.quit()

        decoder.bufferReady.connect(on_buffer_ready)
        decoder.finished.connect(on_finished)
        decoder.error.connect(on_error)
        decoder.start()
        loop.exec()

        if error_holder:
            self.failed.emit(error_holder[0])
            return
        if not chunks:
            self.failed.emit("音声データを読み取れませんでした。")
            return

        mono = np.concatenate(chunks)
        duration = mono.size / float(sample_rate[0])
        peaks = downsample_peaks(mono, self.target_columns)
        self.decoded.emit(peaks, duration)


class AudioEngine(QObject):
    """Thin wrapper around QMediaPlayer + QAudioOutput for song playback."""

    positionChanged = Signal(int)   # ms
    durationChanged = Signal(int)   # ms
    playingChanged = Signal(bool)
    mediaStatusChanged = Signal(object)  # QMediaPlayer.MediaStatus

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)

        self.player.positionChanged.connect(lambda p: self.positionChanged.emit(int(p)))
        self.player.durationChanged.connect(lambda d: self.durationChanged.emit(int(d)))
        self.player.playingChanged.connect(lambda playing: self.playingChanged.emit(bool(playing)))
        self.player.mediaStatusChanged.connect(self.mediaStatusChanged.emit)

    def load(self, path: str):
        self.player.setSource(QUrl.fromLocalFile(path))

    def play(self):
        self.player.play()

    def pause(self):
        self.player.pause()

    def stop(self):
        self.player.stop()

    def toggle_play_pause(self):
        if self.player.isPlaying():
            self.pause()
        else:
            self.play()

    def seek(self, ms: int):
        self.player.setPosition(ms)

    def set_volume(self, volume: float):
        self.audio_output.setVolume(max(0.0, min(1.0, volume)))

    def position(self) -> int:
        return self.player.position()

    def duration(self) -> int:
        return self.player.duration()

    def is_playing(self) -> bool:
        return self.player.isPlaying()


def ensure_click_wav() -> str:
    """Synthesizes a short decaying-sine click sound into a temp WAV (stdlib
    only: wave/struct/math) and returns its path, generating it once and
    reusing it on subsequent calls. The filename is version-tagged so tuning
    the synthesis parameters below doesn't get masked by a stale cached file
    from a previous run."""
    path = os.path.join(tempfile.gettempdir(), "neotja_metronome_click_v2.wav")
    if os.path.exists(path):
        return path

    sample_rate = 44100
    duration = 0.02
    freq = 1800.0
    n = int(sample_rate * duration)

    with wave.open(path, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n):
            t = i / sample_rate
            envelope = math.exp(-t * 200.0)
            sample = int(32767 * 0.8 * envelope * math.sin(2 * math.pi * freq * t))
            frames += struct.pack("<h", sample)
        f.writeframes(bytes(frames))
    return path


class MetronomeEngine(QObject):
    """Plays a click on every scheduled beat while audio is playing. The
    schedule is a precomputed list of 1/4-note click times (see
    TJACourseAnalyzer.build_metronome_clicks) that honors #MEASURE (default
    4/4), #BPMCHANGE and #DELAY, so it stays in sync across tempo/measure
    changes instead of assuming a single constant tempo for the whole song.

    QMediaPlayer's reported position leads what's actually audible from the
    speakers (output buffering), while the short click sound plays back with
    near-zero latency, so clicks fire perceptibly early unless compensated.
    Calibrated empirically against real songs (~25ms)."""

    LATENCY_COMPENSATION_SEC = 0.025

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = False
        self._click_times = []  # audio-time seconds, sorted, offset+latency already applied
        self._last_click_idx = None

        self.sound = QSoundEffect(self)
        self.sound.setSource(QUrl.fromLocalFile(ensure_click_wav()))
        self.sound.setVolume(0.9)

    def set_schedule(self, chart_clicks, offset: float):
        """`chart_clicks` are (chart_time_seconds, is_measure_start) tuples
        from measure-0 start, as returned by build_metronome_clicks. OFFSET
        convention: chart_time = audio_time + OFFSET, so
        audio_time = chart_time - OFFSET."""
        self._click_times = sorted(
            t - offset + self.LATENCY_COMPENSATION_SEC for t, _is_measure in (chart_clicks or [])
        )
        self._last_click_idx = None

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self._last_click_idx = None

    # QMediaPlayer's reported position can wobble by a tick or two instead of
    # advancing perfectly monotonically. At high BPM, beats are close enough
    # together in time that this wobble crosses back and forth over a beat's
    # threshold, which used to fire the same click twice. Only resync
    # _last_click_idx backward on a real seek (a big jump), not on jitter.
    JITTER_TOLERANCE = 2

    def on_position_changed(self, ms: int):
        if not self.enabled or not self._click_times:
            return
        sec = ms / 1000.0
        idx = bisect.bisect_right(self._click_times, sec)
        if self._last_click_idx is None:
            self._last_click_idx = idx
            return
        if idx > self._last_click_idx:
            self.sound.play()
            self._last_click_idx = idx
        elif idx < self._last_click_idx - self.JITTER_TOLERANCE:
            self._last_click_idx = idx
