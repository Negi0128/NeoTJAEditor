"""難易度カードの見出し: 押すとコース切り替え、右クリックで移動 / 畳む。"""
import time
import unittest
from unittest.mock import patch

from gui_support import editor_session, open_test_chart, real_qt_test

TWO = ('TITLE:動作確認\nBPM:120\nOFFSET:0\nWAVE:test.wav\n'
       'COURSE:Oni\nLEVEL:9\n#START\n1111,\n1111,\n#END\n\n'
       'COURSE:Hard\nLEVEL:6\n#START\n1000,\n1000,\n#END\n')


def _wait(cond, ms=4000):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000
    while not cond() and time.monotonic() < end:
        QTest.qWait(20)
    return cond()


class CourseCardHeaderTests(unittest.TestCase):
    @real_qt_test
    def test_click_cycles_course_and_menu_actions(self):
        with editor_session() as (win, root):
            win.show()
            open_test_chart(win, root)          # 音源(test.wav)を作る
            path = root / 'two.tja'
            path.write_text(TWO, encoding='cp932')
            win._open_path(str(path))
            win._force_update()
            pd = win.preview_dock
            lane = pd.chart_preview
            cards = win._course_cards
            self.assertEqual(set(cards), {'Oni', 'Hard'})

            def shown():
                vis = [k for k, c in cards.items() if c.isVisible()]
                return vis[0] if len(vis) == 1 else None

            first = lane._course_key
            self.assertEqual(shown(), first)
            cards[first].header.click()
            self.assertTrue(_wait(lambda: lane._course_key != first))
            second = lane._course_key
            self.assertTrue(_wait(lambda: shown() == second))
            cards[second].header.click()
            self.assertTrue(_wait(lambda: lane._course_key == first))
            self.assertTrue(_wait(lambda: shown() == first))

            # 右クリックのメニュー。
            card = cards[first]
            menu = card.header_menu()
            acts = {a.text(): a for a in menu.actions()}
            self.assertEqual(set(acts), {"本文のこのコースへ移動", "畳む／開く"})
            with patch.object(win, 'jump_to_course') as jump:
                card.jumpRequested.disconnect()
                card.jumpRequested.connect(jump)
                acts["本文のこのコースへ移動"].trigger()
                jump.assert_called_once_with(first)
            self.assertTrue(card.body_visible)
            acts["畳む／開く"].trigger()
            self.assertFalse(card.body_visible)
            acts["畳む／開く"].trigger()
            self.assertTrue(card.body_visible)
            menu.deleteLater()

            # 同時再生では、ボタンと同じく並べる難易度を選ぶ窓を開く。
            with patch.object(pd.bottom_stack, 'currentIndex', return_value=pd.MODE_MULTI), \
                    patch.object(pd, 'open_multi_picker') as picker:
                card.header.click()
                picker.assert_called_once_with()

    @real_qt_test
    def test_jump_to_course_moves_editor_cursor(self):
        with editor_session() as (win, root):
            open_test_chart(win, root)
            path = root / 'two.tja'
            path.write_text(TWO, encoding='cp932')
            win._open_path(str(path))
            win._force_update()
            card = win._course_cards['Hard']
            menu = card.header_menu()
            next(a for a in menu.actions() if a.text() == "本文のこのコースへ移動").trigger()
            line = win.editor.textCursor().block().text()
            self.assertEqual(line, 'COURSE:Hard')
            menu.deleteLater()


if __name__ == '__main__':
    unittest.main()
