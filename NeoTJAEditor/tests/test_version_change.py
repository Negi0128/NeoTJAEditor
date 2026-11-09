import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from gui_support import editor_session, real_qt_test


def release(tag, player=True):
    from neotja.updater import ASSET_NAME, PLAYER_ASSET_NAME
    names = [ASSET_NAME] + ([PLAYER_ASSET_NAME] if player else [])
    return {'tag_name': tag, 'body': '**変更点**\n・確認用',
            'assets': [{'name': n, 'browser_download_url': 'https://example.invalid/' + n}
                       for n in names]}


class VersionChangeTests(unittest.TestCase):
    @real_qt_test
    def test_menu_dialog_search_and_missing_assets(self):
        from neotja.dialogs.version_change_dialog import VersionChangeDialog
        from neotja.constants import VERSION
        from PySide6.QtTest import QTest
        with editor_session() as (win, root):
            win.show()
            names = [a.text() for a in win.menuBar().actions()]
            self.assertEqual(names[names.index('設定') + 1], 'バージョン変更')
            dialog = VersionChangeDialog(win, auto_load=False)
            try:
                dialog._loaded([release('v' + VERSION), release('v14.8.0'),
                                release('v14.7.0', player=False)], False)
                dialog._finished()
                self.assertFalse(dialog.change.isEnabled())
                dialog.versions.setCurrentRow(1)
                self.assertTrue(dialog.change.isEnabled())
                dialog.search.setText('14.7')
                self.assertEqual(dialog.selected_release['tag_name'], 'v14.7.0')
                self.assertFalse(dialog.change.isEnabled())
                self.assertTrue(dialog.versions.item(0).isHidden())
                dialog.search.setText('not-found')
                self.assertIsNone(dialog.selected_release)
                self.assertFalse(dialog.change.isEnabled())
                dialog.search.clear()
                with patch.object(dialog, 'load_more') as fetch:
                    dialog._more = True
                    dialog.search.setText('12.')
                    fetch.assert_called_once()
                output = os.environ.get('NEOTJA_TEST_OUTPUT')
                if output:
                    data = Path(output) / 'version-preview-releases.json'
                    if data.exists():
                        dialog.versions.clear()
                        dialog._releases.clear()
                        dialog.search.clear()
                        dialog._more = False
                        dialog._loaded(json.loads(data.read_text(encoding='utf-8')), False)
                    dialog.show()
                    QTest.qWait(100)
                    dialog.grab().save(str(Path(output) / 'version-change-integrated.png'))
                    win.menuBar().grab().save(str(Path(output) / 'version-change-menu.png'))
            finally:
                dialog.reject()

    @real_qt_test
    def test_selected_pair_is_passed_to_download(self):
        from neotja.dialogs.version_change_dialog import VersionChangeDialog
        with editor_session() as (win, root):
            def choose(dialog):
                dialog.selected_release = release('v14.8.0')
                return 1
            with patch.object(VersionChangeDialog, 'load_more', lambda self: None), \
                 patch.object(VersionChangeDialog, 'exec', choose), \
                 patch('sys.frozen', True, create=True), \
                 patch.object(win, '_run_update_download') as download:
                win.open_version_change()
                self.assertTrue(download.call_args.kwargs['version_change'])
                self.assertTrue(download.call_args.args[0].endswith('NeoTJAEditor.exe'))
                self.assertTrue(download.call_args.args[1].endswith('NeoTJAPlayer.exe'))

    def test_player_failure_aborts_version_change(self):
        from neotja.updater import UpdateDownloadWorker
        worker = UpdateDownloadWorker('editor', 'player', require_player=True)
        success, failures = [], []
        worker.finished_ok.connect(lambda *paths: success.append(paths))
        worker.failed.connect(failures.append)
        with patch.object(worker, '_fetch', side_effect=[1, IOError('failed')]), \
             patch.object(worker, '_discard') as discard:
            worker.run()
            self.assertFalse(success)
            self.assertEqual(len(failures), 1)
            self.assertEqual(discard.call_count, 2)

    def test_settings_backup_preserves_source(self):
        from neotja.updater import backup_version_settings
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'settings.json'
            path.write_text('{"master_volume":0.0}', encoding='utf-8')
            before = path.read_bytes(), path.stat().st_mtime_ns
            config = {'master_volume': 0.0, 'label': '確認'}
            saved = backup_version_settings(path, config)
            self.assertEqual(json.loads(saved.read_text(encoding='utf-8')), config)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)


if __name__ == '__main__':
    unittest.main()
