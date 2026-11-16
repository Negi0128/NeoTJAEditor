# コース記号は「見えている所」を おに にそろえる(game_screen.course_sym_pos)。
# 利用者の報告 2026-11-16: 松竹梅だけ右上へずれて見える。おに は今の位置のまま。
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _png(path, w, h, box):
    from PySide6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0)
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            img.setPixelColor(x, y, QColor(255, 0, 0, 255))
    # 縁のごくうすいにじみは数えない
    img.setPixelColor(0, 0, QColor(255, 0, 0, 2))
    assert img.save(path)


class CourseSymPosTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.dir = tempfile.mkdtemp(prefix="neotja_test_")
        _png(os.path.join(cls.dir, "Oni.png"), 71, 63, (7, 2, 64, 62))
        _png(os.path.join(cls.dir, "Easy.png"), 89, 60, (14, 1, 80, 58))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_visible_box(self):
        from neotja import game_screen as G
        self.assertEqual(G._visible_box(os.path.join(self.dir, "Oni.png")),
                         (7, 2, 64, 62))

    def test_oni_stays(self):
        from neotja import game_screen as G
        self.assertEqual(G.course_sym_pos(os.path.join(self.dir, "Oni.png")),
                         G.COURSE_SYM_POS)

    def test_easy_matches_oni_centre_and_bottom(self):
        from neotja import game_screen as G
        x, y = G.course_sym_pos(os.path.join(self.dir, "Easy.png"))
        ox, oy = G.COURSE_SYM_POS
        self.assertAlmostEqual(x + (14 + 80) / 2, ox + (7 + 64) / 2, delta=0.5)
        self.assertEqual(y + 58, oy + 62)

    def test_no_reference_falls_back(self):
        from neotja import game_screen as G
        d = tempfile.mkdtemp(prefix="neotja_test_")
        self.addCleanup(shutil.rmtree, d, True)
        _png(os.path.join(d, "Easy.png"), 89, 60, (14, 1, 80, 58))
        self.assertEqual(G.course_sym_pos(os.path.join(d, "Easy.png")),
                         G.COURSE_SYM_POS)
        self.assertEqual(G.course_sym_pos(""), G.COURSE_SYM_POS)


if __name__ == "__main__":
    unittest.main()
