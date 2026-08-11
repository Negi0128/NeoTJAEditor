"""どんちゃん(演奏画面のキャラクター)の絵と動き。

素材は skin/1_Chara/<状態>/<番号>.png の連番。太鼓さん次郎/TNDE の
Graphics/5_Game/1_Chara/1P/ をそのまま持ってきた形。**同梱はしない**ので、
無ければ available() が False になり、呼ぶ側は黙って描かない。

動かし方の考え方は OpenTaiko(MIT) の CActImplCharacter / CActImplDancer と
同じ「コマ番号の並び + 何拍で1周」。あちらはスキンの設定ファイルから
Game_Chara_Motion_* / Game_Chara_Beat_* を読むが、TNDE-R のスキンには
その設定が入っていない(動かし方は exe の中)。なのでここでは
「連番をそのままの順で BEATS_PER_LOOP 拍かけて1周する」を既定にする。

  119コマ / 4拍 は BPM120 で 2.0 秒 = 約60fps。素材の枚数から見て
  60fps で描かれた絵だと思われるので、この値を初期値にしている。

状態は3つ:
  Normal      … ふだん
  GoGoStart   … ゴーゴーに入った瞬間に1回だけ流す
  GoGo        … ゴーゴー中のループ
"""

import os

from PySide6.QtGui import QPixmap

from neotja import settings as settings_mod

#: skin/ の下でキャラの連番を置くフォルダ。
CHARA_DIR = "1_Chara"

STATE_NORMAL = "Normal"
STATE_GOGO = "GoGo"
STATE_GOGO_START = "GoGoStart"

#: 読みに行く状態。GoGoStart が無いスキンでも動くよう、欠けは許容する。
STATES = (STATE_NORMAL, STATE_GOGO, STATE_GOGO_START)


class CharaSprites:
    """連番 PNG を必要になった順に読む。

    全状態を先に読むと 300 枚近くを一度に抱えることになるので、コマは
    **最初に映った時だけ**読んでから覚える。1周目だけわずかに重く、
    2周目からは貼るだけになる。"""

    def __init__(self, base_dir=None):
        self._dir = base_dir or os.path.join(str(settings_mod.skin_dir()), CHARA_DIR)
        self._counts = {}
        self._cache = {}
        for state in STATES:
            self._counts[state] = self._count_frames(state)

    def _state_dir(self, state):
        return os.path.join(self._dir, state)

    def _count_frames(self, state):
        """0.png から連番が何枚続いているかを数える。

        歯抜け(0,1,3)は素材の作りとしてありえないので、最初の欠けで止める。
        フォルダの列挙ではなく順に見るのは、番号順を確実にするため。"""
        d = self._state_dir(state)
        if not os.path.isdir(d):
            return 0
        n = 0
        while os.path.exists(os.path.join(d, "%d.png" % n)):
            n += 1
        return n

    def available(self):
        """ふだんの絵があるか。これが False なら描画側は何もしない。"""
        return self._counts.get(STATE_NORMAL, 0) > 0

    def count(self, state):
        return self._counts.get(state, 0)

    def has(self, state):
        return self._counts.get(state, 0) > 0

    def frame(self, state, index):
        """state の index コマ目。無ければ None。"""
        n = self._counts.get(state, 0)
        if n <= 0:
            return None
        index = max(0, min(int(index), n - 1))
        key = (state, index)
        pm = self._cache.get(key)
        if pm is None:
            path = os.path.join(self._state_dir(state), "%d.png" % index)
            pm = QPixmap(path)
            if pm.isNull():
                pm = None
            self._cache[key] = pm
        return pm


class CharaAnimator:
    """再生位置と BPM とゴーゴー状態から、いま出すコマを決める。

    拍で進める(OpenTaiko と同じ)。時間で進めると BPM が変わったときに
    曲と合わなくなるため。"""

    #: 連番を1周するのにかける拍数。
    BEATS_PER_LOOP = 4.0
    #: これ以上時間が飛んだらシークとみなして位相を作り直す(秒)。
    SEEK_JUMP_SEC = 0.4

    def __init__(self, sprites=None):
        self.sprites = sprites if sprites is not None else CharaSprites()
        self.beats_per_loop = self.BEATS_PER_LOOP
        self._state = STATE_NORMAL
        self._frames = 0.0         # いまの状態に入ってから進めたコマ数
        self._last_time = None
        self._last_gogo = False

    def frames_per_beat(self):
        """1拍あたり何コマ送るか。

        「ふだんの絵の枚数 ÷ beats_per_loop」を全状態で共通のコマ送り速度に
        する。状態ごとに『枚数 ÷ 拍数』で決めると、枚数の少ない遷移アニメ
        (GoGoStart は53枚)だけゆっくり再生されてしまうため。"""
        n = self.sprites.count(STATE_NORMAL)
        if n <= 0:
            n = 1
        return n / max(1e-6, self.beats_per_loop)

    def reset(self):
        self._state = STATE_NORMAL
        self._frames = 0.0
        self._last_time = None
        self._last_gogo = False

    def state(self):
        return self._state

    def update(self, now, bpm, gogo):
        """now(秒) と bpm と ゴーゴー中かで状態と位相を進める。

        戻り値は (状態, コマ番号)。素材が無ければ (状態, None)。"""
        gogo = bool(gogo)
        # --- 経過拍を出す ---
        if self._last_time is None:
            dt = 0.0
        else:
            dt = now - self._last_time
            if dt < 0.0 or dt > self.SEEK_JUMP_SEC:
                # シーク・一時停止からの復帰。位相は保ったまま繋ぎ直す。
                dt = 0.0
        self._last_time = now
        try:
            bpm = float(bpm)
        except (TypeError, ValueError):
            bpm = 0.0
        beats = abs(bpm) / 60.0 * dt

        # --- 状態の遷移 ---
        if gogo != self._last_gogo:
            if gogo and self.sprites.has(STATE_GOGO_START):
                # ゴーゴーに入った。遷移の絵があれば1回流してからループへ。
                self._state = STATE_GOGO_START
            else:
                self._state = STATE_GOGO if gogo else STATE_NORMAL
            self._frames = 0.0
            self._last_gogo = gogo

        # --- コマを進める(送りの速さは全状態で共通) ---
        self._frames += beats * self.frames_per_beat()

        if self._state == STATE_GOGO_START:
            n = self.sprites.count(STATE_GOGO_START)
            if n and self._frames < n:
                return (STATE_GOGO_START, int(self._frames))
            # 流し終わったのでループへ。余ったコマはループ側へ引き継ぐ。
            self._frames = max(0.0, self._frames - n)
            self._state = STATE_GOGO

        state = self._state
        if not self.sprites.has(state):
            # ゴーゴーの絵が無いスキンではふだんの絵で通す。
            state = STATE_NORMAL
        n = self.sprites.count(state)
        if n <= 0:
            return (state, None)
        self._frames %= n
        return (state, min(int(self._frames), n - 1))

    def pixmap(self, now, bpm, gogo):
        """update した結果の絵。無ければ None。"""
        state, idx = self.update(now, bpm, gogo)
        if idx is None:
            return None
        return self.sprites.frame(state, idx)
