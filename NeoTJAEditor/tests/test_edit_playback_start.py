import unittest
from unittest.mock import patch

from gui_support import editor_session, open_test_chart, real_qt_test


class EditPlaybackStartTests(unittest.TestCase):
    @real_qt_test
    def test_playback_starts_at_current_view_then_follows_audio(self):
        with editor_session() as (win, root):
            open_test_chart(win, root)
            ce = win.preview_dock.chart_edit
            ce.set_follow_window(6.0)
            ce.scroll_view_to(3.0, animate=False)
            ce.set_position(8.0)
            origin = ce.view_start
            self.assertEqual(origin, 3.0)
            with patch('neotja.chart_edit_widget.time.monotonic') as clock:
                clock.return_value = 10000.0
                ce.set_playing(True)
                ce.set_position_smooth(8.0)
                self.assertAlmostEqual(ce.view_start, origin)
                clock.return_value += ce.VIEW_ANIM_SEC / 2
                ce.set_position_smooth(8.09)
                midway = ce.view_start
                self.assertGreater(midway, origin)
                self.assertLess(midway, 8.09 - 6 * ce.FOLLOW_FRAC)
                self.assertEqual(ce.position_sec, 8.09)
                ce.set_position_smooth(13.09)  # 再生中のシークは即時に反映
                self.assertAlmostEqual(ce.view_start - midway, 5.0)
                clock.return_value += ce.VIEW_ANIM_SEC
                ce.set_position_smooth(13.27)
                self.assertAlmostEqual(ce.view_start, 13.27 - 6 * ce.FOLLOW_FRAC)
                ce.set_playing(False)
                ce.scroll_view_to(11.0, animate=False)
                ce.set_playing(True)
                ce.scroll_view_free(1, 6.0)
                dest = ce.view_target()
                ce.set_position_smooth(13.28)
                self.assertAlmostEqual(ce.view_target(), dest)
                ce.set_playing(False)


if __name__ == '__main__':
    unittest.main()
