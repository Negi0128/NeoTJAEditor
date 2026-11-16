"""Player の操作音。進む・戻る・開く・切り替えるで「ピ」、難易度カードで「ドン」。

**鳴らす道は Editor の音声エンジン(MainWindow.preview_dock.audio)を借りる。**
Player に入ると Editor の曲は止めてあり、そのエンジンは空いている。ここへ
効果音として1つ投げるだけなので:
- 選曲の画面でも、難易度選択の試聴用の PreviewDock(PlayerCore)を作らずに鳴る。
- 試聴(PlayerCore の別のエンジン)とは別の流れなので、試聴を止めたり
  音量を下げたりしない。
- 音量は Editor と同じ扱い(マスター音量 × 打音の音量)。打音の ON/OFF(F1)とは
  関係なく鳴る — ミキサーの "ui_now"(F1 を見ない「今すぐ1回」)を使う。

「ピ」はここで合成する(素材を同梱しない)。約 70ms、1.8kHz の正弦に
2倍音を少し、立ち上がり 3ms・指数で減衰。
"""

import numpy as np

#: 「ピ」を入れておくミキサーの効果音の名前。
PI_SLOT = "ui_pi"

PI_FREQ = 1800.0
PI_SEC = 0.07
PI_ATTACK = 0.003
PI_DECAY = 0.018      # 指数減衰の時定数(秒)
PI_GAIN = 0.32


def synth_pi(sr):
    """「ピ」の PCM。(N, 2) の float32(-1..1)。"""
    n = max(1, int(sr * PI_SEC))
    t = np.arange(n, dtype=np.float32) / float(sr)
    wave = np.sin(2 * np.pi * PI_FREQ * t) + 0.18 * np.sin(2 * np.pi * PI_FREQ * 2 * t)
    env = np.exp(-t / PI_DECAY)
    att = np.minimum(1.0, t / PI_ATTACK)
    # 最後の 5ms は 0 へ寄せて、切れ目のプチッを消す。
    tail = np.clip((PI_SEC - t) / 0.005, 0.0, 1.0)
    mono = (wave * env * att * tail * PI_GAIN / 1.18).astype(np.float32)
    # 一度 int16 に丸めてから戻す(打音の WAV と同じ精度に揃える)。
    mono = (np.round(mono * 32767.0).astype(np.int16).astype(np.float32) / 32767.0)
    return np.repeat(mono[:, None], 2, axis=1)


class UiSounds:
    """audio_provider() が返す音声エンジンへ操作音を投げる。"""

    def __init__(self, audio_provider):
        self._provider = audio_provider
        self._loaded_into = None     # 「ピ」を入れたエンジン(作り直されたら入れ直す)

    def _engine(self):
        try:
            return self._provider() if self._provider is not None else None
        except Exception:  # noqa: BLE001
            return None

    def play(self, kind):
        if kind == "don":
            self.don()
        else:
            self.pi()

    def pi(self):
        eng = self._engine()
        core = getattr(eng, "core", None)
        if core is None:
            return      # ミキサーでない経路(旧式)では鳴らさない
        try:
            if self._loaded_into is not eng:
                core.post(("sfx", PI_SLOT, synth_pi(int(core.device_sr))))
                self._loaded_into = eng
            core.post(("ui_now", PI_SLOT))
        except Exception:  # noqa: BLE001
            pass

    def don(self):
        eng = self._engine()
        core = getattr(eng, "core", None)
        try:
            if core is not None:
                core.post(("ui_now", "don"))
            else:
                eng.hit_sounds.play_once("don")     # ミキサーでない経路
        except Exception:  # noqa: BLE001
            pass
