"""NeoTJAPlayer の入口。

    python -m neotja.player                 曲を選ぶところから
    python -m neotja.player 譜面.tja         その譜面を開いて再生画面へ
    python -m neotja.player 譜面.tja --course Oni --at 12.3

Editor から呼ぶときもこの形。「Player の曲読み込みをすでにしてある状態」で
開くために、譜面・コース・再生位置をそのまま渡す。
"""

import sys

# 重い依存(sounddevice 495ms / numpy 236ms)の import を裏で始める。
# **いちばん最初に呼ぶ。** 下の import と重ならなければ意味が無い。
from neotja import prewarm                       # noqa: E402

prewarm.start()

from neotja import crashlog                      # noqa: F401  (記録の仕掛け)
from neotja import settings as settings_mod
from neotja import skin_cache


def _parse_args(argv):
    """引数を (譜面, コース, 開始位置) に分ける。

    argparse を使わないのは、Editor から渡す形しか無く、余計な依存と
    起動時間を増やしたくないため。知らない指定は黙って無視する
    (将来 Editor 側だけ先に新しくなっても、古い Player が落ちない)。"""
    path, course, at = "", None, 0.0
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--course" and i + 1 < len(argv):
            course = argv[i + 1]
            i += 2
            continue
        if a == "--at" and i + 1 < len(argv):
            try:
                at = float(argv[i + 1])
            except ValueError:
                at = 0.0
            i += 2
            continue
        if not a.startswith("--") and not path:
            path = a
        i += 1
    return path, course, at


def main():
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    crashlog.install_qt()
    app = QApplication(sys.argv)
    app.setApplicationName("NeoTJAPlayer")
    icon = settings_mod.icon_path()
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    cfg = settings_mod.load_settings()
    # 素材は Editor と同じキャッシュを使う。見つからなければ内蔵スキンで
    # 動く(案内は出さない — Player は「見る」道具で、素材が無いなら無いなりに
    # 動いてほしい。素材の入れ方の案内は Editor 側が持っている)。
    system_dir, _searched, _unusable = skin_cache.find_system_dir(cfg)
    if system_dir is not None:
        try:
            skin_cache.ensure_cache(system_dir)
        except Exception:  # noqa: BLE001
            pass
    else:
        skin_cache.use_bundled_only(True)

    from neotja.player.window import PlayerWindow

    path, course, at = _parse_args(sys.argv[1:])
    win = PlayerWindow(cfg)
    win.show()
    if path:
        win.open_chart(path, course_key=course, at_seconds=at)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
