"""プレイリスト(第1段): 利用者フォルダ・playlists.json・選曲の左の列と一覧・作成/編集/削除。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_playlists.py`
実設定・exe の横のフォルダには触らない(置き場所は一時フォルダへ差し替える)。
窓(作成/編集・確かめ・ファイル選択)は出さない。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-playlist-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from neotja import user_dirs  # noqa: E402

_USER = Path(_TMP) / 'user'
user_dirs.base_override = str(_USER)

from gui_support import real_qt_test  # noqa: E402


def _png(path, color='#3366ff', size=64):
    from PySide6.QtGui import QColor, QImage
    img = QImage(size, size, QImage.Format_RGB32)
    img.fill(QColor(color))
    img.save(str(path))
    return str(path)


def _reset_user_dir():
    import shutil
    if _USER.exists():
        shutil.rmtree(_USER)


class UserDirsTests(unittest.TestCase):
    def test_base_frozen_and_source(self):
        with mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'executable', r'C:\Apps\Neo\NeoTJAEditor.exe'):
            self.assertEqual(user_dirs.app_base(), Path(r'C:\Apps\Neo').resolve())
        with mock.patch.object(sys, 'frozen', False, create=True):
            app_dir = Path(user_dirs.__file__).resolve().parent.parent
            self.assertEqual(user_dirs.app_base(), app_dir)
            self.assertTrue((app_dir / 'neotja').is_dir())

    def test_override_and_lazy(self):
        _reset_user_dir()
        self.assertEqual(user_dirs.root(), _USER)
        p = user_dirs.user_dir('playlists', create=False)
        self.assertEqual(p, _USER / 'プレイリスト')
        self.assertFalse(p.exists())
        self.assertTrue(user_dirs.user_dir('jackets').is_dir())
        self.assertFalse((_USER / '録画').exists())
        self.assertEqual(set(user_dirs.SUBDIRS.values()),
                         {'録画', 'YouTube', 'プレイリスト', 'ジャケット'})

    def test_env_override(self):
        old = user_dirs.base_override
        user_dirs.base_override = None
        try:
            with tempfile.TemporaryDirectory() as d, \
                    mock.patch.dict(os.environ, {user_dirs.ENV_VAR: d}):
                self.assertEqual(user_dirs.root(), Path(d))
        finally:
            user_dirs.base_override = old


class StoreTests(unittest.TestCase):
    def setUp(self):
        _reset_user_dir()

    def test_roundtrip_atomic(self):
        from neotja.player import playlists
        self.assertEqual(playlists.load(), [])
        self.assertFalse((_USER / 'プレイリスト').exists())   # 読むだけでは作らない
        st = playlists.PlaylistStore()
        a = st.create('A', '説明A')
        st.create('B')
        path = playlists.file_path()
        data = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(data['version'], 1)
        self.assertEqual([p['title'] for p in data['playlists']], ['A', 'B'])
        self.assertEqual(data['playlists'][0],
                         {'id': a['id'], 'title': 'A', 'description': '説明A',
                          'jacket': None, 'items': []})
        self.assertEqual([x.name for x in path.parent.iterdir()], ['playlists.json'])
        again = playlists.load()
        self.assertEqual(again, st.items)

    def test_items_schema_kept(self):
        from neotja.player import playlists
        raw = {'playlists': [{'id': 'x', 'title': 'T', 'items': [
            {'path': 'C:/a.tja', 'course': 'Oni'}, {'path': ''}, 'bad', {'path': 'b.tja'}]}]}
        f = playlists.file_path()
        f.write_text(json.dumps(raw), encoding='utf-8')
        got = playlists.load()
        self.assertEqual(got[0]['items'], [{'path': 'C:/a.tja', 'course': 'Oni'},
                                           {'path': 'b.tja', 'course': None}])

    def test_broken_file_set_aside(self):
        from neotja.player import playlists
        f = playlists.file_path()
        f.write_text('{broken', encoding='utf-8')
        self.assertEqual(playlists.load(), [])
        self.assertFalse(f.exists())
        self.assertTrue(any(x.name.startswith('playlists.json.broken-')
                            for x in f.parent.iterdir()))

    def test_jacket_copied_and_survives(self):
        from neotja.player import playlists
        with tempfile.TemporaryDirectory() as d:
            src = _png(Path(d) / 'j.png')
            st = playlists.PlaylistStore()
            pl = st.create('J', '', src)
        # 元の絵は消えたが、写したものは残る。
        self.assertFalse(os.path.exists(src))
        self.assertNotIn(os.sep, pl['jacket'])
        jp = playlists.jacket_path(pl)
        self.assertTrue(os.path.isfile(jp))
        self.assertEqual(Path(jp).parent, _USER / 'ジャケット')
        # 外すと写したものも消える。
        st.update(pl['id'], 'J', '', clear_jacket=True)
        self.assertIsNone(st.get(pl['id'])['jacket'])
        self.assertFalse(os.path.exists(jp))

    def test_delete_removes_jacket(self):
        from neotja.player import playlists
        with tempfile.TemporaryDirectory() as d:
            src = _png(Path(d) / 'j.png')
            st = playlists.PlaylistStore()
            pl = st.create('J', '', src)
        jp = playlists.jacket_path(pl)
        self.assertTrue(st.delete(pl['id']))
        self.assertFalse(os.path.exists(jp))
        self.assertEqual(playlists.load(), [])


class LibraryTests(unittest.TestCase):
    def setUp(self):
        _reset_user_dir()

    def _lib(self):
        from neotja.player.library_view import LibraryView
        lib = LibraryView({'master_volume': 0.0}, lambda: None)
        lib.resize(1400, 900)
        lib.show()
        lib.show_section('open')
        return lib

    @real_qt_test
    def test_sidebar_entry_below_saved(self):
        lib = self._lib()
        lay = lib.nav_saved.parentWidget().layout()
        idx = [lay.indexOf(b) for b in (lib.nav_saved, lib.nav_playlists)]
        self.assertEqual(idx[1], idx[0] + 1)
        self.assertEqual(lib.nav_playlists.text(), 'プレイリスト')
        self.assertTrue(lib.nav_playlists.property('sub'))
        switched = []
        lib.list_switched.connect(lambda: switched.append(1))
        lib.nav_playlists.click()
        self.assertIs(lib.pages.currentWidget(), lib.page_playlists)
        self.assertEqual(lib.current_section(), 'playlists')
        self.assertEqual(len(switched), 1)
        self.assertEqual(lib.playlists_list.rows(), [])
        self.assertTrue(lib.playlists_list.empty.isVisibleTo(lib))
        # 読むだけではフォルダを作らない。
        self.assertFalse((_USER / 'プレイリスト').exists())
        lib.close()

    @real_qt_test
    def test_create_edit_delete_open(self):
        from neotja.player.playlist_view import PlaylistRow
        lib = self._lib()
        lib.show_section('playlists')
        answers = []
        lib.playlist_editor = lambda pl: answers.pop(0)
        with tempfile.TemporaryDirectory() as d:
            src = _png(Path(d) / 'j.png', '#ff8800')
            answers.append(('夏の曲', '暑い日に', src, False))
            a = lib.create_playlist()
            answers.append(('  ', '', None, False))    # 題が空なら作らない
            self.assertIsNone(lib.create_playlist())
            answers.append(None)                      # キャンセル
            self.assertIsNone(lib.create_playlist())
            answers.append(('練習', '', None, False))
            b = lib.create_playlist()
        rows = lib.playlists_list.rows()
        self.assertEqual([r.pl['title'] for r in rows], ['夏の曲', '練習'])
        self.assertTrue(all(isinstance(r, PlaylistRow) for r in rows))
        self.assertIsNotNone(rows[0].jacket.pixmap)       # 写したジャケット
        self.assertIsNone(rows[1].jacket.pixmap)          # No Image
        # 編集
        answers.append(('夏の曲2', '説明を変えた', None, False))
        lib.playlist_action(a['id'], 'edit')
        rows = lib.playlist_store().items
        self.assertEqual(rows[0]['title'], '夏の曲2')
        self.assertEqual(rows[0]['description'], '説明を変えた')
        self.assertTrue(rows[0]['jacket'])                 # 絵はそのまま
        self.assertEqual(lib.playlists_list.rows()[0].pl['title'], '夏の曲2')
        # 開く: 空の曲の一覧、試聴を止める合図。
        switched = []
        lib.list_switched.connect(lambda: switched.append(1))
        lib.playlists_list.rows()[1].opened.emit(b['id'])
        self.assertIs(lib.pages.currentWidget(), lib.page_playlist)
        self.assertEqual(lib.page_playlist.lbl_title.text(), '練習')
        self.assertEqual(lib.playlist_song_list.rows(), [])
        self.assertEqual(len(switched), 1)
        self.assertTrue(lib.nav_playlists.isChecked())
        lib.open_playlist(a['id'])
        self.assertEqual(len(switched), 2)
        lib.open_playlist(a['id'])                         # 同じものを押し直しただけ
        self.assertEqual(len(switched), 2)
        lib.show_section('playlists')
        self.assertEqual(len(switched), 3)
        # 削除: 確かめで「いいえ」なら残る、「削除」なら消える。
        asked = []
        lib.playlist_confirm = lambda head, ok, yes: asked.append((head, ok, yes))
        lib.playlist_action(b['id'], 'delete')
        self.assertEqual(asked[0][:2], ('「練習」を削除', '削除'))
        self.assertIsNotNone(lib.playlist_store().get(b['id']))
        asked[0][2]()
        self.assertIsNone(lib.playlist_store().get(b['id']))
        self.assertEqual([r.pl['title'] for r in lib.playlists_list.rows()], ['夏の曲2'])
        # 開いているものを消したら一覧へ戻る。
        lib.open_playlist(a['id'])
        lib.delete_playlist(a['id'])
        self.assertIs(lib.pages.currentWidget(), lib.page_playlists)
        self.assertEqual(lib.playlists_list.rows(), [])
        lib.close()

    @real_qt_test
    def test_fallback_first_item_jacket(self):
        from neotja.player import playlists
        from neotja.player.playlist_view import playlist_jacket_pixmap
        with tempfile.TemporaryDirectory() as d:
            tja = Path(d) / 'song' / 'a.tja'
            tja.parent.mkdir()
            tja.write_text('TITLE:A\n', encoding='cp932')
            jk = _png(Path(d) / 'jk.png', '#00ff00')
            key = os.path.normcase(os.path.normpath(os.path.abspath(str(tja))))
            cfg = {'player_chart_art': {key: {'path': str(tja), 'jacket': jk}}}
            pl = playlists.new_playlist('X')
            self.assertIsNone(playlist_jacket_pixmap(pl, cfg))
            pl['items'] = [{'path': str(tja), 'course': 'Oni'}]
            pm = playlist_jacket_pixmap(pl, cfg)
            self.assertIsNotNone(pm)
            self.assertEqual(pm.toImage().pixelColor(5, 5).name(), '#00ff00')

    @real_qt_test
    def test_dialog_values_without_showing(self):
        from neotja.player.playlist_view import PlaylistDialog
        dlg = PlaylistDialog(None)
        self.assertFalse(dlg.btn_ok.isEnabled())
        self.assertFalse(dlg.btn_clear.isEnabled())
        dlg.ed_title.setText('題')
        dlg.ed_desc.setPlainText('説明')
        self.assertTrue(dlg.btn_ok.isEnabled())
        with tempfile.TemporaryDirectory() as d:
            src = _png(Path(d) / 'j.png')
            dlg.pick_image = lambda: src
            dlg.btn_pick.click()
            self.assertIsNotNone(dlg.jacket.pixmap)
            self.assertTrue(dlg.btn_clear.isEnabled())
            self.assertEqual(dlg.values(), ('題', '説明', src, False))
        dlg.btn_clear.click()
        self.assertEqual(dlg.values(), ('題', '説明', None, True))
        self.assertIsNone(dlg.jacket.pixmap)
        self.assertFalse(dlg.isVisible())
        dlg.deleteLater()


if __name__ == '__main__':
    unittest.main(verbosity=2)
