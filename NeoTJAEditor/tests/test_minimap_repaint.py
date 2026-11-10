"""Check real Qt dirty regions against a complete minimap repaint (no settings)."""
import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from neotja import minimap_widget


class ObservedMinimap(minimap_widget.ChartMinimap):
    def __init__(self):
        super().__init__()
        self.backing = QImage(1000, self.height(), QImage.Format_ARGB32)
        self.backing.fill(QColor(self.EMPTY_COLOR))
        self.paint_count = 0

    def paintEvent(self, event):
        super().paintEvent(event)
        self.paint_count += 1
        # Mirror only the actual Qt dirty region into a persistent image.
        # QWidget.grab() would force a full repaint and hide the regression.
        def painter(_widget):
            result = QPainter(self.backing)
            result.setClipRegion(event.region())
            return result
        with patch.object(minimap_widget, 'QPainter', painter):
            super().paintEvent(event)


class MinimapRepaintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_view_fill_matches_complete_repaint(self):
        widget = ObservedMinimap()
        widget.resize(1000, widget.height())
        widget.set_chart([(15, '1'), (31, '2')], duration=100)
        widget.set_view(10, 30)
        widget.show()
        QTest.qWait(60)
        try:
            for view in ((12, 32), (10, 30), (11, 29), (10, 30)):
                widget.set_view(*view)
                QTest.qWait(30)
                partial = widget.backing.copy()
                widget.update()
                QTest.qWait(30)
                # Compare the bar itself; surrounding QWidget background is
                # intentionally not mirrored by our painter.
                rect = (0, widget.PAD_V, widget.width(), widget.BAR_H)
                self.assertEqual(partial.copy(*rect), widget.backing.copy(*rect),
                                 'Partial repaint differs for %r' % (view,))
            count = widget.paint_count
            widget.set_view(10.001, 30.001)
            QTest.qWait(30)
            self.assertEqual(widget.paint_count, count)
        finally:
            widget.close()


if __name__ == '__main__':
    unittest.main()
