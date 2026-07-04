import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat, QAudioOutput, QMediaPlayer

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)

        self.player.positionChanged.connect(lambda p: self.positionChanged.emit(int(p)))
        self.player.durationChanged.connect(lambda d: self.durationChanged.emit(int(d)))
        self.player.playingChanged.connect(lambda playing: self.playingChanged.emit(bool(playing)))

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
