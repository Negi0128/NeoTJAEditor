from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont

from neotja.constants import VALID_MEASURE_COUNTS
from neotja.theme import COLORS
from neotja.tja_analyzer import TJACourseAnalyzer


class HighlightData:
    """Result of one whole-document analysis pass, consumed by TJAHighlighter.
    All line numbers are 1-based (matching QTextBlock.blockNumber() + 1)."""

    def __init__(self):
        self.global_warnings = []
        self.invalid_lines = {}          # line -> note count that violated VALID_MEASURE_COUNTS
        self.warn_lines = set()          # lines needing the "consecutive #SCROLL" warning background
        self.cmd_lines = set()           # lines that are a whole "#..." command
        self.comment_ranges = {}         # line -> col where a "//" comment starts
        self.header_ranges = {}          # line -> col of the "KEY:VALUE" separator
        self.digit_spans = {}            # line -> [(start_col, end_col, tag)]
        self.color_band_spans = {}       # line -> [(start_col, end_col_or_None, tag)]  (roll/roll_big/balloon_tag, may span lines)
        self.hover_spans = {}            # line -> [(start_col, end_col, kind, index)]  kind = "roll" | "balloon"
        self.roll_hits = {}              # index -> {"duration": float, "hits": int}
        self.balloon_hits = {}           # index -> {"duration": float, "hits": int}


def _find_char_class(lines, start_line, start_col, end_line, chars):
    for ln in range(start_line, end_line + 1):
        row = lines[ln - 1]
        col0 = start_col if ln == start_line else 0
        for col in range(col0, len(row)):
            if row[col] in chars:
                return ln, col
    return None


def _add_band(data, sline, scol, eline, ecol_exclusive, tag):
    if sline == eline:
        data.color_band_spans.setdefault(sline, []).append((scol, ecol_exclusive, tag))
    else:
        data.color_band_spans.setdefault(sline, []).append((scol, None, tag))
        for ln in range(sline + 1, eline):
            data.color_band_spans.setdefault(ln, []).append((0, None, tag))
        data.color_band_spans.setdefault(eline, []).append((0, ecol_exclusive, tag))


def compute_highlight_data(content: str, courses_info: list) -> HighlightData:
    lines = content.split('\n')
    data = HighlightData()

    sc = content.count("#START")
    ec = content.count("#END")
    if sc != ec:
        data.global_warnings.append(f"⚠ START/END 不一致 ({sc}/{ec})")

    gs = content.count("#GOGOSTART")
    ge = content.count("#GOGOEND")
    if gs > ge:
        data.global_warnings.append(f"⚠ GOGOEND 不足 ({gs}/{ge})")

    score_ranges = []
    in_r = False
    sl = 0
    current_course = "Oni"

    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith("COURSE:"):
            current_course = TJACourseAnalyzer.DIFF.get(s[7:].strip(), "Oni")
        elif s.startswith("#START"):
            in_r = True
            sl = i
        elif s.startswith("#END") and in_r:
            score_ranges.append((sl, i, current_course))
            in_r = False

    m_count = 0
    m_lines = set()
    prev_line_is_scroll = False
    prev_scroll_line = None

    for i, line in enumerate(lines, 1):
        s = line.strip()
        in_score = any(sl < i < el for sl, el, _ in score_ranges)

        if s.startswith("#"):
            data.cmd_lines.add(i)
            if s.startswith("#SCROLL"):
                if prev_line_is_scroll:
                    data.warn_lines.add(prev_scroll_line)
                    data.warn_lines.add(i)
                prev_line_is_scroll = True
                prev_scroll_line = i
            else:
                prev_line_is_scroll = False
            continue

        if not s or s.startswith("//"):
            prev_line_is_scroll = False
            if "//" in line:
                data.comment_ranges[i] = line.index("//")
            continue

        code = line.split("//")[0]
        if any(c in "0123456789," for c in code):
            prev_line_is_scroll = False

        if in_score:
            spans = []
            for ci, ch in enumerate(code):
                if ch in "1234" or ch in "09":
                    tag = f"num_{ch}"
                    # Merge with the previous span when it's an adjacent run of
                    # the same note digit (e.g. "0000"), instead of emitting
                    # one span per character - highlightBlock() below turns
                    # each span into a QSyntaxHighlighter.setFormat() call,
                    # and those add up fast on long charts.
                    if spans and spans[-1][2] == tag and spans[-1][1] == ci:
                        spans[-1] = (spans[-1][0], ci + 1, tag)
                    else:
                        spans.append((ci, ci + 1, tag))
                if ch == ",":
                    if m_count > 0 and m_count not in VALID_MEASURE_COUNTS:
                        for ml in m_lines:
                            data.invalid_lines[ml] = m_count
                        data.invalid_lines[i] = m_count
                    m_count = 0
                    m_lines = set()
                elif ch in "0123456789":
                    m_count += 1
                    m_lines.add(i)
            if spans:
                data.digit_spans[i] = spans
        else:
            if ":" in code and not s.startswith("//"):
                data.header_ranges[i] = code.index(":")

    courses_dict = {c["key"]: c for c in courses_info}
    b_idx = 0
    r_idx = 0

    for sl, el, cname in score_ranges:
        c_info = courses_dict.get(cname, {})
        c_rolls = c_info.get("rolls_info", [])
        c_balloons = c_info.get("balloons_info", [])
        r_local = 0
        b_local = 0

        cur_line, cur_col = sl, 0
        while True:
            pos = _find_char_class(lines, cur_line, cur_col, el, "567")
            if pos is None:
                break
            pline, pcol = pos
            row_text = lines[pline - 1]
            if row_text.strip().startswith("#"):
                cur_line, cur_col = pline, pcol + 1
                continue
            ch = row_text[pcol]

            end_pos = _find_char_class(lines, pline, pcol, el, "8")

            if ch in "56":
                tag = "roll" if ch == "5" else "roll_big"
                data.hover_spans.setdefault(pline, []).append((pcol, pcol + 1, "roll", r_idx))
                if r_local < len(c_rolls):
                    data.roll_hits[r_idx] = c_rolls[r_local]
                r_local += 1
                r_idx += 1
            else:
                tag = "balloon_tag"
                data.hover_spans.setdefault(pline, []).append((pcol, pcol + 1, "balloon", b_idx))
                if b_local < len(c_balloons):
                    data.balloon_hits[b_idx] = c_balloons[b_local]
                b_local += 1
                b_idx += 1

            if end_pos:
                eline, ecol = end_pos
                _add_band(data, pline, pcol, eline, ecol + 1, tag)
                cur_line, cur_col = eline, ecol + 1
            else:
                _add_band(data, pline, pcol, pline, pcol + 1, tag)
                cur_line, cur_col = pline, pcol + 1

    return data


class TJAHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.data: HighlightData = HighlightData()
        self.formats: dict = {}
        self.rebuild_formats()

    @staticmethod
    def _line_signature(data: "HighlightData", line: int):
        return (
            line in data.cmd_lines,
            line in data.warn_lines,
            data.comment_ranges.get(line),
            data.header_ranges.get(line),
            tuple(data.digit_spans.get(line, ())),
            tuple(data.color_band_spans.get(line, ())),
        )

    def apply_data(self, new_data: "HighlightData"):
        """Swaps in a freshly computed HighlightData, but only re-runs
        highlightBlock() for the lines whose formatting actually changed
        instead of the whole document. On a large real chart (tens of
        thousands of lines), rehighlight() alone can take over a second;
        a single edit usually only changes a handful of lines."""
        old_data = self.data
        self.data = new_data

        touched = set()
        for d in (old_data, new_data):
            touched.update(d.cmd_lines)
            touched.update(d.warn_lines)
            touched.update(d.comment_ranges.keys())
            touched.update(d.header_ranges.keys())
            touched.update(d.digit_spans.keys())
            touched.update(d.color_band_spans.keys())

        doc = self.document()
        total_blocks = max(1, doc.blockCount())

        # Individually rehighlighting many blocks can end up costing more
        # than one bulk pass; fall back to a full rehighlight when most of
        # the document is affected (e.g. loading a new file).
        if not touched:
            return
        changed = [ln for ln in touched if self._line_signature(old_data, ln) != self._line_signature(new_data, ln)]
        if len(changed) > max(50, total_blocks // 4):
            self.rehighlight()
            return

        for ln in changed:
            block = doc.findBlockByNumber(ln - 1)
            if block.isValid():
                self.rehighlightBlock(block)

    def rebuild_formats(self):
        def fmt(color_key, bold=False):
            f = QTextCharFormat()
            f.setForeground(QColor(COLORS[color_key]))
            if bold:
                f.setFontWeight(QFont.Bold)
            return f

        warn_fmt = QTextCharFormat()
        warn_fmt.setBackground(QColor(COLORS["warn"]))
        warn_fmt.setForeground(QColor("#000000"))

        self.formats = {
            "num_1": fmt("don"),
            "num_2": fmt("ka"),
            "num_3": fmt("don", bold=True),
            "num_4": fmt("ka", bold=True),
            "num_0": fmt("zero"),
            "num_9": fmt("zero"),
            "roll": fmt("roll"),
            "roll_big": fmt("roll", bold=True),
            "balloon_tag": fmt("balloon"),
            "cmd": fmt("cmd"),
            "header_key": fmt("header_key"),
            "header_val": fmt("header_val"),
            "comment": fmt("comment"),
            "warn": warn_fmt,
        }

    def highlightBlock(self, text: str) -> None:
        line = self.currentBlock().blockNumber() + 1
        data = self.data

        if line in data.cmd_lines:
            self.setFormat(0, len(text), self.formats["cmd"])
            if line in data.warn_lines:
                self.setFormat(0, len(text), self.formats["warn"])
            return

        if line in data.comment_ranges:
            ci = data.comment_ranges[line]
            self.setFormat(ci, len(text) - ci, self.formats["comment"])
            return

        if line in data.header_ranges:
            ci = data.header_ranges[line]
            self.setFormat(0, ci, self.formats["header_key"])
            self.setFormat(ci + 1, len(text) - (ci + 1), self.formats["header_val"])

        for start, end, tag in data.digit_spans.get(line, []):
            self.setFormat(start, end - start, self.formats[tag])

        for start, end, tag in data.color_band_spans.get(line, []):
            end = end if end is not None else len(text)
            self.setFormat(start, end - start, self.formats[tag])
