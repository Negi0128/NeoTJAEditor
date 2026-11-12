"""曲ファイルから BPM と OFFSET の「最初の当たり」を出す(利用者の指定 2026-11-12)。

新規作成ダイアログで曲を選んだ/落とし終わった直後に走り、BPM 欄と OFFSET 欄を
埋める。あくまで手直し前提の下書きなので、外れても落ちず、欄は編集できる
ままにしておく。依存は numpy だけ(scipy/librosa は venv に無い)。

audio_engine.detect_bpm_offset(実験的機能のもの)とは別物にした理由:
あちらは 256 サンプルのエネルギー差分 + 単純な自己相関で、半分/倍のテンポに
よく化けるし OFFSET も「強い立ち上がり」頼みで曲によって前後する。こちらは
  - BPM: スペクトル・フラックスの立ち上がり包絡 → 自己相関の山を候補に
    → 候補ごとに 1〜16 倍の周期の山の高さを掛け合わせて評価(偽の周期は
    どこかの倍数で拍の裏に落ちる)→ 長い周期(数拍ぶん)の山で小数点以下を
    詰める → 90〜200 BPM へ畳んで整数に丸める
  - OFFSET: 10 ms ごとの RMS が雑音の床(-45 dBFS か山の 3%)を初めて
    超えた所を、標本単位まで寄せて拾う
という作りで、整数 BPM と「最初の音」の位置をそれぞれ別の物差しで測る。
"""
import math

import numpy as np
from PySide6.QtCore import QThread, Signal

# 出す BPM の範囲(太鼓の譜面はほぼこの中に収まる)。外で見つかった周期は
# 倍/半分に畳んでここへ入れる。
BPM_PREFER_LO = 90.0
BPM_PREFER_HI = 200.0

# OFFSET 用の雑音の床。-45 dBFS はエンコードのノイズやフェードの頭より上、
# 実際の打楽器・歌より下に来る値。山の 3% は録音レベルが低い曲向けの保険。
NOISE_FLOOR_DBFS = -45.0
NOISE_FLOOR_RATIO = 0.03
RMS_WINDOW_SEC = 0.010


# ----------------------------------------------------------------------
# デコード
# ----------------------------------------------------------------------
def decode_mono(path: str):
    """曲を float32 のモノラルへ。(mono, sr) か、読めなければ (None, 0)。

    まず audio_engine.decode_stereo_ffmpeg(ffmpeg 直叩き、速い)、だめなら
    QAudioDecoder 経路。どちらもアプリ本体が波形表示や再生で使っている
    ものをそのまま借りる(デコード処理を増やさない)。"""
    from neotja import audio_engine
    got = audio_engine.decode_stereo_ffmpeg(path)
    if got is not None:
        stereo, sr = got
        return stereo.mean(axis=1).astype(np.float32, copy=False), int(sr)
    mono, sr, err = audio_engine._decode_audio_file_sync(path)
    if err or mono is None or mono.size == 0:
        return None, 0
    return np.asarray(mono, dtype=np.float32), int(sr)


# ----------------------------------------------------------------------
# OFFSET
# ----------------------------------------------------------------------
def first_sound_time(mono: np.ndarray, sr: int):
    """最初に音が鳴る時刻(秒)。無音なら None。

    10 ms 窓の RMS が床を超えた最初の窓を見つけ、その窓の中で床を超えた
    最初の標本まで寄せる(窓の粒度 10 ms のままだと OFFSET が最大 10 ms
    ずれるので)。"""
    if mono is None or mono.size == 0:
        return None
    x = np.asarray(mono, dtype=np.float32)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 0.0:
        return None
    floor = max(10.0 ** (NOISE_FLOOR_DBFS / 20.0), NOISE_FLOOR_RATIO * peak)

    win = max(1, int(round(sr * RMS_WINDOW_SEC)))
    n_win = x.size // win
    if n_win == 0:
        idx = int(np.argmax(np.abs(x) >= floor))
        return idx / sr
    rms = np.sqrt(np.mean(x[: n_win * win].reshape(n_win, win).astype(np.float64) ** 2, axis=1))
    over = np.flatnonzero(rms >= floor)
    if over.size == 0:
        # 端数(最後の 10 ms 未満)だけに音があるような極端な場合。
        tail = np.abs(x[n_win * win:])
        if tail.size and tail.max() >= floor:
            return (n_win * win + int(np.argmax(tail >= floor))) / sr
        return None
    w = int(over[0])
    seg = np.abs(x[w * win:(w + 1) * win])
    hit = np.flatnonzero(seg >= floor)
    # RMS は超えたのに個々の標本が床に届かない(薄く広く鳴っている)
    # ときは窓の頭を採る。
    sample = w * win + (int(hit[0]) if hit.size else 0)
    return sample / sr


# ----------------------------------------------------------------------
# BPM
# ----------------------------------------------------------------------
def onset_envelope(mono: np.ndarray, sr: int, hop_sec: float = 0.005):
    """スペクトル・フラックスの立ち上がり包絡と、そのフレームレート。

    5 ms 刻み(200 fps)。短い窓(約 23 ms)の対数振幅スペクトルの
    フレーム間増分を正の側だけ足す。エネルギー差分より音色の変化
    (ハイハットやスネア)に反応しやすく、持続音の揺れには鈍い。
    足りないときは (None, 0)。"""
    if mono is None or mono.size == 0:
        return None, 0.0
    hop = max(1, int(round(sr * hop_sec)))
    # 窓は 2 の冪で hop の 4 倍以上。44.1k だと hop=220 → 1024。
    win = 1
    while win < hop * 4:
        win *= 2
    x = np.asarray(mono, dtype=np.float32)
    n_frames = (x.size - win) // hop + 1
    if n_frames < 16:
        return None, 0.0
    frame_rate = sr / hop

    # 1 フレームずつ処理せず、stride で (n_frames, win) の眺めを作って
    # まとめて FFT する(コピーはしない)。
    view = np.lib.stride_tricks.as_strided(
        x, shape=(n_frames, win), strides=(x.strides[0] * hop, x.strides[0]), writeable=False)
    hann = np.hanning(win).astype(np.float32)
    flux = np.empty(n_frames, dtype=np.float64)
    prev = None
    # 3 分の曲で 36000 × 513 の複素配列は 300MB 近くになるので、塊ごと。
    chunk = 4096
    for s in range(0, n_frames, chunk):
        block = view[s:s + chunk] * hann
        mag = np.log1p(np.abs(np.fft.rfft(block, axis=1)) * 10.0)
        if prev is None:
            prev = mag[0]
        d = np.diff(mag, axis=0, prepend=prev[None, :])
        np.clip(d, 0.0, None, out=d)
        flux[s:s + block.shape[0]] = d.sum(axis=1)
        prev = mag[-1]

    # ゆっくりした音量の変化(フェード等)を引いて、拍の立ち上がりだけを残す。
    k = max(1, int(round(frame_rate * 0.4)))
    kernel = np.ones(k) / k
    local = np.convolve(flux, kernel, mode="same")
    onset = flux - local
    np.clip(onset, 0.0, None, out=onset)
    if onset.std() < 1e-9:
        return None, 0.0
    return onset, frame_rate


def _autocorr(sig: np.ndarray) -> np.ndarray:
    """FFT で自己相関。lag 0 で 1 になるよう正規化。"""
    c = sig - sig.mean()
    n = 1
    while n < 2 * c.size:
        n *= 2
    spec = np.fft.rfft(c, n)
    ac = np.fft.irfft(spec * np.conj(spec))[: c.size]
    if ac[0] <= 0:
        return np.zeros_like(ac)
    return ac / ac[0]


def _peak_interp(y: np.ndarray, i: int) -> float:
    """整数位置 i の山を放物線で補間した実数位置。"""
    if i <= 0 or i >= y.size - 1:
        return float(i)
    y0, y1, y2 = y[i - 1], y[i], y[i + 1]
    den = y0 - 2 * y1 + y2
    if abs(den) < 1e-12:
        return float(i)
    return i + 0.5 * (y0 - y2) / den


def _peak_near(ac: np.ndarray, pos: float, tol: float) -> float:
    """lag=pos の周り ±tol の自己相関の最大。周期が整数でなくても k 倍先の
    山を取りこぼさないため。"""
    lo = max(1, int(round(pos - tol)))
    hi = min(ac.size - 1, int(round(pos + tol)))
    if hi < lo:
        return 0.0
    return float(ac[lo:hi + 1].max())


# 櫛の歯の数。拍の周期 L なら L, 2L, ... 16L(4小節ぶん)のどこにも山が
# 立つが、付点(1.5L)や 16 分 5 つぶんのような偽の周期は、その倍数のどこか
# (9L, 15L など)で必ず拍の裏に落ちて山が消える。歯を多くするほど偽物が
# ふるい落とされやすい(実曲 70 曲で 4→16 本にして正答が増えた)。
COMB_TEETH = 16
# 自己相関の対数を足すときの床。0 や負(拍の裏)を log に入れないため。
# 1 本でも床を踏むと大きく減点されるのが「積」の狙い。
COMB_FLOOR = 0.02
# 探す範囲(出力範囲より広く取り、見つけた周期を 90〜200 へ畳む)。
SEARCH_BPM_LO = 50.0
SEARCH_BPM_HI = 320.0
# 90〜200 の外にある候補への掛け目。外でも勝てるくらい強ければ採り、
# あとで倍/半分に畳む。
OUT_OF_RANGE_WEIGHT = 0.6
# 半分の周期の山が本命のこの割合以上なら、倍の BPM を採る(estimate_bpm 末尾)。
FASTER_RATIO = 0.7


def fold_bpm(bpm: float) -> float:
    """倍/半分にして 90〜200 へ入れる。太鼓の譜面はほぼこの範囲なので、
    外で見つかった周期は同じ拍の別の読み方と見なす。"""
    while bpm > BPM_PREFER_HI:
        bpm /= 2.0
    while bpm < BPM_PREFER_LO:
        bpm *= 2.0
    return bpm


def estimate_bpm(onset: np.ndarray, frame_rate: float):
    """立ち上がり包絡から BPM(実数、90〜200 へ畳んだもの)。決められなければ None。

    1. 自己相関の山(極大)を 50〜320 BPM の範囲で候補にする
    2. 候補 L ごとに、L, 2L, ..., 16L の山の高さの対数を足す(= 積)。
       本当の拍なら全部の歯が山に乗る。偽の周期はどこかの歯が拍の裏に
       落ちて床を踏み、大きく減点される
    3. 90〜200 の外の候補は少し不利にする
    4. 勝った周期を放物線補間 + 数拍先の山で小数点以下まで詰め、90〜200 へ畳む
    """
    if onset is None or onset.size < 16:
        return None
    ac = _autocorr(onset)
    lag_min = max(2, int(math.floor(frame_rate * 60.0 / SEARCH_BPM_HI)))
    lag_max = int(math.ceil(frame_rate * 60.0 / SEARCH_BPM_LO))
    if lag_max >= ac.size // 2:
        lag_max = ac.size // 2 - 1
    if lag_max <= lag_min + 2:
        return None

    seg = ac[lag_min:lag_max + 1]
    inner = np.arange(1, seg.size - 1)
    is_peak = (seg[inner] > seg[inner - 1]) & (seg[inner] >= seg[inner + 1]) & (seg[inner] > 0)
    cands = (inner[is_peak] + lag_min).tolist()
    if not cands:
        return None

    best_lag = None
    best_score = -1e18
    for lag in cands:
        score = 0.0
        for k in range(1, COMB_TEETH + 1):
            pos = lag * k
            if pos * 1.05 >= ac.size:
                v = 0.0
            else:
                v = max(0.0, _peak_near(ac, pos, max(1.0, 0.03 * pos)))
            score += math.log(v + COMB_FLOOR)
        bpm = 60.0 * frame_rate / lag
        if not (BPM_PREFER_LO <= bpm <= BPM_PREFER_HI):
            score += math.log(OUT_OF_RANGE_WEIGHT)
        if score > best_score:
            best_score, best_lag = score, lag

    lag = _peak_interp(ac, best_lag)
    if lag <= 0:
        return None

    # 細かい位置合わせ: 周期 lag を何個も並べた先(数拍ぶん)の自己相関の
    # 山を使う。1 フレームの量子化誤差が k 分の 1 になる。
    # 例: 200 fps, 174 BPM → 1 拍 34.5 フレームで ±0.5 フレームは ±2.5 BPM
    # だが、8 拍先(276 フレーム)なら ±0.3 BPM。
    refined = lag
    for k in (8, 4, 2):
        center = lag * k
        half = max(2, int(round(lag * 0.25)))
        lo_i = int(round(center - half))
        hi_i = int(round(center + half))
        if lo_i < 1 or hi_i >= ac.size:
            continue
        window = ac[lo_i:hi_i + 1]
        j = int(np.argmax(window))
        p = _peak_interp(ac, lo_i + j)
        # 山が範囲の端に張り付いている=別の山を拾ったので採用しない。
        if 0 < j < window.size - 1 and p > 0:
            refined = p / k
            break
    bpm = fold_bpm(60.0 * frame_rate / refined)

    # 倍にしても範囲に収まり、半分の周期にも拍と呼べる山(本命の 7 割以上)
    # があるなら倍を採る。櫛の積は「全部の歯が高い」方を選ぶので、範囲の
    # 下寄り(98 と 197 など)で遅い読み方に倒れやすく、太鼓の譜面は速い
    # 読み方で書かれることが多いため。
    if bpm * 2.0 <= BPM_PREFER_HI:
        lag_now = 60.0 * frame_rate / bpm
        v_half = _peak_near(ac, lag_now / 2.0, max(1.0, 0.03 * lag_now / 2.0))
        v_now = _peak_near(ac, lag_now, max(1.0, 0.03 * lag_now))
        if v_now > 0 and v_half >= FASTER_RATIO * v_now:
            bpm *= 2.0
    return bpm


def guess_bpm_offset(mono: np.ndarray, sr: int):
    """(整数 BPM か None, OFFSET 秒か None)。

    OFFSET はアプリ全体の約束(audio = chart - OFFSET)に合わせて、最初の
    音が音声時刻 t なら -t。小数 3 桁に丸める。"""
    t0 = first_sound_time(mono, sr)
    offset = round(-t0, 3) if t0 is not None else None
    onset, fr = onset_envelope(mono, sr)
    bpm = estimate_bpm(onset, fr) if onset is not None else None
    if bpm is not None:
        bpm = int(round(bpm))
    return bpm, offset


# ----------------------------------------------------------------------
# ダイアログから使うワーカー
# ----------------------------------------------------------------------
class AudioGuessWorker(QThread):
    """曲ファイルを別スレッドでデコードして guess_bpm_offset() を回す。

    guessed(bpm, offset) の bpm は整数(決められなければ 0)、offset は
    秒(決められなければ nan)。Signal は None を運べないので番兵で代用し、
    受け手は AudioGuessWorker.unpack() で戻す。
    失敗は failed(短い理由) で、例外はすべてここで止める(ダイアログを
    落とさない)。"""

    guessed = Signal(int, float)
    failed = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @staticmethod
    def unpack(bpm: int, offset: float):
        return (bpm if bpm > 0 else None,
                None if (offset != offset) else offset)  # nan は自分と等しくない

    def run(self):
        try:
            mono, sr = decode_mono(self.path)
            if self._cancelled:
                return
            if mono is None or sr <= 0:
                self.failed.emit("音声を読み込めませんでした")
                return
            bpm, offset = guess_bpm_offset(mono, sr)
        except Exception as e:  # noqa: BLE001 - 解析の失敗で窓を落とさない
            self.failed.emit(f"解析に失敗しました: {e}")
            return
        if self._cancelled:
            return
        if bpm is None and offset is None:
            self.failed.emit("音が小さすぎるか短すぎて推定できませんでした")
            return
        self.guessed.emit(bpm or 0, float("nan") if offset is None else float(offset))
