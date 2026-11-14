"""小節の途中で BPM が変わるときのグリッド(作譜ペインの目盛り・波形のビート線)。

利用者の報告 2026-11-14「BPM変化途中で入れた時のグリッドが治ってない」。
音符とカーソルは NoteTiming の区分ごとの時刻で置かれるのに、目盛りは小節を
等分して引いていたため、途中で BPM が変わった小節で目盛りと音符がずれていた。
"""
import unittest
from unittest.mock import MagicMock

from gui_support import editor_session, open_test_chart, real_qt_test
from neotja.tja_analyzer import TJACourseAnalyzer
from neotja.waveform_data import bar_grid_clicks

# BPM145 の 4/4、16分の 8 個目で 161、12 個目で 148 に変わる。全スロットに音符。
SPLIT_BODY = ('11111111\n#BPMCHANGE 161\n1111\n#BPMCHANGE 148\n1111,\n'
              '1111111111111111,\n1111111111111111,')
# 途中では変わらない(小節頭でだけ変わる)譜面。
PLAIN_BODY = ('1111111111111111,\n#BPMCHANGE 161\n1111111111111111,\n'
              '#BPMCHANGE 148\n1111111111111111,')


def chart(body, bpm=145):
    return 'BPM:%s\nOFFSET:0\nCOURSE:oni\n#START\n%s\n#END\n' % (bpm, body)


def grid_ticks(ce, m):
    """小節 m に引いた目盛りの時刻(小節頭を除く)。

    _sec_to_x を「時刻をそのまま返す」に差し替え、偽の painter が受けた
    drawLine の x を集める。"""
    lo, hi = ce._bar_time(m), ce._bar_time(m + 1)
    ce._sec_to_x = lambda t: t
    ce._xs_val = 1e6              # 間引かれないように(1秒あたり px)
    p = MagicMock()
    try:
        ce._draw_edit_grid(p, 0, 100, lo - 1e-3, hi + 1e-3)
    finally:
        del ce._sec_to_x
    xs = sorted(c.args[0] for c in p.drawLine.call_args_list)
    return [x for x in xs if lo + 1e-9 < x < hi - 1e-9]


class EditGridBpmChangeTests(unittest.TestCase):
    @real_qt_test
    def test_pane_ticks_follow_mid_measure_bpm_change(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, SPLIT_BODY, bpm=145)
            ce = win.preview_dock.chart_edit
            ce.set_grid(16)
            self.assertIsNotNone(ce.note_timing)
            self.assertTrue(ce._measure_has_tempo_break(0))
            self.assertFalse(ce._measure_has_tempo_break(1))
            # ペインの時刻は「譜面の時刻 - offset」(音の時刻)。
            notes = sorted(n[0] - ce.offset for n in win.preview_dock._preview_notes)
            ticks = grid_ticks(ce, 0)
            self.assertEqual(len(ticks), 15)
            for k, tick in enumerate(ticks, start=1):
                with self.subTest(slot=k):
                    self.assertAlmostEqual(tick, ce._address_time(0, k, 16), delta=1e-6)
                    self.assertAlmostEqual(tick, notes[k], delta=1e-6)
                    self.assertEqual(ce._address_from_time(tick), (0, k))
            # 等分していた頃は 8 個目(拍)が 25ms 早かった。それが直っていること。
            even = ce._bar_time(0) + (ce._bar_time(1) - ce._bar_time(0)) * 8 / 16
            self.assertGreater(ticks[7] - even, 0.02)

    @real_qt_test
    def test_pane_ticks_without_mid_change_are_unchanged(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, PLAIN_BODY, bpm=145)
            ce = win.preview_dock.chart_edit
            ce.set_grid(16)
            for m in range(3):
                with self.subTest(measure=m):
                    self.assertFalse(ce._measure_has_tempo_break(m))
                    a, b = ce._bar_time(m), ce._bar_time(m + 1)
                    old = [a + (b - a) * (k / 16) for k in range(1, 16)]
                    self.assertEqual(grid_ticks(ce, m), sorted(old))

    def test_waveform_beat_lines_follow_mid_measure_bpm_change(self):
        data = TJACourseAnalyzer({}).build_preview_timeline(chart(SPLIT_BODY), 4)
        notes = sorted(n[0] for n in data['notes'])
        clicks = bar_grid_clicks(data['bar_times'], data['bpm_changes'])
        bar0, bar1 = data['bar_times'][0][0], data['bar_times'][1][0]
        beats = [t for t, is_bar in clicks if not is_bar and bar0 < t < bar1]
        self.assertEqual(len(beats), 3)
        for t, slot in zip(beats, (4, 8, 12)):
            self.assertAlmostEqual(t, notes[slot], delta=1e-6)

    def test_waveform_beat_lines_without_mid_change_are_unchanged(self):
        data = TJACourseAnalyzer({}).build_preview_timeline(chart(PLAIN_BODY), 4)
        self.assertEqual(bar_grid_clicks(data['bar_times'], data['bpm_changes']),
                         bar_grid_clicks(data['bar_times']))


if __name__ == '__main__':
    unittest.main()
