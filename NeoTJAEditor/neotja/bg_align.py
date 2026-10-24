# -*- coding: utf-8 -*-
"""録画の背景動画と曲の、ずれを音から当てる(利用者の仕様 2026-10-24)。

MV を背景に敷くとき、その MV の音と譜面の音源はたいてい同じ曲なので、
**両方の打点の出かた(オンセット)を見比べれば**ずれが分かる。

戻す値は RecordBackground.offset と同じ決まり — 「曲の 0 秒のときに、
背景動画の何秒目を出すか」。当てるだけで、入れるかどうかは利用者が決める
(欄に入れて見本で確かめてもらう)。
"""
import os
import subprocess

import numpy as np

from neotja import recorder
from neotja.audio_engine import _onset_envelope

#: 見比べに使う音の細かさ。オンセットを見るだけなので粗くてよい。
RATE = 11025
#: 見比べる長さ(秒)。頭からこれだけあれば十分で、長くすると遅くなる。
LOOK_SEC = 180.0
#: 探すずれの幅(秒)。これより大きくずれていることは、まず無い。
MAX_SHIFT = 120.0


def _decode(path, seconds=LOOK_SEC):
    """音を単声・RATE の数の並びにして返す。取れなければ None。"""
    if not path or not os.path.exists(path):
        return None
    cmd = [recorder._ffmpeg_exe(), "-v", "error", "-nostdin",
           "-i", path, "-vn", "-t", "%.3f" % seconds,
           "-ac", "1", "-ar", str(RATE),
           "-f", "f32le", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=180,
                           **recorder._no_window())
    except (OSError, subprocess.SubprocessError):
        return None
    if not p.stdout:
        return None                      # 音の入っていない動画
    buf = np.frombuffer(p.stdout, dtype="<f4")
    return buf if buf.size else None


def _envelope(mono):
    onset, rate = _onset_envelope(mono, RATE)
    if onset is None or not rate:
        return None, 0.0
    v = onset.astype(np.float64)
    v -= v.mean()
    s = v.std()
    if s < 1e-9:
        return None, 0.0                 # のっぺりしていて見分けが付かない
    return v / s, float(rate)


def estimate_offset(song_path, bg_path):
    """ずれ(秒)と、どれくらい確からしいかを返す。

    戻り値 (offset, 確からしさ 0〜1)。当てられなければ (None, 0.0)。
    確からしさは「いちばん高い山 ÷ 2番目に高い山」から作る — 同じ曲なら
    山が1つだけ飛び抜ける。似たところが多いと山が並び、1 に近づかない。
    """
    a_raw = _decode(song_path)
    b_raw = _decode(bg_path)
    if a_raw is None or b_raw is None:
        return None, 0.0
    a, rate = _envelope(a_raw)
    b, rate_b = _envelope(b_raw)
    if a is None or b is None or abs(rate - rate_b) > 1e-6:
        return None, 0.0
    # sum_i a[i] * b[i + k] がいちばん大きい k を探す。
    # np.correlate(b, a, "full") の添字 j は k = j - (len(a) - 1)。
    corr = np.correlate(b, a, "full")
    lags = np.arange(corr.size) - (a.size - 1)
    span = int(MAX_SHIFT * rate)
    keep = np.abs(lags) <= span
    if not keep.any():
        return None, 0.0
    corr = corr[keep]
    lags = lags[keep]
    top = int(np.argmax(corr))
    peak = float(corr[top])
    if peak <= 0.0:
        return None, 0.0
    # 山のまわり(前後 0.25 秒)を外して、2番目の高さを見る。
    near = max(1, int(0.25 * rate))
    other = np.concatenate([corr[:max(0, top - near)], corr[top + near:]])
    second = float(other.max()) if other.size else 0.0
    sure = 0.0 if peak <= 0 else max(0.0, 1.0 - max(0.0, second) / peak)
    return float(lags[top]) / rate, sure
