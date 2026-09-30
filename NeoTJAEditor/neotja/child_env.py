"""凍結した exe から別のプログラムを起動するときに渡す環境変数。

**なぜ要るのか**

PyInstaller の onefile 版は2段構えで動く。1段目(ブートローダ)が中身を
`%TEMP%\\_MEIxxxxxxxx` へ展開し、そこを指す環境変数を立てて2段目を起動する。
アプリの Python が動いているのは2段目なので、`os.environ` には次のものが
**入ったまま**になっている。

    _PYI_APPLICATION_HOME_DIR   展開先(_MEIxxxxxxxx)
    _PYI_ARCHIVE_FILE           その中身の出どころ(= 自分の exe のパス)
    _PYI_PARENT_PROCESS_LEVEL   何段目か
    _PYI_SPLASH_IPC             スプラッシュとの通信口

ここから `subprocess.Popen` で何かを起動すると、これらがそのまま子へ渡る。
渡された先が **自分と同じパスの exe** だと、ブートローダは「自分は既に
展開済みの2段目だ」と判断して展開をやり直さず、環境変数が指す展開先を使う。

それで壊れるのが**更新のあとの起動**だった。更新は exe を同じ場所へ上書き
してから起動し直すので、新しい exe から見た `_PYI_ARCHIVE_FILE` は自分自身。
ところが展開先は、更新前のプロセスが終わるときに消してしまっている。結果、
新しい exe は消えた場所を見に行き、

    Security validation failure: unexpected name of application's home directory!

という英語のダイアログだけを出して止まる(利用者の報告 2026-10-01)。
「新しいウィンドウで開く」も同じ組み合わせ(同じパスの exe)なので、2つ目の
ウィンドウは1つ目の展開先に相乗りしており、1つ目を閉じると足元が消えていた。

**どうするか**

子へ渡す環境から `_PYI_*` を落とす。落とせば新しいプロセスは1段目から
やり直し、自分の中身を自分で展開する。PyInstaller が退避しておく
`<名前>_ORIG` も、あれば元の名前へ戻す(ライブラリ探索パスを書き換える
プラットフォームのため。Windows では普通は無い)。
"""

import os

#: 落とす環境変数。_MEIPASS2 は PyInstaller 5 以前の呼び名(古い版で組んだ
#: exe から起動されることもあるので、ついでに落としておく)。
_BOOTLOADER_VARS = (
    "_PYI_APPLICATION_HOME_DIR",
    "_PYI_ARCHIVE_FILE",
    "_PYI_PARENT_PROCESS_LEVEL",
    "_PYI_SPLASH_IPC",
    "_MEIPASS2",
)

#: ブートローダが書き換え、元の値を `<名前>_ORIG` へ逃がすことがあるもの。
#: 名前を決め打ちにしているのは、末尾が _ORIG というだけの利用者の環境変数を
#: 巻き込まないため。
_RESTORE_VARS = (
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "SSL_CERT_FILE",
)


def child_env(base=None):
    """別のプログラムへ渡す環境変数の辞書。

    base を渡さなければ今の環境を元にする。凍結していないとき(ソースから
    動かしているとき)は落とすものが無いので、実質そのままの写しになる。
    """
    env = dict(os.environ if base is None else base)
    for name in _BOOTLOADER_VARS:
        env.pop(name, None)
    for name in _RESTORE_VARS:
        orig = env.pop(name + "_ORIG", None)
        if orig is not None:
            env[name] = orig
    return env
