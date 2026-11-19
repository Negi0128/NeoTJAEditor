"""プレイリスト(第5段): 範囲の曲を録画して、まとめて1本・曲ごと・両方を作る。

小さな譜面・短い音・低い fps で回す。窓・エクスプローラーは出さない。
実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_playlist_record.py`
"""
import json
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-plrec-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from neotja import user_dirs  # noqa: E402

user_dirs.base_override = str(Path(_TMP) / 'user')

from gui_support import real_qt_test  # noqa: E402

#: 速く回すための画質(720p・10fps・いちばん速い x264)。
FAST = {'fps': 10, 'canvas': '720p', 'supersample': 1.0, 'preset': 'ultrafast'}


def _wait_until(cond, sec):
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication
    end = time.monotonic() + sec
    while time.monotonic() < end and not cond():
        QApplication.processEvents(QEventLoop.AllEvents, 20)
        time.sleep(0.002)
    return cond()


def _chart(folder, name, courses=(('Oni', 8), ('Hard', 5))):
    folder.mkdir(parents=True, exist_ok=True)
    with wave.open(str(folder / 'song.wav'), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b'\x00\x00' * 22050)          # 1秒
    lines = ['TITLE:%s' % name, 'BPM:240', 'WAVE:song.wav', 'OFFSET:0', '']
    for c, lv in courses:
        lines += ['COURSE:%s' % c, 'LEVEL:%d' % lv, '#START', '1010,', '#END', '']
    p = folder / (name + '.tja')
    p.write_text('\n'.join(lines), encoding='cp932')
    return str(p)


def _seconds(path):
    from neotja import recorder
    return recorder.probe_song_seconds(path)


class RecorderTests(unittest.TestCase):
    def _run(self, items, mode, out, title='テスト'):
        from neotja.player.playlist_record import PlaylistRecorder
        rec = PlaylistRecorder({'master_volume': 0.0}, items, out, mode=mode,
                               quality=FAST, title=title, card_sec=1.0)
        got, prog = [], []
        rec.finished.connect(got.append)
        rec.progress.connect(lambda i, n, f: prog.append((i, n, f)))
        rec.start()
        self.assertTrue(_wait_until(lambda: got, 240), 'timeout')
        return got[0], prog

    @real_qt_test
    def test_both_combined_and_each(self):
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'あ')
            b = _chart(Path(d) / 'b', 'い', (('Normal', 3),))
            out = str(Path(d) / 'out')
            res, prog = self._run([{'path': a, 'course': 'Hard'},
                                   {'path': str(Path(d) / 'gone.tja'), 'course': 'Oni'},
                                   {'path': b, 'course': 'Oni'}], 'both', out)
            self.assertFalse(res['cancelled'])
            self.assertEqual([os.path.basename(p) for p in res['songs']],
                             ['01_あ_むずかしい.mp4', '03_い_ふつう.mp4'])
            self.assertEqual(len(res['failed']), 1)
            self.assertEqual(os.path.basename(res['combined']), 'テスト.mp4')
            for p in res['songs'] + [res['combined']]:
                self.assertTrue(os.path.isfile(p), p)
            each = sum(_seconds(p) for p in res['songs'])
            total = _seconds(res['combined'])
            # 曲名表示 1秒 × 2 をはさんでつながっている。
            self.assertAlmostEqual(total, each + 2.0, delta=0.6)
            # 最後まで読み通せる(つなぎ目で壊れていない)。
            import subprocess
            from neotja import recorder
            r = subprocess.run([recorder._ffmpeg_exe(), '-v', 'error', '-i', res['combined'],
                                '-f', 'null', '-'], capture_output=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stderr.strip(), b'')
            self.assertEqual(sorted(os.listdir(out)),
                             ['01_あ_むずかしい.mp4', '03_い_ふつう.mp4', 'テスト.mp4'])
            self.assertEqual(prog[-1][2], 1.0)

    @real_qt_test
    def test_combined_only_leaves_one_file(self):
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a')
            out = str(Path(d) / 'out')
            res, _ = self._run([{'path': a, 'course': 'Oni'}, {'path': a, 'course': 'Hard'}],
                               'combined', out, title='P:1')
            self.assertEqual(res['songs'], [])
            self.assertEqual(os.listdir(out), ['P_1.mp4'])
            self.assertGreater(_seconds(res['combined']), 2.0)

    @real_qt_test
    def test_each_only_and_cancel(self):
        from neotja.player.playlist_record import PlaylistRecorder
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a')
            out = str(Path(d) / 'out')
            res, _ = self._run([{'path': a, 'course': 'Oni'}], 'each', out)
            self.assertEqual(res['combined'], '')
            self.assertEqual(os.listdir(out), ['01_a_おに.mp4'])
            # 中止: 書きかけは残さず、まとめても作らない。
            out2 = str(Path(d) / 'out2')
            rec = PlaylistRecorder({'master_volume': 0.0}, [{'path': a, 'course': 'Oni'}] * 3,
                                   out2, mode='both', quality=FAST, title='c', card_sec=1.0)
            got = []
            rec.finished.connect(got.append)
            rec.start()
            self.assertTrue(_wait_until(lambda: rec._rec is not None and rec._rec.frame > 3, 120))
            rec.cancel()
            self.assertTrue(got and got[0]['cancelled'])
            self.assertEqual(os.listdir(out2), [])
            self.assertIsNone(rec._work)

    @real_qt_test
    def test_panel_and_bar(self):
        import shutil
        from neotja.player.library_view import LibraryView
        shutil.rmtree(user_dirs.base_override, ignore_errors=True)
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a')
            lib = LibraryView({'master_volume': 0.0}, lambda: None)
            lib.resize(1400, 900)
            lib.show()
            st = lib.playlist_store()
            pl = st.create('夏/曲')
            st.add_item(pl['id'], a, 'Oni')
            st.add_item(pl['id'], a, 'Hard')
            lib.open_playlist(pl['id'])
            panel = lib.open_play_panel()
            self.assertEqual(panel.rec_mode, 'both')            # 既定は両方
            self.assertEqual([b.text() for b in panel.rec_buttons.values()],
                             ['まとめて', '曲ごと', '両方'])
            panel.rec_buttons['each'].click()
            panel.sp_to.setValue(1)
            # 画質だけ試験用に速くする(板の番号は覚えるが、中身は差し替え)。
            started = []
            orig = lib.start_playlist_record

            def fast(pid, opts):
                started.append(opts)
                return orig(pid, opts, quality=FAST, card_sec=1.0)
            panel.record_requested.disconnect()
            panel.record_requested.connect(lambda o: fast(pl['id'], o))
            panel.btn_record.click()
            self.assertEqual(started, [{'start': 0, 'end': 0, 'mode': 'each', 'quality': 1}])
            self.assertTrue(lib.record_bar.isVisible())
            self.assertTrue(lib.playlist_recording())
            self.assertIsNone(lib.start_playlist_record(pl['id'], {}))   # 2本目は始めない
            self.assertTrue(_wait_until(lambda: not lib.playlist_recording(), 120))
            self.assertEqual(lib.cfg['player_playlist_rec_mode'], 'each')
            out = Path(user_dirs.base_override) / '録画' / '夏_曲'
            self.assertEqual(os.listdir(out), ['01_a_おに.mp4'])
            self.assertEqual(lib.record_bar.count.text(), '完了')
            self.assertTrue(lib.record_bar.btn_open.isVisibleTo(lib))
            opened = []
            lib.open_folder = opened.append
            lib.record_bar.btn_open.click()
            self.assertEqual([Path(p) for p in opened], [out])
            lib.record_bar.btn_close.click()
            self.assertFalse(lib.record_bar.isVisible())
            # 次に開くと前の選び方。
            panel = lib.open_play_panel()
            self.assertEqual(panel.rec_mode, 'each')
            panel.close_panel()
            lib.shutdown()
            lib.close()

    def test_out_dir_under_user_data(self):
        from neotja.player.playlist_record import playlist_out_dir
        p = playlist_out_dir('夏:の曲?')
        self.assertEqual(Path(p), Path(user_dirs.base_override) / '録画' / '夏_の曲_')


if __name__ == '__main__':
    unittest.main(verbosity=2)
