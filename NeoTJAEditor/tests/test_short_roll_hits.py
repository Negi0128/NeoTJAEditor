"""短い連打の打数の回帰テスト(利用者の報告 2026-11-13)。

BPM の速い2マスの連打が「短い連打の補正」(秒速50〜60)で数えられ、
連打秒速より多く、しかも長い連打より多くなることがあった。今は
打数 = max(1, 切り捨て(長さ × 連打秒速)) の一本で、コースカード・打音・
スコアの加算が同じ数を使う。"""
import hashlib
import json
from pathlib import Path
import tempfile
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


if __name__ == '__main__':
    unittest.main()
