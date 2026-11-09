import unittest
from fractions import Fraction

from neotja import note_edit as edit
from neotja.note_timing import NoteTiming, TimingError
from neotja.tja_analyzer import TJACourseAnalyzer


def chart(body, bpm=120):
    return 'BPM:%s\nCOURSE:oni\n#START\n%s\n#END\n' % (bpm, body)


def bounds(text):
    lines = text.splitlines()
    return lines.index('#START') + 1, lines.index('#END') + 1


def timeline(text):
    body = edit.course_body_span(text, bounds(text))
    return NoteTiming(text, body, edit.measure_spans(text, body))


class MoveTimingTests(unittest.TestCase):
    def moved(self, text, positions, steps, grids=16):
        items = [{'kind': 'note', 'pos': (p.numerator, p.denominator)}
                 for p in positions]
        result = edit.op_move_items(text, bounds(text), items, steps, grids)
        self.assertIsNotNone(result)
        old = TJACourseAnalyzer({}).build_preview_timeline(text, 4)['notes']
        new = TJACourseAnalyzer({}).build_preview_timeline(result['text'], 4)['notes']
        self.assertEqual([n[1] for n in old], [n[1] for n in new])
        shifts = [b[0] - a[0] for a, b in zip(old, new)]
        for shift in shifts:
            self.assertAlmostEqual(shift, shifts[0], places=9)
        return result

    def test_24ths_moved_on_16th_grid_keep_four_notes(self):
        self.moved(chart('2221' + '0' * 20 + ',\n0,'),
                   [Fraction(i, 24) for i in range(4)], 1)

    def test_cross_measure_and_tempo_preserves_seconds(self):
        for tempo in ('', '\n#BPMCHANGE 240'):
            with self.subTest(tempo=tempo):
                self.moved(chart('0' * 20 + '2221,\n#MEASURE 3/4' + tempo
                                 + '\n0,\n#MEASURE 4/4\n0,'),
                           [Fraction(i, 24) for i in range(20, 24)], 4,
                           edit._Grids(16, {0: 16, 1: 12, 2: 16}))

    def test_bpm_inside_bar_is_not_linear_interpolation(self):
        text = chart('12\n#BPMCHANGE 240\n21,\n0,\n0,')
        timing = timeline(text)
        self.assertEqual(timing.time_at(Fraction(0)), 0)
        self.assertEqual(timing.time_at(Fraction(1, 2)), 1)
        self.assertEqual(timing.time_at(Fraction(3, 4)), Fraction(5, 4))
        self.moved(text, [Fraction(i, 4) for i in range(4)], 16)

    def test_long_note_duration_survives_tempo_change(self):
        text = chart('5000000080000000,\n#BPMCHANGE 240\n0,\n0,')
        result = edit.op_move_items(text, bounds(text),
                                   [{'kind': 'note', 'pos': (0, 1)}], 16)
        analyzer = TJACourseAnalyzer({})
        old = analyzer.build_preview_timeline(text, 4)['rolls'][0]
        new = analyzer.build_preview_timeline(result['text'], 4)['rolls'][0]
        self.assertAlmostEqual(old[1] - old[0], new[1] - new[0])

    def test_unrepresentable_move_is_rejected_before_writing(self):
        text = chart('222100000000000000000000,\n#BPMCHANGE 997\n0,\n0,')
        with self.assertRaises(TimingError):
            edit.op_move_items(text, bounds(text),
                               [{'kind': 'note', 'pos': (i, 24)} for i in range(4)], 16)

    def test_delay_gap_is_not_a_note_position(self):
        text = chart('11\n#DELAY 0.5\n11,')
        with self.assertRaises(TimingError):
            timeline(text).position_at(Fraction(5, 4))


if __name__ == '__main__':
    unittest.main()
