"""窓だけのアプリ(exe・pythonw)で、標準入出力の口が無い/壊れているときに
NUL へつなぎ直す。

つながっていないまま子プロセス(yt-dlp が呼ぶ ffmpeg など)を
subprocess で起こすと、受け継ぐ口を複製できずに
「[WinError 6] ハンドルが無効です。」で落ちる(YouTube から落とせない)。
"""
import os
import sys

_STD = ((0, -10, "stdin", "r"), (1, -11, "stdout", "w"), (2, -12, "stderr", "w"))


def _handle_ok(h):
    """Windows の口として使えるか(複製できるか)。"""
    if not h or h == -1:
        return False
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                  ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                  wintypes.BOOL, wintypes.DWORD]
    out = wintypes.HANDLE()
    me = k.GetCurrentProcess()
    if not k.DuplicateHandle(me, wintypes.HANDLE(h), me, ctypes.byref(out), 0, False, 2):
        return False
    k.CloseHandle(out)
    return True


def fix():
    """使えない標準の口を NUL へ。直したものの名前の一覧を返す。"""
    if sys.platform != "win32":
        return []
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.GetStdHandle.restype = wintypes.HANDLE
    k.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    fixed = []
    for fd, std, name, mode in _STD:
        try:
            if _handle_ok(k.GetStdHandle(std)):
                continue
            f = open(os.devnull, mode)
            k.SetStdHandle(std, msvcrt.get_osfhandle(f.fileno()))
            if getattr(sys, name) is None:
                setattr(sys, name, f)
            else:
                globals().setdefault("_keep", []).append(f)
            fixed.append(name)
        except Exception:  # noqa: BLE001
            pass
    return fixed
