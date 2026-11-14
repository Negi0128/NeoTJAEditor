"""編集してすぐ再生しても、その編集がレーンに入っている。"""
import unittest
from unittest.mock import patch

from gui_support import editor_session, open_test_chart, real_qt_test


class PlayFlushEditsTests(unittest.TestCase):
    def _edit(self, win):
        res = win._apply_chart_op({"kind": "key", "a": (1, 0), "grid": 16, "char": "1"})
        self.assertIsNotNone(res)

    @real_qt_test
    def test_play_entries_flush_pending_edit(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, '1000,\n0000,\n0000,')
            pd = win.preview_dock
            lane = pd.chart_preview
            win.config_data["play_flush_before_play"] = True
            self.assertEqual(len(lane._note_times), 1)
            for entry in (lambda: lane.toggle_play(),
                          lambda: pd._toggle_play_pause(),
                          lambda: pd.seek_to_seconds(0.0),
                          lambda: lane.play_from_current_measure(fade=True)):
                win._force_update()
                n0 = len(lane._note_times)
                self._edit(win)
                self.assertTrue(win._heavy_timer.isActive())
                self.assertEqual(len(lane._note_times), n0)
                with patch.object(pd.audio, 'play') as play, \
                        patch.object(pd.audio, 'toggle_play_pause') as toggle, \
                        patch.object(pd.audio, 'is_playing', return_value=False):
                    entry()
                    self.assertEqual(play.call_count + toggle.call_count, 1)
                self.assertEqual(len(lane._note_times), n0 + 1)
                self.assertFalse(win._heavy_timer.isActive())
                # 消して次へ(同じ所に打ち直すため)。
                win.editor.undo()

    @real_qt_test
    def test_setting_off_keeps_old_behaviour(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, '1000,\n0000,\n0000,')
            pd = win.preview_dock
            lane = pd.chart_preview
            win.config_data["play_flush_before_play"] = False
            self._edit(win)
            with patch.object(pd.audio, 'play') as play, \
                    patch.object(pd.audio, 'is_playing', return_value=False):
                lane.toggle_play()
                self.assertEqual(play.call_count, 1)
            self.assertEqual(len(lane._note_times), 1)
            self.assertTrue(win._heavy_timer.isActive())

    @real_qt_test
    def test_settings_dialog_checkbox(self):
        from neotja import settings
        from neotja.dialogs.settings_dialog import SettingsDialog
        self.assertIn("play_flush_before_play", settings._SETTINGS_KEYS)
        self.assertFalse(settings.default_settings()["play_flush_before_play"])
        with editor_session() as (win, root):
            dlg = SettingsDialog(win)
            try:
                self.assertFalse(dlg.play_flush_check.isChecked())
                dlg.play_flush_check.setChecked(True)
                dlg._save()
                self.assertTrue(win.config_data["play_flush_before_play"])
                self.assertTrue(win.preview_dock.config_data["play_flush_before_play"])
            finally:
                dlg.close()
                dlg.deleteLater()

    @real_qt_test
    def test_nothing_pending_does_nothing(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, '1000,\n0000,')
            win._force_update()
            with patch.object(win, '_force_update') as force, \
                    patch.object(win, '_preview_tick') as tick:
                win.flush_pending_preview()
                force.assert_not_called()
                tick.assert_not_called()


if __name__ == '__main__':
    unittest.main()
