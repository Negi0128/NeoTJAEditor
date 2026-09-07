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
from neotja.score import ROLL_HIT_SCORE, compute_scoring

#: 面(ドン)の音符と縁(カツ)の音符。大音符も同じ扱いにする。
DON_CHARS = frozenset("13")
KA_CHARS = frozenset("24")

#: 入力の種類。定義は constants(譜面プレビュー側も使うため)。

#: 判定の名前。画面の絵(Judge.png)の段の並びと同じ順。
JUDGE_GOOD = "good"
JUDGE_OK = "ok"
JUDGE_BAD = "bad"


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

        # 連打・風船・くす玉。叩いた回数を数えるだけなので、区間だけ持つ。
        self._spans = []
        for group in ("rolls", "balloons", "kusudamas"):
            for span in data.get(group) or []:
                need = int(span[-1]) if group != "rolls" else 0
                self._spans.append([float(span[0]), float(span[1]), need, 0])
        self._spans.sort(key=lambda s: s[0])
        self._span_starts = [s[0] for s in self._spans]

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
        for s in self._spans:
            s[3] = 0

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
        self._judge[i] = JUDGE_BAD
        self.bad += 1
        self.combo = 0
        self.last_judge = (when, JUDGE_BAD, self._chars[i], 0)

    # ------------------------------------------------------------------
    def press(self, now: float, kind: str):
        """叩いた。結果を返す。

        戻り値は次のどれか。
          ("note", 音符の番号, 判定)   … 音符を叩いた
          ("roll", 区間の番号)         … 連打・風船を叩いた
          ("empty", None)              … 何も無いところを叩いた(空打ち)

        空打ちでも打音は鳴らす(本家と同じ)ので、呼び出し側は戻り値に
        関わらず音を出してよい。
        """
        now = float(now)
        self.last_press = (now, kind)
        self._presses.append((now, kind))
        if len(self._presses) > 64:
            del self._presses[:-64]

        # --- 連打・風船が先。区間の中では音符の判定より優先する ---
        j = self._span_at(now)
        if j is not None:
            span = self._spans[j]
            if span[2] <= 0 or span[3] < span[2]:
                span[3] += 1
                self.roll_hits += 1
                self.score += ROLL_HIT_SCORE
            return ("roll", j)

        # --- いちばん近い未判定の音符 ---
        i = self._nearest(now)
        if i is None:
            return ("empty", None)
        dt = abs(now - self._times[i])
        c = self._chars[i]
        want_don = c in DON_CHARS
        if (kind == KIND_DON) != want_don:
            # 面と縁を取り違えた。窓の中なら不可として食う(本家と同じ)。
            self._judge[i] = JUDGE_BAD
            self._hit_at[i] = now
            self.bad += 1
            self.combo = 0
            self.last_judge = (now, JUDGE_BAD, c, 0)
            return ("note", i, JUDGE_BAD)

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
        else:
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
            self.score += gain
        self.last_judge = (now, res, c, self.combo)
        return ("note", i, res)

    # ------------------------------------------------------------------
    def _nearest(self, now):
        """不可の窓に入っている未判定の音符のうち、いちばん近いもの。"""
        lo = bisect.bisect_left(self._times, now - self.BAD_SEC)
        hi = bisect.bisect_right(self._times, now + self.BAD_SEC)
        best, best_dt = None, None
        for i in range(lo, hi):
            if self._judge[i] is not None:
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
        return i if now <= self._spans[i][1] else None

    # ------------------------------------------------------------------
    def judge_of(self, i):
        """音符 i の判定。まだなら None。"""
        return self._judge[i] if 0 <= i < self.note_count else None

    def hit_time(self, i):
        """音符 i を叩いた譜面時刻。叩いていなければ None(見送りの不可を含む)。"""
        return self._hit_at[i] if 0 <= i < self.note_count else None

    def span_hits(self, j):
        """区間 j をいま何回叩いたか。"""
        return self._spans[j][3] if 0 <= j < len(self._spans) else 0

    def live_span_count(self, now):
        """now が区間の中なら、叩いた数(連打)/残り(風船)。外なら None。"""
        j = self._span_at(now)
        if j is None:
            return None
        start, end, need, hits = self._spans[j]
        return (need - hits) if need > 0 else hits

    def recent_presses(self, now, window):
        """window 秒以内の入力 [(経過秒, 種類), ...]、新しい順。太鼓の光用。"""
        out = []
        for t, kind in reversed(self._presses):
            el = now - t
            if el < 0.0:
                continue
            if el >= window:
                break
            out.append((el, kind))
        return out

    def gauge_hits(self):
        """魂ゲージへ渡す「叩いた数」。良を1、可を半分、不可を引き算で。"""
        v = (self.good * self.GAUGE_GOOD + self.ok * self.GAUGE_OK
             + self.bad * self.GAUGE_BAD)
        return max(0.0, v)

    def finished(self, now):
        """最後の音符の不可窓を過ぎたか。リザルトを出す合図に使う。"""
        if not self._times:
            return True
        return float(now) > self._times[-1] + self.BAD_SEC
