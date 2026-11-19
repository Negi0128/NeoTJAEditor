"""旧 NeoTJAPlayer(別 exe)を外したことの確認(v15-design/PLAYER_REDESIGN.md 6章)。"""
import os
import unittest

from gui_support import editor_session, real_qt_test


def _menu_texts(win, title):
    for action in win.menuBar().actions():
        if action.text() == title:
            return [a.text() for a in action.menu().actions()]
    raise AssertionError('menu not found: ' + title)


class EditorEntryPointTests(unittest.TestCase):
    @real_qt_test
    def test_run_menu_has_no_old_player(self):
        with editor_session() as (win, _root):
            texts = _menu_texts(win, '起動')
            self.assertNotIn('NeoTJAPlayer を別ウィンドウで開く', texts)
            self.assertNotIn('NeoTJAPlayer を入れ直す', texts)
            # F1 の項目も出さない(F1 キーは打音の切替)。
            self.assertFalse([t for t in texts if 'NeoTJAPlayer' in t or t.startswith('F1')],
                             texts)
            self.assertEqual(sorted(win._run_actions), ['F2', 'F3'])
            # 古い設定にある run_config["F1"] はそのまま読めている。
            self.assertIn('F1', win.config_data['run_config'])
            for name in ('launch_player', '_offer_player_download',
                         '_find_player', 'check_player_pending'):
                self.assertFalse(hasattr(win, name), name)

    @real_qt_test
    def test_f1_only_toggles_auto(self):
        from unittest.mock import patch
        with editor_session() as (win, _root):
            with patch.object(win.preview_dock, 'toggle_editor_auto') as toggle, \
                 patch.object(win.preview_dock, 'set_game_preview_visible') as show:
                win._on_f1()
                toggle.assert_called_once_with()
                show.assert_not_called()


class OldPlayerCleanupTests(unittest.TestCase):
    """本物のごみ箱は触らない。版の読み取りとごみ箱への移動は偽物を渡す。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(prefix='neotja-oldplayer-')
        self.dir = self._tmp.name
        self.exe = os.path.join(self.dir, 'NeoTJAPlayer.exe')

    def tearDown(self):
        self._tmp.cleanup()

    def _put_exe(self):
        with open(self.exe, 'wb') as f:
            f.write(b'MZ')

    def _run(self, version, recycle_ok=True, raises=False):
        from neotja.old_player_cleanup import cleanup_old_player
        calls = []

        def recycle(path):
            calls.append(path)
            if raises:
                raise OSError('locked')
            if recycle_ok:
                os.remove(path)
            return recycle_ok

        result = cleanup_old_player(self.dir, read_version=lambda p: version,
                                    recycle=recycle)
        return result, calls

    def test_absent(self):
        self.assertEqual(self._run((15, 3, 0, 0))[0], 'absent')

    def test_old_versions_are_recycled(self):
        for version in ((15, 3, 0, 0), (15, 3, 9, 0), (15, 0, 0, 0), (14, 9, 1, 0),
                        (12, 0, 1, 0)):
            with self.subTest(version=version):
                self._put_exe()
                result, calls = self._run(version)
                self.assertEqual(result, 'recycled')
                self.assertEqual(calls, [self.exe])
                self.assertFalse(os.path.exists(self.exe))

    def test_newer_versions_are_kept(self):
        for version in ((15, 4, 0, 0), (15, 10, 0, 0), (16, 0, 0, 0)):
            with self.subTest(version=version):
                self._put_exe()
                result, calls = self._run(version)
                self.assertEqual(result, 'newer')
                self.assertEqual(calls, [])
                self.assertTrue(os.path.exists(self.exe))

    def test_unreadable_version_is_kept(self):
        self._put_exe()
        result, calls = self._run(None)
        self.assertEqual(result, 'unknown')
        self.assertEqual(calls, [])
        self.assertTrue(os.path.exists(self.exe))

    def test_real_reader_on_non_exe_keeps_file(self):
        # 版情報の無いファイルは本物の読み取りでも None → 残す。
        from neotja.old_player_cleanup import cleanup_old_player
        self._put_exe()
        calls = []
        result = cleanup_old_player(self.dir, recycle=calls.append)
        self.assertEqual(result, 'unknown')
        self.assertEqual(calls, [])
        self.assertTrue(os.path.exists(self.exe))

    def test_recycle_failure_keeps_file_without_exception(self):
        for kwargs in ({'recycle_ok': False}, {'raises': True}):
            with self.subTest(**kwargs):
                self._put_exe()
                result, calls = self._run((15, 3, 0, 0), **kwargs)
                self.assertEqual(result, 'failed')
                self.assertEqual(calls, [self.exe])
                self.assertTrue(os.path.exists(self.exe))

    def test_reported_success_but_file_remains_is_failure(self):
        from neotja.old_player_cleanup import cleanup_old_player
        self._put_exe()
        result = cleanup_old_player(self.dir, read_version=lambda p: (15, 3, 0, 0),
                                    recycle=lambda p: True)
        self.assertEqual(result, 'failed')

    def test_reader_exception_never_escapes(self):
        from neotja.old_player_cleanup import cleanup_old_player
        self._put_exe()

        def boom(_path):
            raise RuntimeError('broken')
        self.assertEqual(cleanup_old_player(self.dir, read_version=boom,
                                            recycle=lambda p: True), 'failed')
        self.assertTrue(os.path.exists(self.exe))

    def test_run_at_startup_only_when_frozen(self):
        from unittest.mock import patch
        from neotja import old_player_cleanup
        with patch.object(old_player_cleanup, 'cleanup_old_player') as cleanup:
            self.assertIsNone(old_player_cleanup.run_at_startup())
            cleanup.assert_not_called()
        fake_exe = os.path.join(self.dir, 'NeoTJAEditor.exe')
        with patch('sys.frozen', True, create=True), \
             patch('sys.executable', fake_exe), \
             patch.object(old_player_cleanup, 'cleanup_old_player',
                          return_value='absent') as cleanup:
            self.assertEqual(old_player_cleanup.run_at_startup(), 'absent')
            cleanup.assert_called_once_with(self.dir)
        with patch('sys.frozen', True, create=True), \
             patch.object(old_player_cleanup, 'cleanup_old_player',
                          side_effect=RuntimeError('x')):
            self.assertIsNone(old_player_cleanup.run_at_startup())


if __name__ == '__main__':
    unittest.main()
