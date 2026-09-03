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

from PySide6.QtCore import QThread
from PySide6.QtGui import QImage, QPixmap

from neotja import settings as settings_mod

from neotja import worker_util

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


def _content_box_of(img):
    """QImage の「中身(不透明な画素)の外接」(左, 上, 右, 下)。空なら None。

    **なぜ numpy で見るのか**
    以前はここを `img.pixelColor(x, y)` の二重ループで回していた。1コマ
    360x184 でも 66,240 回、風船の 648x345 では 223,560 回の呼び出しになり、
    実測で 1状態あたり 77ms かかっていた。しかもこれが走るのは「その状態が
    初めて画面に出たコマ」— つまりゴーゴーに入った瞬間に 2状態ぶん 154ms
    まとめて持っていかれ、そのコマだけ描画が止まって見える。

    アルファ値だけ見れば済む話なので、画素の並びをそのまま numpy の配列に
    して端を探す。実測 77ms -> 0.4ms。

    numpy が無い環境でも動くようにフォールバックを残す(numpy はこのアプリの
    必須依存ではあるが、ここで転んでキャラが出なくなるより、遅くても出る方が
    まし)。フォールバックでも走るのは preload 中の裏スレッドなので、
    再生中のコマには乗らない。"""
    if img is None or img.isNull():
        return None
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return None
    try:
        import numpy as np

        # アルファの位置を固定するため、並びの分かっている形式へ揃える。
        # 既に同じ形式なら Qt 側で複製は起きない。
        conv = img.convertToFormat(QImage.Format_RGBA8888)
        ptr = conv.constBits()
        # bytesPerLine は 4 バイト境界へ切り上げられていることがあるので、
        # 行の余りごと取ってから幅のぶんだけ切る。
        stride = conv.bytesPerLine()
        arr = np.frombuffer(ptr, dtype=np.uint8, count=stride * h)
        arr = arr.reshape(h, stride // 4, 4)[:, :w, 3]
        mask = arr > 8
        rows = np.flatnonzero(mask.any(axis=1))
        cols = np.flatnonzero(mask.any(axis=0))
        if rows.size == 0 or cols.size == 0:
            return None
        return (int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1]))
    except Exception:  # noqa: BLE001
        pass
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
    if right < 0:
        return None
    return (left, top, right, bottom)


class _PreloadWorker(QThread):
    """連番 PNG の復号を GUI スレッドの外でやるワーカー。

    QPixmap は GUI スレッドでしか作れないが、QImage は作れる。重いのは
    PNG の復号のほう(実測 1コマ 1.6ms、332コマで 530ms)なので、そこだけ
    裏へ出して、GUI スレッドには「QImage -> QPixmap」(同 0.16ms)だけを
    残す。

    中身を持ち帰るのに Signal を使わないのは、332枚の QImage を Qt の
    キューに載せる意味が無いため。走り終えたら `images` を読むだけでよい
    (finished は GUI スレッドで受けるので、そこで読めば競合しない)。"""

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self._jobs = jobs
        self._cancel = False
        self.images = []            # [((state, index), QImage), ...]

    def cancel(self):
        self._cancel = True

    def run(self):
        out = []
        for key, path in self._jobs:
            if self._cancel:
                return
            img = QImage(path)
            out.append((key, None if img.isNull() else img))
        self.images = out


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
        self._preloader = None
        self._preloaded = False
        for state in STATES:
            self._counts[state] = self._count_frames(state)

    # ------------------------------------------------------------------
    def preload(self, on_done=None):
        """全コマを再生前に読んでおく。裏スレッドで復号し、GUI スレッドで
        QPixmap にする。すでに読んであれば何もしない。

        **なぜ要るのか(遅延読みでは駄目な理由)**
        コマ送りは「ふだんの絵の枚数 ÷ 4拍」で決まる(frames_per_beat)。
        TNDE-R の素材は Normal が 119枚なので、BPM120 では **1秒あたり約60枚**
        の新しいコマが要る。遅延読みだとその 60枚が再生中に1枚ずつ読まれる
        ことになり、1枚の QPixmap 生成が実測 7.5ms — 120fps の締切 8.3ms を
        まるごと使い切る。結果、2コマに1コマが締切落ちして実効 fps が
        **125 -> 70 に半減**する。これが「ときどき 70fps に落ちる」の正体で、
        しかも Normal(119枚)とゴーゴー(GoGoStart 53 + GoGo 118)で
        **曲の頭 2秒とゴーゴー突入の 3秒**という、いちばん目立つ場所で起きる。

        332枚を一度に抱えても実測 40MB 弱で、演奏画面を開いている間だけの
        話なので、素直に全部持つ。

        読み込みそのものを速くする道(縮小して持つ・アトラスに焼く)も考えたが、
        絵が変わってしまう/焼くのに結局同じだけかかるので採らない。"""
        if self._preloaded or self._preloader is not None:
            return
        if not self.available():
            self._preloaded = True
            if on_done:
                on_done()
            return
        jobs = []
        for state in STATES:
            n = self._counts.get(state, 0)
            for i in range(n):
                if (state, i) not in self._cache:
                    jobs.append(((state, i), os.path.join(self._state_dir(state),
                                                          "%d.png" % i)))
        if not jobs:
            self._preloaded = True
            if on_done:
                on_done()
            return
        w = _PreloadWorker(jobs)
        self._preloader = w
        w.finished.connect(lambda: self._absorb(on_done))
        w.start()

    def _absorb(self, on_done=None):
        """裏で復号した QImage を QPixmap に直して抱える(GUI スレッド)。

        ついでに状態ごとの content_box もここで済ませる。再生中に初めて
        測ると、その状態が出た最初のコマだけ止まって見えるため。"""
        w = self._preloader
        self._preloader = None
        if w is None:
            return
        for key, img in w.images:
            if key in self._cache:
                continue
            if img is None:
                self._cache[key] = None
                continue
            pm = QPixmap.fromImage(img)
            self._cache[key] = None if pm.isNull() else pm
            if key[1] == 0 and key[0] not in self._boxes:
                self._boxes[key[0]] = _content_box_of(img)
        self._preloaded = True
        if on_done:
            on_done()

    def cancel_preload(self):
        """走っている先読みを切り離す(ウィジェットが消えるときなど)。"""
        w = self._preloader
        self._preloader = None
        if w is not None:
            worker_util.detach_worker(w)

    def _state_dir(self, state):
        return os.path.join(self._dir, state)

    def _count_frames(self, state):
        """0.png から連番が何枚続いているかを数える。

        歯抜け(0,1,3)は素材の作りとしてありえないので、最初の欠けで止める。

        **なぜ列挙するのか**
        以前は 0.png から1枚ずつ os.path.exists で確かめていた。5状態で
        300枚近くあるので、それだけで 300回以上の問い合わせになり、実測 98ms
        かかっていた(キャッシュは %LOCALAPPDATA% にあり、1回の stat が
        ウイルス対策ソフトを通るぶん高くつく)。フォルダを1回 os.scandir して
        名前を集めれば、状態ごとに1回の問い合わせで済む(実測 5ms)。

        列挙しても「最初の欠けで止める」判定は変えていない — 集めた名前の
        集合に対して 0 から数え上げるので、歯抜けの扱いは以前と同じ。"""
        d = self._state_dir(state)
        try:
            with os.scandir(d) as it:
                names = set(e.name for e in it)
        except OSError:
            # フォルダごと無い(= 素材が入っていない)。以前の os.path.isdir と
            # 同じく 0 枚として扱う。
            return 0
        n = 0
        while ("%d.png" % n) in names:
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
        img = None
        pm = self.frame(state, 0)
        if pm is not None and not pm.isNull():
            img = pm.toImage()
        box = _content_box_of(img)
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

    def frame_index(self):
        """いま出しているコマ番号。update() のあとに読む。素材が無ければ None。

        画面側が「同じコマをもう一度、別の板に描く」ために要る(風船の絵だけ
        レーンより手前に出す)。update() をもう一度呼ぶと時間が二重に進むので、
        結果だけをここから取る。"""
        n = self.sprites.count(self._state)
        if n <= 0:
            return None
        return min(int(self._frames), n - 1)

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
