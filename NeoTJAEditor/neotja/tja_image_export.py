import os
import re

from PIL import Image, ImageDraw, ImageFont


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


def generate_chart_image(content: str, selected_label: str, courses: list, sprites: dict = None) -> Image.Image:
    """Renders a static timeline/score-sheet PNG for one course of a TJA file.

    `courses` is the output of TJACourseAnalyzer.parse_courses(content).
    """
    sprites = sprites or {}
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

    gogo_active = False
    raw_measures, cur_m_lines = [], []
    for line in target_course["data"]:
        line = line.split("//")[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            cur_m_lines.append(('cmd', line))
        else:
            parts = line.split(',')
            for i, part in enumerate(parts):
                if part:
                    cur_m_lines.append(('notes', part))
                if i < len(parts) - 1:
                    raw_measures.append(cur_m_lines)
                    cur_m_lines = []
    if cur_m_lines:
        raw_measures.append(cur_m_lines)

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
            'gogo_toggles': []
        }

        for type_, val in rm:
            frac = note_idx / total_notes if total_notes > 0 else 0.0
            if type_ == 'cmd':
                if val.startswith("#MEASURE"):
                    m = re.search(r"(\d+)/(\d+)", val)
                    if m:
                        current_num, current_den = int(m.group(1)), int(m.group(2))
                elif val.startswith("#GOGOSTART"):
                    gogo_active = True
                    m_data['gogo_toggles'].append((frac, True))
                elif val.startswith("#GOGOEND"):
                    gogo_active = False
                    m_data['gogo_toggles'].append((frac, False))
                elif val.startswith("#BPMCHANGE"):
                    try:
                        m_data['commands'].append((frac, f"BPM {float(val.split()[1]):g}", "#0088ff"))
                    except Exception:
                        pass
                elif val.startswith("#SCROLL"):
                    try:
                        m_data['commands'].append((frac, f"HS {float(val.split()[1]):g}", "#ff0000"))
                    except Exception:
                        pass
            elif type_ == 'notes':
                for ch in val:
                    frac = note_idx / total_notes if total_notes > 0 else 0.0
                    if ch in "123456789":
                        m_data['notes'].append((frac, ch))
                    if ch in "0123456789":
                        note_idx += 1

        m_data['num'] = current_num
        m_data['den'] = current_den
        m_data['length_beats'] = current_num * 4 / current_den
        m_data['start_beat'] = total_beats
        total_beats += m_data['length_beats']

        parsed_measures.append(m_data)

    # -------------------------------------------------------------
    # 2. イベントの絶対座標(beat)化
    # -------------------------------------------------------------
    events_cmd = []
    events_note = []
    events_roll = []
    active_roll = None

    for m in parsed_measures:
        m_start = m['start_beat']
        m_len = m['length_beats']

        for frac, cmd_str, color in m['commands']:
            b = m_start + frac * m_len
            events_cmd.append({'beat': b, 'text': cmd_str, 'color': color})

        for frac, c in m['notes']:
            b = m_start + frac * m_len
            if c in '567':
                active_roll = {'r_type': c, 'start_beat': b}
                events_note.append({'beat': b, 'note': c})
            elif c == '8' and active_roll:
                events_roll.append({'r_type': active_roll['r_type'], 'start_beat': active_roll['start_beat'], 'end_beat': b})
                active_roll = None
            elif c in '12349':
                events_note.append({'beat': b, 'note': c})

    if active_roll:
        events_roll.append({'r_type': active_roll['r_type'], 'start_beat': active_roll['start_beat'], 'end_beat': total_beats})

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
    balloon_idx = 0
    for n in events_note:
        if n['note'] == '7':
            n['hits'] = balloon_defs[balloon_idx] if balloon_idx < len(balloon_defs) else 0
            balloon_idx += 1

    # 左から重ねるため、時間を反転
    events_note.sort(key=lambda n: n['beat'], reverse=True)

    # -------------------------------------------------------------
    # 3. 描画設定と画像生成
    # -------------------------------------------------------------
    BEAT_WIDTH = 340 / 4   # 4分音符1つあたりのピクセル幅 (デフォルト340pxの4/4小節を基準)
    ROW_BEATS = 16.0       # 1行に入る最大拍数 (4/4小節が4つ分 = 16拍)
    LANE_HEIGHT = 46
    ROW_SPACING = 130
    MARGIN_X = 60
    MARGIN_Y = 170
    GOGO_BAND_HEIGHT = 20

    total_rows = int((total_beats - 1e-6) // ROW_BEATS) + 1 if total_beats > 0 else 1
    img_width = MARGIN_X * 2 + int(ROW_BEATS * BEAT_WIDTH)
    img_height = MARGIN_Y + total_rows * ROW_SPACING

    img = Image.new("RGB", (img_width, img_height), "#dbdbdb")
    draw = ImageDraw.Draw(img)

    font_title = get_font(40, bold=True)
    font_diff = get_font(24, bold=True)
    font_cmd = get_font(12, bold=True)
    font_num = get_font(13, bold=True)
    font_balloon = get_font(12, bold=True)

    title = next((l[6:].strip() for l in content.split('\n') if l.startswith("TITLE:")), "No Title")
    draw.text((MARGIN_X, 20), title, fill="#000000", font=font_title)
    draw.text((MARGIN_X, 65), f"{selected_label} {'★' * level}", fill=target_course["color"], font=font_diff)

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
            ly = MARGIN_Y + r * ROW_SPACING

            draw_func(r, sx, ex, ly, row_start_b == start_b, row_end_b == end_b)

    # --- A. 背景とレーン ---
    for r in range(total_rows):
        ly = MARGIN_Y + r * ROW_SPACING
        draw.rectangle([0, ly, img_width, ly + LANE_HEIGHT], fill="#757575")
        draw.line([0, ly + LANE_HEIGHT / 2, img_width, ly + LANE_HEIGHT / 2], fill="#cccccc", width=1)

    # --- B. ゴーゴータイム ---
    def draw_gogo(r, sx, ex, ly, is_first, is_last):
        if not is_first and sx <= MARGIN_X + 1e-6:
            sx = 0
        if not is_last and ex >= MARGIN_X + ROW_BEATS * BEAT_WIDTH - 1e-6:
            ex = img_width
        draw.rectangle([sx, ly - GOGO_BAND_HEIGHT, ex, ly], fill="#ffc2c2")

    for gb_start, gb_end in gogo_regions:
        draw_band(gb_start, gb_end, draw_gogo)

    # --- C. グリッド線 ---
    for m in parsed_measures:
        num, den = m['num'], m['den']
        div = (num * 2) if den % 4 == 0 else den
        step = m['length_beats'] / div
        for i in range(1, div):
            b = m['start_beat'] + i * step
            r, x = get_row_x(b)
            ly = MARGIN_Y + r * ROW_SPACING
            color = "#aaaaaa" if (den % 4 == 0 and i % 2 != 0) else "#888888"
            draw.line([x, ly, x, ly + LANE_HEIGHT], fill=color, width=1)

    # --- D. コマンド線とテキスト (階段状) ---
    row_cmds = {r: [] for r in range(total_rows + 1)}
    for cmd in events_cmd:
        r, x = get_row_x(cmd['beat'])
        row_cmds[r].append((x, cmd['text'], cmd['color']))

    for r in range(total_rows):
        cmds = sorted(row_cmds[r], key=lambda c: c[0])
        last_end_x = [0, 0, 0]
        ly = MARGIN_Y + r * ROW_SPACING
        for x, text, color in cmds:
            try:
                tw = draw.textlength(text, font=font_cmd)
            except Exception:
                tw = len(text) * 7

            level = 0
            if x < last_end_x[0] + 8:
                level = 1
                if x < last_end_x[1] + 8:
                    level = 2

            last_end_x[level] = x + 2 + tw
            cmd_y = ly - 36 - level * 14

            draw.line([x, cmd_y + 6, x, ly + LANE_HEIGHT], fill="#333333", width=2)
            draw.text((x + 2, cmd_y), text, fill=color, font=font_cmd)

    # --- E. 小節線（白太線） ---
    for m in parsed_measures:
        b = m['start_beat']
        r, x = get_row_x(b)
        ly = MARGIN_Y + r * ROW_SPACING

        draw.line([x, ly, x, ly + LANE_HEIGHT], fill="#ffffff", width=3)
        draw.text((x + 4, ly - 18), str(m['idx'] + 1), fill="#000000", font=font_num)

        # 行の右端にぴったり小節線が重なる場合の描画
        end_b = m['start_beat'] + m['length_beats']
        if end_b > 0 and end_b < total_beats and abs(end_b % ROW_BEATS) < 1e-6:
            r_end = int((end_b - 1e-6) // ROW_BEATS)
            x_end = MARGIN_X + ROW_BEATS * BEAT_WIDTH
            ly_end = MARGIN_Y + r_end * ROW_SPACING
            draw.line([x_end, ly_end, x_end, ly_end + LANE_HEIGHT], fill="#ffffff", width=3)

    # 曲の最後の小節線
    if total_beats > 0:
        r, x = get_row_x(total_beats)
        if abs(total_beats % ROW_BEATS) < 1e-6:
            r = int((total_beats - 1e-6) // ROW_BEATS)
            x = MARGIN_X + ROW_BEATS * BEAT_WIDTH
        ly = MARGIN_Y + r * ROW_SPACING
        draw.line([x, ly, x, ly + LANE_HEIGHT], fill="#ffffff", width=3)

    # --- F. 連打帯 ---
    def draw_roll(r, sx, ex, ly, is_first, is_last, color, thick):
        cy = ly + LANE_HEIGHT / 2
        draw.line([sx, cy, ex, cy], fill=color, width=thick)
        if is_first:
            draw.ellipse([sx - thick / 2, cy - thick / 2, sx + thick / 2, cy + thick / 2], fill=color)
        if is_last:
            draw.ellipse([ex - thick / 2, cy - thick / 2, ex + thick / 2, cy + thick / 2], fill=color)
        draw.line([sx, cy - thick / 2, ex, cy - thick / 2], fill="#000000", width=1)
        draw.line([sx, cy + thick / 2, ex, cy + thick / 2], fill="#000000", width=1)

    for roll in events_roll:
        color = "#fcdb38" if roll['r_type'] in '56' else "#ffb74d"
        thick = 34 if roll['r_type'] == '6' else 24
        draw_band(roll['start_beat'], roll['end_beat'], lambda r, sx, ex, ly, first, last: draw_roll(r, sx, ex, ly, first, last, color, thick))

    # --- G. ノーツ（左から重なる） ---
    for n in events_note:
        r, x = get_row_x(n['beat'])
        y = MARGIN_Y + r * ROW_SPACING + LANE_HEIGHT / 2
        nt = n['note']
        r_sm, r_bg = 12, 17

        if nt in sprites or (nt in '56' and f"{nt}_head" in sprites):
            spr = sprites.get(f"{nt}_head" if nt in '56' else nt)
            target_w = (r_bg * 2 + 4 if nt in '3469' else r_sm * 2 + 4)
            if nt in '79':
                target_w = int(target_w * 1.5)
            spr = spr.resize((target_w, target_w if nt not in '79' else int(target_w * 0.8)), Image.Resampling.LANCZOS)
            img.paste(spr, (int(x - spr.width / 2), int(y - spr.height / 2)), spr)
            if nt == '7' and n.get('hits', 0) > 0:
                hits = n['hits']
                draw.ellipse([x - 10, y - 10, x + 10, y + 10], fill="#ffffff", outline="#000000", width=1)
                draw.text((x - (3 if hits < 10 else 6), y - 7), str(hits), fill="#000000", font=font_balloon)
        else:
            if nt in '12345679':
                fill_c = {"1": "#f44336", "2": "#29b6f6", "3": "#f44336", "4": "#29b6f6", "5": "#fcdb38",
                          "6": "#fcdb38", "7": "#ff9800", "9": "#9c27b0"}[nt]
                r_radius = r_bg if nt in '3469' else r_sm
                draw.ellipse([x - r_radius, y - r_radius, x + r_radius, y + r_radius], fill=fill_c, outline="#ffffff", width=2)
                if nt == '7' and n.get('hits', 0) > 0:
                    hits = n['hits']
                    draw.ellipse([x - 10, y - 10, x + 10, y + 10], fill="#ffffff", outline="#000000", width=1)
                    draw.text((x - (3 if hits < 10 else 6), y - 7), str(hits), fill="#000000", font=font_balloon)

    return img
