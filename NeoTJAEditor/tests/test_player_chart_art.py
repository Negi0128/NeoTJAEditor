"""曲別素材設定の確定/取消と既定保護。実ファイルは一時フォルダのみ。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
import test_player_record as record_tests
from test_player_record import _wait, real_qt_test

class ArtEntryTests(unittest.TestCase):
    setUp = record_tests.HostRecordTests.setUp
    tearDown = record_tests.HostRecordTests.tearDown
    _host = record_tests.HostRecordTests._host
    @real_qt_test
    def test_art_cancel_and_accept_keep_course_and_defaults(self):
        from PySide6.QtWidgets import QDialog
        from neotja.player.host import PlayerHost
        from neotja.recorder import RecordBackground
        from neotja.chart_art import record_background
        with tempfile.TemporaryDirectory() as folder:
            host, path, core = self._host(folder)
            host.cfg['rec_bg_default'] = {'dim': .3}
            host.cfg['rec_bg_path'] = 'global.mp4'
            saves=[]
            host._save_cb=lambda:saves.append(True)
            dialogs=[]
            class ArtDialog(QDialog):
                def background(self):
                    return RecordBackground('', art='', dim=.2, offset=1.25)
            def builder(*args):
                d=ArtDialog(host)
                dialogs.append(d)
                return d
            host.art_dialog_builder=builder
            host.difficulty.select_slot('Hard')
            before=copy.deepcopy(host.cfg)
            sidecar=Path(path+'.jacket.json')
            raw=sidecar.read_bytes()
            host.difficulty.settings.btn_art.click()
            _wait(100)
            self.assertEqual(len(dialogs),1)
            dialogs[-1].reject()
            _wait(100)
            self.assertEqual(host.cfg,before)
            self.assertEqual(sidecar.read_bytes(),raw)
            self.assertEqual(saves,[])
            host.difficulty.settings.btn_art.click()
            _wait(100)
            dialogs[-1].accept()
            _wait(100)
            bg=record_background(host.cfg,path)
            self.assertEqual((bg.path,bg.art,bg.offset,bg.dim),('','',1.25,.2))
            self.assertEqual(host.cfg['rec_bg_default'],before['rec_bg_default'])
            self.assertEqual(host.cfg['rec_bg_path'],'global.mp4')
            self.assertEqual(host.difficulty.current_course(),'Hard')
            self.assertEqual(saves,[True])
            self.assertEqual(core.log[-1],'demo')
            self.assertIsNone(host._art_dialog)
            host.shutdown();host.close();host.deleteLater()

    @real_qt_test
    def test_real_art_dialog_and_unsaved_editor_lyrics(self):
        from PySide6.QtWidgets import QWidget
        from neotja.lyric_jacket import LyricJackets
        from neotja.player.record import RecordContext, build_art_dialog
        with tempfile.TemporaryDirectory() as folder:
            host, path, core = self._host(folder)
            editor = QWidget()
            editor.current_file = path
            editor._jacket_dirty = True
            editor.lyric_jackets = LyricJackets('unsaved.png', {'未保存': 'live.png'})
            editor.set_jacket_base = editor.lyric_jackets.set_base
            ctx = RecordContext(host.cfg, path, core.dock, 'disk.png', editor)
            self.assertEqual(ctx.lyric_jackets.base, 'unsaved.png')
            ctx.set_jacket_base('changed.png')
            self.assertEqual(editor.lyric_jackets.lyrics, {'未保存': 'live.png'})
            self.assertEqual(editor.lyric_jackets.base, 'changed.png')
            raw = json.loads(Path(path+'.jacket.json').read_text(encoding='utf-8'))
            self.assertNotIn('未保存', raw['lyrics'])
            dlg = build_art_dialog(core, path, host.cfg, host)
            dlg.show();_wait(100)
            self.assertFalse(dlg.btn_def.isVisible())
            self.assertFalse(dlg.btn_def_clear.isVisible())
            before=copy.deepcopy(host.cfg)
            dlg._save_default();dlg._clear_default()
            self.assertEqual(host.cfg,before)
            dlg.reject();dlg.deleteLater()
            editor.close();editor.deleteLater()
            host.shutdown();host.close();host.deleteLater()

if __name__=='__main__':
    unittest.main()
