"""TJA の本文を整える(作譜モードが残していく散らかりを片づける)。

利用者の報告 2026-10-15「あまりにもTJAが汚い」。作譜モードで書いた譜面は、
#MEASURE などの命令が重なり、同じ値が何度も並び、1小節が何十行にも割れる。
**鳴る譜面は変わらないのに読めない**ので、意味を変えずに見た目だけ畳む。

やること
  1. 効かない命令を落とす
     ・直前と同じ値の #BPMCHANGE / #SCROLL / #MEASURE
     ・すでにその状態なのに出す #BARLINEON / #GOGOSTART など
     ・同じ位置で打ち消し合う印(ON の直後に OFF)
  2. 行をつなぐ(命令が間に無い音符行どうし)
  3. 小節の文字数を見慣れた目盛りへそろえる(命令の位置は文字の境目に残す)
     2/4/8/16分 → 16分、3/6/12分 → 12分、24/48分 → 48分(24文字で折り返し)、
     32分はそのまま。1文字の小節と譜面の無い小節、決めていない細かさは
     触らない(利用者の指定 2026-10-15)。いちばん短い数に詰めるだけでは
     かえって読みにくいので、**増える方へそろえることもある**。
  4. 書き方をそろえる(数の末尾の 0、同じ位置の命令の並び)
  5. #START〜#END の中の空行を落とす

**触らないもの**
  音符の位置・種類・長さ、BPM と拍子の効き方、見える速さの値、そして
  1音符ごとの ON/OFF の切り替え(ゴーゴーのチカチカは意図した演出)。
  分岐(#BRANCHSTART 等)の中は**丸ごとそのまま**にする — 系統ごとに
  小節の数え方が変わるので、ここで畳むと別物になる。

呼ぶ側は tidy_text() を使う。中身が変わらなければ元の文字列をそのまま返す。
"""
import math
import re

from neotja.measure_math import convert_notes, min_len

#: 値を持つ命令。直前と同じ値なら落とす。
_VALUE_CMDS = ("BPMCHANGE", "SCROLL", "MEASURE")
#: 状態を切り替える印。(ON にする行, OFF にする行)。
_TOGGLES = {"GOGOSTART": ("GOGO", True), "GOGOEND": ("GOGO", False),
            "BARLINEOFF": ("BARLINE", True), "BARLINEON": ("BARLINE", False)}
#: 同じ位置に積まれた命令を並べる順。ここに無いものは後ろへ(元の順のまま)。
_ORDER = {"MEASURE": 0, "BPMCHANGE": 1, "DELAY": 2, "SCROLL": 3,
          "BARLINEOFF": 4, "BARLINEON": 4, "GOGOSTART": 5, "GOGOEND": 5}
#: 既定の状態(譜面の頭)。小節線は出ている・ゴーゴーではない。
_START_STATE = {"GOGO": False, "BARLINE": False}

_CMD_RE = re.compile(r"^#\s*([A-Za-z]+)\s*(.*)$")
#: 中を畳まない命令。ここから #BRANCHEND までは1文字も変えない。
_BRANCH_OPEN = "BRANCHSTART"
_BRANCH_CLOSE = "BRANCHEND"
#: 分岐の中の系統を表す行。
_BRANCH_SIDES = ("N", "E", "M")


def _cmd_of(line):
    """命令行なら (名前, 値)。音符行や空行なら None。"""
    s = line.strip()
    if not s.startswith("#"):
        return None
    m = _CMD_RE.match(s)
    if not m:
        return None
    return m.group(1).upper(), m.group(2).strip()


def _fmt_num(v):
    """"0.70" → "0.7"、"1.00" → "1"。数でなければそのまま。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    s = ("%.6f" % f).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _norm_value(name, value):
    """命令の値を書き方だけそろえる。中身は変えない。"""
    if name == "MEASURE":
        parts = str(value).replace(" ", "").split("/")
        if len(parts) == 2:
            return "%s/%s" % (_fmt_num(parts[0]), _fmt_num(parts[1]))
        return value
    if name in ("BPMCHANGE", "SCROLL", "DELAY"):
        return _fmt_num(value)
    return value


def _same_value(name, a, b):
    """命令の値が同じか。数は数として比べる(0.70 と 0.7 は同じ)。"""
    if a is None or b is None:
        return False
    if name == "MEASURE":
        return _norm_value(name, a) == _norm_value(name, b)
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


class _Measure:
    """1小節ぶん。notes は音符の文字、cmds は [(何文字目の前か, 行)]。"""

    __slots__ = ("notes", "cmds", "tail", "wrap")

    def __init__(self):
        self.notes = ""
        self.cmds = []
        #: 小節の行についていた「,」より後ろの文字(ふつうは空)。
        self.tail = ""
        #: 1行に何文字で折り返すか(0 は折り返さない)。
        self.wrap = 0


def _split_measures(body):
    """本文の行 → [_Measure]。畳めない行(分岐など)が来たら None を混ぜる。

    None は「そこは1文字も触らない」という合図で、元の行をそのまま持つ。
    """
    out = []
    cur = _Measure()
    raw_hold = []          # 分岐の中など、そのまま通す行
    in_branch = False
    for line in body:
        s = line.strip()
        c = _cmd_of(line)
        if c is not None and c[0] == _BRANCH_OPEN:
            in_branch = True
        if in_branch:
            raw_hold.append(line)
            if c is not None and c[0] == _BRANCH_CLOSE:
                in_branch = False
                out.append(raw_hold)
                raw_hold = []
            continue
        if not s:
            continue                      # 空行は落とす
        if c is not None:
            cur.cmds.append((len(cur.notes), s))
            continue
        if s.startswith("//"):
            cur.cmds.append((len(cur.notes), s))
            continue
        cut = s.find(",")
        if cut < 0:
            cur.notes += re.sub(r"[^0-9]", "", s)
            continue
        cur.notes += re.sub(r"[^0-9]", "", s[:cut])
        cur.tail = s[cut + 1:].strip()
        out.append(cur)
        cur = _Measure()
    if raw_hold:
        out.append(raw_hold)
    if cur.notes or cur.cmds:
        out.append(cur)                   # 「,」で閉じていない最後の塊
    return out


def _collapse_same_pos(m):
    """同じ位置に同じ命令が重なっていたら、**最後の1本**だけ残す。

    TJA は前から読むので、同じ所に #SCROLL が3本あれば最後の値が効く
    (解析器で確かめた。利用者の指定 2026-10-15「最後に追加した値を
    適用したい」)。先に書いた分は1ミリも効かないので捨てる。"""
    last = {}
    for i, (pos, line) in enumerate(m.cmds):
        c = _cmd_of(line)
        if c is None or c[0] not in _VALUE_CMDS:
            continue
        last[(pos, c[0])] = i          # あとから来たもので上書き
    if len(last) == len([1 for _p, l in m.cmds
                         if (_cmd_of(l) or ("",))[0] in _VALUE_CMDS]):
        return 0
    keep = []
    dropped = 0
    for i, (pos, line) in enumerate(m.cmds):
        c = _cmd_of(line)
        if c is not None and c[0] in _VALUE_CMDS and last[(pos, c[0])] != i:
            dropped += 1
            continue
        keep.append((pos, line))
    m.cmds = keep
    return dropped


def _drop_dead(measures):
    """効かない命令を落とす。落とした本数を返す。"""
    state = {n: None for n in _VALUE_CMDS}
    toggles = dict(_START_STATE)
    dropped = 0
    for m in measures:
        if isinstance(m, list):
            # 分岐の中は触らない。中で何が起きたか分からないので、状態の
            # 読みも**ここで捨てる**(続きを間違って落とさないため)。
            state = {n: None for n in _VALUE_CMDS}
            toggles = {k: None for k in _START_STATE}
            continue
        # 同じ所に重なった同じ命令は、先に書いた分が効かない。先に畳む。
        dropped += _collapse_same_pos(m)
        keep = []
        for pos, line in m.cmds:
            c = _cmd_of(line)
            if c is None:
                keep.append((pos, line))
                continue
            name, val = c
            if name in _VALUE_CMDS:
                if _same_value(name, state.get(name), val):
                    dropped += 1
                    continue
                state[name] = val
            elif name in _TOGGLES:
                kind, want = _TOGGLES[name]
                if toggles.get(kind) == want:
                    dropped += 1
                    continue
                # 同じ位置に反対の印があったら、**両方とも**意味が無い。
                # そこで行って戻るだけなので、状態は元のまま
                # (前の印が効いていなければ、ここへは来ない)。
                undone = False
                for j in range(len(keep) - 1, -1, -1):
                    p2, l2 = keep[j]
                    if p2 != pos:
                        break
                    c2 = _cmd_of(l2)
                    if c2 and c2[0] in _TOGGLES and _TOGGLES[c2[0]][0] == kind:
                        del keep[j]
                        dropped += 2
                        undone = True
                        break
                toggles[kind] = want
                if undone:
                    continue
            keep.append((pos, line))
        m.cmds = keep
    return dropped


#: 4/4 の小節に入る数(= 何分か) → そろえる目盛り(利用者の指定 2026-10-15)。
#: 「いちばん短い数」に詰めるだけでは読みにくいので、見慣れた目盛りへ寄せる。
_GRID_OF = {2: 16, 4: 16, 8: 16, 16: 16,
            3: 12, 6: 12, 12: 12,
            24: 48, 48: 48,
            32: 32}
#: 目盛り → 1行に何文字で折り返すか(0 は折り返さない)。
_WRAP_OF = {16: 16, 12: 12, 48: 24, 32: 16}
#: 32分は分割を変えないが、行は 16 文字で折り返す
#: (利用者の指定 2026-10-15「32も16で折り返してほしい」)。
#: これより細かい小節は作らない(measure_math と同じ上限)。
_MAX_DIV = 1 << 12


def _lcm(x, y):
    return x * y // math.gcd(x, y)


def _grid_target(m, meter):
    """その小節をそろえる文字数。そのままにするなら None。

    meter は (分子, 分母)。4/4 なら 16分 = 16文字だが、拍子が違えば
    その割合ぶん増減する(3/16 の小節の 16分は 3文字)。
    """
    L = len(m.notes)
    if L <= 1 or not m.notes.strip("0"):
        return None                   # 1文字 / 譜面の無い小節はそのまま
    need = min_len(m.notes) or L
    n, d = meter
    if n <= 0 or d <= 0:
        return None
    # この小節は 4/4 の何個ぶんか(3/16 なら 3/16)。
    num, den = n * 4, d * 4           # (n/d) / (4/4) = n*4 / (d*4)
    g = math.gcd(num, den)
    num, den = num // g, den // g
    # いまの細かさを「4/4 だったら何分か」に直す。割り切れなければ触らない。
    if (need * den) % num:
        return None
    equiv = need * den // num
    grid = _GRID_OF.get(equiv)
    if grid is None:
        return None                   # 決めていない細かさ。そのまま
    if (grid * num) % den:
        return None                   # この拍子では文字数にならない
    want = grid * num // den
    if want <= 0:
        return None
    # 小節の途中にある命令は、文字の境目に残す。
    for pos, _line in m.cmds:
        if 0 < pos < L:
            want = _lcm(want, L // math.gcd(L, pos))
    if want % need or want > _MAX_DIV:
        return None
    if want > L and want != grid * num // den:
        return None                   # 命令の都合で増えるだけなら触らない
    return want, _WRAP_OF.get(grid, 0)


def _regrid(m, meter):
    """小節の文字数をそろえる。増減した文字数(符号つき)を返す。"""
    L = len(m.notes)
    # **音符が1つも無い小節は「,」だけにする**(利用者の指定 2026-10-17)。
    # 0 を並べても読みは同じで(解析で音符・小節線が一致することを確認済み)、
    # 画面では「ここは何も無い」が一目で分かる。小節の途中に命令がある
    # ものは触らない — 命令の居場所が文字の割合で決まるため。
    inner_cmd = any(0 < pos < L for pos, _line in m.cmds)
    if L > 0 and not m.notes.strip("0") and not inner_cmd:
        m.notes = ""
        m.wrap = 0
        return -L
    got = _grid_target(m, meter)
    if got is None:
        return 0
    want, wrap = got
    m.wrap = wrap
    if want == L:
        return 0
    conv = convert_notes(m.notes, want)
    if len(conv) != want:
        return 0
    m.cmds = [(int(round(p * want / float(L))), l) for p, l in m.cmds]
    m.notes = conv
    return L - want


def _regrid_all(measures):
    """全部の小節の文字数をそろえる。拍子(#MEASURE)を追いながら当てる。

    増減した文字数の合計を返す(増えることもある — 8分の小節は 16分へ
    伸びる。利用者の指定 2026-10-15「見慣れた目盛りに合わせたい」)。
    """
    meter = (4, 4)
    moved = 0
    for m in measures:
        if isinstance(m, list):
            # 分岐の中は触らない。拍子も分からなくなるので 4/4 へ戻さず、
            # そのまま持ち越す(中で変えていたら後ろが狂うが、そこは
            # 触らないと決めた所なので、分割も変えない)。
            continue
        # 小節の頭に置いた #MEASURE は**その小節**から効く。先に読む。
        for pos, line in m.cmds:
            if pos:
                break
            got = _meter_of(line)
            if got:
                meter = got
        moved += _regrid(m, meter)
        # 途中に置いた #MEASURE は、そこから先(=次の小節)の数え方になる。
        for pos, line in m.cmds:
            if not pos:
                continue
            got = _meter_of(line)
            if got:
                meter = got
    return moved


def _meter_of(line):
    """#MEASURE の行なら (分子, 分母)。ほかは None。"""
    c = _cmd_of(line)
    if c is None or c[0] != "MEASURE":
        return None
    parts = str(c[1]).replace(" ", "").split("/")
    if len(parts) != 2:
        return None
    try:
        n, d = int(float(parts[0])), int(float(parts[1]))
    except (TypeError, ValueError):
        return None
    return (n, d) if n > 0 and d > 0 else None


def _render(measures):
    """[_Measure] → 本文の行。"""
    out = []
    for m in measures:
        if isinstance(m, list):
            out.extend(m)
            continue
        cmds = {}
        for pos, line in m.cmds:
            cmds.setdefault(pos, []).append(line)
        for pos in cmds:
            cmds[pos] = _sorted_cmds(cmds[pos])
        n = len(m.notes)
        at = 0
        for pos in sorted(cmds):
            p = max(0, min(n, pos))
            if p > at:
                out.extend(_wrapped(m.notes[at:p], at, m.wrap))
                at = p
            out.extend(cmds[pos])
        rest = _wrapped(m.notes[at:], at, m.wrap)
        if not rest:
            rest = [""]
        rest[-1] = rest[-1] + "," + (m.tail or "")
        out.extend(rest)
    return out


def _wrapped(chunk, start, wrap):
    """音符の並びを wrap 文字ずつの行に割る。wrap=0 なら1行のまま。

    start は小節の頭から何文字目かで、**小節の頭から数えて**折り返す
    (命令で切れた続きでも、行の長さがそろうように)。"""
    if not chunk:
        return []
    if not wrap or wrap <= 0:
        return [chunk]
    out = []
    at = 0
    while at < len(chunk):
        take = wrap - ((start + at) % wrap)
        out.append(chunk[at:at + take])
        at += take
    return out


def _sorted_cmds(lines):
    """同じ位置の命令を決まった順に。書き方もそろえる。"""
    deco = []
    for i, line in enumerate(lines):
        c = _cmd_of(line)
        if c is None:
            deco.append((99, i, line))
            continue
        name, val = c
        val = _norm_value(name, val)
        text = ("#%s %s" % (name, val)).rstrip()
        deco.append((_ORDER.get(name, 90), i, text))
    deco.sort(key=lambda d: (d[0], d[1]))
    return [d[2] for d in deco]


def _course_bodies(lines):
    """[(#START の行番号, #END の行番号)] を前から順に。"""
    out = []
    start = None
    for i, l in enumerate(lines):
        s = l.strip().upper()
        if s.startswith("#START"):
            start = i
        elif s.startswith("#END") and start is not None:
            out.append((start, i))
            start = None
    return out


def _course_keys(text):
    """ファイルに入っているコースの名前(COURSE: の値)。無ければ [None]。"""
    out = []
    for l in text.replace("\r\n", "\n").split("\n"):
        s = l.strip()
        if s.upper().startswith("COURSE:"):
            out.append(s.split(":", 1)[1].strip())
    return out or [None]


def _shape(analyzer, text, course):
    """その譜面の「鳴り方」を、比べられる形で取り出す。"""
    d = analyzer.build_preview_timeline(text, None, course)

    def r(v):
        return round(float(v), 4)

    return {
        "notes": [(r(t), str(c)) for t, c, _b, _s, _e in d.get("notes", [])],
        "rolls": [(r(s[0]), r(s[1])) for s in d.get("rolls", [])],
        "balloons": [(r(s[0]), r(s[1]), int(s[-1]))
                     for s in d.get("balloons", [])],
        "kusudamas": [(r(s[0]), r(s[1]), int(s[-1]))
                      for s in d.get("kusudamas", [])],
        "gogo": [(r(s[0]), r(s[1])) for s in d.get("gogo", [])],
        "bars": [r(t) for t in d.get("bars", [])],
        "scroll": [(float(t), float(v))
                   for t, v in d.get("scroll_changes", [])],
        "bpm": [(float(t), float(v)) for t, v in d.get("bpm_changes", [])],
    }


def _value_at(changes, t):
    import bisect
    if not changes:
        return None
    i = bisect.bisect_right([c[0] for c in changes], t + 1e-9) - 1
    return changes[max(0, i)][1]


def same_sound(old, new):
    """整える前と後で、鳴る譜面が1つも変わっていないか。

    返すのは (同じか, 違いの説明)。**値を持つ命令は「本数」ではなく
    「各時刻で効いている値」で比べる** — 無駄な命令を落とせば本数は
    当然減るが、効き方は変わらないはずだから。

    解析でこけたときは「違う」とみなす(分からないものは書き換えない)。
    """
    from neotja.tja_analyzer import TJACourseAnalyzer
    an = TJACourseAnalyzer({})
    for course in _course_keys(old):
        try:
            a = _shape(an, old, course)
            b = _shape(an, new, course)
        except Exception as e:  # noqa: BLE001
            return False, "譜面を読み直せませんでした (%s)" % (e,)
        for key, label in (("notes", "音符"), ("rolls", "連打"),
                           ("balloons", "風船"), ("kusudamas", "くす玉"),
                           ("gogo", "ゴーゴー"), ("bars", "小節線")):
            if a[key] != b[key]:
                return False, "%sが変わりました (%d → %d)" % (
                    label, len(a[key]), len(b[key]))
        for key, label in (("scroll", "見た目の速さ"), ("bpm", "BPM")):
            times = sorted(set([c[0] for c in a[key]] + [c[0] for c in b[key]]
                               + [t for t, _c in a["notes"]] + a["bars"]))
            for t in times:
                va, vb = _value_at(a[key], t), _value_at(b[key], t)
                if va is None or vb is None:
                    if va != vb:
                        return False, "%sが変わりました (%.3f 秒)" % (label, t)
                elif abs(va - vb) > 1e-6:
                    return False, "%sが変わりました (%.3f 秒: %g → %g)" % (
                        label, t, va, vb)
    return True, ""


def tidy_text(text):
    """TJA の全文を整えて (新しい全文, 控え) を返す。

    控えは {"commands": 落とした命令の数, "lines": 減った行数,
            "chars": 増減した音符の文字数(減れば+), "courses": コースの数}。
    変化が無ければ元の text をそのまま返す。
    """
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    spans = _course_bodies(lines)
    report = {"commands": 0, "lines": 0, "chars": 0, "courses": 0}
    if not spans:
        return text, report
    out = []
    at = 0
    for a, b in spans:
        out.extend(lines[at:a + 1])
        body = lines[a + 1:b]
        measures = _split_measures(body)
        report["commands"] += _drop_dead(measures)
        report["chars"] += _regrid_all(measures)
        new_body = _render(measures)
        report["lines"] += len(body) - len(new_body)
        report["courses"] += 1
        out.extend(new_body)
        at = b
    out.extend(lines[at:])
    new = nl.join(out)
    if new == text:
        return text, report
    return new, report
