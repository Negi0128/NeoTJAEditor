"""解析を貼るとき、前と同じ中身の差し替えを省いても画面の中身は同じになるか。"""
import random
import time
import unittest

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from gui_support import editor_session, open_test_chart, real_qt_test


def _old_parse_preview_headers(content):
    """速くする前の parse_preview_headers(全行を回す)。"""
    title = subtitle = wave = ""
    bpm = None
    offset = 0.0
    for l in content.split("\n"):
        if l.startswith("TITLE:"):
            title = l[6:].strip()
        elif l.startswith("SUBTITLE:"):
            subtitle = l[9:].strip()
            if subtitle.startswith("--"):
                subtitle = subtitle[2:].strip()
        elif l.startswith("WAVE:"):
            wave = l[5:].strip()
        elif l.startswith("BPM:"):
            try:
                bpm = float(l[4:].strip())
            except ValueError:
                pass
        elif l.startswith("OFFSET:"):
            try:
                offset = float(l[7:].strip())
            except ValueError:
                pass
    return {"title": title, "subtitle": subtitle, "wave": wave, "bpm": bpm, "offset": offset}


def _spin_until(cond, timeout=10.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


class HeaderParseTests(unittest.TestCase):
    def test_same_as_line_loop(self):
        from neotja.preview_dock import parse_preview_headers
        rnd = random.Random(7)
        parts = ["TITLE:曲", "SUBTITLE:--副", "SUBTITLE:x", "WAVE:a.ogg", "BPM:150",
                 "BPM:abc", "OFFSET:-1.5", "OFFSET:", " BPM:200", "1000,", "#START",
                 "#END", "TITLE: 二つ目 \r", "BPM:120\r", "", "// TITLE:x"]
        for _ in range(500):
            lines = [rnd.choice(parts) for _ in range(rnd.randrange(0, 12))]
            text = rnd.choice(("\n", "\r\n")).join(lines)
            self.assertEqual(parse_preview_headers(text), _old_parse_preview_headers(text), text)


class PasteSkipTests(unittest.TestCase):
    @real_qt_test
    def test_same_data_does_not_rebake_strip(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, '1000,\n0000,\n0000,')
            win._force_update()
            ce = win.preview_dock.chart_edit
            rev = ce._strip_rev
            win._force_update()             # 同じ本文をもう一度貼る
            self.assertEqual(ce._strip_rev, rev)
            # 音符だけ変えても、帯(小節線・目盛り)は焼き直さない
            win._apply_chart_op({"kind": "key", "a": (1, 0), "grid": 16, "char": "1"})
            win._force_update()
            self.assertEqual(ce._strip_rev, rev)
            self.assertEqual(len(ce._note_audio), 2)
            # 小節が増えれば焼き直す
            text = win.editor.toPlainText().replace("#END", "0000,\n#END")
            win.editor.setPlainText(text)
            win._force_update()
            self.assertNotEqual(ce._strip_rev, rev)

    @real_qt_test
    def test_hidden_note_comes_back(self):
        """消したつもりの見た目(_hide_note_at)のあと、同じ結果が届いても戻る。"""
        with editor_session() as (win, root):
            open_test_chart(win, root, '1000,\n0000,\n0000,')
            win._force_update()
            ce = win.preview_dock.chart_edit
            self.assertEqual(len(ce._note_audio), 1)
            ce._hide_note_at(0, 0, 4)
            self.assertFalse(ce._note_audio)
            win._force_update()             # 本文は変わっていない
            self.assertEqual(len(ce._note_audio), 1)

    @real_qt_test
    def test_lyrics_change_only(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, '#LYRIC あ\n1000,\n0000,\n0000,')
            win._force_update()
            ce = win.preview_dock.chart_edit
            n_cmd = len(ce._cmd_raw or [])
            text = win.editor.toPlainText().replace("#LYRIC あ", "#LYRIC い")
            win.editor.setPlainText(text)
            win._force_update()
            self.assertEqual(len(ce._cmd_raw or []), n_cmd)
            self.assertIn("い", [c[1] for c in ce._cmd_raw])

    @real_qt_test
    def test_note_timing_follows_async_bpm_edit(self):
        """本文で BPM を変えたら、解析スレッドの結果を貼った時点で作譜の
        位置変換も新しい BPM になる(以前は同期の道でしか作り直さなかった)。"""
        with editor_session() as (win, root):
            open_test_chart(win, root, '1000,\n1000,\n0000,', bpm=120)
            win._force_update()
            ce = win.preview_dock.chart_edit
            t1 = float(ce.note_timing.time_at(1))
            self.assertAlmostEqual(t1, 2.0)
            same = ce.note_timing
            # 音符だけの編集(解析スレッド経由)では作り直さない。1002 は詰め
            # られないので、小節の文字数も変わらない。
            win._apply_chart_op({"kind": "key", "a": (1, 3), "grid": 4, "char": "2"})
            self.assertIn("1002,", win.editor.toPlainText())
            self.assertTrue(_spin_until(lambda: not win._heavy_timer.isActive()
                                        and not win._analysis_inflight
                                        and win._pending_preview is None))
            _spin_until(lambda: False, 0.5)
            self.assertIs(ce.note_timing, same)
            # BPM を本文で変える → 解析スレッドの結果で作り直される
            tc = win.editor.textCursor()
            doc = win.editor.document()
            blk = doc.findBlockByNumber(1)          # BPM:120 の行
            self.assertTrue(blk.text().startswith("BPM:"))
            tc.setPosition(blk.position())
            tc.setPosition(blk.position() + len(blk.text()), QTextCursor.KeepAnchor)
            tc.insertText("BPM:240")
            self.assertTrue(_spin_until(lambda: not win._heavy_timer.isActive()
                                        and not win._analysis_inflight
                                        and win._pending_preview is None))
            _spin_until(lambda: False, 0.5)
            self.assertAlmostEqual(float(ce.note_timing.time_at(1)), 1.0)


if __name__ == "__main__":
    unittest.main()
