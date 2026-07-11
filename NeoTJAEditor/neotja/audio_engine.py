import bisect
import math
import os
import random
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


def _onset_envelope(mono: np.ndarray, sample_rate: int, hop: int = 256):
    """Shared first stage for both detect_bpm_offset() and the AI chart
    generator (neotja/ai_chart_gen.py): a cheap onset-strength envelope -
    half-wave-rectified frame-to-frame energy increase, a stand-in for
    spectral flux that doesn't need an FFT per frame. Returns
    (onset: np.ndarray, frame_rate: float), or (None, None) if there isn't
    enough signal (too short, silent, or flat) to say anything useful."""
    n_frames = mono.size // hop
    if n_frames < 8:
        return None, None
    usable = n_frames * hop
    frames = mono[:usable].reshape(n_frames, hop).astype(np.float64)
    energy = np.sqrt(np.mean(frames * frames, axis=1))
    frame_rate = sample_rate / hop

    onset = np.diff(energy, prepend=energy[0])
    np.clip(onset, 0.0, None, out=onset)
    if onset.std() < 1e-9:
        return None, None  # silence or a flat/constant signal
    return onset, frame_rate


def detect_bpm_offset(mono: np.ndarray, sample_rate: int, bpm_min: float = 60.0, bpm_max: float = 300.0):
    """Estimates tempo (BPM) and OFFSET from a decoded mono audio signal,
    using only numpy (no scipy/librosa dependency): a simple energy-onset
    envelope, autocorrelated to find the dominant beat period, plus the
    time of the first strong onset as the downbeat for OFFSET. Returns
    (bpm, offset_seconds) as (float, float), or (None, None) if the audio is
    too short/quiet to analyze.

    This is intentionally lightweight and explicitly experimental - real
    tempo trackers handle syncopation, tempo drift and octave errors (half/
    double-tempo confusion) far better. It's meant as a fast first guess to
    hand-correct via the existing tap-BPM/waveform-OFFSET tools, not a
    guaranteed-correct result.

    OFFSET convention (matches the rest of this app): audio_time =
    chart_time - OFFSET, so a first beat at audio_time T corresponds to
    OFFSET = -T."""
    onset, frame_rate = _onset_envelope(mono, sample_rate)
    if onset is None:
        return None, None

    # Autocorrelation via FFT (much faster than a direct O(n^2) loop for a
    # multi-minute song's worth of frames).
    centered = onset - onset.mean()
    fsize = 1
    while fsize < 2 * onset.size:
        fsize *= 2
    spectrum = np.fft.rfft(centered, fsize)
    autocorr = np.fft.irfft(spectrum * np.conj(spectrum))[: onset.size]

    # A 3-tap smoothing pass before picking the peak: a real period that
    # isn't an exact whole number of frames splits its correlation across
    # two adjacent lag bins ("picket fence" leakage), which can otherwise
    # make a nearby integer-multiple lag (half tempo) look taller than the
    # true, split fundamental. Smoothing recombines the split energy so the
    # fundamental wins again.
    kernel = np.ones(3) / 3.0
    smoothed = np.convolve(autocorr, kernel, mode="same")

    lag_min = max(1, int(frame_rate * 60.0 / bpm_max))
    lag_max = min(onset.size - 2, int(frame_rate * 60.0 / bpm_min))
    if lag_max <= lag_min:
        return None, None
    best_i = lag_min + int(np.argmax(smoothed[lag_min : lag_max + 1]))
    # Parabolic interpolation around the winning integer lag for sub-frame
    # precision - at hop=256 a single frame is already a few BPM wide near
    # the top of the search range, so this materially improves accuracy
    # there instead of quantizing to whatever the nearest frame allows.
    if 0 < best_i < len(smoothed) - 1:
        y0, y1, y2 = smoothed[best_i - 1], smoothed[best_i], smoothed[best_i + 1]
        denom = y0 - 2 * y1 + y2
        refined_lag = best_i + (0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0)
    else:
        refined_lag = float(best_i)
    bpm = 60.0 * frame_rate / refined_lag

    threshold = onset.mean() + onset.std() * 1.5
    strong = np.flatnonzero(onset > threshold)
    first_idx = int(strong[0]) if strong.size else int(np.argmax(onset))
    first_beat_time = first_idx / frame_rate

    return round(bpm, 2), round(-first_beat_time, 3)


def _decode_audio_file_sync(path: str, timeout_ms: int = 20000):
    """Decodes an audio file to a mono float array on the calling thread
    (blocking - callers run this from within a QThread.run(), not the GUI
    thread), via the same QAudioDecoder pattern used by WaveformDecodeWorker.
    Shared by BpmOffsetDetectWorker and ChartGenWorker so the decode-with-
    timeout-and-explicit-release dance only lives in one place. Returns
    (mono: np.ndarray | None, sample_rate: int, error: str | None) - exactly
    one of (mono, error) is set on return."""
    loop = QEventLoop()
    decoder = QAudioDecoder()
    decoder.setSource(QUrl.fromLocalFile(path))

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

    # Guards against QAudioDecoder never firing finished/error for some
    # malformed or unusual file - without this, loop.exec() below could
    # block this worker thread forever, which (since callers disable their
    # "go" button until detected/generated/failed fires) would leave that
    # button stuck disabled indefinitely with no way out.
    timed_out = []
    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(lambda: (timed_out.append(True), loop.quit()))

    decoder.bufferReady.connect(on_buffer_ready)
    decoder.finished.connect(on_finished)
    decoder.error.connect(on_error)
    timeout_timer.start(timeout_ms)
    decoder.start()
    loop.exec()
    timeout_timer.stop()

    # Explicitly release the decoder (and, on Windows, its underlying Media
    # Foundation file handle) as soon as decoding is done, rather than
    # waiting for Python's cyclic GC to eventually collect it - the
    # on_buffer_ready/on_error closures above hold a reference back to
    # decoder, which without this can keep the file locked well past when
    # this method returns. That lock caused a real bug: the new-project
    # wizard moves this same file into its song folder immediately after
    # detection finishes, and hit "PermissionError: [WinError 32] the
    # process cannot access the file" because the decoder was still holding
    # it open.
    try:
        decoder.stop()
        decoder.bufferReady.disconnect(on_buffer_ready)
        decoder.finished.disconnect(on_finished)
        decoder.error.disconnect(on_error)
        decoder.setSource(QUrl())
    except RuntimeError:
        pass
    del decoder

    if timed_out:
        return None, sample_rate[0], "音声の解析がタイムアウトしました。"
    if error_holder:
        return None, sample_rate[0], error_holder[0]
    if not chunks:
        return None, sample_rate[0], "音声データを読み取れませんでした。"
    return np.concatenate(chunks), sample_rate[0], None


class BpmOffsetDetectWorker(QThread):
    """Decodes an audio file and runs detect_bpm_offset() on it in a
    background thread, so analyzing a multi-minute song doesn't freeze the
    UI. Same QAudioDecoder decode pattern as WaveformDecodeWorker."""

    detected = Signal(float, float)  # bpm, offset_seconds
    failed = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        mono, sample_rate, error = _decode_audio_file_sync(self.path)
        if error:
            self.failed.emit(error)
            return

        bpm, offset = detect_bpm_offset(mono, sample_rate)
        if bpm is None:
            self.failed.emit("BPM/OFFSETを検出できませんでした(無音または短すぎる可能性があります)。")
            return
        self.detected.emit(bpm, offset)


class ChartGenWorker(QThread):
    """Decodes an audio file and runs the experimental AI chart draft
    generator (neotja/ai_chart_gen.py) on it in a background thread. Same
    decode pattern as BpmOffsetDetectWorker (via _decode_audio_file_sync)."""

    generated = Signal(str)  # generated TJA course body text
    failed = Signal(str)

    def __init__(self, path: str, bpm: float, offset: float, subdivision: int = 16,
                 density: float = 0.5, measure_num: int = 4, measure_den: int = 4, parent=None):
        super().__init__(parent)
        self.path = path
        self.bpm = bpm
        self.offset = offset
        self.subdivision = subdivision
        self.density = density
        self.measure_num = measure_num
        self.measure_den = measure_den

    def run(self):
        # Imported here, not at module level, since ai_chart_gen imports
        # _onset_envelope from this module - a top-level import here would
        # be circular.
        from neotja.ai_chart_gen import format_tja_body, generate_notes

        mono, sample_rate, error = _decode_audio_file_sync(self.path)
        if error:
            self.failed.emit(error)
            return

        duration = mono.size / sample_rate
        notes = generate_notes(mono, sample_rate, self.bpm, self.offset,
                                subdivision=self.subdivision, density=self.density)
        if not notes:
            self.failed.emit("ノーツを生成できませんでした(無音または短すぎる可能性があります)。")
            return
        body = format_tja_body(notes, self.bpm, subdivision=self.subdivision,
                                measure_num=self.measure_num, measure_den=self.measure_den,
                                duration_seconds=duration)
        self.generated.emit(body)


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


def _synth_wav(filename: str, duration: float, freq: float, decay: float, amplitude: float,
                noise_mix: float = 0.0) -> str:
    """Shared synthesis helper behind ensure_click_wav/ensure_don_wav/ensure_ka_wav:
    a decaying sine (optionally blended with white noise for a more percussive,
    less pure-tone attack) rendered to a temp WAV via stdlib only."""
    path = os.path.join(tempfile.gettempdir(), filename)
    if os.path.exists(path):
        return path

    sample_rate = 44100
    n = int(sample_rate * duration)

    with wave.open(path, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n):
            t = i / sample_rate
            envelope = math.exp(-t * decay)
            tone = math.sin(2 * math.pi * freq * t)
            noise = random.uniform(-1.0, 1.0)
            wave_val = tone * (1.0 - noise_mix) + noise * noise_mix
            sample = int(32767 * amplitude * envelope * wave_val)
            sample = max(-32768, min(32767, sample))
            frames += struct.pack("<h", sample)
        f.writeframes(bytes(frames))
    return path


def ensure_don_wav() -> str:
    """Low-pitched drum-center hit sound for the game preview's don (1/3) notes."""
    return _synth_wav("neotja_hit_don_v1.wav", duration=0.09, freq=110.0, decay=28.0, amplitude=0.9, noise_mix=0.15)


def ensure_ka_wav() -> str:
    """Higher-pitched rim hit sound for the game preview's ka (2/4) notes."""
    return _synth_wav("neotja_hit_ka_v1.wav", duration=0.06, freq=520.0, decay=45.0, amplitude=0.8, noise_mix=0.35)


class HitSoundEngine(QObject):
    """Plays a don/ka hit sound exactly when a note crosses the judgment line
    during game-preview playback - same bisect + jitter-tolerance pattern as
    MetronomeEngine (see its docstring), just with two sounds selected
    per-note instead of one fixed click.

    This needs its own (smaller, negative, and - unlike the metronome's -
    BPM-dependent) compensation rather than reusing MetronomeEngine's fixed
    +0.025. Calibrated from two real measurements, each against a different
    song, using the same method: OFFSET that lines up the metronome (always
    at the metronome's own fixed +0.025s compensation) vs. OFFSET that lines
    up the don/ka hit sound *at whatever LATENCY_COMPENSATION_SEC this class
    was actually using at the time of that measurement* - not always
    +0.025s, since the two measurements were taken in different sessions
    after this value had already been tuned once. Getting that baseline
    wrong (assuming +0.025 for both) is what caused an earlier version of
    this formula to wildly over-delay high-BPM notes. Since offset and this
    compensation shift the trigger point the same way,
      comp_new = comp_baseline - (offset_hitsound - offset_metronome)
      BPM 140: offsets -1.755 (metronome) / -1.720 (hit sound), measured
               while comp_baseline was still +0.025 (the as-yet-untuned
               default copied from the metronome)
               -> comp = 0.025 - 0.035 = -0.010
      BPM 290: offsets -8.19 (metronome) / -8.199 (hit sound), measured
               while comp_baseline was already -0.015 (this class's own
               value from the previous tuning round)
               -> comp = -0.015 - (-0.009) = -0.006
    The BPM 140 point was then nudged by a further -0.001s after listening
    (-0.010 -> -0.011), same as the earlier single-constant tuning rounds
    did before this became BPM-dependent. The two points don't match a
    single constant, so instead of one fixed value this fits a straight line
    through them and interpolates/extrapolates from whichever BPM is active
    at each note, via _compensation_for_bpm(). Likely still needs further
    by-ear correction outside this 140-290 BPM range."""

    _CAL_BPM_1, _CAL_COMP_1 = 140.0, -0.011
    _CAL_BPM_2, _CAL_COMP_2 = 290.0, -0.006
    _COMP_SLOPE = (_CAL_COMP_2 - _CAL_COMP_1) / (_CAL_BPM_2 - _CAL_BPM_1)
    _COMP_INTERCEPT = _CAL_COMP_1 - _COMP_SLOPE * _CAL_BPM_1
    JITTER_TOLERANCE = 2

    @classmethod
    def _compensation_for_bpm(cls, bpm) -> float:
        b = bpm if bpm and bpm > 0 else cls._CAL_BPM_1
        return cls._COMP_INTERCEPT + cls._COMP_SLOPE * b

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = True
        self._note_times = []  # audio-time seconds, sorted
        self._note_is_don = []  # parallel bool list
        self._last_idx = None
        self._last_audio_time = None

        self.sound_don = QSoundEffect(self)
        self.sound_ka = QSoundEffect(self)
        self.set_sound_files("", "")

    def set_sound_files(self, don_path: str, ka_path: str):
        """Uses the given WAV files if they're set and exist (e.g. pointed at
        a real simulator's own sound assets) so the preview can sound exactly
        like the user's usual setup, falling back to the built-in synthesized
        click otherwise. Referenced by absolute path rather than bundled into
        the app, since a real simulator's sound assets aren't ours to
        redistribute."""
        don = don_path if don_path and os.path.exists(don_path) else ensure_don_wav()
        ka = ka_path if ka_path and os.path.exists(ka_path) else ensure_ka_wav()
        self.sound_don.setSource(QUrl.fromLocalFile(don))
        self.sound_don.setVolume(0.9)
        self.sound_ka.setSource(QUrl.fromLocalFile(ka))
        self.sound_ka.setVolume(0.9)

    def set_schedule(self, notes, offset: float):
        """`notes` is [(chart_time_seconds, note_char, bpm)] for '1'-'4'
        (don/ka/big-don/big-ka), as built from build_preview_timeline()'s
        "notes" (bpm carried along per-note so the compensation can vary with
        it - see _compensation_for_bpm). OFFSET convention:
        audio_time = chart_time - OFFSET."""
        pairs = sorted(
            (t - offset + self._compensation_for_bpm(bpm), c in "13")
            for t, c, bpm in (notes or [])
        )
        self._note_times = [p[0] for p in pairs]
        self._note_is_don = [p[1] for p in pairs]
        self._last_idx = None
        self._last_audio_time = None

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self._last_idx = None
        self._last_audio_time = None

    # A seek/cursor-jump/measure-jump can move audio_time_sec by seconds in
    # a single tick, which used to make check_and_play below play every note
    # in between all at once (a burst) since it just looked at how far the
    # bisect index moved. Real forward playback only ever advances by about
    # one tick's worth (~16ms) between calls, so anything far bigger than
    # that is unambiguously a jump, not playback - resync silently instead.
    SEEK_JUMP_THRESHOLD_SEC = 0.5

    def check_and_play(self, audio_time_sec: float):
        """Called from ChartPreviewWidget's own smoothly-interpolated 16ms
        tick (the same "now" that drives the scrolling visuals) rather than
        QMediaPlayer's raw positionChanged signal, which fires irregularly
        and coarsely enough that several notes could land in the same
        update - firing them all in one burst instead of at their real
        individual times, which is what made playback feel uneven/laggy."""
        if not self.enabled or not self._note_times:
            self._last_audio_time = audio_time_sec
            return
        idx = bisect.bisect_right(self._note_times, audio_time_sec)
        if self._last_idx is None:
            self._last_idx = idx
            self._last_audio_time = audio_time_sec
            return
        jumped = (
            self._last_audio_time is not None
            and abs(audio_time_sec - self._last_audio_time) > self.SEEK_JUMP_THRESHOLD_SEC
        )
        if jumped:
            self._last_idx = idx
        elif idx > self._last_idx:
            for i in range(self._last_idx, idx):
                (self.sound_don if self._note_is_don[i] else self.sound_ka).play()
            self._last_idx = idx
        elif idx < self._last_idx - self.JITTER_TOLERANCE:
            self._last_idx = idx
        self._last_audio_time = audio_time_sec


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
