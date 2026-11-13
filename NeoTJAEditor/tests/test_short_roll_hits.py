"""短い連打の打数の回帰テスト(利用者の報告 2026-11-13)。

BPM の速い2マスの連打が「短い連打の補正」(秒速50〜60)で数えられ、
連打秒速より多く、しかも長い連打より多くなることがあった。今は
打数 = max(1, 切り捨て(長さ × 連打秒速)) の一本で、コースカード・打音・
スコアの加算が同じ数を使う。"""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from neotja import settings

# (譜面の1小節16マス, BPM, 連打秒速, 打数)
ROLL_CASES = [
    ('5080', 240, 45, 5), ('5008', 240, 45, 8),
    ('5080', 240, 40, 5), ('5008', 240, 40, 7),
    ('5080', 240, 30, 3), ('5008', 240, 30, 5),
    ('5080', 200, 40, 6), ('5008', 200, 40, 9),
    ('5080', 120, 45, 11), ('5800', 120, 45, 5),
    ('5800', 240, 30, 1),
]


def chart(head, bpm):
    seq = head + '0' * (16 - len(head))
    return f'BPM:{bpm}\nBALLOON:8\nCOURSE:Oni\n#START\n{seq},\n#END'


class ShortRollHitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = settings.settings_path()
        cls.before = hashlib.sha256(cls.real.read_bytes()).digest() if cls.real.exists() else None
        cls.original_path = settings.settings_path
        cls.scratch = tempfile.TemporaryDirectory(prefix='neotja-roll-test-')
        target = Path(cls.scratch.name) / 'settings.json'
        target.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
        settings.settings_path = lambda: target

    @classmethod
    def tearDownClass(cls):
        settings.settings_path = cls.original_path
        cls.scratch.cleanup()
        after = hashlib.sha256(cls.real.read_bytes()).digest() if cls.real.exists() else None
        assert after == cls.before, 'User settings changed'

    def _analyzer(self, rs):
        from neotja.tja_analyzer import TJACourseAnalyzer
        # 古い設定ファイルに補正が残っていても無視されること。
        return TJACourseAnalyzer({'roll_speed': rs,
                                  'short_roll_comp': '段階的補正 (理論値-1)'})

    def test_roll_hits_agree_everywhere(self):
        from neotja.preview_dock import _roll_tick_notes
        from neotja.score import ScoreTimeline
        for head, bpm, rs, expected in ROLL_CASES:
            with self.subTest(head=head, bpm=bpm, rs=rs):
                an = self._analyzer(rs)
                text = chart(head, bpm)
                data = an.build_preview_timeline(text)
                rolls = data['rolls']
                self.assertEqual(len(rolls), 1)
                self.assertEqual(rolls[0][-1], expected)
                self.assertEqual(len(_roll_tick_notes(rolls, bpm_index=3)), expected)
                tl = ScoreTimeline({'rolls': rolls, 'roll_hit_speed': rs})
                self.assertEqual(len(tl.events_in(-1.0, 100.0, limit=1000)), expected)
                card = an.parse_courses(text)[0]
                self.assertEqual(card['rolls_info'][0]['hits'], expected)

    def test_longer_roll_never_gets_fewer_hits(self):
        for rs in (30, 40, 45):
            an = self._analyzer(rs)
            prev = 0
            for ms in range(1, 2001):
                n = an._roll_hits(ms / 1000.0)
                self.assertGreaterEqual(n, prev, (rs, ms))
                self.assertGreaterEqual(n, 1)
                prev = n

    # --- 風船(BALLOON:8, BPM240, 秒速45) ---

    def _balloon_view(self, data, rs):
        from neotja.chart_preview_widget import ChartPreviewWidget as W
        from neotja.tja_analyzer import balloon_pop_spans
        view = SimpleNamespace(_rolls=[], _kusudamas=[], _roll_hit_speed=rs,
                               _play_state=None, _note_times=[], _reveal_time=None)
        view._balloons = balloon_pop_spans(data['balloons'], rs)
        view._span_starts = ([], [b[0] for b in view._balloons], [])
        for name in ('_span_interval', '_span_hits', '_cumulative_hits',
                     '_live_top_count', 'recent_drum_hits', 'roll_tick'):
            setattr(view, name, getattr(W, name).__get__(view))
        return view

    def test_short_balloon_sounds_flashes_and_count_agree(self):
        from neotja.preview_dock import (balloon_pop_schedule, balloon_tick_notes,
                                         hit_notes_from_preview)
        rs = 45
        for head, dons, pops in [('7080', 5, 0), ('7008', 7, 1)]:
            with self.subTest(head=head):
                data = self._analyzer(rs).build_preview_timeline(chart(head, 240))
                ticks = balloon_tick_notes(data['balloons'], rs)
                self.assertEqual(len(ticks), dons)
                self.assertEqual(len(balloon_pop_schedule(data)), pops)
                # 本文を読み直した経路(hit_notes_from_preview)と
                # set_preview_data の経路で同じ打音になること。
                self.assertEqual(sorted(hit_notes_from_preview(data)), sorted(ticks))
                view = self._balloon_view(data, rs)
                start, end = view._balloons[0][0], view._balloons[0][1]
                flashes = view.recent_drum_hits(float(end) + 0.5, 10.0)
                self.assertEqual(len(flashes), dons + pops)
                # roll_tick が数える番号も同じ範囲に収まる。
                seen = {view.roll_tick(start + i / 1000.0) for i in range(0, 400)}
                seen = {s[1] for s in seen if s}
                self.assertEqual(max(seen), dons + pops)
                self.assertEqual(view._cumulative_hits(float(end) + 1), dons + pops)

    def test_balloon_remaining_count_drops_at_first_hit(self):
        rs = 45
        data = self._analyzer(rs).build_preview_timeline(chart('7008', 240))
        view = self._balloon_view(data, rs)
        start = float(view._balloons[0][0])
        self.assertEqual(view._live_top_count(start), 7)
        self.assertEqual(view._live_top_count(start + 1.5 / rs), 6)
        # 最後の1打(割れる音)の時刻で 0。
        self.assertEqual(view._live_top_count(start + 7 / rs), 0)


if __name__ == '__main__':
    unittest.main()
