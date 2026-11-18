"""Player の結果画面: 値は再生画面と同じ計算・王冠の決め方と絵・ボタンと Esc。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_results.py`
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-results-test-')
_CFG = Path(_TMP) / 'settings.json'
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import editor_session, open_test_chart, real_qt_test  # noqa: E402


def _wait(ms):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(10)
        time.sleep(0.002)


# 音符 11 個(1111 / 1111 / 1210)と連打 1 本。
BODY = '1111,\n1111,\n5000,\n0008,\n1210,'


class ValuesTests(unittest.TestCase):
    @real_qt_test
    def test_values_come_from_the_hud_computation(self):
        from neotja.gauge import GaugeModel
        from neotja.score import ScoreTimeline
        from neotja.tja_analyzer import TJACourseAnalyzer
        with editor_session() as (win, root):
            text = open_test_chart(win, root, BODY, bpm=120)
            win._force_update()
            _wait(300)
            gs = win.preview_dock.game_screen
            v = gs.result_values()
            # 同じ譜面を組み直して、既存の表(ScoreTimeline / GaugeModel)で引く。
            preview = TJACourseAnalyzer(win.config_data).build_preview_timeline(text, None, 'Oni')
            tl = ScoreTimeline(preview)
            gauge = GaugeModel(preview)
            n = len(preview['notes'])
            self.assertEqual(n, 11)
            self.assertEqual(v['note_count'], 11)
            self.assertEqual(v['score'], tl.max_score)        # 終わりのスコア = 表の最後
            self.assertGreater(v['score'], 0)
            self.assertEqual((v['good'], v['ok'], v['bad']), (11, 0, 0))
            self.assertEqual(v['max_combo'], 11)
            self.assertGreater(v['rolls'], 0)
            self.assertEqual(v['rolls'], len(tl._tick_times))
            self.assertEqual(v['score'], 11 * tl.scoring['per_note'] + 100 * v['rolls'])
            self.assertAlmostEqual(v['gauge_ratio'], gauge.ratio(11))
            self.assertEqual(v['cleared'], gauge.cleared(11))
            # 途中の時刻で引けば、HUD がその時刻に描く値と同じ。
            lane = win.preview_dock.chart_preview
            t_mid = preview['notes'][4][0] + 0.01
            mid = gs.result_values(at=t_mid)
            self.assertEqual(mid['good'], lane.combo_at(t_mid))
            self.assertEqual(mid['good'], 5)
            self.assertEqual(mid['score'], gs._score_timeline.at(t_mid))
            from neotja.player.results import crown_kind
            self.assertEqual(crown_kind(v), 'rainbow')        # オートは全良
            # 魂ゲージの絵は画面と同じ描き方(素材が無ければ None)。
            pm = gs.gauge_strip(v['gauge_ratio'])
            if gs._skin.get('gauge_base') is not None:
                self.assertIsNotNone(pm)
                self.assertFalse(pm.isNull())
            else:
                self.assertIsNone(pm)


class CrownTests(unittest.TestCase):
    def test_crown_rule(self):
        from neotja.player.results import crown_kind
        base = {'note_count': 100, 'cleared': True}
        self.assertEqual(crown_kind(dict(base, good=100, ok=0, bad=0)), 'rainbow')
        self.assertEqual(crown_kind(dict(base, good=90, ok=10, bad=0)), 'gold')
        self.assertEqual(crown_kind(dict(base, good=90, ok=5, bad=5)), 'silver')
        self.assertIsNone(crown_kind(dict(base, good=100, ok=0, bad=0, cleared=False)))
        self.assertIsNone(crown_kind({'note_count': 0, 'cleared': False}))
        self.assertIsNone(crown_kind({}))

    @real_qt_test
    def test_crown_cells_left_column_and_missing_sheet(self):
        from PySide6.QtGui import QColor, QImage
        from neotja.player import results
        with tempfile.TemporaryDirectory() as d:
            sheet = os.path.join(d, 'Crown.png')
            img = QImage(404, 276, QImage.Format_ARGB32)
            img.fill(QColor(0, 0, 0, 0))
            cols = {0: QColor(200, 200, 210), 1: QColor(240, 190, 40), 2: QColor(255, 0, 200)}
            for row, col in cols.items():
                for x in range(1, 100):
                    for y in range(row * 92 + 1, row * 92 + 91):
                        img.setPixelColor(x, y, col)
                # 右の列(使わない)は別の色。
                for x in range(102, 200):
                    img.setPixelColor(x, row * 92 + 40, QColor(0, 255, 0))
            img.save(sheet)
            for kind, row in (('silver', 0), ('gold', 1), ('rainbow', 2)):
                pm = results.crown_pixmap(kind, sheet)
                self.assertIsNotNone(pm)
                self.assertEqual((pm.width(), pm.height()), (101, 92))
                im = pm.toImage()
                self.assertEqual(im.pixelColor(50, 46).rgb(), cols[row].rgb())
                self.assertEqual(im.pixelColor(0, 0).alpha(), 0)       # 透明の余白ごと
            self.assertIsNone(results.crown_pixmap('silver', os.path.join(d, 'none.png')))
            self.assertIsNone(results.crown_pixmap(None, sheet))


# ----------------------------------------------------------------------
# PlayerHost: 譜面の終わり → 結果画面 → もう一度 / 選曲へ(偽の PlayerCore)
# ----------------------------------------------------------------------
def _chart(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'song.ogg').write_bytes(b'x')
    p = folder / (name + '.tja')
    p.write_text('TITLE:%s\nSUBTITLE:--副題\nBPM:120\nWAVE:song.ogg\nCOURSE:Oni\nLEVEL:9\n'
                 '#START\n1111,\n#END\n' % name, encoding='cp932')
    return str(p)


VALUES = {'score': 1004560, 'good': 640, 'ok': 0, 'bad': 0, 'rolls': 37, 'max_combo': 640,
          'combo': 640, 'note_count': 640, 'gauge_ratio': 1.0, 'cleared': True}


def _fake_core_class():
    from PySide6.QtWidgets import QWidget

    class FakeGpw(QWidget):
        def set_view_only(self, on, with_bottom=False):
            pass

        def set_controls_locked(self, on, key_cb=None):
            pass

        def fit_view_only(self, w, h):
            self.resize(640, 360)

    class FakeScreen(QWidget):
        def result_values(self):
            return dict(VALUES)

        def gauge_strip(self, ratio):
            return None

    class FakeDock:
        def __init__(self):
            self.game_preview_window = FakeGpw()
            self.game_screen = FakeScreen()
            self.chart_preview = type('C', (), {'reset_to_start': lambda s: None,
                                                'judge_sprite': lambda s, k: None})()

        def set_volume(self, v):
            pass

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
            self.log.append('demo')

        def stop_audio(self):
            self.log.append('stop')

        def shutdown(self):
            pass

        def predecode(self, path):
            self.log.append('predecode')

        def load(self, path, course_key=None, play_mode=None):
            self.log.append(('load', course_key))
            return True

        def use_normal_play_mode(self):
            pass

        def chart_ready(self):
            return True

        def play_chart(self, at=0.0):
            self.log.append(('play_chart', at))

        def pause_chart(self):
            self.log.append('pause_chart')

        def resume_chart(self):
            self.log.append('resume_chart')

        def drop_hold(self):
            pass

        def chart_finished(self):
            return self.finished
    return FakeCore


class HostResultsTests(unittest.TestCase):
    def setUp(self):
        from neotja.player.host import PlayerHost
        self.FakeCore = _fake_core_class()
        self._orig = PlayerHost.core_factory
        PlayerHost.core_factory = self.FakeCore

    def tearDown(self):
        from neotja.player.host import PlayerHost
        PlayerHost.core_factory = self._orig

    def _results_host(self, d):
        from neotja.player.host import PlayerHost
        a = _chart(Path(d) / 'a', 'a')
        host = PlayerHost({'master_volume': 0.0, 'preview_volume': 0.5}, lambda: None)
        host.resize(1600, 900)
        host.show()
        host.on_shown()
        host.library.choose(a)
        self.sounds = []
        host.sounds.play = lambda kind: self.sounds.append(kind)
        host.sounds.pi = lambda: self.sounds.append('pi')
        host.sounds.don = lambda: self.sounds.append('don')
        core = self.FakeCore.instances[-1]
        host._enter_play(a, 'Oni')
        _wait(700)
        done = []
        host.play_finished.connect(lambda p, c: done.append((p, c)))
        core.finished = True
        _wait(900)
        self.assertEqual(done, [(a, 'Oni')])
        self.assertIs(host.body.currentWidget(), host.results)
        self.assertFalse(host.top_bar.isVisible())
        core.log.clear()
        self.sounds.clear()
        return host, a, core

    @real_qt_test
    def test_results_show_values_and_crown(self):
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._results_host(d)
            r = host.results
            self.assertEqual(r.values['score'], VALUES['score'])
            self.assertEqual(r.crown, 'rainbow')
            self.assertEqual((r.title, r.subtitle, r.course, r.level), ('a', '副題', 'Oni', 9))
            self.assertTrue(r.btn_retry.hasFocus())
            # 王冠の素材が無いときは丸を描く(落ちない)。
            r.crown_sheet = os.path.join(d, 'missing.png')
            self.assertIsNone(r.crown_pixmap())
            img = r.grab().toImage()
            self.assertFalse(img.isNull())
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_first_paint_already_has_final_layout(self):
        """初めて結果画面を出したとき、最初に塗る時点でボタンが右下にある
        (前の大きさのまま1コマ出て、配置が一瞬崩れて見えない)。"""
        from neotja.player.results import ResultsPage
        seen = []
        orig = ResultsPage.paintEvent

        def spy(page, ev):
            seen.append((page.size(), page.btn_retry.geometry(), page.btn_back.geometry()))
            orig(page, ev)
        ResultsPage.paintEvent = spy
        try:
            with tempfile.TemporaryDirectory() as d:
                host, a, core = self._results_host(d)
                r = host.results
                self.assertTrue(seen)
                self.assertEqual(seen[0], (r.size(), r.btn_retry.geometry(), r.btn_back.geometry()))
                self.assertEqual(r.size(), host.body.contentsRect().size())
                host.shutdown()
                host.deleteLater()
        finally:
            ResultsPage.paintEvent = orig

    @real_qt_test
    def test_background_video_runs_on_results_and_is_released(self):
        from PySide6.QtCore import QObject
        from neotja.player import song_lists

        class Video(QObject):
            made = []

            def __init__(self, core, frame, clear, parent=None):
                super().__init__(parent)
                self.frame, self._clear_callback = frame, clear
                self.stopped, self.free, self.path = False, False, ''
                Video.made.append(self)

            def start(self, path, free=False):
                self.path, self.free = path, free

            def stop(self):
                self.stopped = True

            def set_targets(self, frame, clear):
                self.frame, self._clear_callback = frame, clear

            def free_run(self):
                self.free = True
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._results_host(d)
            movie = os.path.join(d, 'bg.mp4')
            Path(movie).write_bytes(b'fake')
            song_lists.set_chart_art(host.cfg, a, background=movie)
            host.video_factory = Video
            core.finished = False
            host._enter_play(a, 'Oni')
            _wait(700)
            self.assertIsNone(host._demo_video)              # 演奏中は流さない
            core.finished = True
            _wait(900)
            self.assertIs(host.body.currentWidget(), host.results)
            video = host._demo_video
            self.assertIsNotNone(video)
            self.assertTrue(video.free)                      # 無音・自分の時計でループ
            self.assertEqual(video.path, movie)
            self.assertEqual(video.frame, host.results.set_video_frame)
            self.assertIsNone(host.results._bg)              # 動画を静止画として読まない
            # 「もう一度」: 同じ動画を曲名表示へ引き継ぐ。
            host._on_results_retry()
            self.assertIs(host._demo_video, video)
            self.assertFalse(video.stopped)
            self.assertEqual(video.frame, host.intro.set_video_frame)
            host.stop_preview()
            self.assertTrue(video.stopped)
            # 結果画面から選曲へ: 止めて手放す。
            core.finished = False
            host._enter_play(a, 'Oni')
            _wait(700)
            core.finished = True
            _wait(900)
            video = host._demo_video
            host._on_results_library()
            self.assertTrue(video.stopped)
            self.assertIsNone(host._demo_video)
            # 背景を出さない設定なら作らない。
            host.cfg['player_show_bg'] = False
            core.finished = False
            host._enter_play(a, 'Oni')
            _wait(700)
            core.finished = True
            _wait(900)
            self.assertIsNone(host._demo_video)
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_esc_returns_to_library(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._results_host(d)
            lib_page = host.library.pages.currentWidget()
            QTest.keyClick(host.results.btn_retry, Qt.Key_Escape)
            _wait(700)
            self.assertIs(host.body.currentWidget(), host.library)
            self.assertIs(host.library.pages.currentWidget(), lib_page)
            self.assertIn('pi', self.sounds)
            self.assertIn('stop', core.log)
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_buttons_retry_and_back(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._results_host(d)
            r = host.results
            # 矢印で行き来(ピ)、Enter で押す。
            QTest.keyClick(r.btn_retry, Qt.Key_Down)
            self.assertTrue(r.btn_back.hasFocus())
            self.assertEqual(self.sounds, ['pi'])
            QTest.keyClick(r.btn_back, Qt.Key_Up)
            self.assertTrue(r.btn_retry.hasFocus())
            self.sounds.clear()
            core.finished = False                               # 次の演奏はまだ終わっていない
            QTest.keyClick(r.btn_retry, Qt.Key_Return)          # もう一度プレイする
            self.assertEqual(self.sounds, ['don'])
            self.assertIs(host.body.currentWidget(), host.intro)
            self.assertIsNotNone(host._seq)
            self.assertIn('predecode', core.log)
            seq = host._seq
            seq._t0 -= 6.0
            seq.tick()
            seq.tick()
            _wait(700)
            self.assertIn(('load', 'Oni'), core.log)
            self.assertIs(host.body.currentWidget(), host.play_page)
            # もう一度終わって、今度はマウスで「選曲画面に戻る」。
            core.finished = True
            _wait(900)
            self.assertIs(host.body.currentWidget(), host.results)
            QTest.mouseClick(r.btn_back, Qt.LeftButton)
            _wait(700)
            self.assertIs(host.body.currentWidget(), host.library)
            host.shutdown()
            host.deleteLater()


if __name__ == '__main__':
    unittest.main()
