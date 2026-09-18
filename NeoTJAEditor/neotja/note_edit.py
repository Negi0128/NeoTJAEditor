"""音符を「小節番号 + スロット番号」で指して書き換える。純ロジック(Qt非依存)。

TJA の音符は連打も風船も例外なく「**ある小節の、あるスロットの1文字**」なので、
グラフィカルな音符入力に必要な操作は結局これ1つに集約できる:

    小節 M のスロット k (分割数 G のとき) に文字 c を書く

画面側はカーソルを「時刻」ではなく (小節, スロット, 分割数) で持てばよく、
時刻との相互変換は小節の開始/終了時刻からの比例計算で済む。

小節の文字範囲の求め方は measure_edit.measure_span と同じ「カンマまで」の
考え方。小節テキストの分解と再構築は measure_math の parse_measure_lines /
convert_notes / render_converted をそのまま使う(命令行を割合位置へ戻す、
コメント・カンマ・インデントを保つ、といった面倒はすべて実装済み)。

書き換えは既存の小節編集(measure_edit)と同じ
    (start, end, 置換文字列, 置換後カーソル位置)
という契約で返す。呼び出し側は QTextCursor で1回置換するだけでよく、Undo も
1操作にまとまる。
"""

import math

from neotja.measure_math import (convert_notes, min_len, parse_measure_lines,
                                 render_converted)

# 音符として置ける文字。0 は「消す」。
NOTE_CHARS = "0123456789"

# 分割数の上限。これを超える再分割は要求されても行わない(テキストが巨大に
# なるうえ、そこまで細かい位置は別の書き方をしたほうがよいため)。
MAX_DIVISION = 256


def _line_starts(text):
    """各行の開始オフセット。"""
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def course_body_span(text, course_line_range):
    """course_line_range() が返す (#START行, #END行) を文字オフセットへ。

    戻り値は (body_start, body_end) で、body_start は #START 行の次の行頭、
    body_end は #END 行の行頭。範囲が取れなければ None。"""
    if not course_line_range:
        return None
    start_line, end_line = course_line_range
    starts = _line_starts(text)
    n = len(text)
    # 行番号は1始まり。#START の次の行頭 = starts[start_line]。
    if start_line < 0 or start_line >= len(starts):
        return None
    body_start = starts[start_line]
    body_end = starts[end_line - 1] if 0 <= end_line - 1 < len(starts) else n
    if body_end < body_start:
        return None
    return (body_start, body_end)


def measure_spans(text, body):
    """本文 (body_start, body_end) 内の全小節の文字範囲を順に返す。

    measure_edit.measure_span と同じく「カンマまで + 直後の改行1つ」を
    1小節とする。最後のカンマより後ろに文字が残っていれば、それも
    (カンマの無い)小節として1つ返す — 書きかけの末尾小節に音符を置ける
    ようにするため。"""
    if body is None:
        return []
    bstart, bend = body
    spans = []
    a = bstart
    i = bstart
    while i < bend:
        if text[i] == ",":
            b = i + 1
            if b < bend and text[b] == "\n":
                b += 1
            spans.append((a, b))
            a = b
        i += 1
    if a < bend and text[a:bend].strip():
        spans.append((a, bend))
    return spans


def measure_slots(chunk):
    """小節テキストの分割数(数字の総数)。数字が無ければ 0。"""
    parsed = parse_measure_lines(chunk)
    for p in parsed:
        if p["type"] == "measure":
            return len(p["notes"])
    return 0


def slot_char(text, body, m_index, slot, grid):
    """(小節, スロット) に今ある文字。範囲外・空小節なら None。

    分割数 G のスロット k が、その小節の実際の分割数 L のどこに当たるかは
    k * L / G。割り切れない位置(L が G の倍数でない)は「その位置に音符は
    置けない」ではなく「一番近い手前の枠」を見る — 表示のトグル判定用なので
    厳密である必要はない。"""
    spans = measure_spans(text, body)
    if not (0 <= m_index < len(spans)):
        return None
    a, b = spans[m_index]
    parsed = parse_measure_lines(text[a:b])
    notes = ""
    for p in parsed:
        if p["type"] == "measure":
            notes = p["notes"]
            break
    L = len(notes)
    if L == 0 or grid <= 0:
        return None
    idx = (slot * L) // grid
    if not (0 <= idx < L):
        return None
    return notes[idx]


def set_slot(text, body, m_index, slot, grid, char):
    """小節 m_index のスロット slot (分割数 grid) に char を書く。

    戻り値 (start, end, 置換文字列, 置換後カーソル位置)。置けないときは None。

    L(その小節の現在の分割数) が grid の倍数なら**1文字だけ**差し替える。
    行の折り返しもコメントも命令行も一切触らないので、テキストの差分が
    最小で済む(最頻ケース)。倍数でないときだけ、情報を落とさない最小の
    分割数へ小節ごと書き直す。"""
    if char not in NOTE_CHARS or grid <= 0 or m_index < 0:
        return None
    spans = measure_spans(text, body)
    if m_index >= len(spans):
        # 譜面の末尾より先。足りないぶんの空小節を作ってから置く。小節が
        # 1つも無い譜面(新規作成直後)でも、ここを通って打ち始められる。
        return _extend_and_set(text, body, spans, m_index, slot, grid, char)
    a, b = spans[m_index]
    chunk = text[a:b]

    parsed = parse_measure_lines(chunk)
    measure = None
    for p in parsed:
        if p["type"] == "measure":
            measure = p
            break
    if measure is None:
        return None
    notes = measure["notes"]
    L = len(notes)
    if not (0 <= slot < grid):
        return None

    # --- 再分割が要らない場合: 1文字だけ置き換える ---
    if L > 0 and L % grid == 0:
        idx = (slot * L) // grid
        if notes[idx] == char:
            return None          # 変化なし
        pos = _nth_digit_offset(chunk, idx)
        if pos is None:
            return None
        return (a + pos, a + pos + 1, char, a + pos + 1)

    # --- 再分割する場合 ---
    # 情報を落とさずに書ける最小分割数(m)と grid の最小公倍数。
    m = min_len(notes) if L > 0 else grid
    if m <= 0:
        m = grid
    target = m * grid // math.gcd(m, grid)
    if target > MAX_DIVISION:
        return None
    converted = convert_notes(notes, target) if L > 0 else "0" * target
    if len(converted) != target:
        # convert_notes は target が min_len の倍数でないと長さが合わない。
        # 上で LCM を取っているので通常は来ないが、来たら何もしない。
        return None
    idx = (slot * target) // grid
    if not (0 <= idx < target):
        return None
    if converted[idx] == char:
        return None
    # parsed はこの場で作った使い捨てなので、そのまま書き換えてよい。
    measure["notes"] = converted[:idx] + char + converted[idx + 1:]
    # breaks(小節の途中の命令行)の位置は「元の分割数での何文字目か」で
    # 記録されている。notes を target 長へ差し替えた以上、この位置も同じ
    # 座標系へ直さないと、render_converted の割合計算が素通りしてしまい
    # 命令行が元の文字数の位置に取り残される。
    if L > 0 and target != L:
        measure["breaks"] = [(min(target, (bidx * target) // L), line)
                             for bidx, line in measure["breaks"]]
    # render_converted は指定分割へ書き直す。notes は既に target 長なので
    # 中身は変わらず、行の折り返しと命令行の位置だけが組み直される。
    replacement = render_converted(parsed, target, _wrap_for(target))
    # render_converted は末尾に改行を付けない。元の小節が改行で終わって
    # いたら足す — でないと次の小節と1行に繋がってしまう。
    if chunk.endswith("\n") and not replacement.endswith("\n"):
        replacement += "\n"
    return (a, b, replacement, a + len(replacement))


def _extend_and_set(text, body, spans, m_index, slot, grid, char):
    """末尾に空小節を足して、その最後の小節に char を置く。

    足す位置は最後の小節の直後(小節が無ければ本文の先頭)。#END 直前の
    空行やコメントには触らない。消去(0)のために小節を増やすのは無意味
    なので、そのときは何もしない。"""
    if body is None or char == "0":
        return None
    if not (0 <= slot < grid) or grid > MAX_DIVISION:
        return None
    ins = spans[-1][1] if spans else body[0]
    need = m_index - len(spans) + 1
    if need <= 0:
        return None

    wrap = _wrap_for(grid)
    chunks = []
    for i in range(need):
        notes = ["0"] * grid
        if i == need - 1:
            notes[slot] = char
        s = "".join(notes)
        if wrap and grid > wrap:
            lines = [s[j:j + wrap] for j in range(0, grid, wrap)]
        else:
            lines = [s]
        lines[-1] += ","
        chunks.append("\n".join(lines))
    # 直前が改行で終わっていなければ、まず行を改める。
    prefix = "" if (ins == 0 or text[ins - 1] == "\n") else "\n"
    replacement = prefix + "\n".join(chunks) + "\n"
    return (ins, ins, replacement, ins + len(replacement))


def _wrap_for(target):
    """折り返し文字数。16 の倍数なら 16、12 の倍数なら 12、それ以外は無し。
    既存の「ノーツ間隔リサイズ」の選択肢(measure_math.wrap_options)と同じ
    考え方を、いちいち聞かずに済むよう既定値として固定したもの。"""
    if target > 16 and target % 16 == 0:
        return 16
    if target > 12 and target % 12 == 0:
        return 12
    return 0


def _nth_digit_offset(chunk, n):
    """小節テキスト内で n 番目(0始まり)の音符文字のオフセット。

    命令行(#...)と行コメント(//...)の中の数字は数えない。parse_measure_lines
    が notes を作るときと同じ数え方にそろえる必要がある。"""
    count = 0
    pos = 0
    for line in chunk.split("\n"):
        stripped = line.strip()
        is_cmd = stripped.startswith("#") or stripped.startswith("//")
        if not is_cmd:
            code = line.split("//", 1)[0]
            for i, c in enumerate(code):
                if c in "0123456789":
                    if count == n:
                        return pos + i
                    count += 1
        pos += len(line) + 1     # +1 は改行
    return None


def time_to_address(bar_times, t, grid):
    """時刻 t が (小節番号, スロット番号) のどこに当たるか。

    bar_times は build_preview_timeline の "bar_times"(各小節の開始時刻)。
    範囲外なら None。"""
    if not bar_times or grid <= 0:
        return None
    import bisect
    i = bisect.bisect_right(bar_times, t) - 1
    if i < 0:
        return None
    if i >= len(bar_times) - 1:
        # 最終小節は次の小節線が無いので、直前の小節と同じ長さと見なす。
        if len(bar_times) < 2:
            return (i, 0)
        span = bar_times[-1] - bar_times[-2]
    else:
        span = bar_times[i + 1] - bar_times[i]
    if span <= 0:
        return (i, 0)
    frac = (t - bar_times[i]) / span
    slot = int(round(frac * grid))
    if slot >= grid:
        return (i + 1, 0) if i + 1 < len(bar_times) else (i, grid - 1)
    return (i, max(0, slot))


def address_to_time(bar_times, m_index, slot, grid):
    """(小節番号, スロット番号) の時刻。範囲外なら None。

    小節内は等分と見なす。小節の途中に #BPMCHANGE があると実際とわずかに
    ずれるが、これは配置直後の暫定表示にしか使わず、再解析が届けば正しい
    値に置き換わる。"""
    if not bar_times or grid <= 0 or m_index < 0:
        return None
    if m_index >= len(bar_times):
        return None
    if m_index >= len(bar_times) - 1:
        if len(bar_times) < 2:
            return bar_times[m_index]
        span = bar_times[-1] - bar_times[-2]
    else:
        span = bar_times[m_index + 1] - bar_times[m_index]
    return bar_times[m_index] + span * (slot / grid)


# ---------------------------------------------------------------------------
# PeepoDrumKit 式の操作
# ---------------------------------------------------------------------------
# PeepoDrumKit(0auBSQ 版フォーク, src/peepo_drum_kit/chart_editor_timeline.cpp)
# の音符入力をテキストの上で再現する。向こうは音符を「拍の位置 + 長さ」で持つ
# が、こちらは TJA の文字のまま持つので、位置は (小節, その小節の中の割合) で
# 比べる。割合は Fraction にして、分割数が違っても厳密に等しいかを判定する。
#
# 決まりごと(向こうのソースで確かめたもの):
#   ドン/カッのキー … 何も無ければ置く。同じ色の音符があれば消す。違う色なら
#                    その色に変える。長い音符の頭なら長い音符ごと消す。
#                    長い音符の途中(頭以外)では何もしない。カーソルは進めない。
#   Shift + キー    … 範囲選択の中の空いているグリッドをすべて埋める。
#   連打/風船のキー … 押したところから離したところまでの長い音符を置く。
#                    重なっていた音符は消す。風船の打数は「長さ ÷ 1グリッド」。
#   W / Q           … ドン↔カッ / 大小 を入れ替える。

from fractions import Fraction

LONG_HEADS = "5679"
BALLOON_HEADS = "79"
_INF_KEY = (1 << 30, Fraction(0))
_SMALL = {"1": "1", "3": "1", "2": "2", "4": "2"}
_FLIP = {"1": "2", "2": "1", "3": "4", "4": "3"}
_SIZE = {"1": "3", "3": "1", "2": "4", "4": "2", "5": "6", "6": "5", "7": "9", "9": "7"}


def _key(m, slot, grid):
    return (int(m), Fraction(int(slot), int(grid)))


def _measure_notes(text, span):
    a, b = span
    for p in parse_measure_lines(text[a:b]):
        if p["type"] == "measure":
            return p["notes"]
    return ""


def chart_items(text, body):
    """本文の音符を時間順に返す。0 と終端 8 は項目にしない(8 は長い音符に入る)。

    各項目は dict:
      char  … 音符の文字
      head  … (小節, 位置, その小節の分割数)
      tail  … 長い音符の終端 8 の住所。終端が無ければ None
      k0/k1 … 始まり/終わりの位置キー。普通の音符は k0 == k1
      ord   … 風船・くす玉なら BALLOON: の何番目の値か(0始まり)
    """
    items = []
    open_long = None
    ordinal = 0
    for m, span in enumerate(measure_spans(text, body)):
        notes = _measure_notes(text, span)
        L = len(notes)
        for i, c in enumerate(notes):
            if c == "0":
                continue
            k = _key(m, i, L)
            if c == "8":
                if open_long is not None:
                    open_long["tail"] = (m, i, L)
                    open_long["k1"] = k
                    open_long = None
                continue
            if c in LONG_HEADS:
                it = {"char": c, "head": (m, i, L), "tail": None,
                      "k0": k, "k1": _INF_KEY, "ord": None}
                if c in BALLOON_HEADS:
                    it["ord"] = ordinal
                    ordinal += 1
                items.append(it)
                open_long = it
            else:
                items.append({"char": c, "head": (m, i, L), "tail": None,
                              "k0": k, "k1": k, "ord": None})
    return items


def _is_long(it):
    return it["char"] in LONG_HEADS


def _apply_writes(text, body, writes):
    """[(小節, スロット, 分割数, 文字), ...] を順に当てる。本文の終わりは置換の
    たびに動くので追いかける。1つも変わらなければ元の text を返す。"""
    bstart, bend = body
    for m, slot, grid, char in writes:
        r = set_slot(text, (bstart, bend), m, slot, grid, char)
        if r is None:
            continue
        a, b, rep, _cur = r
        text = text[:a] + rep + text[b:]
        bend += len(rep) - (b - a)
    return text


def _removal_writes(it):
    out = [(it["head"][0], it["head"][1], it["head"][2], "0")]
    if it["tail"] is not None:
        out.append((it["tail"][0], it["tail"][1], it["tail"][2], "0"))
    return out


# --- BALLOON: ---------------------------------------------------------------

def _balloon_line_index(lines, start_line):
    """コースの BALLOON: 行の番号(0始まり)。無ければ None。

    探すのは #START の直前から、ひとつ前のコースの #END(無ければファイル先頭)
    まで。1コースしか無いファイルでは COURSE: より上に書かれた BALLOON: も
    そのコースのもの。複数あれば #START に近いほうを採る。"""
    start_idx = start_line - 1
    lo = 0
    for i in range(start_idx - 1, -1, -1):
        if lines[i].split("//")[0].strip().startswith("#END"):
            lo = i + 1
            break
    hit = None
    for i in range(lo, start_idx):
        if lines[i].strip().startswith("BALLOON:"):
            hit = i
    return hit


def balloon_values(text, course_range):
    """コースの BALLOON: の値を文字列のリストで返す(無ければ空)。"""
    lines = text.split("\n")
    idx = _balloon_line_index(lines, course_range[0])
    if idx is None:
        return []
    body = lines[idx].split("//", 1)[0].strip()[len("BALLOON:"):]
    return [v.strip() for v in body.split(",") if v.strip()]


def set_balloon_values(text, course_range, values):
    """コースの BALLOON: を書き換える。行が無ければ #START の直前に足す。

    #START より上の行しか触らないので、本文の文字位置は変わらない
    (行が1つ増えると #START の行番号はずれる。呼ぶのは最後にすること)。"""
    lines = text.split("\n")
    idx = _balloon_line_index(lines, course_range[0])
    new_line = "BALLOON:" + ",".join(str(v) for v in values)
    if idx is None:
        if not values:
            return text
        lines.insert(course_range[0] - 1, new_line)
    else:
        parts = lines[idx].split("//", 1)
        indent = parts[0][: len(parts[0]) - len(parts[0].lstrip())]
        lines[idx] = indent + new_line + ((" //" + parts[1]) if len(parts) > 1 else "")
    return "\n".join(lines)


def _update_balloons(text, course_range, removed_ords, insert=None):
    """BALLOON: から removed_ords の値を抜き、insert=(何番目, 打数) を差し込む。"""
    if not removed_ords and insert is None:
        return text
    vals = balloon_values(text, course_range)
    for o in sorted(set(removed_ords), reverse=True):
        if 0 <= o < len(vals):
            del vals[o]
    if insert is not None:
        pos, count = insert
        while len(vals) < pos:
            vals.append("0")       # 足りないぶんは 0 打(解析側と同じ扱い)
        vals.insert(pos, str(int(count)))
    return set_balloon_values(text, course_range, vals)


# --- 操作 ---------------------------------------------------------------------

def _result(old, new, sound=None, visual=None, reparse=False):
    if new == old:
        return None
    return {"text": new, "sound": sound, "visual": visual or [], "reparse": reparse}


def op_key(text, course_range, m, slot, grid, char):
    """ドン/カッのキー(char は 1〜4)。PeepoDrumKit の音符キーと同じ振る舞い。"""
    body = course_body_span(text, course_range)
    if body is None or char not in _SMALL:
        return None
    k = _key(m, slot, grid)
    existing = None
    for it in chart_items(text, body):
        if _is_long(it) and it["k0"] < k <= it["k1"]:
            return None                     # 長い音符の途中
        if it["k0"] == k:
            existing = it
    if existing is None:
        new = _apply_writes(text, body, [(m, slot, grid, char)])
        return _result(text, new, sound=char, visual=[(m, slot, grid, char)])
    if _is_long(existing):
        new = _apply_writes(text, body, _removal_writes(existing))
        removed = [existing["ord"]] if existing["ord"] is not None else []
        new = _update_balloons(new, course_range, removed)
        return _result(text, new, sound=existing["char"], reparse=True)
    if _SMALL[existing["char"]] == _SMALL[char]:
        new = _apply_writes(text, body, [(m, slot, grid, "0")])
        return _result(text, new, sound=existing["char"], visual=[(m, slot, grid, "0")])
    new = _apply_writes(text, body, [(m, slot, grid, char)])
    return _result(text, new, sound=char, visual=[(m, slot, grid, char)])


def _range_keys(a, b, grid):
    """(小節, スロット) 2つを小さい順に並べ替えて返す。"""
    ta = a[0] * grid + a[1]
    tb = b[0] * grid + b[1]
    if ta > tb:
        ta, tb = tb, ta
    return divmod(ta, grid), divmod(tb, grid)


def op_fill(text, course_range, a, b, grid, char):
    """範囲の中の空いているグリッドをすべて char で埋める(Shift + 音符キー)。"""
    body = course_body_span(text, course_range)
    if body is None or char not in _SMALL:
        return None
    (m0, s0), (m1, s1) = _range_keys(a, b, grid)
    items = chart_items(text, body)
    writes = []
    total = m0 * grid + s0
    last = m1 * grid + s1
    while total <= last:
        m, s = divmod(total, grid)
        k = _key(m, s, grid)
        if not any(it["k0"] <= k <= it["k1"] for it in items):
            writes.append((m, s, grid, char))
        total += 1
    if not writes:
        return None
    new = _apply_writes(text, body, writes)
    return _result(text, new, sound=char, reparse=True)


def op_long(text, course_range, a, b, grid, char):
    """a から b までの長い音符(char は 5/6/7/9)を置く。重なる音符は消す。"""
    body = course_body_span(text, course_range)
    if body is None or char not in LONG_HEADS:
        return None
    (m0, s0), (m1, s1) = _range_keys(a, b, grid)
    k0, k1 = _key(m0, s0, grid), _key(m1, s1, grid)
    if k0 == k1:
        return None
    items = chart_items(text, body)
    writes = []
    removed = []
    for it in items:
        if it["k0"] <= k1 and k0 <= it["k1"]:
            writes.extend(_removal_writes(it))
            if it["ord"] is not None:
                removed.append(it["ord"])
    writes.append((m0, s0, grid, char))
    writes.append((m1, s1, grid, "8"))
    new = _apply_writes(text, body, writes)
    insert = None
    if char in BALLOON_HEADS:
        # 新しい風船が何番目か = 残った風船のうち、これより前にあるものの数。
        before = sum(1 for it in items
                     if it["ord"] is not None and it["ord"] not in removed
                     and it["k0"] < k0)
        steps = (m1 * grid + s1) - (m0 * grid + s0)
        insert = (before, steps)
    new = _update_balloons(new, course_range, removed, insert)
    return _result(text, new, sound=char, reparse=True)


def _items_in(items, a, b, grid, single):
    if single:
        k = _key(a[0], a[1], grid)
        return [it for it in items if it["k0"] == k]
    (m0, s0), (m1, s1) = _range_keys(a, b, grid)
    k0, k1 = _key(m0, s0, grid), _key(m1, s1, grid)
    out = []
    for it in items:
        if k0 <= it["k0"] <= k1:
            out.append(it)
        elif _is_long(it) and it["tail"] is not None and k0 <= it["k1"] <= k1:
            out.append(it)
    return out


def op_delete(text, course_range, a, b, grid):
    """範囲(b が None ならカーソルの1点)の音符を消す。長い音符は終端ごと。"""
    body = course_body_span(text, course_range)
    if body is None:
        return None
    single = b is None
    hits = _items_in(chart_items(text, body), a, b if b else a, grid, single)
    if not hits:
        return None
    writes = []
    removed = []
    for it in hits:
        writes.extend(_removal_writes(it))
        if it["ord"] is not None:
            removed.append(it["ord"])
    new = _apply_writes(text, body, writes)
    new = _update_balloons(new, course_range, removed)
    simple = single and not any(_is_long(it) for it in hits)
    visual = [(a[0], a[1], grid, "0")] if simple else []
    return _result(text, new, visual=visual, reparse=not simple)


def op_transform(text, course_range, a, b, grid, mode):
    """範囲(b が None ならカーソルの1点)の音符を W=flip / Q=size で入れ替える。"""
    body = course_body_span(text, course_range)
    table = _FLIP if mode == "flip" else _SIZE if mode == "size" else None
    if body is None or table is None:
        return None
    single = b is None
    hits = _items_in(chart_items(text, body), a, b if b else a, grid, single)
    writes = []
    sound = None
    for it in hits:
        c = table.get(it["char"])
        if c is None:
            continue
        m, i, L = it["head"]
        writes.append((m, i, L, c))
        sound = sound or c
    if not writes:
        return None
    new = _apply_writes(text, body, writes)
    visual = []
    if single and len(writes) == 1 and writes[0][3] in _SMALL:
        visual = [(a[0], a[1], grid, writes[0][3])]
    return _result(text, new, sound=sound, visual=visual, reparse=not visual)


def run_op(text, course_range, op):
    """画面から来た操作 dict を実行する。変化が無ければ None。

    op の形:
      {"kind": "key",    "a": (小節, スロット), "grid": G, "char": "1"〜"4"}
      {"kind": "fill",   "a": ..., "b": ..., "grid": G, "char": "1"〜"4"}
      {"kind": "long",   "a": ..., "b": ..., "grid": G, "char": "5"/"6"/"7"/"9"}
      {"kind": "delete", "a": ..., "b": None か住所, "grid": G}
      {"kind": "flip" / "size", "a": ..., "b": None か住所, "grid": G}
    """
    kind = op.get("kind")
    g = int(op.get("grid", 16))
    a = tuple(op["a"]) if op.get("a") is not None else None
    b = tuple(op["b"]) if op.get("b") is not None else None
    if a is None:
        return None
    if kind == "key":
        return op_key(text, course_range, a[0], a[1], g, op["char"])
    if kind == "fill" and b is not None:
        return op_fill(text, course_range, a, b, g, op["char"])
    if kind == "long" and b is not None:
        return op_long(text, course_range, a, b, g, op["char"])
    if kind == "delete":
        return op_delete(text, course_range, a, b, g)
    if kind in ("flip", "size"):
        return op_transform(text, course_range, a, b, g, kind)
    if kind == "command":
        return op_command(text, course_range, a[0], a[1], g, op.get("name", ""), op.get("value"))
    if kind == "marker":
        return op_marker(text, course_range, a[0], a[1], g, str(op.get("region", "")).upper(),
                         str(op.get("which", "on")), bool(op.get("present", True)))
    if kind == "region" and b is not None:
        return op_region(text, course_range, a, b, g, str(op.get("region", "")).upper(),
                         bool(op.get("on", True)))
    return None


# ---------------------------------------------------------------------------
# 命令(#BPMCHANGE / #SCROLL)をカーソルの位置に置く
# ---------------------------------------------------------------------------
# 音符と同じ「何小節目の何番目」の住所で、命令の行を1行足す/書き換える/消す。
# テキストの差分は最小にする:
#   ・小節の頭(スロット 0)なら、その小節の最初の音符行の手前に1行足す。
#   ・途中なら、その位置の音符の手前で音符行を2つに割って、間に1行足す。
#   ・位置が今の分割数の文字の切れ目に来ないときだけ、音符と同じやり方で
#     小節を細かく組み直してから足す(既存の命令行は同じ割合の位置に残る)。
# 同じ位置に同じ命令がもうあれば、足さずに値を書き換える(二重に書かない)。

COMMAND_NAMES = ("BPMCHANGE", "SCROLL")


def _fmt_number(v):
    """150.0 → "150"、1.25 → "1.25"。小数は6桁まで。"""
    s = ("%.6f" % float(v)).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _chunk_lines(chunk):
    """小節テキストの各行を (行頭オフセット, 行, その行より前にある音符の数) で返す。

    数え方は _nth_digit_offset / parse_measure_lines と同じ(命令行と行コメントの
    中の数字は数えない、行末コメントの中も数えない)。"""
    out = []
    count = 0
    pos = 0
    for line in chunk.split("\n"):
        out.append((pos, line, count))
        stripped = line.strip()
        if not (stripped.startswith("#") or stripped.startswith("//")):
            code = line.split("//", 1)[0]
            count += sum(1 for c in code if c in "0123456789")
        pos += len(line) + 1
    return out


def _command_name(line):
    s = line.split("//", 1)[0].strip()
    if not s.startswith("#"):
        return None
    return s.split()[0][1:].upper() if s.split() else None


def _find_command(chunk, L, m_key, name):
    """小節テキストの中で、位置 m_key(割合)にある命令 name の行。
    戻り値 (行頭オフセット, 行) か None。"""
    for off, line, idx in _chunk_lines(chunk):
        if _command_name(line) != name:
            continue
        pos = Fraction(idx, L) if L else Fraction(0)
        if pos == m_key:
            return off, line
    return None


def command_at(text, course_range, m, slot, grid, name):
    """(小節, スロット) にある命令 name の値(文字列)。無ければ None。"""
    body = course_body_span(text, course_range)
    if body is None or grid <= 0:
        return None
    spans = measure_spans(text, body)
    if not (0 <= m < len(spans)):
        return None
    a, b = spans[m]
    chunk = text[a:b]
    L = len(_measure_notes(text, (a, b)))
    hit = _find_command(chunk, L, Fraction(int(slot), int(grid)), name)
    if hit is None:
        return None
    parts = hit[1].split("//", 1)[0].split()
    return parts[1] if len(parts) > 1 else ""


def _append_empty_measures(text, body, spans, count, grid):
    """本文の最後の小節の後ろに、空の小節を count 個足す。"""
    ins = spans[-1][1] if spans else body[0]
    wrap = _wrap_for(grid)
    chunks = []
    for _ in range(count):
        s = "0" * grid
        lines = [s[j:j + wrap] for j in range(0, grid, wrap)] if (wrap and grid > wrap) else [s]
        lines[-1] += ","
        chunks.append("\n".join(lines))
    prefix = "" if (ins == 0 or text[ins - 1] == "\n") else "\n"
    added = prefix + "\n".join(chunks) + "\n"
    return text[:ins] + added + text[ins:], len(added)


def _resubdivide(text, span, slot, grid):
    """小節を、(slot/grid) の位置が文字の切れ目になる分割数へ組み直す。

    set_slot の再分割と同じ考え方(情報を落とさない最小の分割数と grid の
    最小公倍数)。戻り値 (新しい text, 新しい小節の範囲) か None。"""
    a, b = span
    chunk = text[a:b]
    parsed = parse_measure_lines(chunk)
    measure = next((p for p in parsed if p["type"] == "measure"), None)
    if measure is None:
        return None
    notes = measure["notes"]
    L = len(notes)
    if L == 0:
        target = grid
        measure["notes"] = "0" * grid
    else:
        mm = min_len(notes) or grid
        target = mm * grid // math.gcd(mm, grid)
        if target > MAX_DIVISION:
            return None
        converted = convert_notes(notes, target)
        if len(converted) != target:
            return None
        measure["breaks"] = [(min(target, (bi * target) // L), ln)
                             for bi, ln in measure["breaks"]]
        measure["notes"] = converted
    rep = render_converted(parsed, target, _wrap_for(target))
    if chunk.endswith("\n") and not rep.endswith("\n"):
        rep += "\n"
    return text[:a] + rep + text[b:], (a, a + len(rep))


def set_command(text, course_range, m, slot, grid, name, value):
    """(小節, スロット) に命令 name を value で置く。value が None なら消す。

    戻り値は新しい text。変化が無ければ None。"""
    name = str(name).upper()
    if name not in COMMAND_NAMES or grid <= 0 or m < 0 or not (0 <= slot < grid):
        return None
    body = course_body_span(text, course_range)
    if body is None:
        return None
    spans = measure_spans(text, body)
    if m >= len(spans):
        if value is None:
            return None
        # 譜面の末尾より先。足りない小節を空で作ってから置く。
        # course_range は足す前の行番号(#END の行はずれている)なので、
        # 本文の終わりは足した文字数ぶん後ろへ伸ばして使う。
        text, added = _append_empty_measures(text, body, spans, m - len(spans) + 1, grid)
        body = (body[0], body[1] + added)
        spans = measure_spans(text, body)
    a, b = spans[m]
    chunk = text[a:b]
    L = len(_measure_notes(text, (a, b)))
    key = Fraction(int(slot), int(grid))
    new_line = "#%s %s" % (name, _fmt_number(value)) if value is not None else None

    # --- 同じ位置に同じ命令があれば、書き換える / 消す ---
    hit = _find_command(chunk, L, key, name)
    if hit is not None:
        off, line = hit
        start = a + off
        end = start + len(line)
        if new_line is None:
            # 行ごと消す(改行も)。
            if end < len(text) and text[end] == "\n":
                end += 1
            return text[:start] + text[end:]
        indent = line[: len(line) - len(line.lstrip())]
        comment = line.split("//", 1)
        rep = indent + new_line + ((" //" + comment[1]) if len(comment) > 1 else "")
        return None if rep == line else text[:start] + rep + text[end:]
    if new_line is None:
        return None
    return _insert_line_at(text, (body[0], body[1]), spans, m, slot, grid, new_line)


def _insert_line_at(text, body, spans, m, slot, grid, new_line):
    """(小節, スロット) の位置に命令の行 new_line を1行差し込む。

    小節は既にあること(末尾より先は呼ぶ側で足しておく)。位置が今の分割数の
    文字の切れ目に来ないときは、音符と同じやり方で小節を組み直してから入れる。
    戻り値は新しい text か None。"""
    if not (0 <= m < len(spans)):
        return None
    a, b = spans[m]
    L = len(_measure_notes(text, (a, b)))
    if L == 0 or (slot * L) % grid != 0:
        r = _resubdivide(text, (a, b), slot, grid)
        if r is None:
            return None
        text, (a, b) = r
        L = len(_measure_notes(text, (a, b)))
    chunk = text[a:b]
    idx = slot * L // grid
    off = _nth_digit_offset(chunk, idx)
    if off is None:
        return None
    line_start = chunk.rfind("\n", 0, off) + 1
    if chunk[line_start:off].strip() == "":
        # 音符行の頭。その行の手前に1行足す(小節の頭もここ)。
        pos = a + line_start
        return text[:pos] + new_line + "\n" + text[pos:]
    # 音符行の途中。そこで行を割って間に入れる。
    pos = a + off
    return text[:pos] + "\n" + new_line + "\n" + text[pos:]


def op_command(text, course_range, m, slot, grid, name, value):
    new = set_command(text, course_range, m, slot, grid, name, value)
    if new is None:
        return None
    return _result(text, new, reparse=True)


# ---------------------------------------------------------------------------
# 範囲の命令(ゴーゴー / 小節線の非表示)
# ---------------------------------------------------------------------------
# 「開始の行」と「終了の行」の組で状態が切り替わる命令を、範囲 [a, b) に掛ける・
# 外す。やり方:
#   1. 範囲 [a, b] の中にある、その種類の命令行をいったん全部消す。
#   2. a の直前の状態が望む状態と違えば、a に切り替えの行を入れる。
#   3. b の直後の状態(元の譜面で b の位置の命令まで効いた状態)が望む状態と
#      違えば、b に戻す行を入れる。
# こうすると範囲の外の状態は1つも変わらず、重複した開始/終了も残らない。
# b が譜面の末尾(またはそれより先)なら戻す行は入れない(最後まで続く扱い)。

REGION_COMMANDS = {
    # 種類: (状態を ON にする行, OFF にする行)
    "GOGO": ("GOGOSTART", "GOGOEND"),
    "BARLINE": ("BARLINEOFF", "BARLINEON"),     # ON = 小節線を隠している
}


def _region_lines(text, body, kind):
    """本文にある、その種類の命令行を時間順に [(位置キー, ON か, 行の開始, 行の終わり)]。

    位置キーは (小節, 割合)。小節の音符をすべて過ぎたあとに書かれた行は、次の
    小節の頭 (小節+1, 0) と同じ扱いにする(同じ時刻なので)。"""
    on_name, off_name = REGION_COMMANDS[kind]
    out = []
    for m, (a, b) in enumerate(measure_spans(text, body)):
        chunk = text[a:b]
        L = len(_measure_notes(text, (a, b)))
        for off, line, idx in _chunk_lines(chunk):
            name = _command_name(line)
            if name not in (on_name, off_name):
                continue
            frac = Fraction(idx, L) if L else Fraction(0)
            key = (m + 1, Fraction(0)) if frac >= 1 else (m, frac)
            out.append((key, name == on_name, a + off, a + off + len(line)))
    return out


def _state_before(lines, key):
    """位置 key より前にある命令だけを当てた状態。"""
    st = False
    for k, on, _s, _e in lines:
        if k < key:
            st = on
    return st


def _state_through(lines, key):
    """位置 key の命令まで当てた状態(key の直後の状態)。"""
    st = False
    for k, on, _s, _e in lines:
        if k <= key:
            st = on
    return st


def region_state(text, course_range, a, b, grid, kind):
    """範囲 [a, b) が全部 ON なら True。範囲が空・コースが無ければ None。"""
    body = course_body_span(text, course_range)
    if body is None or kind not in REGION_COMMANDS:
        return None
    (m0, s0), (m1, s1) = _range_keys(a, b, grid)
    k0, k1 = _key(m0, s0, grid), _key(m1, s1, grid)
    if k0 == k1:
        return None
    lines = _region_lines(text, body, kind)
    if not _state_through(lines, k0):
        return False
    return not any(k0 < k < k1 and not on for k, on, _s, _e in lines)


def set_region(text, course_range, a, b, grid, kind, on):
    """範囲 [a, b) を ON(on=True)/ OFF にする。変化が無ければ None。"""
    body = course_body_span(text, course_range)
    if body is None or kind not in REGION_COMMANDS:
        return None
    (m0, s0), (m1, s1) = _range_keys(a, b, grid)
    k0, k1 = _key(m0, s0, grid), _key(m1, s1, grid)
    if k0 == k1:
        return None
    on_name, off_name = REGION_COMMANDS[kind]
    lines = _region_lines(text, body, kind)
    before = _state_before(lines, k0)
    after = _state_through(lines, k1)
    n_measures = len(measure_spans(text, body))

    # 1. 範囲の中の行を消す(後ろから消せば位置がずれない)。
    new = text
    for k, _on, st, en in sorted(lines, key=lambda x: x[2], reverse=True):
        if k0 <= k <= k1:
            if en < len(new) and new[en] == "\n":
                en += 1
            new = new[:st] + new[en:]
    added = len(new) - len(text)
    body2 = (body[0], body[1] + added)

    # 2. と 3. 切り替えの行。後ろ(b)から入れる。
    if bool(after) != bool(on) and m1 < n_measures:
        spans = measure_spans(new, body2)
        line = "#" + (on_name if after else off_name)
        r = _insert_line_at(new, body2, spans, m1, s1, grid, line)
        if r is None:
            return None
        body2 = (body2[0], body2[1] + len(r) - len(new))
        new = r
    if bool(before) != bool(on):
        spans = measure_spans(new, body2)
        if m0 >= len(spans):
            return None
        line = "#" + (on_name if on else off_name)
        r = _insert_line_at(new, body2, spans, m0, s0, grid, line)
        if r is None:
            return None
        new = r
    return None if new == text else new


def op_region(text, course_range, a, b, grid, kind, on):
    new = set_region(text, course_range, a, b, grid, kind, on)
    if new is None:
        return None
    return _result(text, new, reparse=True)


# ---------------------------------------------------------------------------
# 開始・終了の命令を位置に1つずつ置く(ゴーゴー / 小節線)
# ---------------------------------------------------------------------------
# 範囲でまとめて置くのではなく、カーソルの位置に「開始」か「終了」の行を1行
# ずつ置く/消す。1小節ずつ書き進めるとき、終わりの位置は後から決まるため。

def marker_info(text, course_range, m, slot, grid, kind):
    """位置 (小節, スロット) まわりの状態。

    戻り値 dict:
      before … その位置より前の命令だけを当てた状態(ゴーゴー中 / 小節線を隠して
               いる なら True)
      here_on / here_off … その位置に開始の行 / 終了の行がもうあるか
    コースが無ければ None。"""
    body = course_body_span(text, course_range)
    if body is None or kind not in REGION_COMMANDS or grid <= 0:
        return None
    key = _key(m, slot, grid)
    lines = _region_lines(text, body, kind)
    here = [on for k, on, _s, _e in lines if k == key]
    return {"before": _state_before(lines, key),
            "here_on": any(here), "here_off": any(not x for x in here)}


def set_marker(text, course_range, m, slot, grid, kind, which, present):
    """位置に開始(which="on")/終了(which="off")の行を置く(present=True)/消す。

    戻り値は新しい text。変化が無ければ None。"""
    if kind not in REGION_COMMANDS or which not in ("on", "off"):
        return None
    if grid <= 0 or m < 0 or not (0 <= slot < grid):
        return None
    name = REGION_COMMANDS[kind][0 if which == "on" else 1]
    body = course_body_span(text, course_range)
    if body is None:
        return None
    spans = measure_spans(text, body)
    if m >= len(spans):
        if not present:
            return None
        text, added = _append_empty_measures(text, body, spans, m - len(spans) + 1, grid)
        body = (body[0], body[1] + added)
        spans = measure_spans(text, body)
    a, b = spans[m]
    chunk = text[a:b]
    L = len(_measure_notes(text, (a, b)))
    hit = _find_command(chunk, L, Fraction(int(slot), int(grid)), name)
    if present:
        if hit is not None:
            return None
        return _insert_line_at(text, body, spans, m, slot, grid, "#" + name)
    if hit is None:
        return None
    off, line = hit
    start = a + off
    end = start + len(line)
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:start] + text[end:]


def op_marker(text, course_range, m, slot, grid, kind, which, present):
    new = set_marker(text, course_range, m, slot, grid, kind, which, present)
    if new is None:
        return None
    return _result(text, new, reparse=True)
