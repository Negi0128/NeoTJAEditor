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
