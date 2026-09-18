# -*- coding: utf-8 -*-
"""演奏モードの「叩いた記録」。

**なぜ別の入れ物にするのか**
再生モードの画面は、コンボもスコアも魂ゲージも「良」のポップも、
すべて *いまの時刻* から二分探索で導いている。状態を持たないから、
どこへシークしても矛盾しないし、録画がフレームを飛び飛びに描いても
成立する。判定はその逆で、「どの音符をいつ何で叩いたか」という状態が
要る。混ぜると再生側の性質(シーク安全・録画可能)が壊れるので、
演奏のぶんだけをここに閉じ込め、画面は演奏モードのときだけここを見る。

判定窓は難易度で変えず、**すべて鬼の値**を使う(利用者の指定)。
"""

import bisect

from neotja.constants import KIND_DON, KIND_KA
from neotja.gauge import GaugeModel
from neotja.score import ROLL_HIT_SCORE, compute_scoring

#: 面(ドン)の音符と縁(カツ)の音符。大音符も同じ扱いにする。
DON_CHARS = frozenset("13")
KA_CHARS = frozenset("24")

#: 入力の種類。定義は constants(譜面プレビュー側も使うため)。

#: 判定の名前。画面の絵(Judge.png)の段の並びと同じ順。
JUDGE_GOOD = "good"
JUDGE_OK = "ok"
JUDGE_BAD = "bad"

#: 連打の帯が赤くなる速さ。1打で足すぶんと、毎秒戻るぶんと、ためられる上限。
#: Anasoko(Notes.cs / GameKeyProc.cs)の値をそのまま採った。叩けば 0.25 ずつ
#: 濃くなり、手を止めると毎秒 1.0 の割合で黄色へ戻る。最濃(1.0)は素早い4打で
#: ほぼ届く程度で、上限 1.5 まで貯められるぶん、手を止めてもしばらくは最濃の
#: まま — 「4打ちょうどで最濃」ではない(減衰があるので届かない)。
ROLL_COLOR_PER_HIT = 0.25
ROLL_COLOR_DECAY = 1.0          # 毎秒
ROLL_COLOR_CAP = 1.5


class _Span:
    """連打・風船・くす玉の1区間ぶんの記録。

    数だけでは足りなくなったので入れ物にした。風船は「いつ割れたか」、
    連打は「いまどれだけ赤いか」を持つ必要があり、どちらも叩いた時刻に
    依存する(画面はあとから任意の時刻で引きに来る)。"""

    __slots__ = ("start", "end", "need", "hits", "don_only",
                 "color", "color_t", "begun", "pop_t")

    def __init__(self, start, end, need, don_only):
        self.start = float(start)
        self.end = float(end)
        #: 必要打数。連打は 0(いくらでも叩ける)。
        self.need = int(need)
        #: 風船・くす玉は面でしか叩けない(縁は無反応)。連打は両方。
        self.don_only = bool(don_only)
        self.reset()

    def reset(self):
        self.hits = 0
        #: 連打の赤み。color_t 時点の値で、そこから毎秒 ROLL_COLOR_DECAY 戻る。
        self.color = 0.0
        self.color_t = self.start
        #: 一度でも叩いたか。風船は叩くまで画面に出さない。
        self.begun = False
        #: 叩ききって割れた譜面時刻。割れていなければ None。
        self.pop_t = None

    def _color_raw(self, now):
        """now 時点の貯め値。**1.0 で頭打ちにしない** — 上限は 1.5 で、
        1.0 を超えたぶんが「手を止めてもしばらく最濃のまま」の猶予になる。
        ここで 1.0 に丸めると猶予が貯まらない(一度そう書いて取りこぼした)。"""
        v = self.color - ROLL_COLOR_DECAY * max(0.0, float(now) - self.color_t)
        return max(0.0, v)

    def color_at(self, now):
        """now 時点の赤み 0..1。画面が色を混ぜるのに使う表示用の値。"""
        return min(1.0, self._color_raw(now))

    def hit(self, now):
        """1打入れる。入らなかった(叩ききっている)なら False。"""
        if self.need > 0 and self.hits >= self.need:
            return False
        self.hits += 1
        self.begun = True
        self.color = min(ROLL_COLOR_CAP, self._color_raw(now) + ROLL_COLOR_PER_HIT)
        self.color_t = float(now)
        if self.need > 0 and self.hits >= self.need and self.pop_t is None:
            self.pop_t = float(now)
        return True


class PlayState:
    """1つの譜面を叩いている最中の記録。

    使い方は2つだけ。

      advance(now)      … 時間を進める。通り過ぎた音符を不可にする
      press(now, kind)  … 叩いた。判定して結果を返す

    どちらも譜面時刻(秒)で呼ぶ。判定の結果は音符の番号ごとに覚えるので、
    画面側は judge_of(i) で引ける。
    """

    #: 判定窓(秒、片側)。すべて鬼の値。
    GOOD_SEC = 0.0250
    OK_SEC = 0.0750
    BAD_SEC = 0.1083

    #: 可のときのスコア。良の半分を10点単位に切り捨てる(本家と同じ刻み)。
    OK_SCORE_RATIO = 0.5
    SCORE_UNIT = 10

    #: 魂ゲージの重み。良を1として、可は半分、不可は2つぶん減らす。
    #: 本家の減り方は難易度と譜面で変わるが、そこまでは寄せていない。
    GAUGE_GOOD = 1.0
    GAUGE_OK = 0.5
    GAUGE_BAD = -2.0

    def __init__(self, preview_data: dict, roll_hit_speed: float = 45.0):
        data = preview_data or {}
        notes = data.get("notes") or []
        self._times = [float(n[0]) for n in notes]
        self._chars = [str(n[1]) for n in notes]
        self.note_count = len(self._times)

        self.scoring = compute_scoring(data)
        self._per_note = int(self.scoring.get("per_note") or 0)
        self._ok_score = (int(self._per_note * self.OK_SCORE_RATIO)
                          // self.SCORE_UNIT * self.SCORE_UNIT)

        # 連打・風船・くす玉。
        self._spans = []
        for group in ("rolls", "balloons", "kusudamas"):
            is_roll = (group == "rolls")
            for span in data.get(group) or []:
                self._spans.append(_Span(
                    span[0], span[1],
                    0 if is_roll else int(span[-1]),
                    don_only=not is_roll))
        self._spans.sort(key=lambda s: s.start)
        self._span_starts = [s.start for s in self._spans]

        # 魂ゲージは「走っている値」。上限は入魂に要る打数で、そこで頭打ちに
        # する(頭打ちにしないと、満タンから不可を出しても目盛が減らない)。
        self._gauge_cap = float(GaugeModel(data).notes_to_max)

        self.reset()

    # ------------------------------------------------------------------
    def reset(self):
        """頭から叩き直す。"""
        self._judge = [None] * self.note_count
        self._hit_at = [None] * self.note_count
        #: ここより前の音符は判定が済んでいる(不可の見送りを含む)。
        self._cursor = 0
        self.combo = 0
        self.max_combo = 0
        self.score = 0
        self.good = 0
        self.ok = 0
        self.bad = 0
        self.roll_hits = 0
        #: 直近の判定 (譜面時刻, 判定, 音符の文字, コンボ番号)。無ければ None。
        self.last_judge = None
        #: 直近の入力 (譜面時刻, 種類)。太鼓を光らせるのに使う。
        self.last_press = None
        self._presses = []
        #: 魂ゲージの走っている値(良を1とした「叩いた数」)。式で出し直さず
        #: 打つたびに足し引きする — **毎打ごとに0で止める**ので、一度崩れた
        #: あとでも叩けば戻る。式のままだと引き算が残って戻らなかった。
        self._gauge = 0.0
        for s in self._spans:
            s.reset()

    # ------------------------------------------------------------------
    def advance(self, now: float):
        """時間を進める。不可の窓を出た未判定の音符を不可にする。

        press() より先に呼ぶこと。呼ばないと、通り過ぎた音符がいつまでも
        叩ける状態で残る。"""
        limit = float(now) - self.BAD_SEC
        i = self._cursor
        while i < self.note_count and self._times[i] < limit:
            if self._judge[i] is None:
                self._miss(i, self._times[i] + self.BAD_SEC)
            i += 1
        self._cursor = i

    def _miss(self, i, when):
        """不可の窓を出た音符を見送りにする。

        **last_judge は動かさない。** 本家は判定文字を「叩いたとき」だけ
        出していて、見送りでは「不可」の文字も火花も出ない。ここを動かすと
        画面がそれを拾って出してしまう(実際に出ていた)。"""
        self._judge[i] = JUDGE_BAD
        self.bad += 1
        self.combo = 0
        self._add_gauge(self.GAUGE_BAD)

    def _add_gauge(self, delta):
        """魂ゲージを動かす。0 と入魂で止める。

        **足し引きのたびに止めるのが肝。** まとめて式で出すと、大きく崩れた
        ぶんの引き算が残り、そのあとどれだけ叩いても戻らなかった。"""
        self._gauge = max(0.0, min(self._gauge_cap, self._gauge + float(delta)))

    # ------------------------------------------------------------------
    def press(self, now: float, kind: str, side: int = 0):
        """叩いた。結果を返す。

        戻り値は次のどれか。
          ("note", 音符の番号, 判定)   … 音符を叩いた
          ("roll", 区間の番号)         … 連打・風船を叩いた
          ("empty", None)              … 何も無いところを叩いた(空打ち)

        空打ちでも打音は鳴らす(本家と同じ)ので、呼び出し側は戻り値に
        関わらず音を出してよい。

        side は叩いた側(0=左 / 1=右)。判定には使わず、太鼓のどちらの半分を
        光らせるかにだけ使う。
        """
        now = float(now)
        self.last_press = (now, kind)
        self._presses.append((now, kind, int(side)))
        if len(self._presses) > 64:
            del self._presses[:-64]

        # --- 連打・風船が先。区間の中では音符の判定より優先する ---
        j = self._span_at(now)
        if j is not None:
            span = self._spans[j]
            # 風船・くす玉は面でしか叩けない。縁は区間の中でも素通りさせる
            # (打音だけ鳴って何も起きない)。連打は面でも縁でも1打。
            if span.don_only and kind != KIND_DON:
                return ("empty", None)
            if span.hit(now):
                self.roll_hits += 1
                self.score += ROLL_HIT_SCORE
            return ("roll", j)

        # --- いちばん近い「その面の」未判定の音符 ---
        # **面と縁を取り違えても音符は消費しない。** 本家は面用と縁用の
        # ねらいを別々に持っていて、ドンの音符しか窓に無いところでカツを
        # 叩いても、音符には一切触れず空打ち音だけが鳴る。ここを「窓の中
        # なら不可として食う」にしていたのは根拠のない思い込みで、ドン連打の
        # 途中でカツが暴発すると即不可になる、という別物の挙動だった。
        i = self._nearest(now, kind == KIND_DON)
        if i is None:
            return ("empty", None)
        dt = abs(now - self._times[i])
        c = self._chars[i]

        if dt <= self.GOOD_SEC:
            res, gain = JUDGE_GOOD, self._per_note
            self.good += 1
        elif dt <= self.OK_SEC:
            res, gain = JUDGE_OK, self._ok_score
            self.ok += 1
        else:
            res, gain = JUDGE_BAD, 0
            self.bad += 1
        self._judge[i] = res
        self._hit_at[i] = now
        if res == JUDGE_BAD:
            self.combo = 0
            self._add_gauge(self.GAUGE_BAD)
        else:
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
            self.score += gain
            self._add_gauge(self.GAUGE_GOOD if res == JUDGE_GOOD
                            else self.GAUGE_OK)
        self.last_judge = (now, res, c, self.combo)
        return ("note", i, res)

    # ------------------------------------------------------------------
    def _nearest(self, now, want_don):
        """不可の窓に入っている未判定の音符のうち、いちばん近いもの。

        **叩いた面と同じ種類の音符しか返さない。** 面用と縁用でねらいを
        別々に持つ本家と同じ見え方になる — 種類が違う音符は、窓の中に
        あっても無かったことにして素通りさせる。"""
        lo = bisect.bisect_left(self._times, now - self.BAD_SEC)
        hi = bisect.bisect_right(self._times, now + self.BAD_SEC)
        best, best_dt = None, None
        for i in range(lo, hi):
            if self._judge[i] is not None:
                continue
            if (self._chars[i] in DON_CHARS) != bool(want_don):
                continue
            dt = abs(now - self._times[i])
            if best_dt is None or dt < best_dt:
                best, best_dt = i, dt
        return best

    def _span_at(self, now):
        """now が入っている連打・風船の区間。無ければ None。"""
        if not self._spans:
            return None
        i = bisect.bisect_right(self._span_starts, now) - 1
        if i < 0:
            return None
        return i if now <= self._spans[i].end else None

    # ------------------------------------------------------------------
    def judge_of(self, i):
        """音符 i の判定。まだなら None。"""
        return self._judge[i] if 0 <= i < self.note_count else None

    def hit_time(self, i):
        """音符 i を叩いた譜面時刻。叩いていなければ None(見送りの不可を含む)。"""
        return self._hit_at[i] if 0 <= i < self.note_count else None

    def span_hits(self, j):
        """区間 j をいま何回叩いたか。"""
        return self._spans[j].hits if 0 <= j < len(self._spans) else 0

    #: 開始時刻を突き合わせるときの許容。譜面データは同じ計算から来るので
    #: 本来ぴたり一致するが、浮動小数の往復で末尾がずれても拾えるようにする。
    SPAN_MATCH_SEC = 1e-6

    def span_at(self, now):
        """now が入っている区間。無ければ None。画面から引くための公開口。"""
        j = self._span_at(now)
        return None if j is None else self._spans[j]

    def span_at_start(self, start: float):
        """開始時刻 start の区間。無ければ None。

        レーンの描画は区間を「開始時刻」で持っているので、こちらの通し番号
        ではなく時刻で引けるようにする。"""
        if not self._spans:
            return None
        i = bisect.bisect_left(self._span_starts, start - self.SPAN_MATCH_SEC)
        if i >= len(self._spans):
            return None
        if abs(self._span_starts[i] - start) > self.SPAN_MATCH_SEC:
            return None
        return self._spans[i]

    def live_span_count(self, now):
        """now が区間の中なら、叩いた数(連打)/残り(風船)。外なら None。

        風船は叩ききった時点で消える(残り 0 を出したままにしない)。"""
        sp = self.span_at(now)
        if sp is None:
            return None
        if sp.need <= 0:
            return sp.hits
        if sp.pop_t is not None:
            return None
        return sp.need - sp.hits

    def recent_presses(self, now, window):
        """window 秒以内の入力 [(経過秒, 種類, 側), ...]、新しい順。太鼓の光用。"""
        out = []
        for t, kind, side in reversed(self._presses):
            el = now - t
            if el < 0.0:
                continue
            if el >= window:
                break
            out.append((el, kind, side))
        return out

    def gauge_hits(self):
        """魂ゲージへ渡す「叩いた数」。良を1、可を半分、不可を引き算で。

        数え直しではなく、打つたびに動かして 0 と入魂で止めた値をそのまま
        返す(_add_gauge を参照)。"""
        return self._gauge

    def finished(self, now):
        """最後の音符の不可窓を過ぎたか。リザルトを出す合図に使う。"""
        if not self._times:
            return True
        return float(now) > self._times[-1] + self.BAD_SEC
