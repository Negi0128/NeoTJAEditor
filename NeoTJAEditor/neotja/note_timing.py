"""Exact note-position/time conversion for edits, including commands inside bars."""

from bisect import bisect_right
from fractions import Fraction

from .measure_math import parse_measure_lines


class TimingError(ValueError):
    pass


class NoteTiming:
    def __init__(self, text, body, spans):
        self.segments = []
        self.points = {}
        self.bpm = Fraction(120)
        self.meter = Fraction(1)
        self.time = Fraction(0)
        for line in text[:body[0]].splitlines():
            if line.strip().upper().startswith('BPM:'):
                self.bpm = Fraction(line.split(':', 1)[1].split('//', 1)[0].strip())
                break
        for m, (a, b) in enumerate(spans):
            parsed = parse_measure_lines(text[a:b])
            measure = next((p for p in parsed if p['type'] == 'measure'), None)
            if measure is None:
                continue
            leading = []
            for p in parsed:
                if p is measure:
                    break
                if p['type'] == 'keep':
                    leading.append(p['text'])
            cuts = [(Fraction(0), line) for line in leading]
            size = len(measure['notes']) or 1
            cuts += [(Fraction(i, size), line) for i, line in measure['breaks']]
            pos = Fraction(m)
            self.points[pos] = self.time
            for frac, line in cuts:
                end = Fraction(m) + frac
                self._segment(pos, end)
                pos = end
                self._command(line)
                self.points[pos] = self.time
            self._segment(pos, Fraction(m + 1))
        self.count = len(spans)
        self.end = self.time
        self.duration = self._duration()
        self.starts = [s[0] for s in self.segments]
        self.time_starts = [s[2] for s in self.segments]

    def _duration(self):
        if self.bpm <= 0 or self.meter <= 0:
            raise TimingError('BPM・拍子が不正なため移動できません。')
        return 240 * self.meter / self.bpm

    def _command(self, line):
        parts = line.split('//', 1)[0].strip().split(None, 1)
        if len(parts) != 2:
            return
        name, value = parts[0].upper(), parts[1].strip()
        try:
            if name == '#BPMCHANGE':
                self.bpm = Fraction(value)
            elif name == '#MEASURE':
                n, d = value.split('/')
                self.meter = Fraction(n.strip()) / Fraction(d.strip())
            elif name == '#DELAY':
                delay = Fraction(value)
                if delay < 0:
                    raise TimingError('負のDELAYをまたぐ時間基準の移動はできません。')
                self.time += delay
        except (ValueError, ZeroDivisionError) as exc:
            raise TimingError('時刻を計算できない命令があります。') from exc

    def _segment(self, start, end):
        if end <= start:
            return
        duration = self._duration()
        self.segments.append((start, end, self.time, duration))
        self.time += (end - start) * duration

    def time_at(self, pos):
        pos = Fraction(pos)
        if pos < 0:
            raise TimingError('譜面の先頭より前へは移動できません。')
        if pos >= self.count:
            return self.end + (pos - self.count) * self.duration
        if pos in self.points:
            return self.points[pos]
        i = bisect_right(self.starts, pos) - 1
        if i < 0:
            raise TimingError('移動元の時刻を計算できません。')
        start, end, time, duration = self.segments[i]
        return time + (pos - start) * duration

    def position_at(self, time):
        time = Fraction(time)
        if time < 0:
            raise TimingError('譜面の先頭より前へは移動できません。')
        if time >= self.end:
            return Fraction(self.count) + (time - self.end) / self.duration
        i = bisect_right(self.time_starts, time) - 1
        if i >= 0:
            start, end, at, duration = self.segments[i]
            if at <= time < at + (end - start) * duration:
                return start + (time - at) / duration
        raise TimingError('DELAYの待ち時間には音符を配置できません。')


# ---------------------------------------------------------------------------
# 本文から作る(解析スレッドからも呼ぶ)
# ---------------------------------------------------------------------------
#: 音符の数字を全部 0 にする表。時刻の計算に効くのは「小節の中の何文字目か」
#: だけで、どの音符かは効かない。
_DIGITS_TO_ZERO = str.maketrans("123456789", "000000000")

#: 直前に作ったもの (鍵, 結果)。音符を打っただけなら鍵が同じなので作り直さない
#: (密な譜面では1回 0.4 秒かかる。実測 2026-11-14)。
_timing_memo = (None, None)


def _timing_key(text, body, level, path):
    """NoteTiming の答えを決める物だけを並べた鍵。ヘッダはそのまま、本文は
    命令・コメントの行をそのまま、音符の行は数字を 0 に揃えて使う
    (数字の個数と位置、カンマ、命令の中身が同じなら時刻は同じ)。"""
    lines = text[body[0]:body[1]].split("\n")
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("#") or s.startswith("//"):
            out.append(line)
        else:
            out.append(line.translate(_DIGITS_TO_ZERO))
    return (text[:body[0]], "\n".join(out), body[0], level,
            tuple(path) if path else None)


def timing_for(analyzer, text, course_key, branch_level, branch_path):
    """作譜の位置変換に使う NoteTiming を本文から作る。作れなければ None。

    見ている系統の小節だけを数えるのは作譜の編集と同じ(note_edit.editing_branch
    と同じ決め方)。グローバルな editing_branch は使わない — 解析スレッドから
    呼ぶので、GUI スレッドの編集と取り合いになる。"""
    global _timing_memo
    from neotja import note_edit
    rng = analyzer.course_line_range(text, course_key)
    body = note_edit.course_body_span(text, rng) if rng else None
    if not body:
        return None
    if branch_level in note_edit.BRANCH_LEVELS:
        level = branch_level
    else:
        level = note_edit.BRANCH_AUTO if branch_path else None
    path = tuple(branch_path or ())
    key = _timing_key(text, body, level, path)
    mk, mv = _timing_memo
    if mk == key:
        return mv
    try:
        timing = NoteTiming(text, body,
                            note_edit.measure_spans(text, body, level, path))
    except (ValueError, ZeroDivisionError):
        timing = None
    _timing_memo = (key, timing)
    return timing
