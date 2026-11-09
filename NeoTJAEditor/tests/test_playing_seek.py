import unittest
from unittest.mock import patch

from gui_support import editor_session, open_test_chart


class PlayingSeekTests(unittest.TestCase):
    def test_left_during_playback_ignores_synchronous_old_position(self):
        with editor_session() as (win, root):
            open_test_chart(win, root)
            pd = win.preview_dock
            cp = pd.chart_preview
            cp.set_playback(9.5, True)
            old = 9500
            def stale_seek(ms):
                pd._on_position_changed(old)
            with patch.object(pd.audio, 'is_playing', return_value=True), \
                    patch.object(pd.audio, 'position', return_value=old), \
                    patch.object(pd.audio, 'seek', side_effect=stale_seek):
                pd._last_pos_ui_wall = 0
                cp.seek_relative_measure(-1)
                target = pd._seek_target_ms / 1000
                self.assertLess(target, 9.5)
                self.assertAlmostEqual(cp._pos_sec, target, places=3)
                pd._last_pos_ui_wall = 0
                pd._on_position_changed(old)
                self.assertAlmostEqual(cp._pos_sec, target, places=3)
                pd._on_playing_changed(True)
                self.assertAlmostEqual(cp._pos_sec, target, places=2)
            cp.set_playback(0, False)


if __name__ == '__main__':
    unittest.main()
