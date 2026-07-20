"""単一のソフトウェアミキサーによる再生パス(sounddevice / WASAPI)。

これまでの再生は QMediaPlayer(曲) + QSoundEffect(打音・メトロノーム) の
2つの独立したクロックで動いていたため、両者のズレを BPM 依存の経験的な
レイテンシ補正で埋め合わせる必要があった。ここでは PeepoDrumKit と同じく、
曲と効果音をすべて1つのコールバックでサンプル単位でミックスする。効果音は
出力ブロック内の正確なサンプル位置に前もってスケジュールされるので、打音の
タイミング誤差は原理的に発生しない(補正の概念そのものが不要)。

構成:
- MixerCore: Qt にもデバイスにも依存しない純粋なミキサー本体。render(frames)
  が (frames, 2) float32 を返す。全タイミングロジックはここにあり、実機なしで
  単体テストできる(scratchpad/test_mixer.py)。
- MixerAudioEngine: MixerCore + sd.OutputStream を包み、AudioEngine と同じ
  シグナル/メソッドを提供する drop-in なファサード。打音/メトロノームは
  .hit_sounds / .metronome アダプタとして露出し、preview_dock の差分を最小化する。

スレッド安全性: sd のコールバックスレッドと GUI スレッドの間は「コマンド
キュー(collections.deque)」でやりとりする。GUI 側は append、render 側は
ブロック先頭で popleft してから音を作る(どちらも CPython の GIL 下で原子的)。
read_pos・イベントのカーソル・発音中ボイスなどの可変状態は render スレッド
だけが触るので競合しない。render 内では大きな配列確保やファイル I/O をしない
(出力バッファは事前確保、効果音はビューのスライスで足し込むだけ)。
"""

import bisect
import os
import wave
from collections import deque

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QMediaPlayer

from neotja.audio_engine import ensure_don_wav, ensure_ka_wav, ensure_click_wav

_MAX_VOICES = 32
_MAX_BLOCK = 4096


# ----------------------------------------------------------------------
# WAV 読み込み / リサンプル(いずれも load 時のみ。render では呼ばない)
# ----------------------------------------------------------------------
def _load_wav_stereo(path: str):
    """stdlib の wave で WAV を (frames, 2) float32 として読み込む。8/16/24/32bit
    PCM に対応。モノラルは左右複製、多チャンネルは先頭2chを採用。読めなければ
    (None, sr)。"""
    try:
        with wave.open(path, "rb") as w:
            nch = w.getnchannels()
            sw = w.getsampwidth()
            sr = w.getframerate()
            raw = w.readframes(w.getnframes())
    except Exception:
        return None, 44100

    if sw == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 2:
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8)
        n = b.size // 3 * 3
        b = b[:n].reshape(-1, 3).astype(np.int32)
        val = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        val = np.where(val & 0x800000, val - 0x1000000, val)
        a = val.astype(np.float32) / 8388608.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        return None, sr

    nch = max(1, nch)
    fc = a.size // nch
    if fc == 0:
        return None, sr
    a = a[: fc * nch].reshape(fc, nch)
    if nch == 1:
        st = np.repeat(a, 2, axis=1)
    else:
        st = a[:, :2]
    return np.ascontiguousarray(st, dtype=np.float32), sr


def _resample_linear(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """(N, 2) float32 を線形補間で src_sr -> dst_sr にリサンプル。"""
    if x is None or x.shape[0] == 0:
        return np.zeros((0, 2), dtype=np.float32)
    if src_sr == dst_sr:
        return np.ascontiguousarray(x, dtype=np.float32)
    n = x.shape[0]
    m = int(round(n * dst_sr / float(src_sr)))
    if m <= 0:
        return np.zeros((0, 2), dtype=np.float32)
    idx = np.arange(m, dtype=np.float64) * (src_sr / float(dst_sr))
    i0 = np.floor(idx).astype(np.int64)
    frac = (idx - i0).astype(np.float32)[:, None]
    i0 = np.clip(i0, 0, n - 1)
    i1 = np.clip(i0 + 1, 0, n - 1)
    return np.ascontiguousarray(x[i0] * (1.0 - frac) + x[i1] * frac, dtype=np.float32)


def _load_sfx(path: str, device_sr: int) -> np.ndarray:
    st, sr = _load_wav_stereo(path)
    if st is None:
        return np.zeros((1, 2), dtype=np.float32)
    return _resample_linear(st, sr, device_sr)


# ----------------------------------------------------------------------
# MixerCore: デバイス非依存の純粋ミキサー
# ----------------------------------------------------------------------
class MixerCore:
    """出力1フレームごとに read_pos を song_sr/device_sr*rate だけ進め、曲を
    線形補間で読み出しつつ、スケジュールされた効果音をブロック内の正確な
    サンプル位置から発音する。Qt・sounddevice に一切依存しないので、実機なしで
    render() を直接叩いてテストできる。"""

    def __init__(self, device_sr: int, max_block: int = _MAX_BLOCK):
        self.device_sr = int(device_sr)
        self._out = np.zeros((max(1, int(max_block)), 2), dtype=np.float32)

        # --- 曲 ---
        self.song = None            # (N, 2) float32 or None
        self.song_sr = 44100
        self.read_pos = 0.0         # 曲フレーム単位(float)
        self.rate = 1.0
        self.playing = False
        self.ended = False          # 曲末尾に到達した(GUI側がポーリングして通知)

        # --- 音量 ---
        self.vol_song = 0.8
        self.vol_sfx = 0.9
        self.vol_metro = 0.9
        self.vol_master = 1.0

        # --- 効果音バンク(すべて device_sr の (M, 2) float32)---
        self.bank = {
            "don": np.zeros((1, 2), dtype=np.float32),
            "ka": np.zeros((1, 2), dtype=np.float32),
            "click": np.zeros((1, 2), dtype=np.float32),
        }

        # --- スケジュール(いずれも音声時間 seconds でソート済み)---
        self.hit_times = []
        self.hit_kinds = []         # 'don'/'ka' の並列リスト
        self.metro_times = []
        self.hit_enabled = True
        self.metro_enabled = False
        self._hit_cursor = 0
        self._metro_cursor = 0

        # --- 発音中ボイス: [pcm, pos(int), is_metro(bool), delay(int)] ---
        self.voices = []

        # --- コマンドキュー(GUI -> render)---
        self._cmds = deque()

    # ---- GUI スレッドから呼ぶ(append のみ)----
    def post(self, cmd):
        self._cmds.append(cmd)

    # ---- コマンド適用(render スレッド上)----
    def _drain_commands(self):
        while self._cmds:
            cmd = self._cmds.popleft()
            op = cmd[0]
            if op == "seek":
                self._apply_seek(cmd[1])
            elif op == "play":
                self.ended = False
                self.playing = True
                self._recompute_cursors()
            elif op == "pause":
                self.playing = False
            elif op == "rate":
                self.rate = max(0.25, min(2.0, float(cmd[1])))
            elif op == "vol":
                setattr(self, "vol_" + cmd[1], max(0.0, min(1.0, float(cmd[2]))))
            elif op == "hit_sched":
                self.hit_times = cmd[1]
                self.hit_kinds = cmd[2]
                self._recompute_cursors()
            elif op == "metro_sched":
                self.metro_times = cmd[1]
                self._recompute_cursors()
            elif op == "hit_enabled":
                self.hit_enabled = bool(cmd[1])
            elif op == "metro_enabled":
                self.metro_enabled = bool(cmd[1])
            elif op == "sfx":
                self.bank[cmd[1]] = cmd[2]
            elif op == "song":
                self.song = cmd[1]
                self.song_sr = int(cmd[2])
                self.read_pos = 0.0
                self.ended = False
                self.voices = []
                self._recompute_cursors()

    def _apply_seek(self, frame: float):
        n = self.song.shape[0] if self.song is not None else 0
        self.read_pos = float(max(0, min(frame, n)))
        self.ended = False
        self.voices = []           # 飛び越えた効果音は鳴らさない(バースト不可)
        self._recompute_cursors()

    def _recompute_cursors(self):
        # 現在位置より前のイベントは「消費済み」とし、以降のみ発音対象にする。
        t = self.read_pos / self.song_sr if self.song_sr else 0.0
        self._hit_cursor = bisect.bisect_left(self.hit_times, t)
        self._metro_cursor = bisect.bisect_left(self.metro_times, t)

    # ---- ボイス生成 ----
    def _spawn(self, pcm: np.ndarray, offset: int, is_metro: bool):
        if pcm is None or pcm.shape[0] == 0:
            return
        if len(self.voices) >= _MAX_VOICES:
            self.voices.pop(0)     # 最古を奪う
        self.voices.append([pcm, 0, is_metro, int(offset)])

    def _fire_events(self, times, kinds, cursor_attr, is_metro, enabled,
                     block_start_time, block_end_time, frames):
        cursor = getattr(self, cursor_attr)
        ntimes = len(times)
        while cursor < ntimes and times[cursor] < block_end_time:
            et = times[cursor]
            if et >= block_start_time and enabled:
                # 出力フレーム k の音声時間は (read_pos + inc*k)/song_sr。これを
                # et について解くと k = (et - block_start_time)*device_sr/rate。
                offset = int(round((et - block_start_time) * self.device_sr / self.rate))
                if 0 <= offset < frames:
                    pcm = self.bank["click"] if is_metro else self.bank.get(kinds[cursor], None)
                    self._spawn(pcm, offset, is_metro)
            cursor += 1
        setattr(self, cursor_attr, cursor)

    # ---- 出力1ブロック生成 ----
    def render(self, frames: int) -> np.ndarray:
        if frames > self._out.shape[0]:
            self._out = np.zeros((frames, 2), dtype=np.float32)
        out = self._out[:frames]
        out[:] = 0.0

        self._drain_commands()

        # --- 曲本体 + イベント発火(再生中のみ)---
        if self.playing and self.song is not None and self.song_sr > 0:
            song = self.song
            n = song.shape[0]
            inc = self.song_sr / float(self.device_sr) * self.rate
            block_start_time = self.read_pos / self.song_sr
            end_pos = self.read_pos + inc * frames
            block_end_time = end_pos / self.song_sr

            k = np.arange(frames, dtype=np.float64)
            pos = self.read_pos + inc * k
            i0 = np.floor(pos).astype(np.int64)
            valid = (i0 >= 0) & (i0 < n - 1)
            if valid.any():
                sel = np.nonzero(valid)[0]
                iv = i0[valid]
                fv = (pos[valid] - iv).astype(np.float32)[:, None]
                out[sel] = song[iv] * (1.0 - fv) + song[iv + 1] * fv
            out *= (self.vol_song * self.vol_master)

            # イベントはカーソルを常に前進させる(無効でも消費)。有効なときだけ
            # 発音するので、途中で ON にしても溜まった打音がバーストしない。
            self._fire_events(self.hit_times, self.hit_kinds, "_hit_cursor", False,
                              self.hit_enabled, block_start_time, block_end_time, frames)
            self._fire_events(self.metro_times, None, "_metro_cursor", True,
                              self.metro_enabled, block_start_time, block_end_time, frames)

            self.read_pos = end_pos
            if end_pos >= n - 1:
                self.read_pos = float(n)
                self.playing = False
                self.ended = True

        # --- 効果音ボイス(停止中でも鳴らして減衰を残す)---
        if self.voices:
            master = self.vol_master
            gain_sfx = self.vol_sfx * master
            gain_metro = self.vol_metro * master
            remaining = []
            for v in self.voices:
                pcm, vpos, is_metro, delay = v
                start = delay
                if start >= frames:
                    v[3] = delay - frames
                    remaining.append(v)
                    continue
                avail = pcm.shape[0] - vpos
                nmix = min(avail, frames - start)
                if nmix > 0:
                    gain = gain_metro if is_metro else gain_sfx
                    out[start:start + nmix] += pcm[vpos:vpos + nmix] * gain
                    v[1] = vpos + nmix
                v[3] = 0
                if v[1] < pcm.shape[0]:
                    remaining.append(v)
            self.voices = remaining

        return out


# ----------------------------------------------------------------------
# 打音 / メトロノーム アダプタ(preview_dock が使う set_* を MixerCore へ転送)
# ----------------------------------------------------------------------
class _HitSoundAdapter:
    """HitSoundEngine と同じ呼び出し面(set_schedule/set_sound_files/set_enabled/
    set_playback_rate/check_and_play)を持つが、実体は MixerCore への薄い転送。
    ミキサー経路では打音はサンプル単位で前もってスケジュールされるため、
    レイテンシ補正も 16ms tick からの check_and_play も不要(check_and_play は
    no-op)。"""

    def __init__(self, engine: "MixerAudioEngine"):
        self._engine = engine
        self.enabled = True

    def set_schedule(self, notes, offset: float):
        # notes: [(chart_time, char, bpm)]。音声時間 = chart_time - OFFSET。
        # 補正は一切かけない(原理的に不要)。char in "13" は面(ドン)、他は縁(カッ)。
        pairs = sorted((t - offset, "don" if c in "13" else "ka") for t, c, _bpm in (notes or []))
        times = [p[0] for p in pairs]
        kinds = [p[1] for p in pairs]
        self._engine.core.post(("hit_sched", times, kinds))

    def set_sound_files(self, don_path: str, ka_path: str):
        self._engine._reload_hit_bank(don_path, ka_path)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self._engine.core.post(("hit_enabled", bool(enabled)))

    def set_playback_rate(self, rate: float):
        # ミキサーではスケジュールは音声時間のまま正しいので何もしない。
        pass

    def check_and_play(self, audio_time_sec: float):
        pass


class _MetronomeAdapter:
    def __init__(self, engine: "MixerAudioEngine"):
        self._engine = engine
        self.enabled = False

    def set_schedule(self, chart_clicks, offset: float):
        # chart_clicks: [(chart_time, is_measure_start)]。音声時間 = chart_time - OFFSET。
        times = sorted(t - offset for t, _is_measure in (chart_clicks or []))
        self._engine.core.post(("metro_sched", times))

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self._engine.core.post(("metro_enabled", bool(enabled)))

    def set_playback_rate(self, rate: float):
        pass

    def on_position_changed(self, ms: int):
        # ミキサーが内部でサンプル単位に処理するので positionChanged 駆動は不要。
        pass


# ----------------------------------------------------------------------
# MixerAudioEngine: AudioEngine と drop-in 互換のファサード
# ----------------------------------------------------------------------
class MixerAudioEngine(QObject):
    """AudioEngine(QMediaPlayer ラッパ)と同じシグナル/メソッドを提供しつつ、
    実体は sounddevice の単一ミキサー。打音/メトロノームは .hit_sounds /
    .metronome アダプタとして露出する。ストリームが開けなければ __init__ が
    例外を投げるので、preview_dock 側でレガシー三点セットへフォールバックできる。"""

    positionChanged = Signal(int)        # ms
    durationChanged = Signal(int)        # ms
    playingChanged = Signal(bool)
    mediaStatusChanged = Signal(object)  # QMediaPlayer.MediaStatus 互換

    def __init__(self, parent=None):
        super().__init__(parent)
        import sounddevice as sd  # ここで ImportError ならフォールバックさせる
        self._sd = sd

        self._stream, self.device_sr, self._latency_ms = self._open_stream(sd)
        self.core = MixerCore(self.device_sr)

        self.hit_sounds = _HitSoundAdapter(self)
        self.metronome = _MetronomeAdapter(self)

        self._playing = False
        self._duration_ms = 0
        self._song_frames = 0
        self._song_sr = 44100
        self._loaded = False

        # 既定の効果音バンク(合成音)を読み込む。set_sound_files で差し替え可能。
        self._reload_hit_bank("", "")
        self.core.post(("sfx", "click", _load_sfx(ensure_click_wav(), self.device_sr)))

        # GUI 側の位置通知タイマ(~60Hz)。再生中に positionChanged を出し、曲末尾
        # 到達もここでポーリングして検知する。
        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(16)
        self._pos_timer.timeout.connect(self._on_pos_tick)

        self._stream.start()

    # ---- ストリームを開く(WASAPI native -> default -> auto_convert@44100)----
    def _open_stream(self, sd):
        attempts = []

        # 1) WASAPI ホストの既定出力デバイスをネイティブレートで low latency
        try:
            wasapi = None
            for i, ha in enumerate(sd.query_hostapis()):
                if "WASAPI" in ha["name"]:
                    wasapi = (i, ha)
                    break
            if wasapi is not None:
                dev = wasapi[1].get("default_output_device", -1)
                if dev is not None and dev >= 0:
                    info = sd.query_devices(dev)
                    native_sr = int(info.get("default_samplerate") or 48000)
                    attempts.append(dict(device=dev, samplerate=native_sr,
                                         channels=2, dtype="float32", latency="low"))
        except Exception:
            pass

        # 2) 既定デバイス/ホスト
        try:
            info = sd.query_devices(kind="output")
            default_sr = int(info.get("default_samplerate") or 48000)
        except Exception:
            default_sr = 48000
        attempts.append(dict(samplerate=default_sr, channels=2, dtype="float32", latency="low"))

        # 3) auto_convert 付き 44100(WASAPI が 44100 を直接開けない場合の保険)
        try:
            attempts.append(dict(samplerate=44100, channels=2, dtype="float32",
                                 latency="low",
                                 extra_settings=sd.WasapiSettings(auto_convert=True)))
        except Exception:
            pass

        last_err = None
        for kw in attempts:
            try:
                stream = sd.OutputStream(callback=self._callback, **kw)
                latency_ms = float(getattr(stream, "latency", 0.0) or 0.0) * 1000.0
                return stream, int(stream.samplerate), latency_ms
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        raise RuntimeError(f"出力ストリームを開けませんでした: {last_err}")

    # ---- sd コールバック(render に委譲するだけ)----
    def _callback(self, outdata, frames, time_info, status):  # noqa: ARG002
        try:
            buf = self.core.render(frames)
            outdata[:] = buf
        except Exception:  # noqa: BLE001
            outdata.fill(0)

    # ---- 効果音バンク差し替え ----
    def _reload_hit_bank(self, don_path: str, ka_path: str):
        # HitSoundEngine.set_sound_files と同じ解決順: 指定 WAV(存在すれば)->合成音。
        don = don_path if don_path and os.path.exists(don_path) else ensure_don_wav()
        ka = ka_path if ka_path and os.path.exists(ka_path) else ensure_ka_wav()
        self.core.post(("sfx", "don", _load_sfx(don, self.device_sr)))
        self.core.post(("sfx", "ka", _load_sfx(ka, self.device_sr)))

    # ---- AudioEngine 互換 API ----
    def load(self, path: str):
        # 曲 PCM は preview_dock 側の SongDecodeWorker から set_song_pcm で届く。
        # ここでは読み込み中状態にリセットするだけ。
        self._loaded = False
        self._duration_ms = 0
        self._playing = False
        self.core.post(("pause",))
        self.core.post(("seek", 0))
        self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadingMedia)

    def set_song_pcm(self, pcm: np.ndarray, sr: int):
        """デコード済みステレオ PCM をミキサーに渡す(preview_dock から)。"""
        if pcm is None or pcm.shape[0] == 0:
            self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.InvalidMedia)
            return
        pcm = np.ascontiguousarray(pcm, dtype=np.float32)
        self._song_frames = pcm.shape[0]
        self._song_sr = int(sr)
        self._duration_ms = int(self._song_frames / float(sr) * 1000.0)
        self.core.post(("song", pcm, int(sr)))
        self._loaded = True
        self.durationChanged.emit(self._duration_ms)
        self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)
        self._emit_position()

    def play(self):
        if not self._loaded:
            return
        # 末尾で止まっている場合は先頭付近へ戻さず、そのまま。QMediaPlayer と同様。
        self.core.post(("play",))
        if not self._playing:
            self._playing = True
            self.playingChanged.emit(True)
        self._pos_timer.start()

    def pause(self):
        self.core.post(("pause",))
        if self._playing:
            self._playing = False
            self.playingChanged.emit(False)
        self._pos_timer.stop()
        self._emit_position()

    def stop(self):
        self.pause()
        self.seek(0)

    def toggle_play_pause(self):
        if self._playing:
            self.pause()
        else:
            self.play()

    def seek(self, ms: int):
        frame = int(max(0, ms) / 1000.0 * self._song_sr)
        self.core.post(("seek", frame))
        self._emit_position()

    def set_playback_rate(self, rate: float):
        self.core.post(("rate", float(rate)))

    def set_volume(self, volume: float):
        self.core.post(("vol", "song", float(volume)))

    def set_sfx_volume(self, volume: float):
        v = max(0.0, min(1.0, float(volume)))
        self.core.post(("vol", "sfx", v))
        self.core.post(("vol", "metro", v))

    def position(self) -> int:
        if self._song_sr <= 0:
            return 0
        # 出力レイテンシ分だけ引いて、耳に聞こえる音と視覚を合わせる。低速再生では
        # 音声時間の進みが遅くなるので rate を掛けて実時間レイテンシを音声時間へ換算。
        ms = self.core.read_pos / self._song_sr * 1000.0
        ms -= self._latency_ms * self.core.rate
        return int(max(0.0, ms))

    def duration(self) -> int:
        return self._duration_ms

    def is_playing(self) -> bool:
        return self._playing

    # ---- 内部 ----
    def _emit_position(self):
        self.positionChanged.emit(self.position())

    def _on_pos_tick(self):
        # 曲末尾に到達したか(render スレッドが立てる ended フラグ)を先に見る。
        if self.core.ended and self._playing:
            self._playing = False
            self.playingChanged.emit(False)
            self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
            self._emit_position()
            self._pos_timer.stop()
            return
        self._emit_position()

    def close(self):
        try:
            self._pos_timer.stop()
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass
