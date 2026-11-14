"""レーンのホイールは時間で動く(1目盛 = いまの BPM の 4/4 の1小節)。"""
import unittest

from gui_support import editor_session, open_test_chart, real_qt_test

BODY = '\n'.join(
    ['1000,'] * 4                                   # 0〜8秒   BPM120
    + ['#BPMCHANGE 240'] + ['1000,'] * 4            # 8〜12秒  BPM240
    + ['#BPMCHANGE 120', '#MEASURE 1/16'] + ['1,'] * 32   # 12〜16秒 細かい小節
    + ['#MEASURE 4/4'] + ['1000,'] * 6)


def _wheel(widget, dy, shift=False):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    mods = Qt.ShiftModifier if shift else Qt.NoModifier
    ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, dy),
                     Qt.NoButton, mods, Qt.NoScrollPhase, False)
    widget.wheelEvent(ev)
    return ev


class LaneWheelTimeTests(unittest.TestCase):
    @real_qt_test
    def test_wheel_steps_by_bpm_time(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, BODY, bpm=120)
            win._force_update()
            pd = win.preview_dock
            lane = pd.chart_preview
            # 譜面の秒 → レーン(音源)の秒。OFFSET ぶんずらす。
            off = lane._offset

            def at(chart_t):
                lane.scroll_to_seconds(chart_t - off)
                return lane.scroll_target_time()

            cases = (
                (1.0, 120, False, 2.0),          # BPM120: 240/120
                (9.0, -120, False, -1.0),        # BPM240: 240/240、下へ回すと戻る
                (12.5, 120, False, 2.0),         # 1/16 小節が続く所でも 240/BPM
                (1.0, 30, False, 0.5),           # 高分解能: 1/4 目盛
                (1.0, 120, True, 8.0),           # Shift = 4倍
            )
            for chart_t, dy, shift, want in cases:
                base = at(chart_t)
                ev = _wheel(lane, dy, shift)
                self.assertTrue(ev.isAccepted())
                self.assertAlmostEqual(lane.scroll_target_time() - base, want, places=6,
                                       msg=(chart_t, dy, shift))
            # ゲーム画面・窓の余白から来たホイールも同じ量。
            base = at(1.0)
            _wheel(pd.game_screen, 120)
            self.assertAlmostEqual(lane.scroll_target_time() - base, 2.0, places=6)
            # 続けて回すと、行き先から足していく。
            base = at(1.0)
            _wheel(lane, 120)
            _wheel(lane, 120)
            self.assertAlmostEqual(lane.scroll_target_time() - base, 4.0, places=6)

    @real_qt_test
    def test_wheel_while_playing_coalesces_seeks(self):
        from PySide6.QtTest import QTest
        with editor_session() as (win, root):
            open_test_chart(win, root, BODY, bpm=120)
            win._force_update()
            lane = win.preview_dock.chart_preview
            seeks = []
            orig = lane._seek_seconds_cb
            lane._seek_seconds_cb = lambda t: seeks.append(t)
            try:
                lane._state = "playing"
                lane._pos_sec = 1.0 - lane._offset
                lane._pos_wall = __import__('time').perf_counter()
                lane._wheel_last_seek = 0.0
                for _ in range(5):
                    _wheel(lane, 120)
                self.assertEqual(len(seeks), 1)      # 1回目だけすぐ
                QTest.qWait(120)
                self.assertEqual(len(seeks), 2)      # 残り4回ぶんはまとめて1回
                # 3→5→7→9秒(BPM120 で2秒ずつ)、9秒は BPM240 なので最後は1秒。
                self.assertAlmostEqual(seeks[1] - seeks[0], 7.0, delta=0.1)
            finally:
                lane._state = "stopped"
                lane._seek_seconds_cb = orig


if __name__ == '__main__':
    unittest.main()
