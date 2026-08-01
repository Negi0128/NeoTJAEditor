"""ノーツ間隔リサイズ(小節の分割数を変える)の計算。純ロジック(Qt非依存)。

**1小節はカンマまで**。TJA では 16分の小節を 4文字×4行のように複数行へ
分けて書くのが普通なので、行を1小節として扱ってはいけない。以前はそうして
いたため、行の長さが揃っていない小節(例 `1010` + `10,` = 6分割)で
音符の位置が 0/0.333/0.667 → 0/0.25/0.5 へずれて壊れていた。行ごとに
「その行を target 文字に引き伸ばす」ため、行の長さの違いが消えてしまうのが
原因。ここではカンマまでを1小節としてつなげてから変換する。

小節の途中に命令行(#SCROLL 等)がある場合は、その行が「小節の何割の位置に
あったか」を保ったまま、変換後の対応する位置へ戻す。
"""

import math

BASE = [1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 48, 64, 96]
EXTEND = [128, 192, 256, 384, 512, 768, 1024]


def _is_note_line(stripped):
    return bool(stripped) and not stripped.startswith("#") and not stripped.startswith("//")


def parse_measure_lines(text: str) -> list:
    """テキストを「そのまま残す行」と「小節」に分ける。

    戻り値の要素:
      {"type": "keep", "text": 行}
      {"type": "measure", "notes": 連結した数字, "has_comma": bool,
       "leading_space": 先頭行のインデント, "comment": 行末コメントを連結,
       "breaks": [(小節内の何文字目か, その行), ...]}   # 小節の途中の命令行
    """
    result = []
    cur = None   # 組み立て中の小節

    def flush():
        nonlocal cur
        if cur is not None:
            result.append(cur)
            cur = None

    def new_measure(leading_space):
        return {"type": "measure", "notes": "", "has_comma": False,
                "leading_space": leading_space, "comment": "", "breaks": []}

    for line in text.split("\n"):
        stripped = line.strip()
        if not _is_note_line(stripped):
            if cur is None:
                result.append({"type": "keep", "text": line})
            else:
                # 小節の途中の命令行/コメント行。位置(何文字目の手前か)を覚えて
                # おき、変換後は同じ割合の位置へ戻す。
                cur["breaks"].append((len(cur["notes"]), line))
            continue
        parts = line.split("//", 1)
        code = parts[0]
        comment = "//" + parts[1] if len(parts) > 1 else ""
        notes = "".join(c for c in code if c in "0123456789")
        if not notes and "," not in code:
            # 数字もカンマも無い行(空白だけ等)はそのまま。
            if cur is None:
                result.append({"type": "keep", "text": line})
            else:
                cur["breaks"].append((len(cur["notes"]), line))
            continue
        if cur is None:
            cur = new_measure(code[: len(code) - len(code.lstrip())])
        cur["notes"] += notes
        if comment:
            cur["comment"] = (cur["comment"] + " " + comment).strip() if cur["comment"] else comment
        if "," in code:
            cur["has_comma"] = True
            flush()
    flush()
    return result


def min_len(notes: str) -> int:
    """その小節を情報を落とさずに書ける最小の分割数(末尾の余分な 0 を畳んだ値)。"""
    n = len(notes)
    if n <= 1:
        return max(1, n)
    for d in range(1, n + 1):
        if n % d == 0:
            k = n // d
            ok = True
            for i in range(d):
                if not all(notes[i * k + j] == "0" for j in range(1, k)):
                    ok = False
                    break
            if ok:
                return d
    return n


def valid_targets(parsed: list, use_ext: bool) -> list:
    """すべての小節を情報を落とさず表せる分割数(BASE/EXTEND のうち)。"""
    pool = BASE + (EXTEND if use_ext else [])
    base = 1
    has_notes = False
    for p in parsed:
        if p["type"] == "measure" and p["notes"]:
            m = min_len(p["notes"])
            if m > 0:
                base = base * m // math.gcd(base, m)
                has_notes = True
    if has_notes:
        return [t for t in pool if t % base == 0]
    return list(pool)


def wrap_options(target: int) -> list:
    """折り返し文字数の選択肢。"""
    if target % 12 == 0:
        return ["12", "24", "48", "改行なし"]
    elif target % 16 == 0:
        return ["16", "32", "64", "改行なし"]
    return ["改行なし"]


def convert_notes(notes: str, target: int) -> str:
    if not notes:
        return ""
    m = min_len(notes)
    k1 = len(notes) // m
    shrunk = "".join(notes[i * k1] for i in range(m))
    k2 = target // m
    if k2 < 1:
        return shrunk          # target が min_len を下回る指定(通常は起きない)
    return "".join(c + "0" * (k2 - 1) for c in shrunk)


def _wrap(s: str, wrap_val: int) -> list:
    if wrap_val > 0 and len(s) > wrap_val:
        return [s[i:i + wrap_val] for i in range(0, len(s), wrap_val)]
    return [s]


def render_converted(parsed: list, target: int, wrap_val: int) -> str:
    out = []
    for p in parsed:
        if p["type"] == "keep":
            out.append(p["text"])
            continue
        converted = convert_notes(p["notes"], target)
        src_len = len(p["notes"]) or 1
        dst_len = len(converted)
        # 小節の途中にあった行を、同じ割合の位置へ戻す。
        cuts = []
        for idx, line in p["breaks"]:
            cuts.append((min(dst_len, int(round(idx * dst_len / src_len))), line))
        segments = []          # [(文字列, その後ろに入れる行 or None)]
        prev = 0
        for cut, line in cuts:
            cut = max(prev, cut)
            segments.append((converted[prev:cut], line))
            prev = cut
        segments.append((converted[prev:], None))

        lines = []
        for seg, brk in segments:
            if seg:
                lines.extend(_wrap(seg, wrap_val))
            if brk is not None:
                lines.append(brk)
        if not lines:
            lines = [""]
        # 末尾(カンマ/コメント)は最後の**音符行**に付ける。命令行で終わって
        # いたらその手前に空の音符行を足さず、最後の音符行を探して付ける。
        tail = ("," if p["has_comma"] else "") + ((" " + p["comment"]) if p["comment"] else "")
        if tail:
            for i in range(len(lines) - 1, -1, -1):
                s = lines[i].strip()
                if s and not s.startswith("#") and not s.startswith("//"):
                    lines[i] += tail
                    break
            else:
                lines.append(tail)
        if p["leading_space"] and lines:
            lines[0] = p["leading_space"] + lines[0]
        out.extend(lines)
    return "\n".join(out)
