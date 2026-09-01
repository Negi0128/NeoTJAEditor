"""落ちた理由をファイルに残す仕掛け。

このアプリは exe にすると窓なし(--windowed)で動くので、**標準エラーが
どこにも出ない**。実際、7月から続いている 0xc0000005 の落ちかたは Windows の
イベントログに「shiboken6.abi3.dll のこのオフセット」としか残っておらず、
Python のどの行から呼んだのかが一切分からなかった。

**なぜ __main__.py ではなくパッケージの入口に置くのか**
最初は __main__.py に書いていたが、`python -m neotja` を通らない起動
(IDE から別のファイルを実行する、テスト用のスクリプトから MainWindow を
組み立てる等)では仕掛けが入らず、**いちばん記録が欲しい実行で何も残らない**
ということが実際に2回起きた。neotja を import した時点で必ず有効になるよう、
neotja/__init__.py から呼ぶ。

仕掛けは4つ:
  * faulthandler … アクセス違反(SIGSEGV)を受け取って**全スレッドの Python
    スタック**を吐く。native crash は例外ではないので try/except では
    捕まらず、これでしか足取りが残らない。
  * sys.excepthook / threading.excepthook … 素の未処理例外。
  * sys.unraisablehook … __del__ や GC の途中で起きた「送出できない例外」。
    ふだんは stderr へ一行出て消えるだけで、窓なしでは見えない。落ちる
    タイミングが不規則なときは、ここが唯一の手がかりになる。
  * Qt のメッセージハンドラ … qFatal("QThread: Destroyed while thread is
    still running" など)は文言を出してすぐ abort するだけなので、先に
    書き出しておく。Qt の import より後でないと掛けられないので、
    install_qt() として分けてある。

ログは追記。落ちるたびに上書きされると直前の一回しか見られない。

**フックの中では絶対に落ちない**ようにしてある。これらはインタプリタの終了
処理中にも呼ばれ、そのときモジュールのグローバルは既に片付いていることが
ある。素直に書くとそこでアクセス違反になり(実測)、記録のための仕組みが
落ちる原因になるという本末転倒になる。
"""

import sys
import threading

# 開いているログ。install() が一度だけ開く。None なら仕掛けられなかった。
LOG = None
#: 実際に書き出せた場所。書けなかったら None。ヘルプの末尾に出しているので、
#: 「記録が残らない」と言われたときに、利用者の環境でこの仕掛けが働いて
#: いるのかどうかをその場で確かめられる(推測しなくて済む)。
LOG_PATH = None
#: 仕掛けられなかったときの理由。同じくヘルプに出す。
INSTALL_ERROR = None
_installed = False


def _candidate_paths():
    """書き出し先の候補を、良い順に。

    ふだんは %LOCALAPPDATA%\\NeoTJAEditor\\crash.log。そこへ書けない事情
    (ウイルス対策・ディスク・権限・環境変数が渡っていない)があっても、
    **どこにも残らない**のがいちばん困るので、退避先を2つ用意しておく。
    """
    out = []
    try:
        from neotja import settings as _s
        out.append(_s.crash_log_path())
    except Exception:  # noqa: BLE001
        pass
    import tempfile
    from pathlib import Path
    out.append(Path(tempfile.gettempdir()) / "NeoTJAEditor" / "crash.log")
    try:
        out.append(Path(sys.argv[0]).resolve().parent / "crash.log")
    except Exception:  # noqa: BLE001
        pass
    return out


def _open_log():
    """候補を順に試して、開けたものを (ファイル, パス) で返す。全部だめなら
    (None, 最後の理由)。"""
    last = None
    for path in _candidate_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            f = open(str(path), "a", buffering=1, encoding="utf-8",
                     errors="replace")
            return f, path
        except Exception as exc:  # noqa: BLE001
            last = "%s: %s (%s)" % (type(exc).__name__, exc, path)
    return None, last


def install():
    """フックを掛ける。二度目以降は何もしない。戻り値はログのファイル。"""
    global LOG, LOG_PATH, INSTALL_ERROR, _installed
    if _installed:
        return LOG
    _installed = True
    try:
        import datetime
        import faulthandler
        import traceback

        f, where = _open_log()
        if f is None:
            INSTALL_ERROR = where or "書き込める場所が見つかりませんでした"
            return None
        LOG_PATH = where
        try:
            from neotja.constants import VERSION as _ver
        except Exception:  # noqa: BLE001
            _ver = "?"
        # 版も残す。手元のビルドと配ったリリースがずれることがあり、
        # 「どの版で落ちたのか」が分からないと調べようがない。
        f.write("\n===== 起動 %s  v%s  [frozen=%s]  %s\n"
                % (datetime.datetime.now(), _ver,
                   getattr(sys, "frozen", False), " ".join(sys.argv)))
        faulthandler.enable(file=f, all_threads=True)

        def _write(head, exc_type, exc, tb):
            try:
                f.write("\n--- %s %s ---\n"
                        % (head, datetime.datetime.now()))
                traceback.print_exception(exc_type, exc, tb, file=f)
            except Exception:  # noqa: BLE001
                pass

        def _hook(exc_type, exc, tb):
            _write("未処理の例外", exc_type, exc, tb)
            try:
                sys.__excepthook__(exc_type, exc, tb)
            except Exception:  # noqa: BLE001
                pass

        def _thread_hook(a):
            _write("ワーカースレッドの例外", a.exc_type, a.exc_value,
                   a.exc_traceback)

        def _unraisable(a):
            _write("送出できない例外", a.exc_type, a.exc_value,
                   a.exc_traceback)

        sys.excepthook = _hook
        threading.excepthook = _thread_hook
        sys.unraisablehook = _unraisable
        LOG = f
    except Exception as exc:  # noqa: BLE001
        # ここで黙って諦めると、いちばん記録が欲しい環境で「なぜか残らない」
        # ことになる(実際に起きた)。理由を持っておいてヘルプに出す。
        INSTALL_ERROR = "%s: %s" % (type(exc).__name__, exc)
        LOG = None
    return LOG


def install_qt():
    """Qt が言い残すこともログへ流す。Qt を import したあとに呼ぶ。

    警告以上だけを拾う(情報レベルまで書くとログが流れて読めなくなる)。"""
    if LOG is None:
        return
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler

        def _handler(mode, ctx, msg):  # noqa: ARG001
            if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg,
                        QtMsgType.QtFatalMsg):
                try:
                    LOG.write("[Qt] %s\n" % msg)
                except Exception:  # noqa: BLE001
                    pass

        qInstallMessageHandler(_handler)
    except Exception:  # noqa: BLE001
        pass
