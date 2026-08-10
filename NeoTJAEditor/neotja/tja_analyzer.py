import re
from decimal import Decimal

from neotja.se_text import compute_se_labels
from neotja.theme import COLORS


def _header_value(content, key):
    """TJA のヘッダ(例 TITLE:)の値を返す。無ければ空文字。"""
    prefix = key + ":"
    for line in (content or "").splitlines():
        t = line.strip()
        if t.upper().startswith(prefix):
            return t[len(prefix):].strip()
    return ""


def balloon_pop_spans(spans, roll_hit_speed):
    """風船・くす玉の区間を「叩ききって割れる時刻」で切り詰めて返す。

    本家は指定打数を叩ききった瞬間に割れるので、TJA の区間([7]〜[8])を
    そのまま使うと必ず区間の終わりまで残ってしまう。設定の連打秒速で
    必要打数を割った時間で終わらせる。叩ききれないほど短い区間なら
    区間の終わりのまま。

    表示(レーン)と打音(スケジュール)の両方がこれを通ることが大事で、
    片方だけに掛けると「数字は 0 なのに音がまだ鳴る」ようにズレる。
    スコア(score.py)も加算のタイミングはこの切り詰めに合わせる(音が止まって
    から点だけ入り続けないように)。ただし**打数=合計点**は秒速に依存させたく
    ないので、あちらは打数だけ生の区間から秒速18打で数える。"""
    speed = max(1.0, float(roll_hit_speed or 45))
    out = []
    for sp in spans or []:
        sp = tuple(sp)
        need = int(sp[-1]) if len(sp) > 1 else 0
        end = float(sp[1])
        if need > 0:
            end = min(end, float(sp[0]) + need / speed)
        out.append((sp[0], end) + sp[2:])
    return out


class TJACourseAnalyzer:
    DIFF = {"0": "Easy", "Easy": "Easy", "1": "Normal", "Normal": "Normal",
            "2": "Hard", "Hard": "Hard", "3": "Oni", "Oni": "Oni", "4": "Edit", "Edit": "Edit"}
    DIFF_LABEL = {"Easy": "かんたん", "Normal": "ふつう", "Hard": "むずかしい", "Oni": "おに", "Edit": "おに(裏)"}
    # かんたん=赤, ふつう=黄緑, むずかしい=水色, おに=ピンク, おに(裏)=紫
    DIFF_COLOR = {"Easy": "#F44336", "Normal": "#9ACD32", "Hard": "#4FC3F7", "Oni": "#FF80AB", "Edit": "#9C27B0"}
    # サイドバーの表示順: 裏(4) > おに(3) > むずかしい(2) > ふつう(1) > かんたん(0)
    DIFF_RANK = {"Edit": 4, "Oni": 3, "Hard": 2, "Normal": 1, "Easy": 0}

    def __init__(self, config_data: dict):
        self.config_data = config_data

    def parse_courses(self, content):
        lines = content.split('\n')
        result = []
        buffer = []
        cname = "Oni"
        in_score = False

        for line in lines:
            s = line.strip()
            if s.startswith("COURSE:"):
                if buffer:
                    result.append({"key": cname, "data": buffer})
                cname = self.DIFF.get(s[7:].strip(), "Oni")
                buffer = []
                in_score = False
            elif s.startswith("#START"):
                in_score = True
                buffer.append(s)
            elif s.startswith("#END"):
                buffer.append(s)
                in_score = False
            elif in_score:
                buffer.append(s)
        if buffer:
            result.append({"key": cname, "data": buffer})

        out = []
        for c in result:
            stats = self._analyze(c["data"], lines)
            out.append({
                "key": c["key"],
                "label": self.DIFF_LABEL.get(c["key"], c["key"]),
                "color": self.DIFF_COLOR.get(c["key"], COLORS["fg"]),
                "data": c["data"],
                **stats
            })
        return out

    def _roll_hits(self, duration: float) -> int:
        """Estimated tap count for a roll/big-roll of the given duration
        (seconds), honoring the short-roll compensation setting. Shared by
        _analyze() (sidebar stats) and build_preview_timeline() (live
        preview) so the two never drift out of sync."""
        short_roll = self.config_data.get("short_roll_comp", "段階的補正 (60fps理論値)")
        rs = self.config_data.get("roll_speed", 45)
        if short_roll == "段階的補正 (60fps理論値)":
            if duration <= 0.10:
                return int(duration * max(60, rs))
            elif duration <= 0.15:
                return int(duration * max(55, rs))
            return int(duration * rs)
        elif short_roll == "段階的補正 (理論値-1)":
            if duration <= 0.10:
                return int(duration * max(55, rs))
            elif duration <= 0.15:
                return int(duration * max(50, rs))
            return int(duration * rs)
        return int(duration * rs)

    def _analyze(self, data, all_lines, branch_level: str = "M"):
        bpm = Decimal("120")
        balloon_defs = []

        for l in all_lines:
            if l.startswith("BPM:"):
                try:
                    bpm = Decimal(l[4:].strip())
                except Exception:
                    pass
            elif l.startswith("BALLOON:"):
                balloon_defs = [int(x.strip()) for x in l[8:].split(',') if x.strip().isdigit()]

        events = []
        # Same branch handling as build_preview_timeline() (see its
        # docstring): only branch_level's notes/commas count, so a chart
        # with #BRANCHSTART doesn't triple-count notes/measures across
        # Normal+Expert+Master. No branch selector exists for the sidebar,
        # so this always assumes the hardest (Master) branch.
        branch_active = True
        for line in data:
            line = line.split("//")[0].strip()
            if not line:
                continue
            if line.startswith("#"):
                if line.startswith("#BPMCHANGE"):
                    try:
                        events.append(("#BPMCHANGE", Decimal(line.split()[1])))
                    except Exception:
                        pass
                elif line.startswith("#MEASURE"):
                    m = re.search(r"(\d+)/(\d+)", line)
                    # 分母0 (`#MEASURE 4/0`) は書きかけのタイプミスとして普通に
                    # 起こりうる。Decimal の既定コンテキストは 0 除算を送出するので、
                    # 素直に割ると解析全体が例外で落ちる。不正な #MEASURE は無視して
                    # 直前の拍子を保つ(他のディレクティブの try/except と同じ方針)。
                    if m and Decimal(m.group(2)) != 0:
                        events.append(("#MEASURE", Decimal(m.group(1)) / Decimal(m.group(2))))
                elif line.startswith("#DELAY"):
                    try:
                        events.append(("#DELAY", Decimal(line.split()[1])))
                    except Exception:
                        pass
                elif line.startswith("#BRANCHSTART"):
                    branch_active = False
                elif line.startswith("#BRANCHEND"):
                    branch_active = True
                elif line == "#N":
                    branch_active = (branch_level == "N")
                elif line == "#E":
                    branch_active = (branch_level == "E")
                elif line == "#M":
                    branch_active = (branch_level == "M")
                continue

            for c in line:
                if c in "0123456789":
                    if not branch_active:
                        continue
                    events.append(("NOTE", c))
                elif c == ",":
                    if not branch_active:
                        continue
                    events.append(("COMMA", None))

        measures = []
        cur_m = []
        for ev in events:
            if ev[0] == "COMMA":
                measures.append(cur_m)
                cur_m = []
            else:
                cur_m.append(ev)
        if cur_m:
            measures.append(cur_m)

        total_time = Decimal("0")
        curr_bpm = bpm
        measure_val = Decimal("1")

        don = 0
        ka = 0
        big_don = 0
        big_ka = 0
        roll_start_time = None
        balloon_start_time = None
        b_idx = 0

        rolls_info = []
        balloons_info = []

        for m_events in measures:
            n_len = sum(1 for ev in m_events if ev[0] == "NOTE")
            for ev in m_events:
                if ev[0] == "#BPMCHANGE":
                    curr_bpm = ev[1]
                elif ev[0] == "#MEASURE":
                    measure_val = ev[1]
                elif ev[0] == "#DELAY":
                    total_time += ev[1]
                elif ev[0] == "NOTE":
                    # `#BPMCHANGE 0` でも落ちないよう curr_bpm > 0 を見る
                    # (直下の空小節加算 / build_preview_timeline と同じ防御)。
                    time_per_note = ((Decimal("240") * measure_val / curr_bpm) / n_len
                                     if (n_len > 0 and curr_bpm > 0) else Decimal("0"))
                    c = ev[1]
                    if c == "1":
                        don += 1
                    elif c == "2":
                        ka += 1
                    elif c == "3":
                        big_don += 1
                    elif c == "4":
                        big_ka += 1
                    elif c in "56":
                        roll_start_time = total_time
                    elif c == "7":
                        balloon_start_time = total_time
                        hits = balloon_defs[b_idx] if b_idx < len(balloon_defs) else 0
                        balloons_info.append({"duration": 0.0, "hits": hits})
                        b_idx += 1
                    elif c == "8":
                        if roll_start_time is not None:
                            dur = float(total_time - roll_start_time)
                            hits = self._roll_hits(dur)
                            rolls_info.append({"duration": dur, "hits": hits})
                            roll_start_time = None
                        elif balloon_start_time is not None:
                            dur = float(total_time - balloon_start_time)
                            if balloons_info:
                                balloons_info[-1]["duration"] = dur
                            balloon_start_time = None
                    total_time += time_per_note
            if n_len == 0 and curr_bpm > 0:
                # A measure with no NOTE events (e.g. a bar-line-only measure)
                # still occupies real time, but the per-note total_time +=
                # time_per_note loop above never runs for it - without this
                # it got skipped entirely, playing everything after it early
                # by exactly this measure's length.
                total_time += Decimal("240") * measure_val / curr_bpm

        f = float(total_time)
        m, s = divmod(int(f), 60)
        ms = int((total_time - int(total_time)) * 1000)
        time_str = f"{m}:{s:02}.{ms:03}"

        return {
            "notes": don + ka + big_don + big_ka,
            "don_count": don + big_don,
            "ka_count": ka + big_ka,
            "measures": len(measures),
            "time": time_str,
            "rolls_info": rolls_info,
            "balloons_info": balloons_info,
        }

    def course_line_range(self, content: str, course_key: str):
        """Returns (start_line, end_line) - the 1-indexed line numbers of
        `#START` and `#END` themselves - for the first course matching
        `course_key` (e.g. "Oni"), or None if no such course exists. Same
        COURSE:/#START/#END scanning idiom as build_preview_timeline()'s
        course_bounds, pulled out standalone since callers that just need to
        locate one course's body (to splice new note data into it) don't
        need everything else that method computes."""
        lines = content.split('\n')
        start = None
        cur_key = "Oni"
        for idx, raw in enumerate(lines, start=1):
            s = raw.split("//")[0].strip()
            if s.startswith("COURSE:"):
                cur_key = self.DIFF.get(s[7:].strip(), "Oni")
            elif s.startswith("#START"):
                start = idx
            elif s.startswith("#END") and start is not None:
                if cur_key == course_key:
                    return (start, idx)
                start = None
        return None

    def line_in_course_body(self, content: str, line_no: int) -> bool:
        """True if `line_no` (1-indexed) falls strictly between some course's
        `#START` and `#END` lines (exclusive of those two directive lines
        themselves) - i.e. it's a chart body line, not a header/comment/
        directive line and not inside a course but outside any #START..#END
        span. Same COURSE:/#START/#END scanning idiom as course_line_range()/
        time_at_cursor() above, but course-key agnostic (any course counts)
        and doesn't need the timing math, since the only caller (機能1's
        note-input sound) just needs a yes/no "is this a place where typing a
        note character means anything" check.

        Used to gate the instant note-typing SFX (see main_window._on_note_typed):
        typing in headers, comments, or outside any course must stay silent."""
        lines = content.split('\n')
        start = None
        for idx, raw in enumerate(lines, start=1):
            s = raw.split("//")[0].strip()
            if s.startswith("#START"):
                start = idx
            elif s.startswith("#END") and start is not None:
                if start < line_no < idx:
                    return True
                start = None
        return False

    def measure_at_cursor(self, content: str, line_no: int):
        """カーソル行が属する小節の1始まり番号を返す。#START..#END の外なら
        None。ステータスバーの位置表示用なので、タイミング計算はせずカンマ数
        だけ数える(その行より前のカンマ数 + 1 = その行が入る小節)。"""
        lines = content.split('\n')
        bounds = []
        start = None
        for idx, raw in enumerate(lines, start=1):
            s = raw.split("//")[0].strip()
            if s.startswith("#START"):
                start = idx
            elif s.startswith("#END") and start is not None:
                bounds.append((start, idx))
                start = None
        target = next(((a, b) for a, b in bounds if a <= line_no <= b), None)
        if target is None:
            return None
        a, b = target
        measure = 1
        # 譜面分岐は「同じ小節の別案」なので、3系統ぶん数えてはいけない。
        # #BRANCHSTART で頭を控え、系統が変わるたびそこへ戻し、分岐が閉じたら
        # いちばん進んだところから続ける(プレビューは選んだ系統だけを数える
        # ので、こうしないと分岐のある譜面で小節番号が何十もずれる)。
        br_base = br_max = None
        for idx in range(a + 1, b):
            if idx >= line_no:
                break
            code = lines[idx - 1].split("//")[0]
            s = code.strip()
            if s.startswith("#"):
                head = s.split()[0].split(",")[0]
                if head == "#BRANCHSTART":
                    if br_base is not None:      # #BRANCHEND を書かない譜面向け
                        measure = max(br_max, measure)
                    br_base = br_max = measure
                elif head in ("#N", "#E", "#M") and br_base is not None:
                    br_max = max(br_max, measure)
                    measure = br_base
                elif head == "#BRANCHEND" and br_base is not None:
                    measure = max(br_max, measure)
                    br_base = br_max = None
                # 命令行のカンマは小節の区切りではない(`#BRANCHSTART r,-2,-1`)。
                continue
            measure += code.count(",")
        return measure

    def time_at_cursor(self, content: str, line_no: int):
        """Returns the chart-time (seconds, float) at the start of the measure
        that contains `line_no` (1-indexed), or None if the line isn't inside
        any course's #START..#END body. Reuses the same BPM/#MEASURE/#DELAY
        timeline math as _analyze(), but walks the original lines (instead of
        a pre-stripped course buffer) so it can pinpoint which line lands in
        which measure."""
        lines = content.split('\n')

        course_bounds = []
        start = None
        for idx, raw in enumerate(lines, start=1):
            s = raw.split("//")[0].strip()
            if s.startswith("#START"):
                start = idx
            elif s.startswith("#END") and start is not None:
                course_bounds.append((start, idx))
                start = None

        target = next(((a, b) for a, b in course_bounds if a <= line_no <= b), None)
        if target is None:
            return None
        a, b = target

        bpm = Decimal("120")
        for l in lines:
            if l.startswith("BPM:"):
                try:
                    bpm = Decimal(l[4:].strip())
                except Exception:
                    pass
                break

        total_time = Decimal("0")
        curr_bpm = bpm
        measure_val = Decimal("1")
        cur_events = []
        found = None

        def flush():
            nonlocal total_time, curr_bpm, measure_val
            n_len = sum(1 for t, _ in cur_events if t == "NOTE")
            for t, v in cur_events:
                if t == "#BPMCHANGE":
                    curr_bpm = v
                elif t == "#MEASURE":
                    measure_val = v
                elif t == "#DELAY":
                    total_time += v
                elif t == "NOTE":
                    total_time += ((Decimal("240") * measure_val / curr_bpm) / n_len
                                   if (n_len > 0 and curr_bpm > 0) else Decimal("0"))
            if n_len == 0 and curr_bpm > 0:
                # 数字が1つも無い小節(「,」だけの行)も1小節ぶんの時間を持つ。
                # 上のループは NOTE ごとに足す作りなので、ここで足さないと
                # その小節が丸ごと 0 秒になり、以降の時刻が小節ぶん手前へ
                # ずれる(_analyze / build_preview_timeline は既に同じ手当てを
                # している。ステータスバーの時刻と「カーソル位置から再生」だけが
                # 数小節ずれていたのはこれが原因)。
                total_time += Decimal("240") * measure_val / curr_bpm

        # 譜面分岐の扱いは measure_at_cursor と同じ考え方(同じ小節の別案なので
        # 3系統ぶん時間を足さない)。足していたため、分岐のある譜面では
        # #BRANCHEND 以降の時刻が分岐区間の2倍ぶん先へずれていた
        # (実測: 最終小節が 121.7秒の譜面で 186.9秒と出ていた)。
        br_base_t = br_max_t = None

        for idx in range(a, b + 1):
            if idx == a or idx == b:
                continue
            s = lines[idx - 1].split("//")[0].strip()
            if not s:
                continue

            if found is None and idx >= line_no:
                found = total_time

            if s.startswith("#"):
                head = s.split()[0].split(",")[0]
                if head == "#BRANCHSTART":
                    if br_base_t is not None:
                        total_time = max(br_max_t, total_time)
                    br_base_t = br_max_t = total_time
                    cur_events = []
                    continue
                if head in ("#N", "#E", "#M") and br_base_t is not None:
                    br_max_t = max(br_max_t, total_time)
                    total_time = br_base_t
                    cur_events = []
                    continue
                if head == "#BRANCHEND" and br_base_t is not None:
                    total_time = max(br_max_t, total_time)
                    br_base_t = br_max_t = None
                    cur_events = []
                    continue
                if s.startswith("#BPMCHANGE"):
                    try:
                        cur_events.append(("#BPMCHANGE", Decimal(s.split()[1])))
                    except Exception:
                        pass
                elif s.startswith("#MEASURE"):
                    m = re.search(r"(\d+)/(\d+)", s)
                    if m and Decimal(m.group(2)) != 0:   # 分母0は無視(_analyze と同じ)
                        cur_events.append(("#MEASURE", Decimal(m.group(1)) / Decimal(m.group(2))))
                elif s.startswith("#DELAY"):
                    try:
                        # その場で足す。小節の終わり(カンマ)まで保留していたため、
                        # #DELAY の直後の行にカーソルを置くと、待ち時間ぶん手前の
                        # 時刻が返っていた(小節線・メトロノームは反映済みなので
                        # そこだけ食い違っていた)。
                        total_time += Decimal(s.split()[1])
                    except Exception:
                        pass
                continue

            for c in s:
                if c in "0123456789":
                    cur_events.append(("NOTE", c))
                elif c == ",":
                    flush()
                    cur_events = []

        if found is None:
            found = total_time
        return float(found)

    def build_cursor_index(self, content: str) -> dict:
        """行番号 → (小節番号, 譜面時刻[秒], コース番号) の索引を1パスで作る。

        ステータスバーは measure_at_cursor()/time_at_cursor() をカーソル移動の
        たびに呼んでいたが、どちらも文書全体を走査するため数万行の譜面では
        合計70ms 以上かかり、方向キーを押すたびに画面が固まっていた。解析パス
        (600ms デバウンス)の中で一度だけこの索引を作り、以後は O(1) で引く。

        小節番号・時刻の求め方は measure_at_cursor()/time_at_cursor() と
        完全に同じ(各行を「処理する前」の値を記録する)。3 番目のコース番号は
        「カーソルがどのコース本文に居るか」で、プレビュー/メトロノームの
        再構築が必要かどうかの判定に使う(同じコース内の移動なら不要)。

        #START..#END の外の行は索引に入らない(= None 扱い)。"""
        lines = content.split('\n')

        bpm = Decimal("120")
        for l in lines:
            if l.startswith("BPM:"):
                try:
                    bpm = Decimal(l[4:].strip())
                except Exception:
                    pass
                break

        bounds = []
        start = None
        for idx, raw in enumerate(lines, start=1):
            # 音符行の大半は '#' を含まないので、split/strip を作る前に弾く。
            if "#" not in raw:
                continue
            s = raw.split("//")[0].strip()
            if s.startswith("#START"):
                start = idx
            elif s.startswith("#END") and start is not None:
                bounds.append((start, idx))
                start = None

        index = {}
        for course_ord, (a, b) in enumerate(bounds):
            total_time = Decimal("0")
            curr_bpm = bpm
            measure_val = Decimal("1")
            measure = 1
            cur_events = []
            index[a] = (1, 0.0, course_ord)
            # total_time が動くのは小節の切れ目(カンマ)だけなので、float 変換は
            # 変わったときだけ行う。prec=50 の Decimal -> float は 1 回が地味に
            # 重く、数万行ぶん毎行やると索引作りが 3 割増しになる。
            time_f = 0.0
            time_d = total_time
            # 譜面分岐(同じ小節の別案)を3系統ぶん数えない。詳しくは
            # measure_at_cursor / time_at_cursor の同じ箇所の説明。
            br_base_t = br_max_t = None
            br_base_m = br_max_m = None

            for idx in range(a + 1, b):
                raw = lines[idx - 1]
                code = raw.split("//")[0] if "//" in raw else raw
                # 記録はこの行を処理する前の値(= time/measure_at_cursor と同じ)。
                if total_time is not time_d:
                    time_d = total_time
                    time_f = float(total_time)
                index[idx] = (measure, time_f, course_ord)
                s = code.strip()
                # 命令行のカンマは小節の区切りではない(measure_at_cursor の
                # 同じ箇所の説明を参照)。
                if "," in code and not s.startswith("#"):
                    measure += code.count(",")
                if not s:
                    continue

                if s[0] == "#":
                    head = s.split()[0].split(",")[0]
                    if head == "#BRANCHSTART":
                        if br_base_t is not None:
                            total_time = max(br_max_t, total_time)
                            measure = max(br_max_m, measure)
                        br_base_t = br_max_t = total_time
                        br_base_m = br_max_m = measure
                        cur_events = []
                        continue
                    if head in ("#N", "#E", "#M") and br_base_t is not None:
                        br_max_t = max(br_max_t, total_time)
                        br_max_m = max(br_max_m, measure)
                        total_time = br_base_t
                        measure = br_base_m
                        cur_events = []
                        continue
                    if head == "#BRANCHEND" and br_base_t is not None:
                        total_time = max(br_max_t, total_time)
                        measure = max(br_max_m, measure)
                        br_base_t = br_max_t = br_base_m = br_max_m = None
                        cur_events = []
                        continue
                    if s.startswith("#BPMCHANGE"):
                        try:
                            cur_events.append(("#BPMCHANGE", Decimal(s.split()[1])))
                        except Exception:
                            pass
                    elif s.startswith("#MEASURE"):
                        m = re.search(r"(\d+)/(\d+)", s)
                        if m and Decimal(m.group(2)) != 0:   # 分母0は無視(_analyze と同じ)
                            cur_events.append(("#MEASURE", Decimal(m.group(1)) / Decimal(m.group(2))))
                    elif s.startswith("#DELAY"):
                        try:
                            # time_at_cursor と同じくその場で足す(同じ箇所の説明)。
                            total_time += Decimal(s.split()[1])
                        except Exception:
                            pass
                    continue

                for c in s:
                    if c in "0123456789":
                        cur_events.append(("NOTE", c))
                    elif c == ",":
                        n_len = 0
                        for t, _v in cur_events:
                            if t == "NOTE":
                                n_len += 1
                        for t, v in cur_events:
                            if t == "#BPMCHANGE":
                                curr_bpm = v
                            elif t == "#MEASURE":
                                measure_val = v
                            elif t == "#DELAY":
                                total_time += v
                            elif t == "NOTE":
                                total_time += ((Decimal("240") * measure_val / curr_bpm) / n_len
                                               if (n_len > 0 and curr_bpm > 0) else Decimal("0"))
                        if n_len == 0 and curr_bpm > 0:
                            # 数字の無い小節(「,」だけ)も1小節ぶんの時間を持つ。
                            # time_at_cursor の同じ箇所の説明を参照。
                            total_time += Decimal("240") * measure_val / curr_bpm
                        cur_events = []

            index[b] = (measure, float(total_time), course_ord)


        return index

    def build_metronome_clicks(self, content: str, cursor_line: int = None, min_duration_seconds: float = 0.0) -> list:
        """Returns a list of (chart_time_seconds, is_measure_start) at
        1/4-note resolution, honoring #MEASURE (defaults to 4/4 where
        unspecified), #BPMCHANGE and #DELAY. A measure's leftover fractional
        beat (e.g. the trailing .5 beat in a 7/8 measure) is not clicked.

        TJA files commonly have several COURSE blocks (Easy/Normal/.../Oni)
        that can each declare their own #MEASURE/#BPMCHANGE, so this uses
        whichever course the cursor is currently in (falling back to the
        first course) rather than always the first one - otherwise editing
        e.g. Oni's #MEASURE would have no effect if Easy comes first.

        Once the actual chart data (or the whole file, if there's none yet)
        runs out, quarter-note clicks keep going at the last known tempo
        until `min_duration_seconds` (typically the loaded song's duration),
        so the metronome/beat-grid still work over an un-charted intro,
        outro, or a brand new file with no measures written yet."""
        lines = content.split('\n')

        course_bounds = []
        start = None
        for idx, raw in enumerate(lines, start=1):
            s = raw.split("//")[0].strip()
            if s.startswith("#START"):
                start = idx
            elif s.startswith("#END") and start is not None:
                course_bounds.append((start, idx))
                start = None

        bpm = Decimal("120")
        for l in lines:
            if l.startswith("BPM:"):
                try:
                    bpm = Decimal(l[4:].strip())
                except Exception:
                    pass
                break

        total_time = Decimal("0")
        curr_bpm = bpm
        measure_val = Decimal("1")
        clicks = []

        # 小節内で #BPMCHANGE があると 1 小節の実時間は「音符スロットごとの
        # BPM で積算」しないと正しくない(build_preview_timeline と同じ)。以前は
        # 小節末の 1 つの BPM で 240*measure_val/bpm と一括計算していたため、
        # 小節途中で BPM が変わる曲(SUPERNOVA 等)で小節の頭が音符とずれ、
        # メトロノーム音・波形グリッドが実際の小節線からずれていた。
        #
        # ここでは 1 小節を (音符数, その区間の BPM) のセグメントに分けて集計し、
        # 小節の実時間 dur = Σ (count/N) * 240*measure_val/bpm を求める。拍(4分)
        # は求めた dur を等分して打つ(小節の頭は必ず正確。BPM 変化が小節途中に
        # ある「ギミック」小節の中間拍だけ等間隔近似になるが、頭は音符と一致)。
        seg = []          # [(note_count, bpm)] 現在処理中の小節
        cur_count = 0     # 現在のセグメントの音符スロット数
        cur_seg_bpm = curr_bpm

        def finalize_measure():
            nonlocal total_time, seg, cur_count, cur_seg_bpm
            if cur_count > 0:
                seg.append((cur_count, cur_seg_bpm))
            mv = measure_val
            n_total = sum(c for c, _ in seg)
            if mv <= 0:
                seg = []; cur_count = 0; cur_seg_bpm = curr_bpm
                return
            if n_total == 0:
                dur = (Decimal(240) * mv / curr_bpm) if curr_bpm > 0 else Decimal(0)
            else:
                dur = Decimal(0)
                for c, bp in seg:
                    if bp > 0:
                        dur += Decimal(c) * Decimal(240) * mv / bp / Decimal(n_total)
            n_quarters = int(Decimal(4) * mv)  # floor
            if n_quarters > 0:
                beat = (dur / (Decimal(4) * mv)) if dur > 0 else Decimal(0)
                for k in range(n_quarters):
                    clicks.append((total_time + k * beat, k == 0))
            total_time += dur
            seg = []; cur_count = 0; cur_seg_bpm = curr_bpm

        if course_bounds:
            target = None
            if cursor_line is not None:
                target = next((cb for cb in course_bounds if cb[0] <= cursor_line <= cb[1]), None)
            a, b = target if target is not None else course_bounds[0]

            # 譜面分岐は「同じ小節の別案」。3系統ぶん時間を積むと、分岐の
            # あとのクリックが実際の小節線から大きくずれる(実測: 最終小節線が
            # 121.7秒の譜面で 186.0秒)。分岐の頭を控え、系統が変わるたびそこへ
            # 戻し、閉じたらいちばん進んだところから続ける。
            # 打つクリックは最初の系統ぶんだけ残す(どの系統でも小節の割り方は
            # 同じなので、これで小節の頭は正しい位置に来る)。
            br_base_t = br_max_t = None
            br_base_clicks = None

            for idx in range(a + 1, b):
                s = lines[idx - 1].split("//")[0].strip()
                if not s:
                    continue
                if s.startswith("#"):
                    head = s.split()[0].split(",")[0]
                    if head == "#BRANCHSTART":
                        if br_base_t is not None:
                            total_time = max(br_max_t, total_time)
                        br_base_t = br_max_t = total_time
                        br_base_clicks = len(clicks)
                        seg = []; cur_count = 0; cur_seg_bpm = curr_bpm
                        continue
                    if head in ("#N", "#E", "#M") and br_base_t is not None:
                        br_max_t = max(br_max_t, total_time)
                        total_time = br_base_t
                        # 2系統目以降のクリックは捨てる(同じ位置に重なるだけ)。
                        del clicks[br_base_clicks:]
                        seg = []; cur_count = 0; cur_seg_bpm = curr_bpm
                        continue
                    if head == "#BRANCHEND" and br_base_t is not None:
                        total_time = max(br_max_t, total_time)
                        br_base_t = br_max_t = br_base_clicks = None
                        seg = []; cur_count = 0; cur_seg_bpm = curr_bpm
                        continue
                    if s.startswith("#BPMCHANGE"):
                        try:
                            new_bpm = Decimal(s.split()[1])
                            # 小節途中の変化: 直前までを旧BPMのセグメントとして確定。
                            if cur_count > 0:
                                seg.append((cur_count, cur_seg_bpm))
                                cur_count = 0
                            curr_bpm = new_bpm
                            cur_seg_bpm = new_bpm
                        except Exception:
                            pass
                    elif s.startswith("#MEASURE"):
                        m = re.search(r"(\d+)/(\d+)", s)
                        if m and Decimal(m.group(2)) != 0:   # 分母0は無視(_analyze と同じ)
                            measure_val = Decimal(m.group(1)) / Decimal(m.group(2))
                    elif s.startswith("#DELAY"):
                        try:
                            total_time += Decimal(s.split()[1])
                        except Exception:
                            pass
                    continue
                for c in s:
                    if c in "0123456789":
                        cur_count += 1
                    elif c == ",":
                        finalize_measure()

        min_dur = Decimal(str(min_duration_seconds))
        if curr_bpm > 0:
            quarter_sec = Decimal(60) / curr_bpm
            beat_i = 0
            while total_time < min_dur:
                clicks.append((total_time, beat_i % 4 == 0))
                total_time += quarter_sec
                beat_i += 1

        return [(float(t), is_measure) for t, is_measure in clicks]

    def build_preview_timeline(self, content: str, cursor_line: int = None, course_key: str = None,
                                branch_level: str = "M") -> dict:
        """Returns everything the real-time scrolling chart preview needs.
        Course selection: `course_key` (e.g. "Oni") wins if given and present;
        otherwise whichever course contains cursor_line; otherwise the first
        course in the file. Uses the same #BPMCHANGE/#MEASURE/#DELAY timing
        math as build_metronome_clicks:
          - "notes": [(chart_time_seconds, char, bpm, scroll, se_text)] for
            don/ka/big-don/big-ka ('1'-'4'). `se_text` is the automatic 打音
            表記 syllable ("ド"/"ドン"/"コ"/"カ"/"カッ", or None) computed by
            neotja.se_text.compute_note_se_labels, a port of PeepoDrumKit's
            RecalculateSENotes - see that module's docstring for the full
            spec. It is computed once here (not per frame in the preview
            widget) because classifying a note needs its neighbours' visual
            spacing, which never changes between repaints.
          - "rolls": [(start_seconds, end_seconds, char, bpm, scroll, hits)] for roll/
            big-roll ('5'/'6') spans closed by a '8' tail (an unclosed roll runs to the
            end of the course)
          - "gogo_regions": [(start_seconds, end_seconds)] from #GOGOSTART/#GOGOEND
          - "bar_times": [(chart_time_seconds, bpm, scroll, visible), ...] one entry per
            measure boundary. `visible` (bool) reflects #BARLINEOFF/#BARLINEON state at
            that boundary (see below) - always True if the chart never uses them. This
            is a rendering hint only: every entry, hidden or not, is still a real measure
            boundary for navigation purposes (see ChartPreviewWidget._nav_points).
          - "balloons": [(start_seconds, end_seconds, bpm, scroll, hits)] for '7' spans closed by '8'
          - "kusudamas": [(start_seconds, end_seconds, bpm, scroll, hits)] for '9' spans
            closed by '8', same shape as "balloons" - kusudama draws like a balloon/roll
            (a capsule bar) but in its own color, and consumes BALLOON: entries from the
            same shared counter/order as '7' (whichever of '7'/'9' appears first in the
            chart claims the first BALLOON: value, and so on)
          - "bpm_changes"/"measure_changes"/"scroll_changes": [(chart_time_seconds, ...)],
            sorted, one entry at time 0 plus one per #BPMCHANGE/#MEASURE/#SCROLL - for
            looking up "what was BPM/MEASURE/SCROLL at time T" (bisect) to drive a
            real-time readout. measure_changes entries are (time, num, den). Each
            note/roll/bar/balloon's own bpm+scroll (rather than these bisect-lookup
            tables) is what actually drives its on-screen position/speed.
          - "course_key"/"course_label"/"course_color"/"level": the course actually used
          - "available_courses": [{"key","label","color"}, ...] every course in the file,
            in file order, for building a course picker
          - "has_branches"/"branch_level": whether this course has any #BRANCHSTART
            section at all, and which of "N"/"E"/"M" (Normal/Expert/Master) was used
        Chart branching (#BRANCHSTART/#N/#E/#M/#BRANCHEND): this is a static,
        non-interactive preview (every note is assumed hit), so simulating the
        real game's accuracy-based branch switching would always just resolve
        to the best branch - not useful. Instead `branch_level` statically
        picks one of "N"/"E"/"M" for the whole course; only that branch's
        note/comma events are kept, the other two are skipped entirely (their
        commas don't count as measure boundaries either), so measure count/
        duration reflect a single branch's worth of chart, not all three
        concatenated. #BRANCHSTART's condition/thresholds are intentionally
        never parsed since nothing here switches branches dynamically. If a
        given #BRANCHSTART section doesn't define `branch_level` at all, that
        section simply contributes zero notes (no fallback to another level).
        Commands: #BPMCHANGE と #MEASURE だけは系統を問わず適用する(この2つが
        系統ごとに違うと譜面そのものが成立しないので、取りこぼしへの保険)。
        #DELAY / #SCROLL / #GOGOSTART / #GOGOEND / #BARLINEOFF / #BARLINEON は
        いま選んでいる系統のものだけを効かせる。以前は全部を系統を問わず拾って
        いたため、分岐の中に #DELAY があると待ち時間が系統の数だけ足され
        (3系統なら3倍)、#SCROLL は最後の系統の値が勝ち、ゴーゴーは開始/終了が
        3回ずつ入る、という食い違いが起きていた。
        Each note/roll/bar carries the BPM active when it occurred so the
        preview widget can space notes by beat (PeepoDrumKit-style: pixel
        speed scales with tempo) instead of a single fixed real-time scroll
        speed, which would otherwise cram fast sections and over-space slow
        ones.
        #BARLINEOFF/#BARLINEON: #BARLINEOFF hides subsequently-recorded bar
        lines (the "visible" flag on each "bar_times" entry), #BARLINEON
        restores them; the state persists across measures until changed
        again, and defaults to visible (a chart that never uses either
        command behaves exactly as before). Like #BPMCHANGE/#SCROLL/
        #MEASURE, a #BARLINEOFF/#BARLINEON placed before a measure's first
        note takes effect for that same measure's bar line (see the
        `bar_recorded` comment below); this is a visual instruction only and
        never removes a measure boundary from the underlying timeline."""
        lines = content.split('\n')

        course_bounds = []  # (start, end, key, level)
        start = None
        cur_key = "Oni"
        cur_level = None
        for idx, raw in enumerate(lines, start=1):
            s = raw.split("//")[0].strip()
            if s.startswith("COURSE:"):
                cur_key = self.DIFF.get(s[7:].strip(), "Oni")
                cur_level = None
            elif s.startswith("LEVEL:"):
                try:
                    cur_level = int(s[6:].strip())
                except Exception:
                    pass
            elif s.startswith("#START"):
                start = idx
            elif s.startswith("#END") and start is not None:
                course_bounds.append((start, idx, cur_key, cur_level))
                start = None

        empty = {
            "notes": [], "rolls": [], "balloons": [], "kusudamas": [], "gogo_regions": [], "bar_times": [],
            "roll_hit_speed": float(self.config_data.get("roll_speed", 45)),
            "title": _header_value(content, "TITLE"),
            "bpm_changes": [], "measure_changes": [], "scroll_changes": [],
            "course_key": None, "course_label": "", "course_color": COLORS["fg_bright"],
            "level": None, "available_courses": [], "has_branches": False, "branch_level": branch_level,
        }
        if not course_bounds:
            return empty

        seen = set()
        available_courses = []
        for _, _, k, _ in course_bounds:
            if k not in seen:
                seen.add(k)
                available_courses.append({"key": k, "label": self.DIFF_LABEL.get(k, k), "color": self.DIFF_COLOR.get(k, COLORS["fg"])})
        # Same top-to-bottom rank order as the sidebar, so clicking through
        # the preview's course picker cycles Ura -> Oni -> Hard -> Normal ->
        # Easy -> (wrap) rather than file order.
        available_courses.sort(key=lambda c: -self.DIFF_RANK.get(c["key"], -1))

        target = None
        if course_key is not None:
            target = next((cb for cb in course_bounds if cb[2] == course_key), None)
        if target is None and cursor_line is not None:
            target = next((cb for cb in course_bounds if cb[0] <= cursor_line <= cb[1]), None)
        a, b, sel_key, sel_level = target if target is not None else course_bounds[0]

        bpm = Decimal("120")
        for l in lines:
            if l.startswith("BPM:"):
                try:
                    bpm = Decimal(l[4:].strip())
                except Exception:
                    pass
                break

        balloon_defs = []
        for l in lines:
            if l.startswith("BALLOON:"):
                balloon_defs = [int(x.strip()) for x in l[8:].split(',') if x.strip().isdigit()]
                break

        # Pre-split into measures via COMMA (like _analyze()) instead of a
        # single streaming pass, so a command that lands mid-measure - most
        # visibly #GOGOSTART/#GOGOEND, but also #BPMCHANGE/#MEASURE/#DELAY -
        # gets the correct timestamp. A note's duration depends on how many
        # notes are in its *whole* measure, which isn't known until the
        # measure's closing comma is reached; a streaming pass that advances
        # time command-by-command as it's encountered ends up using a stale
        # total_time for anything after notes that haven't been "paid for"
        # yet, which is what made GOGO regions land in the wrong place when
        # #GOGOSTART/#GOGOEND fell between two note-bearing lines.
        events = []
        has_branches = False
        # branch_active gates only NOTE/COMMA below - a non-selected branch's
        # commas don't count as measure boundaries either, so a chart with
        # branching ends up with exactly one branch's worth of measures/
        # duration instead of all three concatenated. See the branching note
        # in this method's docstring.
        branch_active = True
        for idx in range(a + 1, b):
            s = lines[idx - 1].split("//")[0].strip()
            if not s:
                continue
            if s.startswith("#"):
                if s.startswith("#BPMCHANGE"):
                    try:
                        events.append(("BPMCHANGE", Decimal(s.split()[1])))
                    except Exception:
                        pass
                elif s.startswith("#MEASURE"):
                    m = re.search(r"(\d+)/(\d+)", s)
                    if m and Decimal(m.group(2)) != 0:   # 分母0は無視(_analyze と同じ)
                        events.append(("MEASURE", (Decimal(m.group(1)), Decimal(m.group(2)))))
                elif s.startswith("#DELAY"):
                    try:
                        # #DELAY は「足す」命令なので、選んでいない系統のぶんまで
                        # 拾うと分岐の数だけ待ち時間が増える(3系統なら3倍)。
                        # 同じ理由で、見た目だけの #SCROLL / ゴーゴー / 小節線の
                        # 表示切替も、いま流している系統のものだけを効かせる。
                        # #BPMCHANGE と #MEASURE は系統をまたいで同じでないと
                        # そもそも譜面が成立しないので、従来どおり系統を問わず
                        # 適用する(取りこぼしへの保険)。
                        if branch_active:
                            events.append(("DELAY", Decimal(s.split()[1])))
                    except Exception:
                        pass
                elif s.startswith("#SCROLL"):
                    try:
                        if branch_active:
                            events.append(("SCROLL", Decimal(s.split()[1])))
                    except Exception:
                        pass
                elif s.startswith("#GOGOSTART"):
                    if branch_active:
                        events.append(("GOGOSTART", None))
                elif s.startswith("#GOGOEND"):
                    if branch_active:
                        events.append(("GOGOEND", None))
                elif s.startswith("#BARLINEOFF"):
                    if branch_active:
                        events.append(("BARLINEOFF", None))
                elif s.startswith("#BARLINEON"):
                    if branch_active:
                        events.append(("BARLINEON", None))
                elif s.startswith("#BRANCHSTART"):
                    has_branches = True
                    branch_active = False  # nothing counts until the first #N/#E/#M
                elif s.startswith("#BRANCHEND"):
                    branch_active = True
                elif s == "#N":
                    branch_active = (branch_level == "N")
                elif s == "#E":
                    branch_active = (branch_level == "E")
                elif s == "#M":
                    branch_active = (branch_level == "M")
                continue
            for c in s:
                if c in "0123456789":
                    if not branch_active:
                        continue
                    events.append(("NOTE", c))
                elif c == ",":
                    if not branch_active:
                        continue
                    events.append(("COMMA", None))

        measures = []
        cur_m = []
        for ev in events:
            if ev[0] == "COMMA":
                measures.append(cur_m)
                cur_m = []
            else:
                cur_m.append(ev)
        if cur_m:
            measures.append(cur_m)

        total_time = Decimal("0")
        curr_bpm = bpm
        measure_val = Decimal("1")
        curr_num, curr_den = Decimal(4), Decimal(4)
        curr_scroll = Decimal(1)
        curr_bar_visible = True
        notes = []
        rolls = []
        balloons = []
        kusudamas = []
        bar_times = []
        gogo_regions = []
        bpm_changes = [(Decimal(0), bpm)]
        measure_changes = [(Decimal(0), curr_num, curr_den)]
        scroll_changes = [(Decimal(0), curr_scroll)]
        gogo_start = None
        active_roll = None
        active_balloon = None
        active_kusudama = None
        balloon_idx = 0

        for m_events in measures:
            # Recorded once we reach this measure's first NOTE (or at the
            # end, if it has none) rather than unconditionally up front - a
            # #SCROLL/#BPMCHANGE/#MEASURE command placed before this
            # measure's notes (the usual TJA style) takes effect *for this
            # measure*, so the bar line marking its start should reflect
            # that new value too, not the previous measure's, which is what
            # made the bar line look like it was lagging a beat behind.
            bar_recorded = False
            n_len = sum(1 for t, _ in m_events if t == "NOTE")
            for t, v in m_events:
                if not bar_recorded and t == "NOTE":
                    bar_times.append((total_time, curr_bpm, curr_scroll, curr_bar_visible))
                    bar_recorded = True
                if t == "BPMCHANGE":
                    curr_bpm = v
                    bpm_changes.append((total_time, curr_bpm))
                elif t == "MEASURE":
                    if v[1] == 0:      # 分母0は取り込み側で弾いているが二重に防ぐ
                        continue
                    curr_num, curr_den = v
                    measure_val = curr_num / curr_den
                    measure_changes.append((total_time, curr_num, curr_den))
                elif t == "DELAY":
                    total_time += v
                elif t == "SCROLL":
                    curr_scroll = v
                    scroll_changes.append((total_time, curr_scroll))
                elif t == "GOGOSTART":
                    if gogo_start is None:
                        gogo_start = total_time
                elif t == "GOGOEND":
                    if gogo_start is not None:
                        gogo_regions.append((gogo_start, total_time))
                        gogo_start = None
                elif t == "BARLINEOFF":
                    curr_bar_visible = False
                elif t == "BARLINEON":
                    curr_bar_visible = True
                elif t == "NOTE":
                    time_per_note = Decimal(240) * measure_val / curr_bpm / n_len if (n_len > 0 and curr_bpm > 0) else Decimal("0")
                    if v in "1234":
                        notes.append((total_time, v, curr_bpm, curr_scroll))
                    elif v in "56":
                        active_roll = (total_time, v, curr_bpm, curr_scroll)
                    elif v == "7":
                        hits = balloon_defs[balloon_idx] if balloon_idx < len(balloon_defs) else 0
                        active_balloon = (total_time, hits, curr_bpm, curr_scroll)
                        balloon_idx += 1
                    elif v == "9":
                        hits = balloon_defs[balloon_idx] if balloon_idx < len(balloon_defs) else 0
                        active_kusudama = (total_time, hits, curr_bpm, curr_scroll)
                        balloon_idx += 1
                    elif v == "8":
                        if active_roll is not None:
                            dur = float(total_time - active_roll[0])
                            hits = self._roll_hits(dur)
                            rolls.append((active_roll[0], total_time, active_roll[1], active_roll[2], active_roll[3], hits))
                            active_roll = None
                        elif active_balloon is not None:
                            balloons.append((active_balloon[0], total_time, active_balloon[2], active_balloon[3], active_balloon[1]))
                            active_balloon = None
                        elif active_kusudama is not None:
                            kusudamas.append((active_kusudama[0], total_time, active_kusudama[2], active_kusudama[3], active_kusudama[1]))
                            active_kusudama = None
                    total_time += time_per_note
            if not bar_recorded:
                bar_times.append((total_time, curr_bpm, curr_scroll, curr_bar_visible))
                # n_len == 0 here (that's exactly when no NOTE event ever ran
                # to set bar_recorded) - a measure with no notes still
                # occupies real time, but nothing above advances total_time
                # for it. Without this, an empty leading (or any empty)
                # measure got skipped entirely and everything after it played
                # early by exactly that measure's length.
                if curr_bpm > 0:
                    total_time += Decimal(240) * measure_val / curr_bpm

        if active_roll is not None:
            dur = float(total_time - active_roll[0])
            rolls.append((active_roll[0], total_time, active_roll[1], active_roll[2], active_roll[3], self._roll_hits(dur)))
        if active_balloon is not None:
            balloons.append((active_balloon[0], total_time, active_balloon[2], active_balloon[3], active_balloon[1]))
        if active_kusudama is not None:
            kusudamas.append((active_kusudama[0], total_time, active_kusudama[2], active_kusudama[3], active_kusudama[1]))
        if gogo_start is not None:
            gogo_regions.append((gogo_start, total_time))

        out_notes = [(float(t), c, float(bpm_), float(sc)) for t, c, bpm_, sc in notes]
        out_rolls = [(float(s0), float(e0), c, float(bpm_), float(sc), hits) for s0, e0, c, bpm_, sc, hits in rolls]
        out_balloons = [(float(s0), float(e0), float(bpm_), float(sc), hits) for s0, e0, bpm_, sc, hits in balloons]
        out_kusudamas = [(float(s0), float(e0), float(bpm_), float(sc), hits) for s0, e0, bpm_, sc, hits in kusudamas]
        # 打音表記 (SE text) is classified here, once per chart edit, rather
        # than in ChartPreviewWidget.paintEvent - that repaints at up to
        # 144 Hz and must not run an O(n) neighbour scan per frame. See
        # neotja/se_text.py for the ported PeepoDrumKit algorithm.
        se_labels, roll_se, balloon_se, kusudama_se = compute_se_labels(
            out_notes, out_rolls, out_balloons, out_kusudamas)
        # 連打・風船・くす玉の頭に出す打音表記。音符の tuple と違って各種
        # スパンの tuple には足さない — 帯の tuple は形を決め打ちで読んで
        # いる箇所が多いので、独立した列として持たせる。
        # (時刻, 表記, 大か, BPM, SCROLL) で、x はレーン側が速度から出す。
        span_se = ([(float(r[0]), lb, r[2] == "6", float(r[3]), float(r[4]))
                    for r, lb in zip(out_rolls, roll_se) if lb]
                   + [(float(b[0]), lb, False, float(b[2]), float(b[3]))
                      for b, lb in zip(out_balloons, balloon_se) if lb]
                   + [(float(k[0]), lb, False, float(k[2]), float(k[3]))
                      for k, lb in zip(out_kusudamas, kusudama_se) if lb])
        span_se.sort(key=lambda e: e[0])

        return {
            "notes": [n + (se,) for n, se in zip(out_notes, se_labels)],
            "span_se": span_se,
            "rolls": out_rolls,
            "balloons": out_balloons,
            "kusudamas": out_kusudamas,
            "gogo_regions": [(float(s0), float(e0)) for s0, e0 in gogo_regions],
            # 風船が割れるまでの時間を出すのに使う秒間打数(環境設定の連打秒速)。
            # 譜面と一緒に持たせておくと、描く側が設定を読み直さずに済む。
            "roll_hit_speed": float(self.config_data.get("roll_speed", 45)),
            # 曲名(録画画面の右上に出す)。ヘッダは曲に1つなので素直に拾う。
            "title": _header_value(content, "TITLE"),
            "bar_times": [(float(t), float(bpm_), float(sc), bool(vis)) for t, bpm_, sc, vis in bar_times],
            "bpm_changes": [(float(t), float(v)) for t, v in bpm_changes],
            "measure_changes": [(float(t), int(num), int(den)) for t, num, den in measure_changes],
            "scroll_changes": [(float(t), float(v)) for t, v in scroll_changes],
            "course_key": sel_key,
            "course_label": self.DIFF_LABEL.get(sel_key, sel_key),
            "course_color": self.DIFF_COLOR.get(sel_key, COLORS["fg"]),
            "level": sel_level,
            "available_courses": available_courses,
            "has_branches": has_branches,
            "branch_level": branch_level,
        }
