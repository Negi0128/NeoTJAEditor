import bisect
import os
import re

from PIL import Image, ImageDraw, ImageFont


#: tja-tools の音符の縁。向こうの素材(notes-don.png など)を1pxずつ
#: 読んで採った値。**白フチではなく、外に黒・内に灰の2重**になっている。
#: 外側は半透明(α178)なので、下の色と混ざってやわらかく見える。
TJAT_EDGE_OUT = (10, 10, 10, 178)
TJAT_EDGE_IN = (176, 176, 176, 255)
#: 地の色。こちらも向こうの素材から。
TJAT_DON = (255, 67, 67)
TJAT_KA = (67, 200, 255)
TJAT_ROLL = (255, 239, 67)
#: 風船の帯(notes-spRollMiddle.png)と、玉の中心/縁の色
#: (notes-balloon.png は球のようなグラデ)。
TJAT_BALLOON = (255, 191, 67)
TJAT_BALLOON_IN = (255, 206, 90)
TJAT_BALLOON_OUT = (255, 156, 0)
#: 何倍で描いてから縮めるか。向こうの素材は輪郭がなめらかなので、
#: そのまま円を描くとこちらだけギザギザになる。
_TJAT_SS = 4
#: 作った絵の使い回し(同じ大きさ・色なら1回でよい)。
_TJAT_CACHE = {}


def _tjat_round(size, radius, ring_w, fill, ss):
    """外・内の2重の縁を付けた角丸(= 円/カプセル)を RGBA で作る。

    size は (幅, 高さ)。radius は角の丸み。ss 倍で描いてから縮める。
    """
    w, h = size
    im = Image.new("RGBA", (max(1, w * ss), max(1, h * ss)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    rr = radius * ss

    def box(inset):
        return [inset, inset, im.width - 1 - inset, im.height - 1 - inset]

    d.rounded_rectangle(box(0), radius=rr, fill=TJAT_EDGE_OUT)
    d.rounded_rectangle(box(ring_w * ss), radius=max(0, rr - ring_w * ss),
                        fill=TJAT_EDGE_IN)
    d.rounded_rectangle(box(2 * ring_w * ss),
                        radius=max(0, rr - 2 * ring_w * ss), fill=fill)
    if ss == 1:
        return im
    return im.resize((max(1, w), max(1, h)), Image.Resampling.LANCZOS)


def tjat_note_tile(diameter, fill, ring_w):
    """tja-tools 風の音符1つぶんの絵(RGBA)。"""
    key = ("note", diameter, fill, ring_w)
    im = _TJAT_CACHE.get(key)
    if im is None:
        d = max(3, int(diameter))
        im = _tjat_round((d, d), d / 2.0, max(1, int(ring_w)), fill, _TJAT_SS)
        _TJAT_CACHE[key] = im
    return im


def tjat_balloon_tile(diameter, ring_w):
    """風船の玉。向こうの素材は球のようなグラデなので、中心から縁へ
    色を送りながら円を重ねて似せる。"""
    key = ("balloon", diameter, ring_w)
    im = _TJAT_CACHE.get(key)
    if im is not None:
        return im
    d = max(3, int(diameter))
    ss = _TJAT_SS
    im = Image.new("RGBA", (d * ss, d * ss), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    rw = max(1, int(ring_w)) * ss

    def box(inset):
        return [inset, inset, im.width - 1 - inset, im.height - 1 - inset]

    dr.ellipse(box(0), fill=TJAT_EDGE_OUT)
    dr.ellipse(box(rw), fill=TJAT_EDGE_IN)
    inner = im.width - 4 * rw          # 地の直径(両側の縁を抜いたぶん)
    steps = max(4, inner // (2 * ss))
    for k in range(steps):
        f = k / float(steps - 1) if steps > 1 else 0.0
        col = tuple(int(round(TJAT_BALLOON_OUT[i]
                              + (TJAT_BALLOON_IN[i] - TJAT_BALLOON_OUT[i]) * f))
                    for i in range(3)) + (255,)
        inset = 2 * rw + int(round(f * inner / 2.0))
        if inset * 2 >= im.width:
            break
        dr.ellipse(box(inset), fill=col)
    im = im.resize((d, d), Image.Resampling.LANCZOS)
    _TJAT_CACHE[key] = im
    return im


def tjat_capsule(width, thick, fill, ring_w):
    """連打の帯(両端が丸い)の絵(RGBA)。長さごとに作る。

    長くなることがあるので、拡大率は音符より控えめにする(直線の縁は
    ギザギザにならないので、これで十分)。
    """
    w = max(1, int(round(width)))
    h = max(3, int(round(thick)))
    ss = 2 if w > 400 else _TJAT_SS
    return _tjat_round((w, h), h / 2.0, max(1, int(ring_w)), fill, ss)


#: tja-tools の帯の中の小さい字は、1文字 6x6 の点々(向こうは1文字ずつの
#: PNG を並べている)。素材は持ってこないので、同じ大きさ・同じ使い方の
#: 点字をこちらで用意する。要るのは数字と BPM/HS/NaN に出る字だけ。
#: 1文字の枠は横6・縦6、絵は横4・縦5で、枠の左に1px の余白を置く。
_PIX_GLYPHS = {
    "0": (".##.", "#..#", "#..#", "#..#", ".##."),
    "1": (".#..", "##..", ".#..", ".#..", "###."),
    "2": (".##.", "#..#", "..#.", ".#..", "####"),
    "3": ("###.", "...#", ".##.", "...#", "###."),
    "4": ("#..#", "#..#", "####", "...#", "...#"),
    "5": ("####", "#...", "###.", "...#", "###."),
    "6": (".##.", "#...", "###.", "#..#", ".##."),
    "7": ("####", "...#", "..#.", ".#..", ".#.."),
    "8": (".##.", "#..#", ".##.", "#..#", ".##."),
    "9": (".##.", "#..#", ".###", "...#", ".##."),
    "B": ("###.", "#..#", "###.", "#..#", "###."),
    "H": ("#..#", "#..#", "####", "#..#", "#..#"),
    "M": ("#..#", "####", "####", "#..#", "#..#"),
    "N": ("#..#", "##.#", "#.##", "#..#", "#..#"),
    "P": ("###.", "#..#", "###.", "#...", "#..."),
    "S": (".###", "#...", ".##.", "...#", "###."),
    "a": ("....", ".##.", "#.#.", "#.#.", ".###"),
    "-": ("....", "....", "###.", "....", "...."),
    ".": ("....", "....", "....", "....", ".#.."),
    " ": ("....", "....", "....", "....", "...."),
}
#: 1文字ぶんの送り(向こうの drawImageText も 6px で数えている)。
PIX_ADVANCE = 5


def pix_text_width(text, sc=1.0):
    return int(round(len(text) * PIX_ADVANCE * sc))


def draw_pix_text(draw, x, y, text, color, sc=1.0):
    """点字で1行描く。(x, y) は**枠の左上**。

    倍率のぶんだけ点を大きくするので、拡大してもにじまない。
    知らない字は空けて送る(数は合う)。
    """
    u = max(1, int(round(sc)))
    for i, ch in enumerate(text):
        g = _PIX_GLYPHS.get(ch)
        if g is None:
            continue
        gx = x + i * PIX_ADVANCE * u + u          # 枠の左に1px の余白
        for row, line in enumerate(g):
            run = None
            for col in range(len(line) + 1):
                on = col < len(line) and line[col] == "#"
                if on and run is None:
                    run = col
                elif not on and run is not None:
                    draw.rectangle([gx + run * u, y + row * u,
                                    gx + col * u - 1, y + (row + 1) * u - 1],
                                   fill=color)
                    run = None


#: 向こうの字の色(sprite の bpm-*.png / hs-*.png / num-*.png から)。
PIX_COLOR_BPM = (0, 0, 255)
PIX_COLOR_HS = (255, 0, 0)
PIX_COLOR_NUM = (0, 0, 0)


def _header_fonts(px):
    """見出しの字。tja-tools は Windows では
    `bold {px}px Arial, MS UI Gothic`。英字は Arial Bold、日本語は
    MS UI Gothic に落ちる(ブラウザの代替と同じ)。

    戻りは (欧文, 和文)。和文は太字の face が無いので、呼ぶ側で
    1px ずらして2度描いて太らせる(ブラウザがやるのと同じ手)。
    """
    px = max(6, int(round(px)))
    latin = jp = None
    for name in ("arialbd.ttf", "Arialbd.ttf"):
        try:
            latin = ImageFont.truetype(name, px)
            break
        except IOError:
            pass
    try:
        jp = ImageFont.truetype("msgothic.ttc", px, index=1)   # MS UI Gothic
    except (IOError, OSError):
        jp = None
    if latin is None:
        latin = get_font(px, bold=True)
    if jp is None:
        jp = latin
    return latin, jp


def draw_header_text(draw, x, y, text, color, px):
    """見出しを1行。英字と和文で字を切り替えながら左から送る。

    PIL はブラウザと違って字が無いときに勝手に代替してくれないので、
    ここで切り分ける(でないと日本語が豆腐になる)。
    """
    latin, jp = _header_fonts(px)
    runs = []
    for ch in text:
        # Arial に入っているのは基本的に Latin-1 まで。★ や全角記号まで
        # 回すと豆腐になるので、そこで切る(ブラウザの代替と同じ結果)。
        is_latin = ord(ch) < 0x0100
        if runs and runs[-1][0] == is_latin:
            runs[-1][1].append(ch)
        else:
            runs.append((is_latin, [ch]))
    cx = float(x)
    for is_latin, chars in runs:
        s = "".join(chars)
        fnt = latin if is_latin else jp
        draw.text((cx, y), s, fill=color, font=fnt, anchor="la")
        if not is_latin:
            # 太字の face が無いぶんを、1px ずらして重ねて太らせる。
            draw.text((cx + 1, y), s, fill=color, font=fnt, anchor="la")
        try:
            cx += draw.textlength(s, font=fnt)
        except Exception:  # noqa: BLE001
            cx += fnt.getbbox(s)[2]
    return cx


def get_font(size, bold=True):
    fonts = ["meiryob.ttc", "msgothic.ttc", "YuGothB.ttc", "Arialbd.ttf"] if bold \
        else ["meiryo.ttc", "msgothic.ttc", "YuGothM.ttc", "Arial.ttf"]
    for f in fonts:
        try:
            return ImageFont.truetype(f, size)
        except IOError:
            pass
    return ImageFont.load_default()


def load_sprites(notes_png_path) -> dict:
    """Load the note sprite sheet if present; returns {} on any failure
    (callers fall back to drawing plain circles, same as the original)."""
    sprites = {}
    if notes_png_path and os.path.exists(notes_png_path):
        try:
            sheet = Image.open(notes_png_path).convert("RGBA")
            sw, sh = sheet.size
            row_h = sh // 2
            u = sw / 13.4
            sprites['1'] = sheet.crop((0, 0, int(u), row_h))
            sprites['2'] = sheet.crop((int(u), 0, int(2 * u), row_h))
            sprites['3'] = sheet.crop((int(2 * u), 0, int(3.2 * u), row_h))
            sprites['4'] = sheet.crop((int(3.2 * u), 0, int(4.4 * u), row_h))
            sprites['5_head'] = sheet.crop((int(4.4 * u), 0, int(5.4 * u), row_h))
            sprites['6_head'] = sheet.crop((int(7.4 * u), 0, int(8.4 * u), row_h))
            sprites['7'] = sheet.crop((int(10.4 * u), 0, int(11.9 * u), row_h))
            sprites['9'] = sheet.crop((int(11.9 * u), 0, sw, row_h))
        except Exception as e:
            print(f"画像読み込みエラー: {e}")
    return sprites


def generate_chart_image(content: str, selected_label: str, courses: list,
                         sprites: dict = None,
                         measure_from: int = None, measure_to: int = None,
                         scale: float = 1.0) -> Image.Image:
    """Renders a static timeline/score-sheet PNG for one course of a TJA file.

    `courses` is the output of TJACourseAnalyzer.parse_courses(content).

    描き方は **tja-tools (https://whmhammer.github.io/tja-tools/) と同じ
    寸法**で描く(利用者の指定 2026-11-04「見た目も寄せたい」)。向こうの
    src/js/drawChart.js に書いてある数字をそのまま使っているので、1倍なら
    画の大きさも位置も一致する。音符の絵だけは向こうの素材を持ってきて
    いない(ライセンスの記載が無いため)ので、こちらで丸を描く。

    `scale` は倍率。1.0 が tja-tools と同じ寸法で、読みづらいときに
    2.0 / 3.0 と上げる。寸法も字の大きさも丸ごと掛ける(拡大ではないので
    ぼけない)。
    """
    sprites = sprites or {}
    # レーン(帯)の色。tja-tools の ROW_COLOR['N'] / LINE_COLOR。
    # 真ん中の薄い線は向こうには無いので引かない(None)。
    lane_fill = "#808080"
    lane_mid = None
    ROW_BEATS = 16.0       # 1行に入る最大拍数 (4/4小節が4つ分 = 16拍)
    target_course = next((c for c in courses if c["label"] == selected_label), None)
    if target_course is None:
        raise ValueError("コースが見つかりません。")

    level, balloon_defs = 0, []
    for l in content.split('\n'):
        if l.startswith("LEVEL:"):
            try:
                level = int(l[6:].strip())
            except Exception:
                pass
        if l.startswith("BALLOON:"):
            balloon_defs = [int(x.strip()) for x in l[8:].split(',') if x.strip().isdigit()]

    # 見出しの高さを決めるのに、先にサブタイトルの有無だけ見ておく。
    _has_subtitle = any(l.startswith("SUBTITLE:") and l[9:].strip().lstrip("-+")
                        for l in content.split(chr(10)))

    gogo_active = False
    # #BARLINEOFF / #BARLINEON。小節線を引くかどうか(利用者の報告
    # 2026-11-04、tja-tools と見比べて分かった)。今まで見ていなかったので、
    # 小節線を消してある所にも白い線が引かれていた。
    barline_on = True
    # 譜面分岐(#BRANCHSTART 〜 #N/#E/#M 〜 #BRANCHEND)に対応する。
    # 分岐区間では 普通(N)/玄人(E)/達人(M) が同じ時間を共有するので、区間ごとに
    # 3系統へ振り分けて集め、あとで「同じ位置の小節」を1スロットにまとめる。
    # 分岐外の小節は branch=None の単独スロットになる。
    cur_m_lines = []
    slots = []                      # [{'variants': {branch: measure_lines}}]
    branch_buf = {}                 # 分岐区間中: {'N': [...], 'E': [...], 'M': [...]}
    cur_branch = None
    in_branch = False

    def _flush_branch():
        """集めた分岐3系統を、位置ごとのスロットに変換して slots へ積む。"""
        nonlocal branch_buf
        if branch_buf:
            n = max(len(v) for v in branch_buf.values())
            for k in range(n):
                variants = {}
                for br, lst in branch_buf.items():
                    if k < len(lst):
                        variants[br] = lst[k]
                if variants:
                    slots.append({'variants': variants})
        branch_buf = {}

    for line in target_course["data"]:
        line = line.split("//")[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            head = line.split()[0]
            if head in ("#START", "#END"):
                # 構造上の目印であって小節の中身ではない。以前はこれを命令として
                # 溜め込むため、#END だけを含む「空の小節」が末尾に1つ増えていた。
                continue
            if head == "#BRANCHSTART":
                # 実譜面では #BRANCHEND を書かず、次の #BRANCHSTART で暗黙に
                # 前の分岐区間を閉じる書き方が普通にある(まださいたま2000 等)。
                # ここで閉じずに捨てていたため、その区間の譜面が丸ごと消えていた。
                if cur_m_lines:
                    if in_branch and cur_branch:
                        branch_buf.setdefault(cur_branch, []).append(cur_m_lines)
                    else:
                        slots.append({'variants': {None: cur_m_lines}})
                    cur_m_lines = []
                if in_branch:
                    _flush_branch()
                in_branch = True
                cur_branch = None
                branch_buf = {}
                continue
            if head in ("#N", "#E", "#M") and in_branch:
                if cur_m_lines:
                    # 直前のブランチの最後の小節(カンマ無しで終わった分)を確定
                    branch_buf.setdefault(cur_branch or 'N', []).append(cur_m_lines)
                    cur_m_lines = []
                cur_branch = head[1]
                branch_buf.setdefault(cur_branch, [])
                continue
            if head == "#BRANCHEND":
                if cur_m_lines and cur_branch:
                    branch_buf.setdefault(cur_branch, []).append(cur_m_lines)
                    cur_m_lines = []
                _flush_branch()
                in_branch = False
                cur_branch = None
                continue
            cur_m_lines.append(('cmd', line))
        else:
            parts = line.split(',')
            for i, part in enumerate(parts):
                if part:
                    cur_m_lines.append(('notes', part))
                if i < len(parts) - 1:
                    if in_branch and cur_branch:
                        branch_buf.setdefault(cur_branch, []).append(cur_m_lines)
                    else:
                        slots.append({'variants': {None: cur_m_lines}})
                    cur_m_lines = []
    if cur_m_lines:
        if in_branch and cur_branch:
            branch_buf.setdefault(cur_branch, []).append(cur_m_lines)
        else:
            slots.append({'variants': {None: cur_m_lines}})
    if in_branch:
        _flush_branch()

    # #END 直前などで生まれる「中身の無い最後のスロット」は小節として数えない
    # (放置すると存在しない小節が1つ余計に描かれる)。
    while slots and not any(
            any(t == 'notes' and v for t, v in lines)
            or any(t == 'cmd' for t, v in lines)
            for lines in slots[-1]['variants'].values()):
        slots.pop()

    # 既存処理との互換: 代表系統(達人>玄人>普通の順で存在するもの)を raw_measures と
    # して扱い、時間・小節番号はこの代表で進める(分岐の各系統は同じ時間を占める)。
    BRANCH_ORDER = ('N', 'E', 'M')
    raw_measures = []
    for sl in slots:
        v = sl['variants']
        rep = v.get(None)
        if rep is None:
            for br in ('M', 'E', 'N'):
                if br in v:
                    rep = v[br]; break
        raw_measures.append(rep or [])

    # -------------------------------------------------------------
    # 1. タイムライン解析 (小節を4分音符を1とする「拍(beat)」単位に変換)
    # -------------------------------------------------------------
    parsed_measures = []
    current_num, current_den = 4, 4
    total_beats = 0.0

    for m_idx, rm in enumerate(raw_measures):
        total_notes = sum(len(val) for type_, val in rm if type_ == 'notes')
        note_idx = 0

        m_data = {
            'idx': m_idx,
            'notes': [],
            'commands': [],
            'gogo_start': gogo_active,
            'gogo_toggles': [],
            # その小節の**頭**で小節線を引くか。命令は小節の途中にも書けるが、
            # 効くのは次の小節線からなので、頭の状態だけ見れば足りる。
            'barline': barline_on
        }

        for type_, val in rm:
            frac = note_idx / total_notes if total_notes > 0 else 0.0
            if type_ == 'cmd':
                if val.startswith("#MEASURE"):
                    m = re.search(r"(\d+)/(\d+)", val)
                    # 分母0(#MEASURE 4/0 等の書きかけ)は無視して直前の拍子を維持。
                    # tja_analyzer 側と同じ扱い。無いと length_beats 計算で
                    # ZeroDivisionError になり書き出しが例外を出す。
                    if m and int(m.group(2)) != 0:
                        current_num, current_den = int(m.group(1)), int(m.group(2))
                elif val.startswith("#GOGOSTART"):
                    gogo_active = True
                    m_data['gogo_toggles'].append((frac, True))
                elif val.startswith("#GOGOEND"):
                    gogo_active = False
                    m_data['gogo_toggles'].append((frac, False))
                elif val.startswith("#BARLINEOFF"):
                    barline_on = False
                    # 小節の頭に書いてあれば、**その小節の線から**効く。
                    if frac <= 1e-9:
                        m_data['barline'] = False
                elif val.startswith("#BARLINEON"):
                    barline_on = True
                    if frac <= 1e-9:
                        m_data['barline'] = True
                elif val.startswith("#BPMCHANGE"):
                    try:
                        m_data['commands'].append(
                            (frac, "BPM%g" % float(val.split()[1]), "#0088ff"))
                    except Exception:
                        pass
                elif val.startswith("#SCROLL"):
                    try:
                        m_data['commands'].append(
                            (frac, "HS%g" % float(val.split()[1]), "#ff0000"))
                    except Exception:
                        pass
            elif type_ == 'notes':
                for ch in val:
                    frac = note_idx / total_notes if total_notes > 0 else 0.0
                    if ch in "123456789":
                        m_data['notes'].append((frac, ch))
                    if ch in "0123456789":
                        note_idx += 1

        # 分岐の各系統(N/E/M)の音符も個別に取り出しておく(描画で3段に分ける)。
        # 位置(frac)の求め方は上の代表系統と同じ。
        variants = slots[m_idx]['variants'] if m_idx < len(slots) else {None: rm}
        m_data['is_branch'] = (None not in variants)
        bnotes = {}
        for _br, _lines in variants.items():
            _tot = sum(len(v) for t, v in _lines if t == 'notes')
            _ni, _lst = 0, []
            for _t, _v in _lines:
                if _t != 'notes':
                    continue
                for _ch in _v:
                    _fr = _ni / _tot if _tot > 0 else 0.0
                    if _ch in "123456789":
                        _lst.append((_fr, _ch))
                    if _ch in "0123456789":
                        _ni += 1
            bnotes[_br] = _lst
        m_data['bnotes'] = bnotes

        m_data['num'] = current_num
        m_data['den'] = current_den
        m_data['length_beats'] = current_num * 4 / current_den
        m_data['start_beat'] = total_beats
        total_beats += m_data['length_beats']

        parsed_measures.append(m_data)

    # -------------------------------------------------------------
    # 1a. 小節範囲の切り出し(指定があれば)
    #     measure_from/measure_to は 1 始まり・両端を含む。範囲外の小節を捨て、
    #     残った先頭が拍0になるよう start_beat を寄せ直す。BALLOON: の割り当ては
    #     曲全体での出現順なので、範囲より前にある風船/くす玉の数だけ開始番号を
    #     ずらして、打数が食い違わないようにする。
    # -------------------------------------------------------------
    balloon_skip = 0
    # 0 は「指定なし」ではなく「範囲外の指定」。or で見ると 0 が falsy なので
    # 0..0 を渡したときだけ絞り込みが丸ごと飛んで全体が出ていた。
    if parsed_measures and (measure_from is not None or measure_to is not None):
        n_m = len(parsed_measures)
        lo = max(1, int(measure_from if measure_from is not None else 1))
        hi = min(n_m, int(measure_to if measure_to is not None else n_m))
        hi = max(1, hi)
        if lo > hi:
            lo, hi = hi, lo
        lo_i, hi_i = lo - 1, hi - 1
        for m in parsed_measures[:lo_i]:
            balloon_skip += sum(1 for _f, c in m['notes'] if c in '79')
        sel = parsed_measures[lo_i:hi_i + 1]
        if sel:
            base = sel[0]['start_beat']
            for m in sel:
                m['start_beat'] -= base
            parsed_measures = sel
            total_beats = sel[-1]['start_beat'] + sel[-1]['length_beats']

    # -------------------------------------------------------------
    # 1b. 小節ごとの行(row)割り当て
    # -------------------------------------------------------------
    _EPS = 1e-6
    # tja-tools 風では**どんな長さの小節も行の途中で割らない**。
    # 入らなければ丸ごと次の行へ送る(向こうの drawChart.js の
    # 「whole-measure wrap」。1行に入りきらない小節だけが割れる)。
    # そのぶん行数は増えるが、8/4 の小節が行の端で切れなくなる。
    _protect = float("inf")
    # 向こうの判定は `>= ttRowBeat + 1/BEAT_WIDTH`。ちょうど収まる小節を
    # 送ってしまわないための遊び(1px ぶん)。
    _wrap_tol = 1.0 / 48.0
    _row, _row_used = 0, 0.0
    for m in parsed_measures:
        length = m['length_beats']
        if _row_used >= ROW_BEATS - _EPS:
            _row += 1
            _row_used = 0.0
        elif (length <= _protect + _EPS and _row_used > _EPS
                and _row_used + length >= ROW_BEATS + _wrap_tol):
            _row += 1
            _row_used = 0.0
        m['row'] = _row
        m['row_offset'] = _row_used
        _row_used += length
        while _row_used > ROW_BEATS + _EPS:
            _row_used -= ROW_BEATS
            _row += 1

    measure_starts = [m['start_beat'] for m in parsed_measures]
    total_row_units = (
        parsed_measures[-1]['row'] * ROW_BEATS + parsed_measures[-1]['row_offset'] + parsed_measures[-1]['length_beats']
        if parsed_measures else 0.0
    )

    def row_units(beat):
        """Maps an absolute beat position to the equivalent continuous
        position under the packed-row layout, so it can be fed straight into
        get_row_x()/draw_band() unchanged."""
        if not parsed_measures:
            return beat
        i = bisect.bisect_right(measure_starts, beat) - 1
        i = max(0, min(i, len(parsed_measures) - 1))
        m = parsed_measures[i]
        return m['row'] * ROW_BEATS + m['row_offset'] + (beat - m['start_beat'])

    # -------------------------------------------------------------
    # 2. イベントの絶対座標(beat)化
    # -------------------------------------------------------------
    events_cmd = []
    events_note = []
    events_roll = []
    active_rolls = {}          # branch(None/'N'/'E'/'M') -> 進行中の連打

    # 曲頭の BPM。tja-tools は1小節目にも出すので揃える。
    # ヘッダの BPM: をそのまま使う。
    _b0 = next((l[4:].strip() for l in content.split(chr(10))
                if l.startswith("BPM:")), "")
    try:
        events_cmd.append({'beat': 0.0,
                           'text': "BPM%g" % float(_b0),
                           'color': "#0088ff"})
    except ValueError:
        pass

    for m in parsed_measures:
        m_start = m['start_beat']
        m_len = m['length_beats']

        for frac, cmd_str, color in m['commands']:
            b = m_start + frac * m_len
            events_cmd.append({'beat': b, 'text': cmd_str, 'color': color})

        # 分岐がある小節は系統ごとに、無い小節は単独(branch=None)で積む。
        for br, blist in m['bnotes'].items():
            ar = active_rolls.get(br)
            for frac, c in blist:
                b = m_start + frac * m_len
                if c in '567':
                    ar = {'r_type': c, 'start_beat': b}
                    active_rolls[br] = ar
                    events_note.append({'beat': b, 'note': c, 'branch': br})
                elif c == '8' and ar:
                    events_roll.append({'r_type': ar['r_type'], 'start_beat': ar['start_beat'],
                                        'end_beat': b, 'branch': br})
                    active_rolls[br] = ar = None
                elif c in '12349':
                    events_note.append({'beat': b, 'note': c, 'branch': br})

    for _br, _ar in active_rolls.items():
        if _ar:
            events_roll.append({'r_type': _ar['r_type'], 'start_beat': _ar['start_beat'],
                                'end_beat': total_beats, 'branch': _br})

    # ゴーゴータイム区間の抽出
    gogo_regions = []
    current_gogo = None
    for m in parsed_measures:
        if m['gogo_start'] and current_gogo is None:
            current_gogo = m['start_beat']
        for frac, state in m['gogo_toggles']:
            b = m['start_beat'] + frac * m['length_beats']
            if state and current_gogo is None:
                current_gogo = b
            elif not state and current_gogo is not None:
                gogo_regions.append((current_gogo, b))
                current_gogo = None
    if current_gogo is not None:
        gogo_regions.append((current_gogo, total_beats))

    # 風船の打数を割り当て
    balloon_idx = balloon_skip
    for n in events_note:
        if n['note'] == '7':
            n['hits'] = balloon_defs[balloon_idx] if balloon_idx < len(balloon_defs) else 0
            balloon_idx += 1

    # 左から重ねるため、時間を反転
    events_note.sort(key=lambda n: n['beat'], reverse=True)

    # -------------------------------------------------------------
    # 3. 描画設定と画像生成
    # -------------------------------------------------------------
    sc = max(0.25, float(scale or 1.0))
    # tja-tools の drawChart.js の数字そのもの。
    #   BEAT_WIDTH 48 / ROW_HEIGHT_NOTE 32 / ROW_HEIGHT_INFO 18
    #   ROW_MARGIN_BOTTOM 14 / ROW_LEADING,TRAILING 24
    #   CHART_PADDING_TOP 62 / CHART_PADDING_BOTTOM -14
    #   CHART_BG #cccccc / ROW_COLOR['N'] #808080 / COLOR_GOGO #ffc0c0
    # 16拍 × 48 + 24 + 24 = 816px。向こうの出力と同じ幅になる。
    BEAT_WIDTH = 48
    LANE_HEIGHT = 32
    GOGO_BAND_HEIGHT = 18          # ROW_HEIGHT_INFO
    ROW_SPACING = LANE_HEIGHT + GOGO_BAND_HEIGHT + 14
    MARGIN_X = 24
    # tja-tools の CHART_PADDING_TOP(最低 62)。サブタイトルがある曲は
    # その行のぶんだけ伸びる。
    #
    # 向こうの GET_ROW_Y は「行の上端(= 帯の上端)」を指すが、こちらの
    # MARGIN_Y / row_tops は **レーンの上端** を指す。帯のぶん(18)を
    # 足して、同じ所にレーンが来るようにする。
    MARGIN_Y = 62 + 18 + (21 if _has_subtitle else 0)
    # 最後の行の下は、帯(18)と行間(14)のぶんを削る。向こうの
    # CHART_PADDING_BOTTOM -14 と合わせると同じ高さになる。
    ROW_BOTTOM_TRIM = 18 + 14
    BG_COLOR = "#cccccc"
    GOGO_COLOR = "#ffc0c0"
    GRID_LIGHT, GRID_DARK = "#b4b4b4", "#9a9a9a"
    BEAT_WIDTH *= sc
    LANE_HEIGHT = int(round(LANE_HEIGHT * sc))
    ROW_SPACING = int(round(ROW_SPACING * sc))
    MARGIN_X = int(round(MARGIN_X * sc))
    MARGIN_Y = int(round(MARGIN_Y * sc))
    GOGO_BAND_HEIGHT = int(round(GOGO_BAND_HEIGHT * sc))
    ROW_BOTTOM_TRIM = int(round(ROW_BOTTOM_TRIM * sc))

    total_rows = int((total_row_units - 1e-6) // ROW_BEATS) + 1 if total_row_units > 0 else 1
    img_width = MARGIN_X * 2 + int(ROW_BEATS * BEAT_WIDTH)

    # --- 譜面分岐の段組み ---------------------------------------------
    # 分岐小節を含む行は 普通(N)/玄人(E)/達人(M) の3レーンを縦に積むので、行ごとに
    # 高さが変わる。行の上端は cumulative に持つ(以前は r*ROW_SPACING 固定だった)。
    BRANCH_ORDER = ('N', 'E', 'M')
    BRANCH_LABEL = {'N': '普通', 'E': '玄人', 'M': '達人'}
    # tja-tools の ROW_COLOR。普通/玄人/達人 でレーンの色が変わる。
    BRANCH_TINT = {'N': "#808080", 'E': "#609f9f", 'M': "#9f6060"}
    row_branch = [False] * (total_rows + 1)
    for m in parsed_measures:
        if m.get('is_branch'):
            # その小節が実際に占める行だけを3段にする(以前は前後の行まで巻き込んで
            # いたので、分岐が無い行にまで3段レーンが出ていた)。
            ru0 = m['row'] * ROW_BEATS + m['row_offset']
            ru1 = ru0 + m['length_beats']
            r0 = int(ru0 // ROW_BEATS)
            r1 = int((ru1 - 1e-6) // ROW_BEATS)
            for rr in range(max(0, r0), min(total_rows - 1, max(r0, r1)) + 1):
                row_branch[rr] = True
    row_lanes = [3 if row_branch[r] else 1 for r in range(total_rows + 1)]
    row_tops = []
    _y = MARGIN_Y
    for r in range(total_rows):
        row_tops.append(_y)
        _y += (ROW_SPACING - LANE_HEIGHT) + LANE_HEIGHT * row_lanes[r]
    row_tops.append(_y)
    img_height = max(1, _y - ROW_BOTTOM_TRIM)

    def lane_y(r, branch=None):
        """行 r・系統 branch のレーン上端 y。分岐でない行/系統は先頭レーン。"""
        top = row_tops[r] if r < len(row_tops) else MARGIN_Y
        if branch in BRANCH_ORDER and row_lanes[r] == 3:
            return top + BRANCH_ORDER.index(branch) * LANE_HEIGHT
        return top

    def row_height(r):
        return LANE_HEIGHT * (row_lanes[r] if r < len(row_lanes) else 1)

    img = Image.new("RGB", (img_width, img_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    def _f(px, bold=True):
        return get_font(max(6, int(round(px * sc))), bold=bold)

    def _hdr(tag):
        for l in content.split(chr(10)):
            if l.startswith(tag):
                return l[len(tag):].strip()
        return ""

    title = _hdr("TITLE:") or "No Title"
    subtitle = _hdr("SUBTITLE:")
    # SUBTITLE は `--` や `++` で始めて「字幕の出し方」を指示する慣習がある。
    # 見出しに出すのは中身だけ。
    if subtitle[:2] in ("--", "++"):
        subtitle = subtitle[2:]

    # tja-tools の見出し。大きさと位置は向こうの src/js/font/font.js の
    # Windows の設定そのもの(利用者の希望 2026-11-04「フォントも
    # 合わせて」):
    #   題名   bold 21px Arial / MS UI Gothic   x=11  y=10
    #   副題   bold 17px 同上                   x=11  y=36
    #   難易度 bold 17px 同上                   x=10  y=62(副題あり)
    #                                                 41(副題なし)
    # この積み上げが CHART_PADDING_TOP 62/83 の正体でもある。
    font_cmd = font_balloon = _f(7, False)
    lv = max(0, min(10, level))
    stars = "★" * lv + "☆" * (10 - lv)

    def _px(v):
        return int(round(v * sc))

    draw_header_text(draw, _px(11), _px(10), title, "#000000", 21 * sc)
    if subtitle:
        draw_header_text(draw, _px(11), _px(36), subtitle, "#000000",
                         17 * sc)
    draw_header_text(draw, _px(10), _px(62 if subtitle else 41),
                     "%s %s Lv.%d" % (selected_label, stars, level),
                     "#000000", 17 * sc)

    # -------------------------------------------------------------
    # 4. 描画ユーティリティ
    # -------------------------------------------------------------
    def get_row_x(beat):
        r = int(beat // ROW_BEATS)
        rem = beat - r * ROW_BEATS
        return r, MARGIN_X + rem * BEAT_WIDTH

    def draw_band(start_b, end_b, draw_func):
        # 行をまたぐ帯の描画を自動分割
        s_row = int(start_b // ROW_BEATS)
        e_row = int((end_b - 1e-6) // ROW_BEATS)
        if e_row < s_row:
            e_row = s_row

        for r in range(s_row, e_row + 1):
            row_start_b = max(start_b, r * ROW_BEATS)
            row_end_b = min(end_b, (r + 1) * ROW_BEATS)

            sx = MARGIN_X + (row_start_b - r * ROW_BEATS) * BEAT_WIDTH
            ex = MARGIN_X + (row_end_b - r * ROW_BEATS) * BEAT_WIDTH
            ly = row_tops[r] if r < len(row_tops) else MARGIN_Y

            draw_func(r, sx, ex, ly, row_start_b == start_b, row_end_b == end_b)

    #: tja-tools のレーンの縁。32px の帯を上から
    #: 黒2 / 白2 / #d4d4d4 1 / #aaaaaa 1 / 地 20 / #aaaaaa 1 / #d4d4d4 1 /
    #: 白2 / 黒2 と塗り分けてある(向こうの出力を1pxずつ読んで採った)。
    #: 下半分は上下対称。
    LANE_EDGE = ((2, "#000000"), (2, "#ffffff"), (1, "#d4d4d4"), (1, "#aaaaaa"))

    def draw_lane(ly, fill):
        """レーンを1本(縁取り付き)。"""
        # PIL の rectangle は**両端を含む**。ちょうど LANE_HEIGHT px に
        # するため下端を1つ手前で止める(でないと1px はみ出す)。
        draw.rectangle([0, ly, img_width, ly + LANE_HEIGHT - 1], fill=fill)
        top = ly
        bot = ly + LANE_HEIGHT
        for th, color in LANE_EDGE:
            h = max(1, int(round(th * sc)))
            draw.rectangle([0, top, img_width, top + h - 1], fill=color)
            draw.rectangle([0, bot - h, img_width, bot - 1], fill=color)
            top += h
            bot -= h

    # --- A. 背景とレーン ---
    for r in range(total_rows):
        if row_lanes[r] == 3:
            # 分岐行: 普通/玄人/達人 の3レーンを積み、系統ごとに色味と見出しを付ける。
            for bi, br in enumerate(BRANCH_ORDER):
                ly = row_tops[r] + bi * LANE_HEIGHT
                draw_lane(ly, BRANCH_TINT[br])
                if lane_mid:
                    draw.line([0, ly + LANE_HEIGHT / 2,
                               img_width, ly + LANE_HEIGHT / 2],
                              fill=lane_mid, width=1)
                draw.text((4, ly + 2), BRANCH_LABEL[br], fill="#ffffff", font=font_cmd)
        else:
            ly = row_tops[r]
            draw_lane(ly, lane_fill)
            if lane_mid:
                draw.line([0, ly + LANE_HEIGHT / 2,
                           img_width, ly + LANE_HEIGHT / 2],
                          fill=lane_mid, width=1)

    # --- B. ゴーゴータイム ---
    def draw_gogo(r, sx, ex, ly, is_first, is_last):
        if not is_first and sx <= MARGIN_X + 1e-6:
            sx = 0
        if not is_last and ex >= MARGIN_X + ROW_BEATS * BEAT_WIDTH - 1e-6:
            ex = img_width
        draw.rectangle([sx, ly - GOGO_BAND_HEIGHT, ex, ly], fill=GOGO_COLOR)

    for gb_start, gb_end in gogo_regions:
        draw_band(row_units(gb_start), row_units(gb_end), draw_gogo)

    # --- C. グリッド線 ---
    for m in parsed_measures:
        num, den = m['num'], m['den']
        div = (num * 2) if den % 4 == 0 else den
        step = m['length_beats'] / div
        for i in range(1, div):
            b = m['start_beat'] + i * step
            r, x = get_row_x(row_units(b))
            ly = row_tops[r] if r < len(row_tops) else MARGIN_Y
            color = GRID_LIGHT if (den % 4 == 0 and i % 2 != 0) else GRID_DARK
            draw.line([x, ly, x, ly + row_height(r)], fill=color, width=1)

    # --- D. コマンド線とテキスト (階段状) ---
    row_cmds = {r: [] for r in range(total_rows + 1)}
    for cmd in events_cmd:
        r, x = get_row_x(row_units(cmd['beat']))
        row_cmds[r].append((x, cmd['text'], cmd['color']))

    #: tja-tools の帯(ROW_HEIGHT_INFO=18)の中の段。レーンの上端から上へ
    #: 何 px かで、字の高さは5px。上から HS / BPM / 小節番号 の3段に分けて
    #: あるので、**どれも重ならない**(利用者の報告 2026-11-04
    #: 「小節番号と命令が重なってる」)。
    BAND_HS, BAND_BPM, BAND_NUM = 18, 12, 6
    #: 同じ所に命令がいくつも来たときに、上へずらす幅。
    BAND_STEP = 6

    def band_y(band, up=0):
        return ly - int(round((band + up * BAND_STEP) * sc))

    for r in range(total_rows):
        cmds = sorted(row_cmds[r], key=lambda c: c[0])
        ly = row_tops[r] if r < len(row_tops) else MARGIN_Y
        # tja-tools と同じ積み方。**横にはずらさない。** 同じ位置に
        # 重なったぶんだけ上へ 6px ずつ積む(向こうの scrollCount)。
        # BPM は BPM の段、それ以外は HS の段。
        stack = {}
        for x, text, _color in cmds:
            key = int(round(x))
            is_bpm = text.startswith("BPM")
            band = BAND_BPM if is_bpm else BAND_HS
            up = stack.get((key, band), 0)
            stack[(key, band)] = up + 1
            y0 = band_y(band, up)
            # 命令の縦線は、いちばん上に積んだ字の高さまで伸ばす。
            draw.line([x, y0, x, ly + row_height(r)],
                      fill="#444444", width=max(1, int(round(sc))))
            draw_pix_text(draw, x, y0, text,
                          PIX_COLOR_BPM if is_bpm else PIX_COLOR_HS, sc)

    # --- E. 小節線（白太線） ---
    #: 小節線の太さ。tja-tools は 2px で、**線の中心が x** に来る
    #: (canvas の drawLine と同じ)。PIL の line は x から右へ伸びるので、
    #: 半分ぶん左へ寄せないと1px ずれる。
    BAR_W = max(1, int(round(2 * sc)))

    def draw_barline(x, ly, h):
        # **先に丸めてから寄せる。** 切り捨てだと 1/24 や 1/32 の小節で
        # 1px 手前に出て、tja-tools とずれる。
        x0 = int(round(x)) - BAR_W // 2
        draw.line([x0, ly, x0, ly + h], fill="#ffffff", width=BAR_W)

    for _mi, m in enumerate(parsed_measures):
        ru = m['row'] * ROW_BEATS + m['row_offset']
        r, x = get_row_x(ru)
        ly = row_tops[r] if r < len(row_tops) else MARGIN_Y

        # **#BARLINEOFF の所には線を引かない。** 小節番号は出す
        # (tja-tools も番号だけは出す)。
        if m.get('barline', True):
            draw_barline(x, ly, row_height(r))
        # 帯のいちばん下の段(tja-tools の ROW_HEIGHT_INFO - 6)。
        ny = ly - int(round(BAND_NUM * sc))
        draw_pix_text(draw, x, ny, str(m['idx'] + 1), PIX_COLOR_NUM, sc)

        # **行の終わりを閉じる線。** 次の小節が別の行へ送られたときは、
        # この行の末尾にも小節線が要る(tja-tools も引いている)。前は
        # 「ちょうど16拍で終わるとき」しか引いていなかったので、小節が
        # 入りきらずに送られた行の右端が開いたままだった。
        nxt = (parsed_measures[_mi + 1]
               if _mi + 1 < len(parsed_measures) else None)
        if nxt is not None and nxt['row'] != m['row'] and nxt.get('barline', True):
            end_off = m['row_offset'] + m['length_beats']
            r_end = m['row']
            x_end = MARGIN_X + min(end_off, ROW_BEATS) * BEAT_WIDTH
            ly_end = row_tops[r_end] if r_end < len(row_tops) else MARGIN_Y
            draw_barline(x_end, ly_end, row_height(r_end))

    # 曲の最後の小節線
    if total_row_units > 0:
        r, x = get_row_x(total_row_units)
        if abs(total_row_units % ROW_BEATS) < 1e-6:
            r = int((total_row_units - 1e-6) // ROW_BEATS)
            x = MARGIN_X + ROW_BEATS * BEAT_WIDTH
        ly = row_tops[r] if r < len(row_tops) else MARGIN_Y
        if parsed_measures[-1].get('barline', True):
            draw_barline(x, ly, row_height(r))

    # --- F. 連打帯 ---
    def draw_roll(r, sx, ex, ly, is_first, is_last, color, thick, branch=None):
        # 分岐行では系統ごとのレーンへ(ly は行の上端なのでレーン分だけ下げる)。
        cy = lane_y(r, branch) + LANE_HEIGHT / 2
        # tja-tools の連打は**音符と同じ2重の縁**が付いた帯。
        # 行をまたぐ側は丸めずに済むよう、画像の外へはみ出させる。
        # 帯の上下に黒い線は引かない — 向こうの連打はただの丸い黄色い帯で、
        # 線が横切って見えるのが目立つため(利用者の指定 2026-11-04)。
        x0 = (sx - thick / 2.0) if is_first else (sx - thick)
        x1 = (ex + thick / 2.0) if is_last else (ex + thick)
        tile = tjat_capsule(x1 - x0, thick, color, max(1, int(round(sc))))
        img.paste(tile, (int(round(x0)), int(round(cy - thick / 2.0))), tile)

    for roll in events_roll:
        # 向こうの notes-rollMiddle.png / notes-spRollMiddle.png の色。
        color = TJAT_ROLL if roll['r_type'] in '56' else TJAT_BALLOON
        # 帯の太さは音符の直径に合わせる(倍率にも付いていく)。大連打と
        # 風船・くす玉は大音符と同じ(24)、ふつうの連打は音符と同じ(18)。
        thick = int(round((18 if roll['r_type'] == '5' else 24) * sc))
        _br = roll.get('branch')
        draw_band(row_units(roll['start_beat']), row_units(roll['end_beat']),
                  lambda r, sx, ex, ly, first, last, _c=color, _t=thick, _b=_br:
                      draw_roll(r, sx, ex, ly, first, last, _c, _t, _b))

    # --- G. ノーツ（左から重なる） ---
    for n in events_note:
        r, x = get_row_x(row_units(n['beat']))
        y = lane_y(r, n.get('branch')) + LANE_HEIGHT / 2
        nt = n['note']
        # tja-tools の NOTE_RADIUS は 9。大音符はその 1.44 倍ぐらい。
        # tja-tools の素材は小音符が直径18、**大音符は直径24**(半径12)。
        # 13 にしていたので大音符だけ一回り太かった。
        r_sm, r_bg = (max(2, int(round(9 * sc))),
                      max(3, int(round(12 * sc))))

        if nt in sprites or (nt in '56' and f"{nt}_head" in sprites):
            spr = sprites.get(f"{nt}_head" if nt in '56' else nt)
            target_w = (r_bg * 2 + 4 if nt in '3469' else r_sm * 2 + 4)
            if nt in '79':
                target_w = int(target_w * 1.5)
                spr = spr.resize((target_w, int(target_w * 0.8)),
                                 Image.Resampling.LANCZOS)
                img.paste(spr, (int(x - spr.width / 2),
                                int(y - spr.height / 2)), spr)
            else:
                spr = spr.resize((target_w, target_w), Image.Resampling.LANCZOS)
                img.paste(spr, (int(x - spr.width / 2), int(y - spr.height / 2)), spr)
            if nt == '7' and n.get('hits', 0) > 0:
                hits = n['hits']
                _hr = max(5, int(round(10 * sc)))
                draw.ellipse([x - _hr, y - _hr, x + _hr, y + _hr],
                             fill="#ffffff", outline="#000000",
                             width=max(1, int(round(sc))))
                draw.text((x, y), str(hits), fill="#000000",
                          font=font_balloon, anchor="mm")
        else:
            if nt in '12345679':
                r_radius = r_bg if nt in '3469' else r_sm
                ring_w = max(1, int(round(sc)))
                # tja-tools の音符。**白フチではなく外が黒・内が灰**の
                # 2重の縁(利用者の報告 2026-11-04「音符の見た目が
                # おかしい」)。色も向こうの素材から取った。
                if nt in '79':
                    # 風船・くす玉の玉は大音符と同じ大きさ(直径24)。
                    tile = tjat_balloon_tile(int(round(24 * sc)), ring_w)
                else:
                    fill_c = {"1": TJAT_DON, "2": TJAT_KA, "3": TJAT_DON,
                              "4": TJAT_KA, "5": TJAT_ROLL,
                              "6": TJAT_ROLL}[nt]
                    tile = tjat_note_tile(r_radius * 2, fill_c, ring_w)
                img.paste(tile,
                          (int(round(x - tile.width / 2.0)),
                           int(round(y - tile.height / 2.0))), tile)
                if nt == '7' and n.get('hits', 0) > 0:
                    hits = str(n['hits'])
                    # 向こうは白丸を敷かず、玉の上に黒い点字で打数を
                    # 置くだけ(drawBalloonSprite 参照)。
                    draw_pix_text(draw,
                                  int(round(x - pix_text_width(hits, sc)
                                            / 2.0)),
                                  int(round(y - 3 * sc)),
                                  hits, PIX_COLOR_NUM, sc)

    return img
