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
