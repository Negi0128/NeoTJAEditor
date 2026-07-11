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
                    if m:
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

    def build_preview_timeline(self, content: str, cursor_line: int = None, course_key: str = None,
                                branch_level: str = "M") -> dict:
        """Returns everything the real-time scrolling chart preview needs.
        Course selection: `course_key` (e.g. "Oni") wins if given and present;
        otherwise whichever course contains cursor_line; otherwise the first
        course in the file. Uses the same #BPMCHANGE/#MEASURE/#DELAY timing
        math as build_metronome_clicks:
          - "notes": [(chart_time_seconds, char, bpm, scroll)] for don/ka/big-don/big-ka ('1'-'4')
          - "rolls": [(start_seconds, end_seconds, char, bpm, scroll, hits)] for roll/
            big-roll ('5'/'6') spans closed by a '8' tail (an unclosed roll runs to the
            end of the course)
          - "gogo_regions": [(start_seconds, end_seconds)] from #GOGOSTART/#GOGOEND
          - "bar_times": [(chart_time_seconds, bpm, scroll), ...] one entry per measure boundary
          - "balloons": [(start_seconds, end_seconds, bpm, scroll, hits)] for '7' spans closed by '8'
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
        Commands (#BPMCHANGE/#MEASURE/#DELAY/#SCROLL/#GOGOSTART/#GOGOEND)
        apply regardless of which branch they're nested under, since tempo
        rarely if ever differs between branches in real charts.
        Each note/roll/bar carries the BPM active when it occurred so the
        preview widget can space notes by beat (PeepoDrumKit-style: pixel
        speed scales with tempo) instead of a single fixed real-time scroll
        speed, which would otherwise cram fast sections and over-space slow
        ones. Kusudama ('9') still consumes timing like any other digit but
        isn't emitted here yet."""
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
            "notes": [], "rolls": [], "balloons": [], "gogo_regions": [], "bar_times": [],
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
                    if m:
                        events.append(("MEASURE", (Decimal(m.group(1)), Decimal(m.group(2)))))
                elif s.startswith("#DELAY"):
                    try:
                        events.append(("DELAY", Decimal(s.split()[1])))
                    except Exception:
                        pass
                elif s.startswith("#SCROLL"):
                    try:
                        events.append(("SCROLL", Decimal(s.split()[1])))
                    except Exception:
                        pass
                elif s.startswith("#GOGOSTART"):
                    events.append(("GOGOSTART", None))
                elif s.startswith("#GOGOEND"):
                    events.append(("GOGOEND", None))
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
        notes = []
        rolls = []
        balloons = []
        bar_times = []
        gogo_regions = []
        bpm_changes = [(Decimal(0), bpm)]
        measure_changes = [(Decimal(0), curr_num, curr_den)]
        scroll_changes = [(Decimal(0), curr_scroll)]
        gogo_start = None
        active_roll = None
        active_balloon = None
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
                    bar_times.append((total_time, curr_bpm, curr_scroll))
                    bar_recorded = True
                if t == "BPMCHANGE":
                    curr_bpm = v
                    bpm_changes.append((total_time, curr_bpm))
                elif t == "MEASURE":
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
                    elif v == "8":
                        if active_roll is not None:
                            dur = float(total_time - active_roll[0])
                            hits = self._roll_hits(dur)
                            rolls.append((active_roll[0], total_time, active_roll[1], active_roll[2], active_roll[3], hits))
                            active_roll = None
                        elif active_balloon is not None:
                            balloons.append((active_balloon[0], total_time, active_balloon[2], active_balloon[3], active_balloon[1]))
                            active_balloon = None
                    total_time += time_per_note
            if not bar_recorded:
                bar_times.append((total_time, curr_bpm, curr_scroll))
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
        if gogo_start is not None:
            gogo_regions.append((gogo_start, total_time))

        return {
            "notes": [(float(t), c, float(bpm_), float(sc)) for t, c, bpm_, sc in notes],
            "rolls": [(float(s0), float(e0), c, float(bpm_), float(sc), hits) for s0, e0, c, bpm_, sc, hits in rolls],
            "balloons": [(float(s0), float(e0), float(bpm_), float(sc), hits) for s0, e0, bpm_, sc, hits in balloons],
            "gogo_regions": [(float(s0), float(e0)) for s0, e0 in gogo_regions],
            "bar_times": [(float(t), float(bpm_), float(sc)) for t, bpm_, sc in bar_times],
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
