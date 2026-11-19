"""環境設定: 左の一覧・検索・説明は ⓘ のツールチップ・保存はこれまでどおり。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_settings_dialog_nav.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui_support import editor_session, real_qt_test  # noqa: E402


class SettingsDialogNavTests(unittest.TestCase):
    @real_qt_test
    def test_pages_search_tips_and_save(self):
        from PySide6.QtWidgets import QGroupBox
        from neotja.dialogs.settings_dialog import SettingsDialog, _Hint, _InfoDot
        with editor_session() as (win, root):
            win.config_data["master_volume"] = 0.0
            dlg = SettingsDialog(win)
            dlg.show()
            try:
                names = [n for n, _a, _w in dlg.pages]
                self.assertEqual(names[:4], ["シミュレータ起動", "ショートカット", "エディタ", "書き出し・素材"])
                for n in ("音声", "表示", "ネームプレート", "プレートの名前・段位", "Player",
                          "開発・実験的機能"):
                    self.assertIn(n, names)
                self.assertEqual(dlg.nav.count(), len(names))
                # 演奏モードは Player のページに。
                player = dict((n, w) for n, _a, w in dlg.pages)["Player"]
                self.assertTrue(player.isAncestorOf(dlg.player_unlock_check))
                # 説明は画面に出さず、ⓘ のツールチップへ(状態の知らせだけは残す)。
                for h in dlg.findChildren(_Hint):
                    if not h.live:
                        self.assertFalse(h.isVisible())
                self.assertTrue(dlg.findChildren(_InfoDot))
                self.assertIn("Ctrl+ホイール",
                              " ".join(d.toolTip() for d in dlg.findChildren(_InfoDot)))
                # 検索: 当たるページだけ残り、Enter でそこへ移る。
                dlg.search.setText("fps")
                shown = [dlg.nav.item(i).text() for i in range(dlg.nav.count())
                         if not dlg.nav.item(i).isHidden()]
                self.assertIn("表示", shown)
                self.assertNotIn("ショートカット", shown)
                dlg._jump_first()
                self.assertEqual(dlg.pages[dlg.stack.currentIndex()][0], "表示")
                # 説明(ツールチップ)の言葉でも当たる。
                dlg.search.setText("ティアリング")
                self.assertFalse(all(dlg.nav.item(i).isHidden() for i in range(dlg.nav.count())))
                dlg.search.clear()
                self.assertTrue(all(not dlg.nav.item(i).isHidden() for i in range(dlg.nav.count())))
                # 見本は開いているネームプレートのページへ移る。
                sample = next(b for b in dlg.findChildren(QGroupBox) if b.title() == "見本")
                dlg.nav.setCurrentRow(names.index("プレートの名前・段位"))
                self.assertIs(sample.parentWidget(), dlg.pages[names.index("プレートの名前・段位")][2])
                # どのページも横にはみ出さない。
                for _n, area, _w in dlg.pages:
                    self.assertEqual(area.horizontalScrollBar().maximum(), 0)
                # 保存はこれまでどおり。
                dlg.play_flush_check.setChecked(True)
                dlg._save()
                self.assertTrue(win.config_data["play_flush_before_play"])
            finally:
                dlg.close()
                dlg.deleteLater()


if __name__ == "__main__":
    unittest.main()
