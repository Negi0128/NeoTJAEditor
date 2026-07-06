import re
from decimal import Decimal

from neotja.theme import COLORS


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

    def _analyze(self, data, all_lines):
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
                    if m:
                        events.append(("#MEASURE", Decimal(m.group(1)) / Decimal(m.group(2))))
                elif line.startswith("#DELAY"):
                    try:
                        events.append(("#DELAY", Decimal(line.split()[1])))
                    except Exception:
                        pass
                continue

            for c in line:
                if c in "0123456789":
                    events.append(("NOTE", c))
                elif c == ",":
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
                    time_per_note = (Decimal("240") * measure_val / curr_bpm) / n_len if n_len > 0 else Decimal("0")
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
                            short_roll = self.config_data.get("short_roll_comp", "段階的補正 (60fps理論値)")
                            rs = self.config_data.get("roll_speed", 45)

                            if short_roll == "段階的補正 (60fps理論値)":
                                if dur <= 0.10:
                                    hits = int(dur * max(60, rs))
                                elif dur <= 0.15:
                                    hits = int(dur * max(55, rs))
                                else:
                                    hits = int(dur * rs)
                            elif short_roll == "段階的補正 (理論値-1)":
                                if dur <= 0.10:
                                    hits = int(dur * max(55, rs))
                                elif dur <= 0.15:
                                    hits = int(dur * max(50, rs))
                                else:
                                    hits = int(dur * rs)
                            else:
                                hits = int(dur * rs)

                            rolls_info.append({"duration": dur, "hits": hits})
                            roll_start_time = None
                        elif balloon_start_time is not None:
                            dur = float(total_time - balloon_start_time)
                            if balloons_info:
                                balloons_info[-1]["duration"] = dur
                            balloon_start_time = None
                    total_time += time_per_note

        f = float(total_time)
        m, s = divmod(int(f), 60)
        ms = int((total_time - int(total_time)) * 1000)
        time_str = f"{m}:{s:02}.{ms:03}"

        return {
            "notes": don + ka + big_don + big_ka,
            "measures": len(measures),
            "time": time_str,
            "rolls_info": rolls_info,
            "balloons_info": balloons_info,
        }

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
                    total_time += (Decimal("240") * measure_val / curr_bpm) / n_len if n_len > 0 else Decimal("0")

        for idx in range(a, b + 1):
            if idx == a or idx == b:
                continue
            s = lines[idx - 1].split("//")[0].strip()
            if not s:
                continue

            if found is None and idx >= line_no:
                found = total_time

            if s.startswith("#"):
                if s.startswith("#BPMCHANGE"):
                    try:
                        cur_events.append(("#BPMCHANGE", Decimal(s.split()[1])))
                    except Exception:
                        pass
                elif s.startswith("#MEASURE"):
                    m = re.search(r"(\d+)/(\d+)", s)
                    if m:
                        cur_events.append(("#MEASURE", Decimal(m.group(1)) / Decimal(m.group(2))))
                elif s.startswith("#DELAY"):
                    try:
                        cur_events.append(("#DELAY", Decimal(s.split()[1])))
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

        def flush():
            nonlocal total_time
            if curr_bpm <= 0:
                return
            quarter_sec = Decimal(60) / curr_bpm
            n_quarters = int(Decimal(4) * measure_val)  # truncates toward 0 == floor (measure_val > 0)
            for k in range(max(0, n_quarters)):
                clicks.append((total_time + k * quarter_sec, k == 0))
            total_time += Decimal(240) * measure_val / curr_bpm

        if course_bounds:
            target = None
            if cursor_line is not None:
                target = next((cb for cb in course_bounds if cb[0] <= cursor_line <= cb[1]), None)
            a, b = target if target is not None else course_bounds[0]

            for idx in range(a + 1, b):
                s = lines[idx - 1].split("//")[0].strip()
                if not s:
                    continue
                if s.startswith("#"):
                    if s.startswith("#BPMCHANGE"):
                        try:
                            curr_bpm = Decimal(s.split()[1])
                        except Exception:
                            pass
                    elif s.startswith("#MEASURE"):
                        m = re.search(r"(\d+)/(\d+)", s)
                        if m:
                            measure_val = Decimal(m.group(1)) / Decimal(m.group(2))
                    elif s.startswith("#DELAY"):
                        try:
                            total_time += Decimal(s.split()[1])
                        except Exception:
                            pass
                    continue
                for c in s:
                    if c == ",":
                        flush()

        min_dur = Decimal(str(min_duration_seconds))
        if curr_bpm > 0:
            quarter_sec = Decimal(60) / curr_bpm
            beat_i = 0
            while total_time < min_dur:
                clicks.append((total_time, beat_i % 4 == 0))
                total_time += quarter_sec
                beat_i += 1

        return [(float(t), is_measure) for t, is_measure in clicks]
