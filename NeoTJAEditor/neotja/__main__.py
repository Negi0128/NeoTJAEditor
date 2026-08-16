import importlib
import sys
import threading


def _prewarm(module_name):
    """重い依存モジュールの import を裏で先に済ませておく。

    **なぜこれで速くなるのか**
    起動時に必ず通る import のうち、`sounddevice` は実測 408ms、`numpy` は
    131ms かかる。sounddevice のぶんはミキサー音声(MixerAudioEngine)の
    __init__ に丸ごと乗っていて、その中身は PortAudio の DLL 読み込みと
    初期化だけ(デバイスを開く処理自体は 1ms もかかっていない)。

    かといって音声エンジンごと後回しにすると「最初の再生でそのぶん待たされる」
    だけで体感は良くならない。そこで **import だけ**を別スレッドへ出し、
    主スレッドが PySide6 と neotja を読んでウィンドウを組み立てているあいだ
    (実測 390ms ほど)に裏で終わらせる。本番の import 文へ来たときには
    sys.modules に載っているので、そこはほぼ素通りになる。

    2つを1本のスレッドに並べると、後ろに置いたほうの到着が遅れて
    MixerAudioEngine を待たせてしまい差し引き損をした(実測)。別々の
    スレッドにして主スレッドと三者同時に進めるのが一番速かった。
    起動全体で 1133ms -> 937ms(中央値)。

    まだ終わっていなければ import 機構がそこで待つだけなので、最悪でも
    従来と同じ。失敗しても握りつぶす: 本番の import は MixerAudioEngine 側に
    あり、そこで従来どおり例外を見てレガシー音声へ退避する(ここで先に
    転んでも、あちらの判断は何も変わらない)。

    importlib を使うのは `import x` だと未使用扱いで pyflakes に引っかかる
    ため。読み込ませること自体が目的で、名前は要らない。"""
    try:
        importlib.import_module(module_name)
    except Exception:  # noqa: BLE001
        pass


# 下の import 群(Qt + neotja)だけで 400ms ほどかかる。先読みはその前に
# 走らせないと重ならないので、モジュールの一番上で起動する。
for _mod in ("sounddevice", "numpy"):
    threading.Thread(target=_prewarm, args=(_mod,), daemon=True).start()

from PySide6.QtGui import QIcon           # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from neotja import settings as settings_mod   # noqa: E402
from neotja.main_window import MainWindow     # noqa: E402


def main():
    app = QApplication(sys.argv)
    icon_path = settings_mod.icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    win = MainWindow(app)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
