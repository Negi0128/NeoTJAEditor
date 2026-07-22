"""小節(カンマ区切り)単位の編集。純ロジック(Qt非依存)。

TJA の「1小節」はカンマで区切られたひとかたまり(複数行や #BPMCHANGE 等の
コマンド行を含みうる)。ここではカーソル位置が属する小節の文字範囲を求め、
複製・削除・空小節挿入・前後入れ替えを「1回の置換(start, end, 置換文字列,
置換後カーソル位置)」として返す。main_window はそれを QTextCursor で適用する
だけなので、Undo は1操作にまとまる。

範囲は必ず #START..#END の本文内に収まる。本文の外(ヘッダ等)にカーソルが
あるときは各操作 None を返す(= 何もしない)。
"""


def _body_span(text, pos):
    """pos が属する #START..#END 本文の文字範囲 (body_start, body_end) を返す。
    body_start は #START 行の次の行頭、body_end は #END 行の行頭。本文外なら None。"""
    n = len(text)
    pos = max(0, min(pos, n))
    # 行ごとの開始オフセットと内容。
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    # pos が属する行 index。
    cur_line = 0
    for i in range(len(starts)):
        if starts[i] <= pos:
            cur_line = i
        else:
            break

    def line_text(i):
        a = starts[i]
        b = starts[i + 1] - 1 if i + 1 < len(starts) else n
        return text[a:b]

    # cur_line より前(含む)で最後の #START、その後 pos までに #END が無いこと。
    start_line = None
    for i in range(cur_line, -1, -1):
        s = line_text(i).split("//")[0].strip()
        if s.startswith("#END"):
            return None   # 直近の指令が #END → 本文の外
        if s.startswith("#START"):
            start_line = i
            break
    if start_line is None:
        return None
    # start_line 以降で最初の #END。
    end_line = None
    for i in range(start_line + 1, len(starts)):
        s = line_text(i).split("//")[0].strip()
        if s.startswith("#END"):
            end_line = i
            break
    if end_line is None or cur_line >= end_line:
        return None
    body_start = starts[start_line + 1] if start_line + 1 < len(starts) else n
    body_end = starts[end_line]
    return (body_start, body_end)


def measure_span(text, pos):
    """pos が属する小節(カンマ区切り)の範囲 (a, b) を返す。本文外なら None。

    各小節は「末尾の改行まで」を含む形にそろえる(先頭ではなく末尾に改行を
    付ける)。こうすると全小節が対称になり、複製/削除/入れ替えで改行が
    ずれたり空行が残ったりしない。a は直前小節の末尾改行の次、b は自分の
    末尾カンマ(+その直後の改行1つ)の次。いずれも本文内にクランプ。"""
    body = _body_span(text, pos)
    if body is None:
        return None
    bstart, bend = body
    pos = max(bstart, min(pos, bend))
    # 直前のカンマ。その次から始まるが、直後の改行1つは前小節の末尾なので飛ばす。
    a = bstart
    i = pos - 1
    while i >= bstart:
        if text[i] == ",":
            a = i + 1
            break
        i -= 1
    if a < bend and text[a] == "\n":
        a += 1
    # 次のカンマ。その直後の改行1つは自分の末尾として取り込む。
    b = bend
    j = pos
    while j < bend:
        if text[j] == ",":
            b = j + 1
            break
        j += 1
    if b < bend and text[b] == "\n":
        b += 1
    return (a, b)


def _slot_count(chunk):
    """小節テキスト内のノーツ枠数(コマンド行を除く数字の総数)。空小節挿入時の
    分解能合わせに使う。0 なら 4 を返す。"""
    count = 0
    for line in chunk.split("\n"):
        s = line.split("//")[0]
        if s.strip().startswith("#"):
            continue
        for c in s:
            if c in "0123456789":
                count += 1
    return count or 4


def op_duplicate(text, pos):
    span = measure_span(text, pos)
    if span is None:
        return None
    a, b = span
    chunk = text[a:b]
    # chunk は末尾に改行を含む(最終小節など含まないこともある)。含まなければ
    # 足して、複製が行として分離するようにする。
    insert = chunk if chunk.endswith("\n") else chunk + "\n"
    cursor = b + len(insert)
    return (b, b, insert, cursor)


def op_delete(text, pos):
    span = measure_span(text, pos)
    if span is None:
        return None
    a, b = span
    return (a, b, "", a)


def op_insert_after(text, pos):
    span = measure_span(text, pos)
    if span is None:
        return None
    a, b = span
    slots = _slot_count(text[a:b])
    insert = ("0" * slots) + ",\n"
    cursor = b   # 挿入した空小節の行頭
    return (b, b, insert, cursor)


def op_move(text, pos, direction):
    """direction: "up"=前の小節と入れ替え / "down"=次の小節と入れ替え。"""
    span = measure_span(text, pos)
    if span is None:
        return None
    a, b = span
    body = _body_span(text, pos)
    bstart, bend = body
    if direction == "down":
        if b >= bend:
            return None   # 次が無い
        nxt = measure_span(text, b)      # b は次小節の先頭を指す
        if nxt is None:
            return None
        c, d = nxt
        # a..b(現) と c..d(次) を入れ替え。両者は隣接(b == c)のはず。
        if c != b:
            return None
        new = text[c:d] + text[a:b]
        cursor = a + (d - c)   # 移動後の現小節の頭
        return (a, d, new, cursor)
    else:  # up
        if a <= bstart:
            return None   # 前が無い
        # a-1 は前小節の末尾改行で、その位置は前方(現小節)に解決されてしまう。
        # 前小節の内容(カンマ)を指す a-2 を使う。
        prev = measure_span(text, a - 2)
        if prev is None:
            return None
        p, q = prev
        if q != a:
            return None
        new = text[a:b] + text[p:q]
        cursor = p     # 移動後の現小節の頭
        return (p, b, new, cursor)
