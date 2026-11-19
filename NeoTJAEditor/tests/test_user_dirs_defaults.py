"""ユーザーデータの置き場所(第6段): 録画の既定の保存先・YouTube の背景動画・
選んだジャケットの写し。exe(凍結)のときは exe の横。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_user_dirs_defaults.py`
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-userdirs-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from neotja import user_dirs  # noqa: E402

_USER = Path(_TMP) / 'user'
user_dirs.base_override = str(_USER)

from gui_support import real_qt_test  # noqa: E402


class PathTests(unittest.TestCase):
    def test_frozen_root_is_next_to_exe(self):
        old = user_dirs.base_override
        user_dirs.base_override = None
        try:
            env = {k: v for k, v in os.environ.items() if k != user_dirs.ENV_VAR}
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(sys, 'frozen', True, create=True), \
                    mock.patch.object(sys, 'executable', r'D:\Games\Neo\NeoTJAEditor.exe'):
                # 用意できていないドライブでも落ちない(resolve しない)。
                self.assertEqual(user_dirs.root(), Path(r'D:\Games\Neo') / 'ユーザーデータ')
                self.assertEqual(user_dirs.user_dir('recordings', create=False),
                                 Path(r'D:\Games\Neo') / 'ユーザーデータ' / '録画')
        finally:
            user_dirs.base_override = old

    def test_copy_into(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'jk.png'
            src.write_bytes(b'\x89PNG fake')
            got = user_dirs.copy_into('jackets', str(src))
            self.assertEqual(Path(got).parent, _USER / 'ジャケット')
            self.assertTrue(Path(got).name.startswith('jk_'))
            self.assertEqual(Path(got).read_bytes(), src.read_bytes())
            # 同じ絵をもう一度選んでも1枚。中のものを選べばそのまま。
            self.assertEqual(user_dirs.copy_into('jackets', str(src)), got)
            self.assertEqual(user_dirs.copy_into('jackets', got), str(Path(got).resolve()))
            # 元を消しても写しは残る。
            src.unlink()
            self.assertTrue(os.path.isfile(got))
        self.assertEqual(user_dirs.copy_into('jackets', ''), '')
        self.assertEqual(user_dirs.copy_into('jackets', 'Z:/nope.png'), 'Z:/nope.png')


class DefaultsTests(unittest.TestCase):
    def test_record_dialog_default_dir(self):
        from neotja.dialogs.record_dialog import RecordDialog
        with tempfile.TemporaryDirectory() as d:
            mine = os.path.join(d, 'mine')
            last = os.path.join(d, 'last')
            os.makedirs(mine)
            os.makedirs(last)
            chart_dir = os.path.join(d, 'chart')
            # まだ選んでいない → ユーザーデータ/録画(譜面のフォルダではない)。
            got = RecordDialog._resolve_out_dir({}, chart_dir)
            self.assertEqual(Path(got), _USER / '録画')
            self.assertTrue(os.path.isdir(got))
            # 覚えている場所・環境設定の場所はそのまま優先。
            self.assertEqual(RecordDialog._resolve_out_dir({'record_last_dir': last}, chart_dir), last)
            self.assertEqual(RecordDialog._resolve_out_dir(
                {'record_output_dir': mine, 'record_last_dir': last}, chart_dir), mine)
            # 無くなったフォルダは飛ばす。
            self.assertEqual(Path(RecordDialog._resolve_out_dir(
                {'record_last_dir': os.path.join(d, 'gone')}, chart_dir)), _USER / '録画')

    def test_batch_default_dir(self):
        from neotja.player.batch import BatchPage
        self.assertEqual(Path(BatchPage._out_dir(SimpleNamespace(cfg={}))), _USER / '録画')

    @real_qt_test
    def test_bg_dialog_youtube_and_jacket_copy(self):
        from PySide6.QtWidgets import QLineEdit
        from neotja.dialogs.record_bg_dialog import RecordBackgroundDialog
        fake = SimpleNamespace(ed_art=QLineEdit(), ed_path=QLineEdit(), _out_dir='C:/somewhere')
        self.assertEqual(Path(RecordBackgroundDialog.youtube_dir(fake)), _USER / 'YouTube')
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'cover.jpg'
            src.write_bytes(b'jpeg')
            RecordBackgroundDialog.set_art_file(fake, str(src))
            got = Path(fake.ed_art.text())
            self.assertEqual(got.parent, _USER / 'ジャケット')
            self.assertTrue(got.is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
