"""Real-Qt integration fixture with settings isolated before MainWindow import."""
import hashlib
import json
import tempfile
import time
import wave
from contextlib import contextmanager
from pathlib import Path

from neotja import settings

_app = None


@contextmanager
def editor_session():
    global _app
    real = {settings.settings_path(), settings._primary_settings_path(), settings._fallback_settings_path()}
    def snapshot():
        return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                if p.exists() else None for p in real}
    before = snapshot()
    original_path, original_fallback = settings.settings_path, settings._fallback_settings_path
    with tempfile.TemporaryDirectory(prefix='neotja-regression-') as scratch:
        root = Path(scratch)
        target = root / 'settings.json'
        cfg = settings.default_settings()
        cfg.update(master_volume=0.0, check_updates_on_startup=False,
                   auto_save_enabled=False, gpu_render=False)
        target.write_text(json.dumps(cfg), encoding='utf-8')
        settings.settings_path = lambda: target
        settings._fallback_settings_path = lambda: target
        from PySide6.QtWidgets import QApplication
        from neotja.main_window import MainWindow
        _app = QApplication.instance() or QApplication([])
        win = MainWindow(_app)
        try:
            yield win, root
        finally:
            win.editor.modified_lines.clear()
            win.close()
            _app.processEvents()
            settings.settings_path, settings._fallback_settings_path = original_path, original_fallback
            assert before == snapshot(), 'User settings changed'


def open_test_chart(win, root, body='1111,\n1111,\n1111,\n1111,', bpm=120):
    from PySide6.QtTest import QTest
    wav_path = root / 'test.wav'
    if not wav_path.exists():
        with wave.open(str(wav_path), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(44100)
            wav.writeframes(b'\x00\x00' * 44100 * 20)
    text = ('TITLE:動作確認\nBPM:%s\nOFFSET:0\nWAVE:test.wav\nCOURSE:oni\n#START\n%s\n#END\n'
            % (bpm, body))
    path = root / 'test.tja'
    path.write_text(text, encoding='cp932')
    win._open_path(str(path))
    for _ in range(250):
        if win.preview_dock.btn_play.isEnabled() and not win.preview_dock.is_decoding():
            break
        QTest.qWait(20)
        time.sleep(0.005)  # Pythonのデコードワーカーにも実行時間を渡す
    assert win.preview_dock.btn_play.isEnabled(), win.preview_dock.status_label.text()
    return text
