# 曲全体の録画の終わり(recorder.full_record_end)。
# 利用者の指定 2026-11-15: 最後の小節の終わり + 基本BPM(ヘッダの BPM:)で 1 小節ぶん。
# 音源の長さでは延ばさない。
import unittest

from neotja import recorder
from neotja.tja_analyzer import TJACourseAnalyzer


def _preview(bpm_header, body):
    head = ["TITLE:t"]
    if bpm_header is not None:
        head.append("BPM:%s" % bpm_header)
    content = "\n".join(head + ["OFFSET:0", "", "COURSE:Oni", "LEVEL:10",
                                "#START"] + body + ["#END", ""])
    return TJACourseAnalyzer({}).build_preview_timeline(content, None, "Oni")


class RecordEndTests(unittest.TestCase):
    def test_bpm120_adds_two_seconds(self):
        pd = _preview(120, ["1000,"] * 3)          # 3小節 = 6 秒
        self.assertEqual(pd["header_bpm"], 120.0)
        self.assertAlmostEqual(recorder.chart_end_seconds(pd, 0.0), 6.0)
        self.assertAlmostEqual(recorder.full_record_end(pd, 0.0, 3.0), 8.0)
        # 曲のほうが長くても延ばさない(そこで切る)
        self.assertAlmostEqual(recorder.full_record_end(pd, 0.0, 60.0), 8.0)

    def test_bpm170(self):
        pd = _preview(170, ["1000,"] * 2)
        bar = 240.0 / 170.0
        self.assertAlmostEqual(recorder.record_tail_seconds(pd), bar)
        self.assertAlmostEqual(recorder.full_record_end(pd, 0.0, 100.0), 3 * bar)

    def test_bpmchange_near_end_uses_header_bpm(self):
        # 最後の小節だけ BPM 240。終わりは 2+2+1=5 秒、余白はヘッダの 120 で 2 秒。
        pd = _preview(120, ["1000,", "1000,", "#BPMCHANGE 240", "1000,"])
        self.assertAlmostEqual(recorder.chart_end_seconds(pd, 0.0), 5.0)
        self.assertAlmostEqual(recorder.record_tail_seconds(pd), 2.0)
        self.assertAlmostEqual(recorder.full_record_end(pd, 0.0, 30.0), 7.0)

    def test_offset_shifts_chart_end(self):
        pd = _preview(120, ["1000,"] * 3)
        # OFFSET -1 → 音源時刻では 1 秒遅れる
        self.assertAlmostEqual(recorder.full_record_end(pd, -1.0, 0.0), 9.0)

    def test_fallbacks(self):
        # ヘッダが無ければ譜面の頭の BPM
        self.assertAlmostEqual(recorder.record_tail_seconds(
            {"bar_times": [(0.0, 150.0, 1.0, True)]}), 240.0 / 150.0)
        # 小節も無ければ音源の長さ + 120 の 1 小節
        self.assertAlmostEqual(recorder.full_record_end({}, 0.0, 7.0), 9.0)


if __name__ == "__main__":
    unittest.main()
