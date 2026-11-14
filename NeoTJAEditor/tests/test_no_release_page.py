"""リリースページへ案内しないことの確認(利用者の決定 2026-11-14)。

本体のリポジトリを非公開にすると github.com/Negi0128/NeoTJAEditor/releases は
利用者には 404 になる。アプリからそこを開く口が残っていないことを見る。
"""
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from gui_support import editor_session, real_qt_test

SRC = Path(__file__).resolve().parents[1] / 'neotja'


class NoReleasePageTests(unittest.TestCase):
    def test_source_has_no_release_page(self):
        # API(api.github.com)で取りに行くのは鍵つきで続けるので対象外。
        page = re.compile(r'(?<!api\.)github\.com/Negi0128/NeoTJAEditor/releases')
        hits = []
        for path in SRC.rglob('*.py'):
            text = path.read_text(encoding='utf-8')
            if page.search(text) or 'RELEASES_PAGE_URL' in text or 'リリースページを開く' in text:
                hits.append(str(path.relative_to(SRC)))
        self.assertEqual(hits, [])

    @real_qt_test
    def test_source_run_update_does_not_open_browser(self):
        from PySide6.QtWidgets import QMessageBox
        with editor_session() as (win, _root), \
                patch('webbrowser.open') as web_open, \
                patch('PySide6.QtGui.QDesktopServices.openUrl') as open_url, \
                patch.object(QMessageBox, 'exec', return_value=QMessageBox.Yes), \
                patch.object(QMessageBox, 'information') as info:
            win._prompt_update('v99.0.0', '', 'https://example.invalid/NeoTJAEditor.exe')
            web_open.assert_not_called()
            open_url.assert_not_called()
            info.assert_called_once()
            text = info.call_args[0][2]
            self.assertIn('v99.0.0', text)
            self.assertIn('Discord', text)
            self.assertNotIn('リリースページ', text)


if __name__ == '__main__':
    unittest.main()
