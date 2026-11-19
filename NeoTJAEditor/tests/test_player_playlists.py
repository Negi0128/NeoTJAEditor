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


def _tja(path, title, courses=(('Oni', 8), ('Hard', 6), ('Normal', 4))):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['TITLE:%s' % title, 'BPM:120', '']
    for c, lv in courses:
        lines += ['COURSE:%s' % c, 'LEVEL:%d' % lv, '#START', '1,', '#END', '']
    path.write_text('\n'.join(lines), encoding='cp932')
    return str(path)


def _wait(ms):
    import time
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QApplication.processEvents(QEventLoop.AllEvents, 10)
        time.sleep(0.002)


class StoreItemTests(unittest.TestCase):
    def setUp(self):
        _reset_user_dir()

    def test_item_ops(self):
        from neotja.player import playlists
        st = playlists.PlaylistStore()
        pl = st.create('P')
        for name in ('a', 'b', 'a', 'c'):           # 同じ曲を2回入れてよい
            self.assertTrue(st.add_item(pl['id'], 'C:/x/%s.tja' % name, 'Oni'))
        names = lambda: [os.path.basename(i['path']) for i in playlists.load()[0]['items']]
        self.assertEqual(names(), ['a.tja', 'b.tja', 'a.tja', 'c.tja'])
        self.assertTrue(st.move_item(pl['id'], 3, 0))
        self.assertEqual(names(), ['c.tja', 'a.tja', 'b.tja', 'a.tja'])
        self.assertFalse(st.move_item(pl['id'], 1, 1))
        self.assertTrue(st.set_item_course(pl['id'], 1, 'Hard'))
        self.assertEqual(playlists.load()[0]['items'][1]['course'], 'Hard')
        self.assertTrue(st.remove_item(pl['id'], 2))
        self.assertEqual(names(), ['c.tja', 'a.tja', 'a.tja'])
        self.assertFalse(st.remove_item(pl['id'], 9))

    def test_default_course(self):
        from neotja.player.playlists import default_course
        self.assertEqual(default_course([('Edit', 10), ('Oni', 9), ('Hard', 6)]), 'Oni')
        self.assertEqual(default_course([('Edit', 10), ('Hard', 6)]), 'Edit')
        self.assertEqual(default_course([('Hard', 6), ('Easy', 2)]), 'Hard')
        self.assertIsNone(default_course([]))


class AddAndPlayTests(unittest.TestCase):
    def setUp(self):
        _reset_user_dir()
        self.dir = tempfile.TemporaryDirectory()
        d = Path(self.dir.name)
        self.a = _tja(d / 'a' / 'a.tja', 'Aの曲')
        self.b = _tja(d / 'b' / 'b.tja', 'Bの曲', courses=(('Hard', 5),))
        self.gone = str(d / 'gone' / 'gone.tja')

    def tearDown(self):
        from neotja.worker_util import LINGERING_WORKERS
        _wait(50)
        for _ in range(200):
            if not LINGERING_WORKERS:
                break
            _wait(10)
        self.dir.cleanup()

    def _lib(self):
        from neotja.player.library_view import LibraryView
        lib = LibraryView({'master_volume': 0.0,
                           'player_saved': [{'path': self.a, 'title': 'Aの曲'},
                                            {'path': self.b, 'title': 'Bの曲'}]},
                          lambda: None)
        lib.resize(1400, 900)
        lib.show()
        self.picks = []

        def picker(title, courses, current):
            self.picks.append((title, [c for c, _l in courses], current))
            return self.answer
        self.answer = 'Hard'
        lib.course_picker = picker
        return lib

    @real_qt_test
    def test_add_mode(self):
        lib = self._lib()
        pl = lib.playlist_store().create('練習')
        lib.open_playlist(pl['id'])
        self.assertTrue(lib.btn_add_songs.isVisibleTo(lib))
        switched = []
        lib.list_switched.connect(lambda: switched.append(1))
        selected = []
        lib.song_selected.connect(selected.append)
        lib.btn_add_songs.click()
        self.assertEqual(lib.add_mode_target(), pl['id'])
        self.assertTrue(lib.add_bar.isVisibleTo(lib))
        self.assertEqual(lib.add_bar.title.text(), '練習')
        self.assertEqual(len(switched), 1)                  # 試聴を止める
        lib.show_section('saved')
        rows = lib.saved_list.rows()
        # 押すと追加(選ぶ・試聴はしない)。コースは「おに」を先に選んでおく。
        rows[0].chosen.emit(self.a)
        self.assertEqual(self.picks, [('Aの曲', ['Oni', 'Hard', 'Normal'], 'Oni')])
        self.assertEqual(selected, [])
        self.assertTrue(rows[0]._flash)
        # コースが1つなら聞かない。
        lib.click_song(self.b)
        self.assertEqual(len(self.picks), 1)
        # やめたら入れない。
        self.answer = None
        lib.click_song(self.a)
        self.assertEqual(len(self.picks), 2)
        items = lib.playlist_store().get(pl['id'])['items']
        self.assertEqual([(os.path.basename(i['path']), i['course']) for i in items],
                         [('a.tja', 'Hard'), ('b.tja', 'Hard')])
        # 完了でプレイリストへ戻る。
        lib.add_bar.btn_done.click()
        self.assertEqual(lib.add_mode_target(), '')
        self.assertFalse(lib.add_bar.isVisibleTo(lib))
        self.assertIs(lib.pages.currentWidget(), lib.page_playlist)
        rows = lib.playlist_song_list.rows()
        self.assertEqual([r.num.text() for r in rows], ['1', '2'])
        self.assertEqual([r.course_icon.course for r in rows], ['Hard', 'Hard'])
        # 左の列でプレイリストを開き直すと、追加のあいだは終わる。
        lib.begin_add_mode(pl['id'])
        lib.show_section('playlists')
        self.assertEqual(lib.add_mode_target(), '')
        self.assertFalse(lib.add_bar.isVisibleTo(lib))
        lib.shutdown()
        lib.close()

    @real_qt_test
    def test_toast_after_each_add(self):
        lib = self._lib()
        pl = lib.playlist_store().create('練習')
        self.assertIsNone(lib.toast)
        lib.add_song_to_playlist(pl['id'], self.b)
        self.assertTrue(lib.toast.isVisible())
        self.assertEqual(lib.toast.text(), '「Bの曲」を「練習」に入れました')
        # 続けて入れたら置き換える(1つだけ)。重複も入る(止めない)。
        lib.add_song_to_playlist(pl['id'], self.b)
        self.assertEqual(len([c for c in lib.children() if type(c).__name__ == 'Toast']), 1)
        self.assertEqual(len(lib.playlist_store().get(pl['id'])['items']), 2)
        # やめたら出さない。
        self.answer = None
        lib.toast.hide()
        lib.add_song_to_playlist(pl['id'], self.a)
        self.assertFalse(lib.toast.isVisible())
        # しばらくすると消える。
        self.answer = 'Oni'
        lib.add_song_to_playlist(pl['id'], self.a)
        self.assertEqual(lib.toast.text(), '「Aの曲」を「練習」に入れました')
        _wait(lib.toast.HOLD_MS + lib.toast.FADE_MS + 400)
        self.assertFalse(lib.toast.isVisible())
        lib.shutdown()
        lib.close()

    @real_qt_test
    def test_right_click_add(self):
        from PySide6.QtCore import QPoint
        lib = self._lib()
        st = lib.playlist_store()
        p1 = st.create('一つ目')
        p2 = st.create('二つ目')
        lib.show_section('saved')
        seen = []

        def fake_exec(menu, pos):
            from PySide6.QtWidgets import QMenu
            sub = menu.findChildren(QMenu)[0]
            seen.append((menu.actions()[0].text(), [x.text() for x in sub.actions()]))
            return sub.actions()[1]
        lib.exec_menu = fake_exec
        lib.saved_list.rows()[1].menu_requested.emit(self.b, QPoint(0, 0))
        # プレイリストの曲の行の右クリック: 難易度を変更 / 外す。
        lib.open_playlist(p2['id'])
        lib.exec_menu = lambda menu, pos: (seen.append([x.text() for x in menu.actions()]),
                                           menu.actions()[1])[1]
        lib.playlist_song_list.rows()[0].item_menu.emit(0, QPoint(0, 0))
        self.assertEqual(seen[-1], ['難易度を変更', '外す'])
        self.assertEqual(st.get(p2['id'])['items'], [])
        seen.pop()
        st.add_item(p2['id'], self.b, 'Hard')
        self.assertEqual(seen, [('プレイリストに追加', ['一つ目', '二つ目'])])
        self.assertEqual(st.get(p1['id'])['items'], [])
        self.assertEqual([(i['path'], i['course']) for i in st.get(p2['id'])['items']],
                         [(os.path.normpath(self.b), 'Hard')])
        lib.shutdown()
        lib.close()

    @real_qt_test
    def test_playlist_rows_click_order_and_missing(self):
        from neotja.player.library_view import PlaylistSongRow
        lib = self._lib()
        st = lib.playlist_store()
        pl = st.create('P')
        st.add_item(pl['id'], self.a, 'Oni')
        st.add_item(pl['id'], self.b, 'Hard')
        st.add_item(pl['id'], self.a, 'Normal')        # 同じ曲を別のコースで
        st.add_item(pl['id'], self.gone, 'Oni')
        lib.open_playlist(pl['id'])
        for _ in range(200):
            if lib.playlist_song_list.rows()[0].title.text() == 'Aの曲':
                break
            _wait(10)
        rows = lib.playlist_song_list.rows()
        self.assertTrue(all(isinstance(r, PlaylistSongRow) for r in rows))
        self.assertEqual(rows[0].title.text(), 'Aの曲')
        self.assertEqual([r.num.text() for r in rows], ['1', '2', '3', '4'])
        self.assertTrue(rows[3].missing)
        self.assertEqual([c.text() for c in rows[3].chips], ['ファイルなし'])
        selected, chosen = [], []
        lib.song_selected.connect(selected.append)
        lib.song_chosen.connect(chosen.append)
        # 見つからない曲は押しても何もしない。
        rows[3].item_clicked.emit(3)
        self.assertEqual(selected, [])
        # 1回目: 選んで試聴。同じ曲の3行目は選ばない。
        rows[2].item_clicked.emit(2)
        self.assertEqual(selected, [self.a])
        self.assertEqual([r.mark.state for r in rows], [None, None, 'playing', None])
        # 同じ曲でも別の行を押したら選び直し(難易度選択へは行かない)。
        _wait(200)
        rows[0].item_clicked.emit(0)
        self.assertEqual(chosen, [])
        self.assertEqual(lib.chosen_course(), 'Oni')
        _wait(200)
        rows[0].item_clicked.emit(0)
        self.assertEqual(chosen, [self.a])
        self.assertEqual(lib.chosen_course(), 'Oni')
        # 並べ替え: 1行目を最後へ。選んでいる行も付いていく。
        self.assertTrue(lib.move_playlist_item(0, 3))
        rows = lib.playlist_song_list.rows()
        self.assertEqual([r.course for r in rows], ['Hard', 'Normal', 'Oni', 'Oni'])
        self.assertEqual(rows[3].path, os.path.normpath(self.a))
        self.assertEqual(lib._chosen_index, 3)
        self.assertEqual(rows[3].mark.state, 'playing')
        # 難易度を変更
        self.answer = 'Hard'
        self.assertTrue(lib.change_playlist_item_course(3))
        self.assertEqual(self.picks[-1][2], 'Oni')
        self.assertEqual(st.get(pl['id'])['items'][3]['course'], 'Hard')
        self.assertEqual(lib.chosen_course(), 'Hard')
        # 外す
        self.assertTrue(lib.remove_playlist_item(0))
        self.assertEqual(lib._chosen_index, 2)
        self.assertEqual(len(lib.playlist_song_list.rows()), 3)
        self.assertEqual(len(playlists_items(pl['id'])), 3)
        # ふつうの行を押せば、保存したコースは使わない。
        lib.show_section('saved')
        lib.click_song(self.b)
        self.assertIsNone(lib.chosen_course())
        lib.shutdown()
        lib.close()

    @real_qt_test
    def test_drop_reorders(self):
        from PySide6.QtCore import QMimeData, QPointF, Qt
        from PySide6.QtGui import QDropEvent
        from neotja.player.library_view import PLAYLIST_MIME
        lib = self._lib()
        st = lib.playlist_store()
        pl = st.create('P')
        for p in (self.a, self.b, self.a):
            st.add_item(pl['id'], p, 'Hard')
        lib.open_playlist(pl['id'])
        _wait(100)
        lst = lib.playlist_song_list
        rows = lst.rows()
        # 1行目を、3行目の下半分へ落とす → 最後へ。
        g = rows[2].geometry()
        pos = QPointF(40, g.center().y() + 10 - lst.verticalScrollBar().value())
        mime = QMimeData()
        mime.setData(PLAYLIST_MIME, b'0')
        ev = QDropEvent(pos, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        lst.dropEvent(ev)
        got = [os.path.basename(i['path']) for i in st.get(pl['id'])['items']]
        self.assertEqual(got, ['b.tja', 'a.tja', 'a.tja'])
        lib.shutdown()
        lib.close()

    def test_host_passes_saved_course(self):
        from types import SimpleNamespace
        from neotja.player.host import PlayerHost
        calls = []
        fake = SimpleNamespace(
            _select_timer=SimpleNamespace(stop=lambda: None), _select_path='',
            current_song='', song_chosen=SimpleNamespace(emit=lambda p: None),
            library=SimpleNamespace(chosen_course=lambda: 'Normal'),
            show_difficulty=lambda path, **kw: calls.append((path, kw)))
        PlayerHost._on_song_chosen(fake, 'x.tja')
        self.assertEqual(calls, [('x.tja', {'course': 'Normal', 'restart': False})])


def playlists_items(pid):
    from neotja.player import playlists
    return next(p for p in playlists.load() if p['id'] == pid)['items']


if __name__ == '__main__':
    unittest.main(verbosity=2)
