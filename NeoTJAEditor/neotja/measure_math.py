import math

BASE = [1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 48, 64, 96]
EXTEND = [128, 192, 256, 384, 512, 768, 1024]


def parse_measure_lines(text: str) -> list:
    """Split TJA note text into lines, tagging note-bearing lines separately
    from headers/comments/blank lines so structure survives a resolution change."""
    result = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            result.append({"type": "keep", "text": line})
            continue
        parts = line.split("//", 1)
        code = parts[0]
        comment = "//" + parts[1] if len(parts) > 1 else ""
        notes = "".join(c for c in code if c in "0123456789")
        if notes:
            ls = code[: len(code) - len(code.lstrip())]
            result.append({
                "type": "notes",
                "original": line,
                "notes": notes,
                "has_comma": "," in code,
                "leading_space": ls,
                "comment": comment,
            })
        else:
            result.append({"type": "keep", "text": line})
    return result


def min_len(notes: str) -> int:
    """Smallest note-count this measure could be losslessly expressed in
    (i.e. the GCD-like resolution after collapsing redundant trailing zeros)."""
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
    """Target resolutions (from BASE/EXTEND) that every measure in `parsed`
    can be converted to without loss, based on each measure's min_len."""
    pool = BASE + (EXTEND if use_ext else [])
    base = 1
    has_notes = False
    for p in parsed:
        if p["type"] == "notes":
            m = min_len(p["notes"])
            if m > 0:
                base = base * m // math.gcd(base, m)
                has_notes = True
    if has_notes:
        return [t for t in pool if t % base == 0]
    return list(pool)


def wrap_options(target: int) -> list:
    """Valid line-wrap width choices for the line-wrap combo, given a target resolution."""
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
    return "".join(c + "0" * (k2 - 1) for c in shrunk)


def render_converted(parsed: list, target: int, wrap_val: int) -> str:
    out = []
    for p in parsed:
        if p["type"] == "keep":
            out.append(p["text"])
            continue
        converted = convert_notes(p["notes"], target)
        if wrap_val > 0 and len(converted) > wrap_val:
            chunks = [converted[i:i + wrap_val] for i in range(0, len(converted), wrap_val)]
            for i, chunk in enumerate(chunks):
                is_last = i == len(chunks) - 1
                line = p["leading_space"] if i == 0 else ""
                line += chunk
                if is_last and p["has_comma"]:
                    line += ","
                if is_last and p["comment"]:
                    line += " " + p["comment"]
                out.append(line)
        else:
            line = p["leading_space"] + converted + ("," if p["has_comma"] else "")
            if p["comment"]:
                line += " " + p["comment"]
            out.append(line)
    return "\n".join(out)
