# -*- coding: utf-8 -*-
"""落ちた理由を残す仕掛けを、**PySide6 が読まれるより前**に掛ける。

**なぜ要るのか**
7月から続いている 0xc0000005(shiboken6.abi3.dll)の落ちかたは、crash.log に
一行も残らなかった。ログは行バッファで開いているので、起動行が書けていれば
必ずディスクに届く。届いていない = 仕掛けが掛かる前に落ちている。

PyInstaller の onefile は、本体(neotja/__main__.py)より先にランタイムフックを
走らせる。そのうち pyi_rth_pyside6 が **PySide6 を import する** ので、
shiboken の読み込みはそこで起きる。neotja/__init__.py から掛けていたのでは
間に合わない。

PyInstaller の仕様で、spec の runtime_hooks に書いたものは
「started before any existing PyInstaller runtime hooks」(build_main.py の
コメント)。つまりこのファイルは pyi_rth_pyside6 より先に走る。ここで
faulthandler を掛けておけば、PySide6 の読み込みで落ちても全スレッドの
スタックが残る。

**何も邪魔しない。** neotja.crashlog.install() は二度目以降なにもしないので、
このあと neotja/__init__.py が呼んでも二重にはならない。失敗しても握りつぶす
(記録のための仕掛けが起動を止めては本末転倒)。
"""

try:
    from neotja import crashlog as _crashlog
    _crashlog.install()
    if _crashlog.LOG is not None:
        _crashlog.LOG.write("(ここまでは本体より前。次は PySide6 の読み込み)\n")
except Exception:  # noqa: BLE001
    pass
