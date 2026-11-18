"""native GPUメニューの停止画像は詳細ペインを含み、リサイズに追従する。"""
import unittest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage,QColor
from PySide6.QtWidgets import QApplication,QWidget,QLabel,QVBoxLayout
from PySide6.QtTest import QTest
from neotja.player.play_page import PlayPage

class BackdropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_detail_is_captured_and_resize_updates_geometry(self):
        page=PlayPage();page.resize(1000,800)
        g=QWidget(page);layout=QVBoxLayout(g);layout.setContentsMargins(0,0,0,0);layout.setSpacing(0)
        g.scaled_host=QLabel();g.scaled_host.setFixedHeight(200)
        bottom=QLabel('TIMELINE');bottom.setStyleSheet('background:#224466')
        layout.addWidget(g.scaled_host);layout.addWidget(bottom)
        g.fit_view_only=lambda w,h:g.resize(w-20,h-60)
        page.gpw=g;page.native_gl=True;page.show();g.show();page._fit();QTest.qWait(50)
        frame=QImage(100,100,QImage.Format_RGB32);frame.fill(QColor('#cc1122'))
        page.show_menu(frame)
        try:
            image=page.menu.backdrop
            self.assertEqual(image.pixelColor(10,image.height()-10).name(),'#224466')
            self.assertEqual(image.pixelColor(10,10).name(),'#cc1122')
            page.resize(800,650);QTest.qWait(100)
            self.assertEqual(page.menu.backdrop_rect,QRectF(g.geometry()))
            self.assertEqual(page.menu.backdrop.size(),g.size())
        finally:page.hide_menu();page.close();page.deleteLater()

if __name__=='__main__':unittest.main()
