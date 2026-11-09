"""選択中の描画は全譜面を再変換せず、譜面変更時は住所を更新する。"""
import unittest
from unittest.mock import patch
from fractions import Fraction
from gui_support import editor_session, open_test_chart, real_qt_test


class SelectionCacheTests(unittest.TestCase):
    @real_qt_test
    def test_selection_and_invalidation(self):
        from PySide6.QtGui import QImage, QPainter
        with editor_session() as (win, root):
            open_test_chart(win, root,
                '#GOGOSTART\n#BARLINEOFF\n5080,\n#GOGOEND\n#BARLINEON\n1234,')
            ce = win.preview_dock.chart_edit
            ce.resize(1000, 400)
            objects = ce._objects()
            self.assertEqual(objects, ce._build_objects())
            keys = {obj[0] for obj in objects}
            ce.set_selection(keys)
            pairs = list(ce._selected_spans())
            self.assertEqual({item[2] for item in pairs}, {'note', 'gogo', 'barline'})
            self.assertEqual(set(ce._selected_objects(keys)), set(objects))
            image = QImage(1000, 400, QImage.Format_ARGB32)
            image.fill(0)
            painter = QPainter(image)
            try:
                with patch.object(ce, '_pos_of_time', side_effect=AssertionError('per-frame conversion')):
                    for _ in range(3):
                        ce._draw_selection(painter, 60)
                    done = ce._draw_span_selection(painter, ce._row_rects())
                    self.assertEqual(done, {k for pair in pairs for k in pair[:2]})
                    ce.set_selection({('note', Fraction(1))})
                    self.assertEqual(list(ce._selected_spans()), [])
                    self.assertEqual(len(list(ce._selected_objects(ce._sel))), 1)
            finally:
                painter.end()
            ce._apply_offset_local(ce.offset + 0.25)
            shifted = ce._objects()
            self.assertIsNot(shifted, objects)
            self.assertEqual(shifted, ce._build_objects())
            self.assertEqual({obj[0] for obj in shifted}, keys)
            self.assertAlmostEqual(shifted[0][2], objects[0][2] - 0.25)
            open_test_chart(win, root, '2222,')
            self.assertEqual(ce._objects(), ce._build_objects())
            self.assertEqual(len(ce._objects()), 4)


if __name__ == '__main__':
    unittest.main()
