"""Player の音量(全体・曲・効果音)と音の設定の当て直し。

- PlayerCore.apply_audio_settings: 音量・打音の WAV・ワイヤレス調整・出力デバイス。
- 選曲の小窓・難易度設定・途中メニューの3か所が同じ値を変え、互いにそろう。
- Editor の音と欄にもすぐ当たり、Player を出ると保存も済んでいる。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_volume.py`
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-volume-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import editor_session, real_qt_test  # noqa: E402


def _wait(ms):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(10)
        time.sleep(0.002)


class _Audio:
    def __init__(self, device=''):
        self.device = device
        self.reopened = []

    def output_device_name(self):
        return self.device

    def reopen_stream(self, name=None, rescan=False):
        self.reopened.append((name, rescan))
        self.device = name
        return True, ''


class _Dock:
    def __init__(self):
        self.audio = _Audio()
        self.log = []

    def __getattr__(self, name):
        if name.startswith('set_'):
            return lambda *a: self.log.append((name,) + a)
        raise AttributeError(name)


def _core(cfg):
    from neotja.player.core import PlayerCore
    core = PlayerCore.__new__(PlayerCore)
    core.cfg = cfg
    core.dock = _Dock()
    return core


class CoreApplyTests(unittest.TestCase):
    def test_apply_sets_volumes_hits_offset_and_device(self):
        cfg = {'master_volume': 0.0, 'preview_volume': 0.4, 'sfx_volume': 0.7,
               'wireless_offset_enabled': True, 'wireless_offset_ms': 120.0,
               'audio_output_device': 'Speakers X'}
        core = _core(cfg)
        core.apply_audio_settings()
        log = dict((e[0], e[1:]) for e in core.dock.log)
        self.assertEqual(log['set_master_volume'], (0.0,))
        self.assertEqual(log['set_volume'], (0.4,))
        self.assertEqual(log['set_sfx_volume'], (0.7,))
        self.assertEqual(log['set_output_offset_ms'], (120.0,))
        self.assertIn('set_hit_sound_files', log)
        # 出力デバイスは違うときだけ開き直す。一覧の読み直しはしない(Editor の
        # 出力まで無効になるため)。
        self.assertEqual(core.dock.audio.reopened, [('Speakers X', False)])
        core.dock.log.clear()
        core.apply_audio_settings()
        self.assertEqual(core.dock.audio.reopened, [('Speakers X', False)])
        # 打音の WAV は変わっていなければ読み直さない。
        self.assertNotIn('set_hit_sound_files', [e[0] for e in core.dock.log])
        # ワイヤレス調整を切ると 0ms。
        cfg['wireless_offset_enabled'] = False
        core.apply_audio_settings()
        self.assertIn(('set_output_offset_ms', 0.0), core.dock.log)

    def test_broken_values_fall_back(self):
        from neotja.player.volume import level_of
        self.assertEqual(level_of({'master_volume': 'x'}, 'master_volume'), 1.0)
        self.assertEqual(level_of({'sfx_volume': 3}, 'sfx_volume'), 1.0)
        self.assertEqual(level_of({}, 'preview_volume'), 0.8)


class _Core:
    instances = []

    def __init__(self, cfg):
        self.cfg = cfg
        self.applied = 0
        self.dock = type('D', (), {'cancel_predecode': lambda s: None})()
        _Core.instances.append(self)

    def apply_audio_settings(self):
        self.applied += 1

    def __getattr__(self, name):
        return lambda *a, **k: None


class HostVolumeTests(unittest.TestCase):
    @real_qt_test
    def test_three_places_share_one_value(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from neotja.player.host import PlayerHost
        cfg = {'master_volume': 0.0, 'preview_volume': 0.8, 'sfx_volume': 0.9}
        saves, editor = [], []
        orig = PlayerHost.core_factory
        PlayerHost.core_factory = _Core
        host = PlayerHost(cfg, lambda: saves.append(dict(cfg)),
                          editor_audio_cb=lambda: editor.append(dict(cfg)))
        try:
            host.resize(1600, 900)
            host.show()
            host.on_shown()
            core = host._ensure_core()
            saves.clear()
            # 選曲の上の段のスピーカー → 小窓。
            self.assertTrue(host.btn_volume.isVisible())
            self.assertTrue(host.btn_volume.muted)        # 全体 0
            host.btn_volume.click()
            pop = host.volume_popover
            self.assertTrue(pop.isVisible())
            sl = pop.rows.sliders['preview_volume']
            self.assertEqual(sl.value(), 80)
            t = sl._track()
            QTest.mouseClick(sl, Qt.LeftButton, pos=QPoint(int(t.left() + t.width() * 0.5),
                                                          int(t.center().y())))
            self.assertAlmostEqual(cfg['preview_volume'], 0.5, places=2)
            self.assertGreaterEqual(core.applied, 1)
            self.assertEqual(editor[-1]['preview_volume'], cfg['preview_volume'])
            # ほかの2か所もそろう。
            self.assertEqual(host.difficulty.settings.volume_sliders['preview_volume'].value(), 50)
            self.assertEqual(host.play_page.menu.volume.sliders['preview_volume'].value(), 50)
            # 保存は動かし終わってからまとめて1回。
            self.assertEqual(saves, [])
            _wait(600)
            self.assertEqual(len(saves), 1)
            # 外を押すと閉じる。
            QTest.mouseClick(host.library, Qt.LeftButton, pos=QPoint(5, host.library.height() - 5))
            self.assertFalse(pop.isVisible())
            self.assertFalse(host.btn_volume.isChecked())
            # 難易度設定の段(ホイール)。
            host.difficulty.settings.volume_sliders['sfx_volume'].setValue(30, emit=True)
            self.assertAlmostEqual(cfg['sfx_volume'], 0.3)
            self.assertEqual(pop.rows.sliders['sfx_volume'].value(), 30)
            # 途中メニューの段。全体を変えたらスピーカーの絵も。
            host.play_page.menu.volume.sliders['master_volume'].setValue(0, emit=True)
            self.assertTrue(host.btn_volume.muted)
            host.flush_volume_save()
            self.assertEqual(saves[-1]['sfx_volume'], 0.3)
        finally:
            PlayerHost.core_factory = orig
            host.shutdown()
            host.close()
            host.deleteLater()

    @real_qt_test
    def test_pause_menu_rows_do_not_resume(self):
        from neotja.player.pause_menu import PauseMenu
        from PySide6.QtWidgets import QWidget
        par = QWidget()
        par.resize(1920, 1009)
        menu = PauseMenu(par)
        par.show()
        chosen = []
        menu.chosen.connect(chosen.append)
        menu.open()
        try:
            self.assertTrue(menu.volume.isVisible())
            # 板の中に収まる。
            vg = menu.volume.geometry()
            self.assertTrue(menu._panel.contains(vg.topLeft()) and menu._panel.contains(vg.bottomRight()))
            self.assertGreater(vg.top(), menu.items[-1].geometry().bottom())
            menu.volume.sliders['sfx_volume'].setValue(10, emit=True)
            self.assertEqual(chosen, [])          # 動かしても続けない
            self.assertTrue(menu.is_open())
            # キーは項目のほう(横棒はフォーカスを取らない)。
            self.assertEqual(menu.volume.sliders['sfx_volume'].focusPolicy(), 0)
        finally:
            par.deleteLater()


class EditorSyncTests(unittest.TestCase):
    @real_qt_test
    def test_editor_engine_and_sliders_follow(self):
        from neotja.player.host import PlayerHost
        orig = PlayerHost.core_factory
        PlayerHost.core_factory = _Core
        try:
            with editor_session() as (win, root):
                win.show()
                win.start_screen.btn_player.click()
                host = win.player_page
                pd = win.preview_dock
                host.set_audio_level('preview_volume', 0.4)
                host.set_audio_level('sfx_volume', 0.55)
                # Editor の欄はその場で(操作音も Editor のエンジンで鳴るため)。
                self.assertEqual(pd.volume_slider.value(), 40)
                self.assertEqual(pd.sfx_volume_slider.value(), 55)
                self.assertAlmostEqual(pd._sfx_ratio, 0.55)
                self.assertEqual(pd.master_volume_slider.value(), 0)
                host.back_requested.emit()
                saved = json.loads((root / 'settings.json').read_text(encoding='utf-8'))
                self.assertAlmostEqual(saved['preview_volume'], 0.4)
                self.assertAlmostEqual(saved['sfx_volume'], 0.55)
                self.assertEqual(saved['master_volume'], 0.0)
        finally:
            PlayerHost.core_factory = orig


if __name__ == '__main__':
    unittest.main()
