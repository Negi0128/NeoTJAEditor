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

状態:
  Normal            … ふだん
  GoGoStart         … ゴーゴーに入った瞬間に1回だけ流す
  GoGo              … ゴーゴー中のループ
  Balloon_Breaking  … 風船を叩いている間のループ
  Balloon_Broke     … 風船が割れた瞬間に1回だけ流す

風船の2つだけは**拍ではなく時間で**送る。OpenTaiko の CActImplCharacter は
状態ごとに nCharaBeat を持っていて、ふだんの絵は拍で進めるのに対し

    case Anime.Balloon_Breaking: nCharaBeat = 0.2f;
    case Anime.Balloon_Broke:    nCharaBeat = 0.2f;
    void updateBalloon() { nNowCharaCounter += DeltaTime / nCharaBeat; }

と、風船だけ DeltaTime をそのまま割っている(= BPM に依らず 0.2 秒で1周)。
画布も大きく(TNDE-R では ふだん 360x184 に対し 648x345)、キャラの立ち位置が
状態ごとに違うので、置き場所は状態ごとに絵の中身から測って合わせる。
"""

import os

from PySide6.QtGui import QPixmap

from neotja import settings as settings_mod

#: skin/ の下でキャラの連番を置くフォルダ。
CHARA_DIR = "1_Chara"

STATE_NORMAL = "Normal"
STATE_GOGO = "GoGo"
STATE_GOGO_START = "GoGoStart"
STATE_BALLOON = "Balloon_Breaking"
STATE_BALLOON_BROKE = "Balloon_Broke"

#: 読みに行く状態。欠けは許容する(無い状態はふだんの絵で通す)。
STATES = (STATE_NORMAL, STATE_GOGO, STATE_GOGO_START,
          STATE_BALLOON, STATE_BALLOON_BROKE)
#: 拍ではなく時間で送る状態(OpenTaiko の updateBalloon と同じ扱い)。
TIME_BASED_STATES = (STATE_BALLOON, STATE_BALLOON_BROKE)


class CharaSprites:
    """連番 PNG を必要になった順に読む。

    全状態を先に読むと 300 枚近くを一度に抱えることになるので、コマは
    **最初に映った時だけ**読んでから覚える。1周目だけわずかに重く、
    2周目からは貼るだけになる。"""

    def __init__(self, base_dir=None):
        self._dir = base_dir or os.path.join(str(settings_mod.skin_dir()), CHARA_DIR)
        self._counts = {}
        self._cache = {}
        self._boxes = {}
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

    def content_box(self, state):
        """その状態の1コマ目の「中身の外接」(左, 上, 右, 下)。無ければ None。

        画布の大きさもキャラの立ち位置も状態ごとに違う(TNDE-R では
        ふだん 360x184 / 風船 648x345)ので、画布の左上をそのまま画面に
        置くと状態が変わった瞬間にキャラが飛ぶ。1コマ目の中身がどこに
        あるかを測っておいて、そこを基準に置く。

        測るのは1コマ目だけ。同じ状態の他のコマは同じ基準で描かれている
        ので、コマごとの動き(跳ねる・移動する)はそのまま残る。"""
        if state in self._boxes:
            return self._boxes[state]
        box = None
        pm = self.frame(state, 0)
        if pm is not None and not pm.isNull():
            img = pm.toImage()
            w, h = img.width(), img.height()
            left, top, right, bottom = w, h, -1, -1
            for y in range(h):
                for x in range(w):
                    if img.pixelColor(x, y).alpha() > 8:
                        if x < left:
                            left = x
                        if x > right:
                            right = x
                        if y < top:
                            top = y
                        if y > bottom:
                            bottom = y
            if right >= 0:
                box = (left, top, right, bottom)
        self._boxes[state] = box
        return box

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
    #: 風船の状態を1周するのにかける秒数(OpenTaiko の nCharaBeat = 0.2f)。
    BALLOON_LOOP_SEC = 0.2

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

    def update(self, now, bpm, gogo, balloon=None):
        """now(秒) と bpm と ゴーゴー中かで状態と位相を進める。

        balloon は風船の様子: "hitting"(叩いている最中) / "broke"(割れた) /
        None(風船に関係なし)。風船はゴーゴーより優先する — 叩いている間は
        そちらの絵に切り替わるのが本家の挙動(b風船連打中 を先に見ている)。

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

        # --- 風船(ゴーゴーより優先) ---
        want = None
        if balloon == "hitting" and self.sprites.has(STATE_BALLOON):
            want = STATE_BALLOON
        elif balloon == "broke" and self.sprites.has(STATE_BALLOON_BROKE):
            want = STATE_BALLOON_BROKE
        if want is not None:
            if self._state != want:
                self._state = want
                self._frames = 0.0
            self._last_gogo = gogo      # 抜けたときに遷移が二重に走らないよう
            n = self.sprites.count(want)
            if n <= 0:
                return (want, None)
            self._frames += dt / max(1e-6, self.BALLOON_LOOP_SEC) * n
            if want == STATE_BALLOON_BROKE and self._frames >= n:
                # 割れる絵は1回だけ。流し終わったら次のフレームで戻る。
                return (want, n - 1)
            self._frames %= n
            return (want, min(int(self._frames), n - 1))
        if self._state in TIME_BASED_STATES:
            # 風船が終わったので、ふだん/ゴーゴーへ戻す。
            self._state = STATE_GOGO if gogo else STATE_NORMAL
            self._frames = 0.0

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

    def pixmap(self, now, bpm, gogo, balloon=None):
        """update した結果の絵。無ければ None。"""
        state, idx = self.update(now, bpm, gogo, balloon)
        if idx is None:
            return None
        return self.sprites.frame(state, idx)
