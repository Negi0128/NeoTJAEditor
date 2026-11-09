"""静的なグリッド・帯の再描画とドラッグ中の追従を確認する。"""
import unittest
from unittest.mock import patch
from gui_support import editor_session, open_test_chart, real_qt_test


class StaticTrackTests(unittest.TestCase):
    @real_qt_test
    def test_cache_matches_direct_drawing_and_invalidates(self):
        from PySide6.QtGui import QImage, QPainter
        with editor_session() as (win, root):
            open_test_chart(win, root,
                '#GOGOSTART\n#BARLINEOFF\n1234,\n#GOGOEND\n#BARLINEON\n5678,')
            ce = win.preview_dock.chart_edit
            ce.resize(1000, 400)
            ce.set_follow_window(6)
            ce.view_start = 0
            ce._show_legend = False
            def render():
                image = QImage(1000, 400, QImage.Format_ARGB32)
                image.fill(0)
                painter = QPainter(image)
                try:
                    ce._paint_rows(painter)
                finally:
                    painter.end()
                return image
            with patch.object(ce, '_draw_edit_grid', wraps=ce._draw_edit_grid) as grid, \
                 patch.object(ce, '_draw_region_tracks', wraps=ce._draw_region_tracks) as regions:
                cached = render()
                self.assertEqual(grid.call_count, 1)
                self.assertEqual(regions.call_count, 1)
                self.assertEqual(render(), cached)
                self.assertEqual(grid.call_count, 1)
                self.assertEqual(regions.call_count, 1)
                with patch.object(ce, '_static_strip', return_value=None):
                    direct = render()
                # 定規（境界線の1pxを含む）は帯の右端の小節まで描くため、直接描画とは文字の範囲が異なる。
                # 今回保存するグリッド・範囲命令を含む全行を画素単位で比較する。
                self.assertEqual(cached.copy(0, ce.RULER_H + 1, 1000, 399-ce.RULER_H),
                                 direct.copy(0, ce.RULER_H + 1, 1000, 399-ce.RULER_H))
                ce.set_grid(24)
                ce.view_start = 0
                render()
                self.assertEqual(grid.call_count, 3)
                ce._note_drag = {'dx': 0, 'x0': 0}
                with patch.object(ce, '_drag_dx_for', return_value=12), \
                     patch.object(ce, '_draw_note_drag'):
                    render()
                    before = regions.call_count
                    render()
                    self.assertEqual(regions.call_count, before + 1)
                ce._note_drag = None
                render()
                before = regions.call_count
                render()
                self.assertEqual(regions.call_count, before)


if __name__ == '__main__':
    unittest.main()
