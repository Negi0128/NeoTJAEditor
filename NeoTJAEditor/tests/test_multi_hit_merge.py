"""同時再生の打音の回帰テスト(利用者の指定 2026-11-14)。

以前は難易度ぶんの打音を全部足して、SE を本数で割っていた。そのせいで
1本のときより 6〜12dB 小さく聞こえた。今は同じ時刻・同じ音を1つに
まとめ、割らずに打音だけ MULTI_HIT_GAIN(1.5)倍にする。1本のときは
今までどおり。"""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

from neotja import settings

BODY = {'Oni': '1212,\n3434,\n1111,\n',
        'Hard': '1210,\n3030,\n1010,\n',
        'Easy': '1000,\n3000,\n1000,\n'}


def chart():
    text = 'BPM:120\nOFFSET:0\n'
    for name, body in BODY.items():
        text += f'\nCOURSE:{name}\nLEVEL:5\n#START\n{body}#END\n'
    return text


class MultiHitMergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = settings.settings_path()
        cls.before = hashlib.sha256(cls.real.read_bytes()).digest() if cls.real.exists() else None
        cls.original_path = settings.settings_path
        cls.scratch = tempfile.TemporaryDirectory(prefix='neotja-multi-hit-test-')
        target = Path(cls.scratch.name) / 'settings.json'
        target.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
        settings.settings_path = lambda: target

    @classmethod
    def tearDownClass(cls):
        settings.settings_path = cls.original_path
        cls.scratch.cleanup()
        after = hashlib.sha256(cls.real.read_bytes()).digest() if cls.real.exists() else None
        assert after == cls.before, 'User settings changed'

    def _previews(self):
        from neotja.tja_analyzer import TJACourseAnalyzer
        an = TJACourseAnalyzer({})
        return [an.build_preview_timeline(chart(), course_key=k) for k in BODY]

    def _dock(self, previews, muted=()):
        from neotja.preview_dock import PreviewDock
        calls = []
        audio = SimpleNamespace(
            set_sfx_volume=lambda v: calls.append(('sfx', v)),
            set_hit_gain=lambda g: calls.append(('gain', g)))
        d = SimpleNamespace(
            _multi_built=previews, _multi_keys=list(BODY),
            _multi_muted=set(muted), _multi_notes=None, _multi_count=1,
            _sfx_ratio=0.9, _mixer_active=True, audio=audio,
            _grid_offset=lambda: 0.0, _apply_hit_schedule=lambda off: None,
            _apply_legacy_sfx_volume=lambda: None)
        for name in ('_rebuild_multi_notes', '_apply_sfx_volume',
                     '_sfx_effective', '_hit_gain'):
            setattr(d, name, getattr(PreviewDock, name).__get__(d))
        return d, calls

    def test_merge_has_no_duplicate_time_kind(self):
        from neotja.preview_dock import (hit_notes_from_preview, hit_sound_kind,
                                         merge_multi_hit_notes)
        lists = [hit_notes_from_preview(p) for p in self._previews()]
        merged = merge_multi_hit_notes(lists)
        pairs = [(round(t * 1000), hit_sound_kind(c)) for t, c, _b in merged]
        self.assertEqual(len(pairs), len(set(pairs)))
        # どの難易度の打音も、時刻と音の組としては1つも落ちていない。
        want = {(round(t * 1000), hit_sound_kind(c))
                for notes in lists for t, c, _b in notes}
        self.assertEqual(set(pairs), want)
        self.assertEqual([n[0] for n in merged], sorted(n[0] for n in merged))

    def test_same_time_don_and_ka_both_kept(self):
        from neotja.preview_dock import merge_multi_hit_notes
        merged = merge_multi_hit_notes([[(1.0, '1', 120)], [(1.0004, '3', 120)],
                                        [(1.0, '2', 120)], [(1.5, '1', 120)]])
        self.assertEqual([(t, c) for t, c, _b in merged],
                         [(1.0, '1'), (1.0, '2'), (1.5, '1')])

    def test_multi_gain_and_no_division(self):
        from neotja.preview_dock import MULTI_HIT_GAIN
        d, calls = self._dock(self._previews())
        d._rebuild_multi_notes()
        self.assertEqual(d._multi_count, 3)
        self.assertEqual(MULTI_HIT_GAIN, 1.5)
        self.assertEqual(calls[-2:], [('sfx', 0.9), ('gain', 1.5)])

    def test_single_live_band_is_unchanged(self):
        d, calls = self._dock(self._previews(), muted=('Oni', 'Hard'))
        d._rebuild_multi_notes()
        self.assertEqual(d._multi_count, 1)
        self.assertEqual(calls[-2:], [('sfx', 0.9), ('gain', 1.0)])
        # 同時再生でないとき(_multi_count=1)も同じ。
        d2, calls2 = self._dock([])
        d2._apply_sfx_volume()
        self.assertEqual(calls2, [('sfx', 0.9), ('gain', 1.0)])

    def test_mixer_hit_gain_scales_hits_only(self):
        from neotja.mixer_engine import MixerCore

        def render(gain, metro=False):
            core = MixerCore(48000)
            pcm = np.full((480, 2), 0.1, dtype=np.float32)
            core.post(('sfx', 'don', pcm))
            core.post(('sfx', 'click', pcm))
            core.post(('song', np.zeros((48000, 2), dtype=np.float32), 48000))
            if metro:
                core.post(('metro_sched', [0.01]))
                core.post(('metro_enabled', True))
                core.post(('hit_enabled', False))
            else:
                core.post(('hit_sched', [0.01], ['don']))
                core.post(('hit_enabled', True))
            core.post(('vol', 'sfx', 0.9))
            core.post(('vol', 'metro', 0.9))
            core.post(('vol', 'master', 0.3))
            core.post(('hit_gain', gain))
            core.post(('play',))
            return np.concatenate([core.render(480).copy() for _ in range(4)])

        one = np.abs(render(1.0)).max()
        self.assertAlmostEqual(one, 0.1 * 0.9 * 0.3, places=5)
        self.assertAlmostEqual(np.abs(render(1.5)).max(), one * 1.5, places=5)
        # メトロノームには掛けない。
        self.assertAlmostEqual(np.abs(render(1.5, metro=True)).max(), one, places=5)


if __name__ == '__main__':
    unittest.main()
