"""Zoom-adaptive waveform data (mip chain), Qt-free so it can be unit tested
and built off the GUI thread.

PeepoDrumKit renders its waveform from a per-channel "mip chain": level 0 is a
decimated absolute-value envelope of the PCM, and every next level halves the
previous one by averaging adjacent pairs. At draw time it picks the level whose
sample spacing best matches the current seconds-per-pixel, so zooming in reveals
real detail instead of stretching one fixed envelope. This module is the same
idea, vectorized with numpy.

なぜ level 0 を間引く(BASE_DECIMATION)のか
------------------------------------------
level 0 に生サンプルをそのまま持つと、チェイン全体で生 PCM の約2倍のメモリを
食う(1/2 + 1/4 + ... = 1)。5分ステレオ 44.1kHz なら PCM だけで 106MB、チェインで
212MB になり実用的でない。level 0 を BASE_DECIMATION=8 サンプルの絶対値平均に
間引くと、level 0 の時間分解能は 8/44100 ≈ 0.181ms。波形の横幅を 2000px と
すると、これは表示スパン 0.36 秒相当まで「1列=1サンプル以上」を保てる計算で、
OFFSET 合わせに必要なズーム(WaveformWidget.MAX_ZOOM=1000 → 5分曲で 0.3 秒)を
十分カバーする。メモリは 5分/44.1kHz/ステレオで level0 が 6.3MB/ch、チェイン
合計で約 12.6MB/ch = 25MB(float32)。
"""

import numpy as np

# level 0 を作るときの間引き率(何サンプルの絶対値平均を1点にするか)。
BASE_DECIMATION = 8


class WaveformMips:
    """Per-channel chains of absolute-value amplitude envelopes.

    Attributes
    ----------
    chains : list[list[np.ndarray]]
        chains[channel][level] -> float32 abs-amplitude array.
    step0 : float
        Seconds represented by one level-0 sample.
    duration, samplerate, n_channels
    """

    # 「全チャンネル合成(モノラル表示)」を peaks() の channel に渡すための番兵。
    MIX = -1

    def __init__(self, chains, samplerate, duration, step0, n_channels):
        self.chains = chains
        self.samplerate = int(samplerate) if samplerate else 0
        self.duration = float(duration)
        self.step0 = float(step0)
        self.n_channels = int(n_channels)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------
    @classmethod
    def build(cls, pcm, samplerate, *, min_mip_samples=256, max_levels=24,
              base_decimation=BASE_DECIMATION):
        """`pcm` is (frames,) mono or (frames, channels) float32. Returns a
        WaveformMips; an empty/None input yields an empty-but-usable object
        (peaks() then just returns zeros)."""
        samplerate = int(samplerate) if samplerate else 0
        if pcm is None:
            pcm = np.zeros((0, 1), dtype=np.float32)
        pcm = np.asarray(pcm)
        if pcm.ndim == 1:
            pcm = pcm.reshape(-1, 1)
        if pcm.ndim != 2:
            pcm = pcm.reshape(-1, 1)
        frames, n_channels = int(pcm.shape[0]), max(1, int(pcm.shape[1]))
        duration = (frames / samplerate) if samplerate else 0.0
        decim = max(1, int(base_decimation))
        step0 = (decim / samplerate) if samplerate else 0.0

        chains = []
        for ch in range(n_channels):
            col = np.abs(np.asarray(pcm[:, ch], dtype=np.float32)) if frames else \
                np.zeros(0, dtype=np.float32)
            # level 0: decim サンプルごとの絶対値平均。端数は切り捨て(最大でも
            # decim-1 サンプル = 0.18ms 未満なので表示上は無視できる)。
            if decim > 1 and col.size >= decim:
                usable = (col.size // decim) * decim
                level0 = col[:usable].reshape(-1, decim).mean(axis=1)
            elif col.size:
                level0 = col.copy() if decim == 1 else np.array([col.mean()], dtype=np.float32)
            else:
                level0 = np.zeros(0, dtype=np.float32)
            level0 = level0.astype(np.float32, copy=False)

            levels = [level0]
            while len(levels) < max_levels:
                prev = levels[-1]
                if prev.size <= min_mip_samples or prev.size < 2:
                    break
                usable = (prev.size // 2) * 2
                levels.append(prev[:usable].reshape(-1, 2).mean(axis=1).astype(np.float32, copy=False))
            chains.append(levels)

        return cls(chains, samplerate, duration, step0, n_channels)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------
    @property
    def n_levels(self) -> int:
        return len(self.chains[0]) if self.chains else 0

    def is_empty(self) -> bool:
        return not self.chains or self.chains[0][0].size == 0 or self.step0 <= 0

    def level_step(self, level: int) -> float:
        """Seconds per sample at `level`."""
        return self.step0 * (2 ** max(0, int(level)))

    def pick_level(self, seconds_per_pixel: float) -> int:
        """Index of the mip whose sample spacing is closest (in log2 space) to
        `seconds_per_pixel`, clamped to the chain. Monotonically
        non-decreasing in seconds_per_pixel, i.e. zooming out picks coarser
        levels and zooming in picks finer ones."""
        if self.is_empty():
            return 0
        n = self.n_levels
        if seconds_per_pixel <= self.step0:
            return 0
        level = int(round(np.log2(seconds_per_pixel / self.step0)))
        return max(0, min(level, n - 1))

    def total_bytes(self) -> int:
        return sum(a.nbytes for levels in self.chains for a in levels)

    def peaks(self, channel: int, t_start: float, t_end: float, n_columns: int):
        """Per-column envelope over [t_start, t_end) as (mins, maxs) float32
        arrays of length `n_columns`. Amplitudes are stored as absolute values,
        so mins == -maxs. Times outside the song are padded with zeros.

        `channel` may be WaveformMips.MIX (-1) for a combined (mono) view,
        which takes the per-column max across channels - cheaper and visually
        equivalent to keeping a third downmixed chain around."""
        n_columns = max(1, int(n_columns))
        out = np.zeros(n_columns, dtype=np.float32)
        if self.is_empty() or t_end <= t_start:
            return -out, out

        if channel == self.MIX or channel is None:
            for ch in range(self.n_channels):
                np.maximum(out, self._peaks_channel(ch, t_start, t_end, n_columns), out=out)
            return -out, out

        ch = max(0, min(int(channel), self.n_channels - 1))
        out = self._peaks_channel(ch, t_start, t_end, n_columns)
        return -out, out

    def _peaks_channel(self, ch: int, t_start: float, t_end: float, n_columns: int) -> np.ndarray:
        out = np.zeros(n_columns, dtype=np.float32)
        spp = (t_end - t_start) / n_columns
        level = self.pick_level(spp)
        arr = self.chains[ch][level]
        n = arr.size
        if n == 0:
            return out
        step = self.level_step(level)

        # 列境界をレベル内のサンプル添字(実数)へ。
        edges = np.linspace(t_start / step, t_end / step, n_columns + 1)
        starts = np.floor(edges[:-1]).astype(np.int64)
        ends = np.maximum(np.ceil(edges[1:]).astype(np.int64), starts + 1)
        valid = (ends > 0) & (starts < n)
        vi = np.nonzero(valid)[0]
        if vi.size == 0:
            return out

        s = np.clip(starts[vi], 0, n - 1)
        lo = int(s[0])
        hi = int(min(max(ends[vi][-1], lo + 1), n))
        sub = arr[lo:hi]
        if sub.size == 0:
            return out
        ind = s - lo
        # reduceat は [ind[k], ind[k+1]) を畳み込み、最後は sub の末尾まで =
        # ちょうど最終列の範囲。ind は非減少・全て < sub.size なので安全。
        np.clip(ind, 0, sub.size - 1, out=ind)
        out[vi] = np.maximum.reduceat(sub, ind)
        return out
