"""スコア(配点)の算出。

このプレビューは「全部良で叩いた」前提の再生なので、スコアは譜面が決まれば
一意に決まる。配点は次の考え方で求める:

    ノーツ数 × 一ノーツ当たりの配点 + 連打打数 × 100 ≒ 1,000,000

連打・風船・くす玉の打数は **秒速 18 打** で数える(環境設定の roll_speed は
見た目の推定用なので、ここでは使わない — 人によって値が違うとスコアまで
変わってしまうため)。風船とくす玉は「18打で叩ける数」と「割るのに必要な
打数」の小さいほうを採る。

打数(=合計点)は上のとおり秒速に依存しないが、**いつ入るか**は打音に合わせる。
風船・くす玉は割れた時点で打音が止まるので、加算もそこまでに収める
(tja_analyzer.balloon_pop_spans と同じ切り詰め)。

一ノーツ当たりの配点は本家にならって 10 点単位に丸める。丸めるぶん合計は
1,000,000 ちょうどにはならないので、実際の合計値も併せて返す。
"""

import bisect

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

    if note_count > 0:
        raw = max(0.0, (target - bonus) / note_count)
        per_note = int(round(raw / SCORE_UNIT)) * SCORE_UNIT
    else:
        per_note = 0

    return {
        "note_count": note_count,
        "roll_hits": roll_hits,
        "balloon_hits": balloon_hits,
        "bonus": bonus,
        "per_note": per_note,
        "total": per_note * note_count + bonus,
    }


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
        for span in (data.get("rolls") or []):
            events += self._tick_events(span[0], span[1], _span_hits(span[0], span[1]))
        # 風船・くす玉は「叩ききって割れた時点」で打音が止まる(tja_analyzer の
        # balloon_pop_spans と同じ切り詰め)。加算だけを区間の終わりまで薄く
        # 引き伸ばすと、音が止まったあとも点が入り続けて見た目と食い違うので、
        # 加算も割れる時刻までに収める。
        # ただし **合計点** は連打秒速(環境設定)に左右させたくないので、打数は
        # 従来どおり秒速18打で数える(上の説明を参照)。ここで変えるのは
        # 「いつ入るか」だけ。
        speed = max(1.0, float(data.get("roll_hit_speed") or 45))
        for group in ("balloons", "kusudamas"):
            for span in data.get(group) or []:
                need = int(span[4]) if len(span) > 4 else 0
                hits = min(need, _span_hits(span[0], span[1])) if need > 0 else 0
                end = float(span[1])
                if need > 0:
                    end = min(end, float(span[0]) + need / speed)
                events += self._tick_events(span[0], end, hits)

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
