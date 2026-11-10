"""Short balloon/kusudama audio and displayed actual-hit regression checks."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from neotja import settings


class ShortBalloonTicksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = settings.settings_path()
        cls.before = hashlib.sha256(cls.real.read_bytes()).digest() if cls.real.exists() else None
        cls.original_path = settings.settings_path
        cls.scratch = tempfile.TemporaryDirectory(prefix='neotja-balloon-test-')
        target = Path(cls.scratch.name) / 'settings.json'
        target.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
        settings.settings_path = lambda: target

    @classmethod
    def tearDownClass(cls):
        settings.settings_path = cls.original_path
        cls.scratch.cleanup()
        after = hashlib.sha256(cls.real.read_bytes()).digest() if cls.real.exists() else None
        assert after == cls.before, 'User settings changed'

    def test_audio_and_actual_count_match_short_and_long_spans(self):
        from neotja.chart_preview_widget import ChartPreviewWidget
        from neotja.preview_dock import balloon_tick_notes, balloon_pop_schedule
        from neotja.tja_analyzer import TJACourseAnalyzer, balloon_pop_spans

        for head, group in [('7', 'balloons'), ('9', 'kusudamas')]:
            for zeros, expected in [(1, 5), (2, 8)]:
                with self.subTest(head=head, zeros=zeros):
                    seq = head + '0' * zeros + '8' + '0' * (14 - zeros)
                    text = 'BPM:240\nBALLOON:8\nCOURSE:Oni\n#START\n' + seq + ',\n#END'
                    data = TJACourseAnalyzer({'roll_speed': 45}).build_preview_timeline(text)
                    spans = data[group]
                    ticks = balloon_tick_notes(spans, 45)
                    pops = balloon_pop_schedule(data)
                    times = sorted([t[0] for t in ticks] + pops)
                    self.assertEqual(len(times), expected)
                    self.assertEqual(len(pops), int(zeros == 2))
                    for i, t in enumerate(times):
                        self.assertAlmostEqual(t, i / 45)
                    view = SimpleNamespace(_rolls=[], _balloons=[], _kusudamas=[], _roll_hit_speed=45)
                    setattr(view, '_' + group, balloon_pop_spans(spans, 45))
                    count = lambda t: ChartPreviewWidget._cumulative_hits(view, t)
                    self.assertEqual(count(-0.001), 0)
                    for i, t in enumerate(times):
                        self.assertEqual(count(t), i + 1)
                        if i:
                            self.assertEqual(count(t - 1e-5), i)
                    self.assertEqual(count(spans[0][1] + 1), expected)

    def test_normal_roll_compensation_is_preserved(self):
        from neotja.tja_analyzer import TJACourseAnalyzer
        from neotja.chart_preview_widget import ChartPreviewWidget
        analyzer = TJACourseAnalyzer({'roll_speed': 45})
        self.assertEqual(analyzer._roll_hits(0.1), 6)
        view = SimpleNamespace(_rolls=[(0, 0.1, '5', 120, 1, 6)],
                               _balloons=[], _kusudamas=[], _roll_hit_speed=45)
        self.assertEqual(ChartPreviewWidget._cumulative_hits(view, 0.1), 6)


if __name__ == '__main__':
    unittest.main()
