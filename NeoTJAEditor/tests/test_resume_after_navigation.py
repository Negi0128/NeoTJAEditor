import unittest

from gui_support import editor_session, open_test_chart


class ResumeNavigationTests(unittest.TestCase):
    def test_resume_uses_destination_before_scroll_animation_finishes(self):
        with editor_session() as (win, root):
            open_test_chart(win, root)
            cp = win.preview_dock.chart_preview
            seeks = []
            cp._seek_seconds_cb = seeks.append
            cp._play_cb = lambda: None
            for direction in (-1, 1):
                with self.subTest(direction=direction):
                    cp._animating = False
                    cp.set_playback(7.5, False)
                    cp.seek_relative_measure(direction)
                    self.assertTrue(cp._animating)
                    target = seeks[-1]
                    cp.toggle_play()
                    self.assertEqual(seeks[-1], target)
                    self.assertEqual(cp._pos_sec, target)
                    self.assertFalse(cp._animating)


if __name__ == '__main__':
    unittest.main()
