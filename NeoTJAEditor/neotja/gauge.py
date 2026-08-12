"""魂ゲージの伸び方(おに譜面基準)。

出典: 太鼓の達人 譜面とか Wiki 「魂ゲージの伸び率」
https://wikiwiki.jp/taiko-fumen/システム/魂ゲージの伸び率

仕組み:
  * ゲージは内部的に 10000 点満点。200 点ごとに1本増え、**8000 点でクリア
    (ノルマ)**、10000 点で入魂(MAX)。
  * 良1個の点数は「ランク」で決まる。ランクは譜面の総コンボ数で決まり、
    Wiki の表では 8〜53 の範囲。魂MAXに必要な良の数は ceil(10000/ランク)。
  * つまり **良1個 = ランク点**。ランクが高い(=音符が少ない)ほど1個が重い。

このプレビューは全ノーツを良で叩く前提なので、可・不可の減点は使わない。
連打・風船がゲージに効くかは Wiki に記載が無いため、音符のみで数える
(現行の実装と同じ)。

ランクの決め方について:
  Wiki の表は「実在する曲を観測した結果」なので、総コンボ数の範囲に**空きが
  ある**(例: 417〜436 はどのランクにも載っていない。ランク31・41・46・49〜52
  は総コンボ数の欄が空)。そこで、範囲に入ればその通り、入らなければ最も近い
  範囲のランクを採る。表の外側は端のランクで頭打ちにする。ずれても隣の
  ランクなので、1音符あたり 10000 分の 1 点しか変わらない。
"""

# (ランク, 総コンボ数の下限, 総コンボ数の上限)
# Wiki「おにコースのクリア基準」の表そのまま。総コンボ数が空欄のランクは
# 載っていないので入れていない(その範囲は隣のランクに寄せる)。
_RANK_RANGES = (
    (8, 1582, 1608),
    (9, 1396, 1487),
    (10, 1257, 1365),
    (11, 1141, 1244),
    (12, 1052, 1138),
    (13, 973, 1048),
    (14, 905, 970),
    (15, 847, 904),
    (16, 796, 846),
    (17, 750, 794),
    (18, 709, 749),
    (19, 673, 708),
    (20, 641, 672),
    (21, 610, 639),
    (22, 583, 609),
    (23, 559, 582),
    (24, 536, 558),
    (25, 516, 535),
    (26, 497, 512),
    (27, 477, 491),
    (28, 465, 476),
    (29, 448, 454),
    (30, 437, 437),
    (32, 408, 416),
    (33, 393, 407),
    (34, 381, 390),
    (35, 375, 378),
    (36, 364, 364),
    (37, 356, 356),
    (38, 341, 348),
    (39, 334, 337),
    (40, 326, 329),
    (42, 316, 316),
    (43, 311, 311),
    (44, 298, 303),
    (45, 290, 290),
    (47, 280, 280),
    (48, 272, 274),
    (53, 246, 246),
)

GAUGE_MAX = 10000       # 入魂
GAUGE_CLEAR = 8000      # ノルマ(クリア)
GAUGE_STEP = 200        # 1本ぶんの点。10000/200 = 50本
CLEAR_RATIO = GAUGE_CLEAR / GAUGE_MAX      # 0.8

# 表の外側(音符が 246 未満 / 1608 超)を埋めるための外挿。
# 表を見ると「ランク × 総コンボ数」がほぼ一定なので、その平均を定数にする。
# (= 入魂が総コンボ数の 7割半ばで来る、という関係。表の各行の中央値から算出)
_RANK_TIMES_NOTES = sum(r * (lo + hi) / 2.0 for r, lo, hi in _RANK_RANGES) / len(_RANK_RANGES)
_TABLE_MIN_NOTES = min(lo for _r, lo, _hi in _RANK_RANGES)
_TABLE_MAX_NOTES = max(hi for _r, _lo, hi in _RANK_RANGES)


def rank_for_notes(note_count: int) -> int:
    """総コンボ数からランク(=良1個の点数)を返す。

    表に載っている範囲はその通り。表の中の空き(例: 417〜436)は最も近い
    範囲のランク。表の外(246未満 / 1608超)は「ランク×総コンボ数がほぼ一定」
    という表の性質から外挿する — 端のランクで頭打ちにすると、音符の少ない
    譜面で入魂に永遠に届かなくなるため(例: 100音符だとランク53では入魂に
    189個必要で、譜面を全部叩いても届かない)。"""
    n = int(note_count or 0)
    if n <= 0:
        return 1
    if n < _TABLE_MIN_NOTES or n > _TABLE_MAX_NOTES:
        return max(1, int(round(_RANK_TIMES_NOTES / n)))
    best, best_dist = None, None
    for rank, lo, hi in _RANK_RANGES:
        if lo <= n <= hi:
            return rank
        dist = lo - n if n < lo else n - hi
        if best_dist is None or dist < best_dist:
            best, best_dist = rank, dist
    return best


def notes_to_max(rank: int) -> int:
    """入魂までに必要な良の数。Wiki の表と同じ ceil(10000/ランク)。"""
    r = max(1, int(rank))
    return -(-GAUGE_MAX // r)      # 切り上げ


def notes_to_clear(rank: int) -> int:
    """クリア(8000点)までに必要な良の数。"""
    r = max(1, int(rank))
    return -(-GAUGE_CLEAR // r)


class GaugeModel:
    """譜面1つぶんの魂ゲージ。全良前提なので「叩いた数」だけで決まる。"""

    def __init__(self, preview_data: dict):
        notes = (preview_data or {}).get("notes") or []
        self.note_count = len(notes)
        self.rank = rank_for_notes(self.note_count)
        self.notes_to_max = notes_to_max(self.rank)
        self.notes_to_clear = notes_to_clear(self.rank)

    def points(self, hit_notes: int) -> int:
        """叩いた数から現在のゲージ点(0..10000)。"""
        return min(GAUGE_MAX, max(0, int(hit_notes)) * self.rank)

    def ratio(self, hit_notes: int) -> float:
        """ゲージの満ち具合 0.0..1.0。"""
        return self.points(hit_notes) / float(GAUGE_MAX)

    def cleared(self, hit_notes: int) -> bool:
        return self.points(hit_notes) >= GAUGE_CLEAR
