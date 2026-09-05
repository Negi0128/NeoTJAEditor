"""スコア(配点)の算出。

このプレビューは「全部良で叩いた」前提の再生なので、スコアは譜面が決まれば
一意に決まる。配点は次の考え方で求める:

    ノーツ数 × 一ノーツ当たりの配点 + 連打打数 × 100 ≒ 1,000,000

配点(一ノーツ当たり)を決めるときは、連打・風船・くす玉の打数を **秒速 18 打**
で数える。ここは「満点がいくつになるか」の見積もりなので、環境設定の
roll_speed に左右されてはいけない(人によって配点そのものが変わってしまう)。

**実際に叩いて入る点は別。** 打音も太鼓の光も環境設定の連打秒速で刻んで
いるので、加算もそれに合わせる — 合わせないと「叩いた回数」と「加算された
回数」が食い違う。そのぶん、連打で入る合計は設定で上下する。
風船・くす玉は割れた時点で打音が止まるので、加算もそこまでに収める
(tja_analyzer.balloon_pop_spans と同じ切り詰め)。

一ノーツ当たりの配点は本家にならって 10 点単位に丸める。丸めるぶん合計は
1,000,000 ちょうどにはならないので、実際の合計値も併せて返す。
"""

import bisect
import math

TARGET_SCORE = 1_000_000
# 連打の打数を数えるときの秒間打数。表示用の roll_speed とは別物(上の説明参照)。
ROLL_HITS_PER_SEC = 18.0
# 連打1打あたりの点。
ROLL_HIT_SCORE = 100
# 一ノーツ当たりの配点の刻み(本家と同じ10点単位)。
SCORE_UNIT = 10


def _span_hits(start, end):
    """連打区間を秒速18打で叩いたときの打数。"""
    return int(max(0.0, float(end) - float(start)) * ROLL_HITS_PER_SEC)


def compute_scoring(preview_data: dict, target: int = TARGET_SCORE) -> dict:
    """譜面から配点を決める。

    返り値:
      note_count      音符数(連打・風船を除く)
      roll_hits       連打の打数(秒速18打)
      balloon_hits    風船・くす玉の打数(必要打数で頭打ち)
      bonus           連打系の合計点
      per_note        一ノーツ当たりの配点(10点単位)
      total           実際の満点(端数があるので target ちょうどとは限らない)
    """
    data = preview_data or {}
    note_count = len(data.get("notes") or [])

    roll_hits = sum(_span_hits(s[0], s[1]) for s in (data.get("rolls") or []))
    balloon_hits = 0
    for group in ("balloons", "kusudamas"):
        for span in data.get(group) or []:
            # span = (start, end, bpm, scroll, 必要打数)
            need = int(span[4]) if len(span) > 4 else 0
            balloon_hits += min(need, _span_hits(span[0], span[1])) if need > 0 else 0

    bonus = (roll_hits + balloon_hits) * ROLL_HIT_SCORE

    # 譜面に書いてあればそれが正解。自動計算は「書いていないとき」の代わり。
    per_note = _score_init_per_note(data)
    from_tja = per_note is not None
    if not from_tja:
        if note_count > 0:
            raw = max(0.0, (target - bonus) / note_count)
            # **切り上げる。** 全部良で叩いたときの満点が target を下回らない、
            # いちばん小さい 10 点単位の値。切り捨て/四捨五入だと満点が
            # 100万点に届かない譜面が出る(はやさいたま2000 の むずかしい で
            # 実際にそうなった)。
            per_note = int(math.ceil(raw / SCORE_UNIT)) * SCORE_UNIT
        else:
            per_note = 0

    return {
        "note_count": note_count,
        "roll_hits": roll_hits,
        "balloon_hits": balloon_hits,
        "bonus": bonus,
        "per_note": per_note,
        "from_tja": from_tja,
        "total": per_note * note_count + bonus,
    }


def _score_init_per_note(data):
    """譜面に書いてある真打の基礎点。無ければ None。

    TJA の SCOREINIT は「旧配点の初期値, 真打の基礎点」の2つ組で、2つめが
    1打あたりの点そのもの。譜面作者が本家の値を写していることが多く、
    こちらの推定より確実なので、あればそれを使う。

    1つしか書いていない場合は SCOREMODE:2(真打)のときだけ採る。旧配点
    (0/1)の1つめは「コンボで増えていく配点の初期値」で意味が違うため。
    """
    init = data.get("score_init") or ()
    mode = data.get("score_mode")
    value = None
    if len(init) >= 2:
        value = init[1]
    elif len(init) == 1 and mode == 2:
        value = init[0]
    if value is None or value <= 0:
        return None
    return int(value)


class ScoreTimeline:
    """時刻からその瞬間のスコアを引ける表。

    音符は「判定線を通過した時点」で配点ぶん、連打は秒速18打で1打ずつ加算
    する。描画のたびに引かれるので、累積和を先に作って二分探索するだけに
    してある(数万打あっても1フレームぶんの負荷にならない)。
    """

    def __init__(self, preview_data: dict, scoring: dict = None):
        self.scoring = scoring or compute_scoring(preview_data)
        data = preview_data or {}
        per_note = self.scoring["per_note"]

        events = [(float(n[0]), per_note) for n in (data.get("notes") or [])]
        # **叩いた回数ぶん入れる。** 打音も太鼓の光も環境設定の連打秒速
        # (roll_speed)で刻んでいるので、加算もそれに合わせる。合わせないと
        # 「叩いた回数」と「加算された回数」が食い違う(実測で 40打/秒 鳴って
        # いるのに加算は 18回/秒 だった)。
        #
        # 秒速18打(ROLL_HITS_PER_SEC)は**配点を決めるときだけ**の数字。
        # 一ノーツ当たりの配点を求める compute_scoring では引き続きそちらを
        # 使う — あれは「満点がいくつになるか」の見積もりで、人の設定で
        # 配点そのものが変わってはいけないため。そのぶん、実際に叩いて出る
        # 合計は連打秒速の設定で上下する(利用者の選択)。
        speed = max(1.0, float(data.get("roll_hit_speed") or 45))
        for span in (data.get("rolls") or []):
            dur = max(0.0, float(span[1]) - float(span[0]))
            events += self._tick_events(span[0], span[1], int(dur * speed))
        # 風船・くす玉は「叩ききって割れた時点」で打音が止まる(tja_analyzer の
        # balloon_pop_spans と同じ切り詰め)。加算だけを区間の終わりまで薄く
        # 引き伸ばすと、音が止まったあとも点が入り続けて見た目と食い違うので、
        # 加算も割れる時刻までに収める。
        for group in ("balloons", "kusudamas"):
            for span in data.get(group) or []:
                need = int(span[4]) if len(span) > 4 else 0
                start, end = float(span[0]), float(span[1])
                hits = int(max(0.0, end - start) * speed)
                if need > 0:
                    hits = min(need, hits)
                    end = min(end, start + hits / speed)
                events += self._tick_events(start, end, hits)

        events.sort(key=lambda e: e[0])
        self._times = [e[0] for e in events]
        self._cum = []
        acc = 0
        for _t, v in events:
            acc += v
            self._cum.append(acc)
        self.max_score = acc

    @staticmethod
    def _tick_events(start, end, hits):
        """連打を等間隔の1打ずつに割る(打音のスケジュールと同じ割り方)。"""
        if hits <= 0:
            return []
        start, end = float(start), float(end)
        step = (end - start) / hits
        return [(start + step * i, ROLL_HIT_SCORE) for i in range(hits)]

    def events_in(self, t0: float, t1: float, limit: int = 40):
        """(t0, t1] に入った加算を、**古い順**に [(時刻, 点), ...] で返す。

        加算表示は同時に何枚も出る。ふつうの密度の譜面なら音符が 0.1 秒
        おきに来るし、連打を叩いている間はもっと詰まるので、「直近の1件」
        しか出さないと出た端から消えて点滅して見える。重なるぶんは重ねる。

        limit は保険。極端に詰まった連打で何十枚も描くことになっても、
        古いほう(=いちばん薄い)から捨てる。"""
        if not self._times:
            return []
        lo = bisect.bisect_right(self._times, t0)
        hi = bisect.bisect_right(self._times, t1)
        if hi - lo > limit:
            lo = hi - limit
        out = []
        for i in range(lo, hi):
            out.append((self._times[i],
                        self._cum[i] - (self._cum[i - 1] if i else 0)))
        return out

    def last_event(self, seconds: float):
        """その時刻までに入った直近の加算 (加算した時刻, 点)。まだ何も
        入っていなければ None。加算分をスコアの上へ浮かべる表示に使う。"""
        if not self._times:
            return None
        i = bisect.bisect_right(self._times, float(seconds))
        if i <= 0:
            return None
        gain = self._cum[i - 1] - (self._cum[i - 2] if i >= 2 else 0)
        return self._times[i - 1], gain

    def at(self, seconds: float) -> int:
        """その譜面時刻までに入っているスコア。"""
        if not self._times:
            return 0
        i = bisect.bisect_right(self._times, float(seconds))
        return self._cum[i - 1] if i > 0 else 0
