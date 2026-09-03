"""重い依存モジュールの import を、裏で先に済ませておく。

**なぜ速くなるのか**
起動時に必ず通る import のうち、`sounddevice` は実測 495ms(PortAudio の
DLL 読み込みと初期化)、`numpy` は 236ms かかる。sounddevice のぶんは音声
エンジン(MixerAudioEngine)の __init__ に丸ごと乗っていて、そこが再生
ウィンドウの組み立ての大半を占めていた。

かといって音声エンジンごと後回しにすると「最初の再生でそのぶん待たされる」
だけで体感は良くならない。そこで **import だけ**を別スレッドへ出し、主
スレッドが PySide6 と neotja を読んで窓を組み立てているあいだに裏で
終わらせる。本番の import 文へ来たときには sys.modules に載っているので、
そこはほぼ素通りになる。

2つを1本のスレッドに並べると、後ろに置いたほうの到着が遅れて音声エンジンを
待たせてしまい差し引き損をした(実測)。別々のスレッドにして主スレッドと
三者同時に進めるのが一番速い。

まだ終わっていなければ import 機構がそこで待つだけなので、最悪でも従来と
同じ。失敗しても握りつぶす: 本番の import は MixerAudioEngine 側にあり、
そこで従来どおり例外を見てレガシー音声へ退避する。

**終了時に必ず合流させる。** デーモンスレッドはインタプリタの終了処理で
任意の場所で打ち切られ、import の途中で打ち切られると、読み込みかけの
拡張モジュール(PortAudio の DLL)を掴んだまま解放が進んでアクセス違反で
プロセスごと落ちる。起動してすぐ閉じた場合に 5回中5回 再現した。
atexit のコールバックはデーモンスレッドが打ち切られる**前**に走るので、
そこで合流させれば起動は 1ms も遅くならず、危ない打ち切りだけが無くなる。
"""

import atexit
import importlib
import threading

#: 先に読ませておくもの。
MODULES = ("sounddevice", "numpy")

_threads = []


def _load(module_name):
    try:
        importlib.import_module(module_name)
    except Exception:  # noqa: BLE001
        pass


def start():
    """先読みを始める。**Qt や neotja の import より前に呼ぶこと。**
    そのあとの import と重ならなければ、裏へ出した意味が無い。"""
    if _threads:
        return
    for name in MODULES:
        t = threading.Thread(target=_load, args=(name,), daemon=True)
        t.start()
        _threads.append(t)
    atexit.register(_join)


def _join():
    """終了処理に入る前に合流させる。上の説明を参照。

    上限を付けてあるのは、音声機器の事情で PortAudio の初期化が戻って
    こない環境でも終了できるようにするため。"""
    for t in _threads:
        try:
            t.join(timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
