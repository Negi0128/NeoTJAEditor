# Alt+Space で窓のシステムメニューが開かない(neotja.win_keys)。
# 利用者の報告 2026-11-16。止めるのは Space の WM_SYSKEYDOWN / WM_SYSCHAR /
# SC_KEYMENU だけで、WM_SYSKEYUP やほかのキーは通す。
import ctypes
import sys
import unittest

from neotja import win_keys as W


class ShouldSwallowTests(unittest.TestCase):
    def test_alt_space_messages(self):
        self.assertTrue(W.should_swallow(0x0104, 0x20, 0x20390001))
        self.assertTrue(W.should_swallow(0x0106, 0x20, 0x20390001))
        self.assertTrue(W.should_swallow(0x0112, 0xF100, 0x20))
        # SC_KEYMENU の下位4ビットは Windows が使うことがある
        self.assertTrue(W.should_swallow(0x0112, 0xF105, 0x20))

    def test_others_pass(self):
        self.assertFalse(W.should_swallow(0x0105, 0x20, 0))        # SYSKEYUP
        self.assertFalse(W.should_swallow(0x0104, 0x73, 0))        # Alt+F4
        self.assertFalse(W.should_swallow(0x0104, 0x12, 0))        # Alt 単独
        self.assertFalse(W.should_swallow(0x0112, 0xF100, 0))      # F10/Alt
        self.assertFalse(W.should_swallow(0x0112, 0xF060, 0x20))   # SC_CLOSE
        self.assertFalse(W.should_swallow(0x0100, 0x20, 0))        # 素の Space


@unittest.skipUnless(sys.platform == "win32", "Windows だけ")
class NativeFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.filt = W._make_filter_class()()

    def _feed(self, msg_id, wparam, lparam, event_type=b"windows_generic_MSG"):
        import ctypes.wintypes
        msg = ctypes.wintypes.MSG()
        msg.message = msg_id
        msg.wParam = wparam
        msg.lParam = lparam
        return self.filt.nativeEventFilter(event_type, ctypes.addressof(msg))

    def test_filter_reads_msg(self):
        self.assertEqual(self._feed(0x0104, 0x20, 0x20390001), (True, 0))
        self.assertEqual(self._feed(0x0106, 0x20, 0x20390001), (True, 0))
        self.assertEqual(self._feed(0x0112, 0xF100, 0x20), (True, 0))
        self.assertEqual(self._feed(0x0105, 0x20, 0), (False, 0))
        self.assertEqual(self._feed(0x0104, 0x73, 0), (False, 0))

    def test_other_event_type_passes(self):
        self.assertEqual(self._feed(0x0104, 0x20, 0, b"xcb_generic_event_t"),
                         (False, 0))

    def test_install_keeps_reference(self):
        filt = W.install(self.app)
        self.assertIsNotNone(filt)
        self.assertIs(W.install(self.app), filt)
        self.assertIs(W._installed, filt)


if __name__ == "__main__":
    unittest.main()
