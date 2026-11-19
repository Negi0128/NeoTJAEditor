"""プレイリスト(第3段): 連続再生の曲名表示(左に寄せた表示＋右の板)・途中メニューの
「次の曲へ」「やめる」・結果画面を出さずに次の曲へ・最後でプレイリストへ戻る。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_playlist_play.py`
実設定・exe の横のフォルダには触らない。窓は出さない。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-plplay-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from neotja import user_dirs  # noqa: E402

user_dirs.base_override = str(Path(_TMP) / 'user')

from gui_support import real_qt_test  # noqa: E402


def _wait(ms):
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QApplication.processEvents(QEventLoop.AllEvents, 10)
        time.sleep(0.002)


def _key(widget, key):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))



def _through_overview(host):
    """連続再生の最初の照会画面(自分では進まない)で「再生する」を押す。"""
    _wait(700)
    assert host.body.currentWidget() is host.playlist_overview
    _wait(300)
    assert host.body.currentWidget() is host.playlist_overview   # 待っても進まない
    host.playlist_overview.btn_play.click()
    _wait(700)

def _chart(folder, name, courses=(('Oni', 9), ('Edit', 10))):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'song.ogg').write_bytes(b'x')
    lines = ['TITLE:%s' % name, 'BPM:120', 'WAVE:song.ogg', 'DEMOSTART:3', '']
    for c, lv in courses:
        lines += ['COURSE:%s' % c, 'LEVEL:%d' % lv, '#START', '1111,', '#END', '']
    p = folder / (name + '.tja')
    p.write_text('\n'.join(lines), encoding='cp932')
    return str(p)


def _fake_core_class():
    from PySide6.QtWidgets import QWidget

    class FakeGpw(QWidget):
        def __init__(self):
            super().__init__()
            self.locks = []
            self.resize(640, 360)

        def set_view_only(self, on):
            pass

        def set_controls_locked(self, on, key_cb=None):
            self.locks.append(bool(on))

        def fit_view_only(self, w, h):
            pass

    class FakeDock:
        def __init__(self):
            self.game_preview_window = FakeGpw()
            self.game_screen = QWidget()
            self.chart_preview = type('C', (), {'reset_to_start': lambda s: None})()
            self.volumes = []

        def set_volume(self, v):
            self.volumes.append(v)

        def set_master_volume(self, v):
            pass

        def cancel_predecode(self):
            pass

    class FakeCore:
        instances = []

        def __init__(self, cfg):
            self.dock = FakeDock()
            self.log = []
            self.finished = False
            FakeCore.instances.append(self)

        def play_demo(self, path, demo):
            self.log.append(('demo', os.path.basename(path)))

        def stop_audio(self):
            self.log.append('stop')

        def shutdown(self):
            pass

        def predecode(self, path):
            pass

        def load(self, path, course_key=None, play_mode=None):
            self.log.append(('load', os.path.basename(path), course_key))
            return True

        def use_normal_play_mode(self):
            pass

        def chart_ready(self):
            return True

        def play_chart(self, at=0.0):
            self.log.append('play_chart')

        def pause_chart(self):
            self.log.append('pause_chart')

        def resume_chart(self):
            pass

        def chart_finished(self):
            return self.finished
    return FakeCore


class PauseMenuTests(unittest.TestCase):
    @real_qt_test
    def test_playlist_items(self):
        from neotja.player.pause_menu import PauseMenu
        m = PauseMenu()
        m.resize(1600, 900)
        normal = [it.text() for it in m.items]
        self.assertEqual(normal, ['演奏を続ける', '初めからやり直す', '詳細モードに移行する',
                                  '選曲画面にもどる'])
        m.set_playlist_mode(True)
        self.assertEqual([it.text() for it in m.items],
                         ['演奏を続ける', '初めからやり直す', '次の曲へ',
                          '詳細モードに移行する', 'やめる'])
        self.assertEqual([it.number for it in m.items], [1, 2, 3, 4, 5])
        got = []
        m.chosen.connect(got.append)
        m.open()
        from PySide6.QtCore import Qt
        _key(m, Qt.Key_3)
        _key(m, Qt.Key_5)
        self.assertEqual(got, ['next', 'library'])
        m.set_playlist_mode(False)
        self.assertEqual([it.text() for it in m.items], normal)
        self.assertEqual([it.number for it in m.items], [1, 2, 3, 4])
        self.assertFalse(m.item('next') is not None)
        m.close_menu()
        m.deleteLater()


class IntroPanelTests(unittest.TestCase):
    @real_qt_test
    def test_normal_unchanged_and_playlist_panel(self):
        from PySide6.QtCore import Qt
        from neotja.player.intro import IntroPage
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a', (('Hard', 6), ('Oni', 9), ('Edit', 10)))
            page = IntroPage({'master_volume': 0.0})
            page.resize(1600, 900)
            page.show()
            page.set_song(a, 'Oni', 9)
            normal_jr = page.jacket_rect()
            self.assertAlmostEqual(normal_jr.center().x(), 800.0, places=3)
            self.assertIsNone(page.btn_play)
            page.set_playlist(2, 5, [('Edit', 10), ('Oni', 9), ('Hard', 6)])
            self.assertLess(page.jacket_rect().center().x(), 600)
            self.assertTrue(page.btn_play.isVisible())
            self.assertEqual(page.btn_play.text(), '再生する')
            self.assertEqual(page.btn_quit.text(), 'やめる')
            self.assertEqual(list(page._pl_cards), ['Hard', 'Oni', 'Edit'])
            self.assertTrue(page._pl_cards['Oni'].selected)
            self.assertGreater(page.btn_play.geometry().left(), page._left_width() - 1)
            ev = []
            page.play_requested.connect(lambda: ev.append('play'))
            page.quit_requested.connect(lambda: ev.append('quit'))
            page.course_changed.connect(lambda c: ev.append(c))
            snd = []
            page.sound_requested.connect(snd.append)
            _key(page, Qt.Key_Right)
            self.assertEqual(page.course, 'Edit')
            self.assertEqual(page.level, 10)
            page._pl_cards['Hard'].clicked.emit('Hard')
            self.assertEqual(page.course, 'Hard')
            _key(page, Qt.Key_Return)
            page.btn_quit.click()
            self.assertEqual(ev, ['Edit', 'Hard', 'play', 'quit'])
            self.assertEqual(snd, ['pi', 'pi', 'don', 'pi'])
            # 普段の曲名表示に戻すと、板は消えて真ん中に戻る。
            page.clear_playlist()
            self.assertFalse(page.btn_play.isVisible())
            self.assertEqual(page.jacket_rect(), normal_jr)
            ev.clear()
            _key(page, Qt.Key_Return)
            self.assertEqual(ev, [])
            page.close()
            page.deleteLater()


class HostSessionTests(unittest.TestCase):
    def setUp(self):
        from neotja.player.host import PlayerHost
        import shutil
        shutil.rmtree(user_dirs.base_override, ignore_errors=True)
        self.FakeCore = _fake_core_class()
        self._orig = PlayerHost.core_factory
        PlayerHost.core_factory = self.FakeCore

    def tearDown(self):
        from neotja.player.host import PlayerHost
        PlayerHost.core_factory = self._orig

    def _host(self, d):
        from neotja.player.host import PlayerHost
        self.a = _chart(Path(d) / 'a', 'a')
        self.b = _chart(Path(d) / 'b', 'b', (('Hard', 5),))
        host = PlayerHost({'master_volume': 0.0, 'preview_volume': 0.5}, lambda: None)
        host.resize(1600, 900)
        host.show()
        host.on_shown()
        self.sounds = []
        host.sounds.play = lambda kind: self.sounds.append(kind)
        host.sounds.pi = lambda: self.sounds.append('pi')
        st = host.library.playlist_store()
        pl = st.create('P')
        st.add_item(pl['id'], self.a, 'Oni')
        st.add_item(pl['id'], str(Path(d) / 'gone.tja'), 'Oni')   # 見つからない → 飛ばす
        st.add_item(pl['id'], self.b, 'Oni')                      # 無いコース → ある中で
        host.library.open_playlist(pl['id'])
        self.pid = pl['id']
        return host

    def _press_play_and_enter(self, host):
        host.intro.btn_play.click()
        seq = host._seq
        self.assertIsNotNone(seq)
        seq._t0 -= 2.0
        seq.tick()
        _wait(800)
        self.assertIs(host.body.currentWidget(), host.play_page)

    @real_qt_test
    def test_manual_flow_without_results(self):
        with tempfile.TemporaryDirectory() as d:
            host = self._host(d)
            lib = host.library
            self.assertTrue(lib.btn_play_all.isEnabled())
            lib.btn_play_all.click()
            # 板(範囲・手動/自動)が出る。既定は 1〜最後・手動。
            panel = lib.play_panel
            self.assertTrue(panel.isVisible())
            self.assertEqual(panel.options(), {'start': 0, 'end': 2, 'auto': False, 'wait': 10})
            panel.btn_start.click()
            self.assertFalse(panel.isVisible())
            _through_overview(host)
            core = self.FakeCore.instances[-1]
            self.assertIs(host.body.currentWidget(), host.intro)
            self.assertEqual(host.intro.playlist, (1, 2))
            self.assertEqual(host.intro.title, 'a')
            self.assertIn(('demo', 'a.tja'), core.log)
            self.assertTrue(host.play_page.menu.playlist_mode)
            # 手動: 待っても進まない。
            _wait(400)
            self.assertIsNone(host._seq)
            self.assertIs(host.body.currentWidget(), host.intro)
            # この曲だけコースを替えて再生する(ドン)。
            host.intro.pick_course('Edit')
            self._press_play_and_enter(host)
            self.assertIn('don', self.sounds)
            self.assertIn(('load', 'a.tja', 'Edit'), core.log)
            self.assertEqual(host.library.playlist_store().get(self.pid)['items'][0]['course'],
                             'Oni')                              # 保存した値は変えない
            # 終わったら結果を出さずに次の曲(消えた曲は飛ばす)。
            done = []
            host.play_finished.connect(lambda p, c: done.append(os.path.basename(p)))
            core.finished = True
            _wait(900)
            core.finished = False
            self.assertEqual(done, ['a.tja'])
            self.assertIs(host.body.currentWidget(), host.intro)
            self.assertEqual(host.intro.playlist, (2, 2))
            self.assertEqual(host.intro.course, 'Hard')
            self.assertNotEqual(host.body.currentWidget(), host.results)
            # 途中メニュー: 次の曲へ(最後なので) → プレイリストの画面へ戻る。
            self._press_play_and_enter(host)
            self.assertIn(('load', 'b.tja', 'Hard'), core.log)
            host.open_pause_menu()
            menu = host.play_page.menu
            self.assertIn('次の曲へ', [it.text() for it in menu.items])
            menu.choose('next')
            _wait(900)
            self.assertIsNone(host.playlist_session())
            self.assertIs(host.body.currentWidget(), host.library)
            self.assertIs(lib.pages.currentWidget(), lib.page_playlist)
            self.assertFalse(menu.playlist_mode)
            self.assertIsNone(host.intro.playlist)
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_quit_from_intro_and_menu(self):
        with tempfile.TemporaryDirectory() as d:
            host = self._host(d)
            lib = host.library
            self.assertTrue(host.start_playlist(self.pid))
            _through_overview(host)
            host.intro.btn_quit.click()
            _wait(800)
            self.assertIsNone(host.playlist_session())
            self.assertIs(host.body.currentWidget(), host.library)
            self.assertIs(lib.pages.currentWidget(), lib.page_playlist)
            # 再生中の「やめる」もプレイリストごと終わる。「初めから」はこの曲の曲名表示へ。
            host.start_playlist(self.pid)
            _through_overview(host)
            self._press_play_and_enter(host)
            host.open_pause_menu()
            host.play_page.menu.choose('restart')
            _wait(800)
            self.assertIs(host.body.currentWidget(), host.intro)
            self.assertEqual(host.intro.playlist, (1, 2))
            self._press_play_and_enter(host)
            host.open_pause_menu()
            host.play_page.menu.choose('library')
            _wait(800)
            self.assertIsNone(host.playlist_session())
            self.assertIs(lib.pages.currentWidget(), lib.page_playlist)
            # 普段の曲名表示は板なし。
            host.start_intro(self.a, 'Oni')
            self.assertIsNone(host.intro.playlist)
            self.assertIsNotNone(host._seq)
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_empty_playlist_does_not_start(self):
        with tempfile.TemporaryDirectory() as d:
            host = self._host(d)
            st = host.library.playlist_store()
            empty = st.create('E')
            st.add_item(empty['id'], str(Path(d) / 'nope.tja'), 'Oni')
            self.assertFalse(host.start_playlist(empty['id']))
            self.assertIsNone(host.playlist_session())
            host.library.open_playlist(empty['id'])
            self.assertFalse(host.library.btn_play_all.isEnabled())
            host.shutdown()
            host.deleteLater()


class Step4Tests(unittest.TestCase):
    """範囲・自動(待ってから自分で進む)。"""

    setUp = HostSessionTests.setUp
    tearDown = HostSessionTests.tearDown
    _host = HostSessionTests._host

    @real_qt_test
    def test_panel_saves_mode_and_range(self):
        with tempfile.TemporaryDirectory() as d:
            host = self._host(d)
            lib = host.library
            started = []
            host.start_playlist = lambda pid, **kw: started.append((pid, kw)) or True
            panel = lib.open_play_panel()
            self.assertEqual(panel.sp_to.maximum(), 3)
            self.assertFalse(panel.sp_wait.isEnabled())
            panel.sp_from.setValue(2)
            panel.sp_to.setValue(1)                    # 前より前にはならない
            self.assertEqual((panel.sp_from.value(), panel.sp_to.value()), (1, 1))
            panel.sp_to.setValue(3)
            panel.sp_from.setValue(2)
            panel.btn_auto.click()
            self.assertTrue(panel.sp_wait.isEnabled())
            panel.sp_wait.setValue(99)                 # 幅の中へ
            self.assertEqual(panel.sp_wait.value(), 60)
            panel.sp_wait.setValue(5)
            panel.btn_start.click()
            self.assertEqual(started, [(self.pid, {'start': 1, 'end': 2, 'auto': True, 'wait': 5})])
            self.assertTrue(lib.cfg['player_playlist_auto'])
            self.assertEqual(lib.cfg['player_playlist_wait'], 5)
            # 次に開くと前の選び方のまま。外を押すと閉じる。
            panel = lib.open_play_panel()
            self.assertTrue(panel.auto)
            self.assertEqual(panel.sp_wait.value(), 5)
            panel.close_panel()
            self.assertFalse(panel.isVisible())
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_range(self):
        with tempfile.TemporaryDirectory() as d:
            host = self._host(d)
            # 3番目だけ(2番目は見つからない曲)。
            self.assertTrue(host.start_playlist(self.pid, start=2, end=2))
            _through_overview(host)
            self.assertEqual(host.intro.playlist, (1, 1))
            self.assertEqual(host.intro.title, 'b')
            host.end_playlist()
            _wait(700)
            # 見つからない曲だけの範囲は始めない。
            self.assertFalse(host.start_playlist(self.pid, start=1, end=1))
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_auto_counts_down_and_advances(self):
        with tempfile.TemporaryDirectory() as d:
            host = self._host(d)
            core = None
            now = [1000.0]
            host.intro.clock = lambda: now[0]
            self.assertTrue(host.start_playlist(self.pid, auto=True, wait=10))
            _through_overview(host)
            core = self.FakeCore.instances[-1]
            intro = host.intro
            self.assertEqual(intro.playlist, (1, 2))
            self.assertEqual(intro._pl_cards, {})                 # 自動ではコースを変えない
            self.assertAlmostEqual(intro.countdown_left(), 10.0)
            self.assertEqual(intro.btn_play.secs, 10)
            now[0] += 4.2
            _wait(120)
            self.assertEqual(intro.btn_play.secs, 6)
            self.assertIsNone(host._seq)
            now[0] += 6.0
            _wait(120)
            self.assertIsNotNone(host._seq)                       # 自分で「再生する」
            self.assertIsNone(intro.countdown_left())
            host._seq._t0 -= 2.0
            host._seq.tick()
            _wait(800)
            self.assertIs(host.body.currentWidget(), host.play_page)
            self.assertIn(('load', 'a.tja', 'Oni'), core.log)
            core.finished = True
            _wait(900)
            core.finished = False
            self.assertEqual(intro.playlist, (2, 2))
            self.assertAlmostEqual(intro.countdown_left(), 10.0)  # 次の曲もまた待つ
            # 「再生する」ならすぐ。
            intro.btn_play.click()
            self.assertIsNotNone(host._seq)
            self.assertIsNone(intro.countdown_left())
            host._seq._t0 -= 2.0
            host._seq.tick()
            _wait(800)
            core.finished = True
            _wait(900)
            self.assertIsNone(host.playlist_session())            # 最後 → プレイリストへ
            self.assertIs(host.library.pages.currentWidget(), host.library.page_playlist)
            # やめるで待ちも止まる。
            core.finished = False
            host.start_playlist(self.pid, auto=True, wait=3)
            _through_overview(host)
            self.assertIsNotNone(intro.countdown_left())
            intro.btn_quit.click()
            self.assertIsNone(intro.countdown_left())
            now[0] += 10
            _wait(150)
            self.assertIsNone(host._seq)
            _wait(700)
            self.assertIsNone(host.playlist_session())
            host.shutdown()
            host.deleteLater()


class OverviewTests(unittest.TestCase):
    """連続再生の最初のプレイリストの照会画面。"""

    setUp = HostSessionTests.setUp
    tearDown = HostSessionTests.tearDown
    _host = HostSessionTests._host

    @real_qt_test
    def test_overview_lists_range_and_waits_even_in_auto(self):
        from PySide6.QtCore import Qt
        with tempfile.TemporaryDirectory() as d:
            host = self._host(d)
            now = [0.0]
            host.intro.clock = lambda: now[0]
            self.assertTrue(host.start_playlist(self.pid, auto=True, wait=3))
            _wait(700)
            ov = host.playlist_overview
            self.assertIs(host.body.currentWidget(), ov)
            self.assertFalse(host.top_bar.isVisible())
            self.assertEqual(ov.lbl_title.text(), 'P')
            rows = [(r.num.text(), r.title.text(), r.missing,
                     r.mark.course if r.mark else None, r.star.text()) for r in ov.rows]
            self.assertEqual(rows, [('1', 'a', False, 'Oni', '★9'),
                                    ('2', 'gone', True, None, 'ファイルなし'),
                                    ('3', 'b', False, 'Hard', '★5')])
            self.assertEqual(ov.lbl_count.text(), '2曲')
            self.assertIsNone(ov.jacket.pixmap)                 # No Image
            # 自動でも進まない(時計を進めても、待っても)。
            now[0] += 100
            _wait(400)
            self.assertIs(host.body.currentWidget(), ov)
            self.assertIsNone(host.intro.countdown_left())
            # Enter で「再生する」(ドン) → 1曲目の曲名表示、そこから自動の数え。
            _key(ov, Qt.Key_Return)
            _wait(700)
            self.assertIn('don', self.sounds)
            self.assertIs(host.body.currentWidget(), host.intro)
            self.assertEqual(host.intro.playlist, (1, 2))
            self.assertIsNotNone(host.intro.countdown_left())
            host.end_playlist()
            _wait(700)
            # 範囲の分だけ・やめる(Esc)でプレイリストへ。
            host.start_playlist(self.pid, start=2, end=2)
            _wait(700)
            self.assertEqual([r.num.text() for r in ov.rows], ['3'])
            self.assertEqual(ov.lbl_count.text(), '1曲')
            _key(ov, Qt.Key_Escape)
            _wait(700)
            self.assertIsNone(host.playlist_session())
            self.assertIs(host.body.currentWidget(), host.library)
            self.assertIs(host.library.pages.currentWidget(), host.library.page_playlist)
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_long_list_scrolls(self):
        with tempfile.TemporaryDirectory() as d:
            host = self._host(d)
            st = host.library.playlist_store()
            for _ in range(30):
                st.add_item(self.pid, self.a, 'Oni')
            host.start_playlist(self.pid)
            _wait(700)
            ov = host.playlist_overview
            self.assertEqual(len(ov.rows), 33)
            self.assertGreater(ov.scroll.verticalScrollBar().maximum(), 0)
            self.assertLessEqual(ov.btn_play.geometry().bottom(), ov.height())
            host.end_playlist()
            host.shutdown()
            host.deleteLater()


class CardRowsTests(unittest.TestCase):
    @real_qt_test
    def test_five_courses_two_rows_readable(self):
        from neotja.player.intro import IntroPage
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a')
            page = IntroPage({'master_volume': 0.0})
            page.resize(1920, 1009)
            page.show()
            page.set_song(a, 'Oni', 9)
            five = [('Edit', 10), ('Oni', 9), ('Hard', 7), ('Normal', 5), ('Easy', 3)]
            page.set_playlist(1, 3, five)
            cards = [page._pl_cards[k] for k in ('Easy', 'Normal', 'Hard', 'Oni', 'Edit')]
            tops = sorted({c.geometry().top() for c in cards})
            self.assertEqual(len(tops), 2)                         # 2段
            self.assertEqual([c.geometry().top() for c in cards[:3]], [tops[0]] * 3)
            # ★の字が 20px 以上(1段に5枚だった前は約 8px)。
            self.assertGreaterEqual(cards[0].LEVEL_PX * cards[0].scale, 20.0)
            # 板は窓に収まり、ボタンはカードの下。
            panel = page._pl_panel
            self.assertGreaterEqual(panel.top(), 0)
            self.assertLessEqual(panel.bottom(), page.height())
            self.assertGreater(page.btn_play.geometry().top(), cards[-1].geometry().bottom())
            # 3つまでは1段。
            page.set_playlist(1, 3, five[:3])
            self.assertEqual(len({c.geometry().top() for c in page._pl_cards.values()}), 1)
            page.close()
            page.deleteLater()


if __name__ == '__main__':
    unittest.main(verbosity=2)
