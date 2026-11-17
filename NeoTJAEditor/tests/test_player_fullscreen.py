"""Player F11は親ウィンドウを切替え、EscやEditorには干渉しない。"""
import tempfile
import unittest
from pathlib import Path
from neotja import settings as S
_tmp=tempfile.TemporaryDirectory()
_p=Path(_tmp.name)/'settings.json'
_p.write_text('{"master_volume":0.0}',encoding='utf-8')
S.settings_path=S._primary_settings_path=S._fallback_settings_path=lambda:_p
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QLineEdit
from neotja.main_window import MainWindow

class Window(QMainWindow):
    _toggle_player_fullscreen=MainWindow._toggle_player_fullscreen
    _restore_player_fullscreen=MainWindow._restore_player_fullscreen
    player=True
    def is_player_visible(self):return self.player

class FullscreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_restore_normal_and_maximized(self):
        w=Window();w.resize(900,600);w.show();self.app.processEvents()
        try:
            original=w.size()
            w._toggle_player_fullscreen();self.app.processEvents()
            self.assertTrue(w.isFullScreen())
            w._toggle_player_fullscreen();self.app.processEvents()
            self.assertFalse(w.isFullScreen());self.assertEqual(w.size(),original)
            w.showMaximized();self.app.processEvents()
            w._toggle_player_fullscreen();self.assertTrue(w.isFullScreen())
            w._restore_player_fullscreen();self.app.processEvents()
            self.assertTrue(w.isMaximized());self.assertFalse(w.isFullScreen())
            w.player=False;w._toggle_player_fullscreen()
            self.assertFalse(w.isFullScreen())
        finally:w.close();w.deleteLater()
    def test_key_filter_claims_f11_and_ignores_repeat(self):
        # MainWindow.eventFilterを実際のMainWindow型で、重い初期化なしに検証。
        w=MainWindow.__new__(MainWindow);QMainWindow.__init__(w)
        w.is_player_visible=lambda:True
        calls=[];w._toggle_player_fullscreen=lambda:calls.append(1)
        child=QLineEdit(w)
        for kind in (QEvent.ShortcutOverride,QEvent.KeyPress,QEvent.KeyRelease):
            self.assertTrue(w.eventFilter(child,QKeyEvent(kind,Qt.Key_F11,Qt.NoModifier)))
        self.assertEqual(calls,[1])
        w.eventFilter(child,QKeyEvent(QEvent.KeyPress,Qt.Key_F11,Qt.NoModifier,'',True))
        self.assertEqual(calls,[1])
        # closeEventは重いMainWindow初期化を前提とするため破棄予約だけ行う。
        w.deleteLater()

if __name__=='__main__':unittest.main()
