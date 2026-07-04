import re
from decimal import Decimal

from neotja.theme import COLORS


class TJACourseAnalyzer:
    DIFF = {"0": "Easy", "Easy": "Easy", "1": "Normal", "Normal": "Normal",
            "2": "Hard", "Hard": "Hard", "3": "Oni", "Oni": "Oni", "4": "Edit", "Edit": "Edit"}
    DIFF_LABEL = {"Easy": "かんたん", "Normal": "ふつう", "Hard": "むずかしい", "Oni": "おに", "Edit": "おに(裏)"}
    DIFF_COLOR = {"Easy": "#55efc4", "Normal": "#74b9ff", "Hard": "#ffeaa7", "Oni": "#ff7675", "Edit": "#a29bfe"}

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
