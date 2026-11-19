"""歌詞ごとのジャケット: 選んだ絵は ユーザーデータ/ジャケット へ写して使う。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_lyric_jacket_copy.py`
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-lyricjacket-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG
os.environ['NEOTJA_USER_DIR'] = os.path.join(_TMP, 'user')

from gui_support import real_qt_test  # noqa: E402


class CopyTests(unittest.TestCase):
    @real_qt_test
    def test_browse_copies_into_user_jackets(self):
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtWidgets import QFileDialog
        from neotja import user_dirs
        from neotja.dialogs.lyric_jacket_dialog import LyricJacketDialog
        src = os.path.join(_TMP, 'pic.png')
        img = QImage(8, 8, QImage.Format_ARGB32)
        img.fill(QColor(200, 0, 0))
        img.save(src)
        orig = QFileDialog.getOpenFileName
        QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (src, ''))
        try:
            old = os.path.join(_TMP, 'old.png')
            img.save(old)
            dlg = LyricJacketDialog('歌詞', current=old)
            self.assertEqual(dlg._path, old)          # 今までの参照はそのまま
            dlg._browse()
        finally:
            QFileDialog.getOpenFileName = orig
        folder = user_dirs.user_dir('jackets').resolve()
        self.assertEqual(Path(dlg._path).resolve().parent, folder)
        self.assertTrue(os.path.isfile(dlg._path))
        dlg.deleteLater()


if __name__ == '__main__':
    unittest.main()
