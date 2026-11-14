"""解析結果を貼ったときの色の塗り直しで、同じ本文の解析をもう一度予約しない。"""
import time
import unittest

from PySide6.QtWidgets import QApplication

from gui_support import editor_session, open_test_chart, real_qt_test


def _spin_until(cond, timeout=10.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


class HighlightNoReanalysisTests(unittest.TestCase):
    def _edit(self, win, m=1):
        res = win._apply_chart_op({"kind": "key", "a": (m, 0), "grid": 16, "char": "1"})
        self.assertIsNotNone(res)

    @real_qt_test
    def test_sync_update_does_not_rearm_timer(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, '1000,\n0000,\n0000,')
            self._edit(win)
            self.assertTrue(win._heavy_timer.isActive())
            win._force_update()
            self.assertFalse(win._heavy_timer.isActive())

    @real_qt_test
    def test_async_result_does_not_rearm_timer(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, '1000,\n0000,\n0000,')
            calls = []
            orig = win._request_analysis

            def counted(kind):
                calls.append(kind)
                return orig(kind)
            win._request_analysis = counted
            self._edit(win)
            # 600ms 静まって解析 → 貼る、まで待つ。
            self.assertTrue(_spin_until(
                lambda: calls and not win._analysis_inflight
                and not win._heavy_timer.isActive()))
            _spin_until(lambda: False, 1.0)     # 貼ったあと 1 秒ようすを見る
            self.assertEqual(calls, ["full"])
            self.assertFalse(win._heavy_timer.isActive())

    @real_qt_test
    def test_real_text_change_still_schedules(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, '1000,\n0000,\n0000,')
            win._force_update()
            win.editor.insertPlainText("2")
            self.assertTrue(win._heavy_timer.isActive())


if __name__ == "__main__":
    unittest.main()
