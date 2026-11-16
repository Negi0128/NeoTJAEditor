"""旧 NeoTJAPlayer.exe(v15.3.x まで配っていた別 exe)をごみ箱へ移す。

v15 で旧 Player は配らなくなった(v15-design/PLAYER_REDESIGN.md 6章)。
更新の bat を作るのは**更新前の(古い)Editor**なので、bat では消せない。
そこで新しい Editor が起動したあと、自分の exe の隣にある NeoTJAPlayer.exe を
ごみ箱へ移す。

決まり:
  - **完全には消さない。** ごみ箱へ移すだけ(間違いでも戻せるように)。
  - **版が読めて、かつ旧 Player の版(OLD_PLAYER_LAST 以下)のときだけ**移す。
    版が読めない・新しい版なら残す。いずれ新しい Player を同じ名前で隣へ
    置くかもしれず、それを誤って消すことは絶対に避けたい。
  - **設定に「済んだ」印は付けない。** 旧版へ戻す(バージョン変更)と、旧版の
    Editor が旧 Player を置き直す。そこから更新し直したときにも片付けられる
    よう、毎回の起動で確かめる(無ければ exe の有無を見るだけで終わる)。
  - 失敗しても(旧 Player が起動中で掴まれている等)何も聞かず、次の起動で
    もう一度試す。起動を止めたり落としたりしない。
  - 消すのは Editor の隣にあるものだけ。よそに置いたもの・ショートカットは
    触らない。
"""
import ctypes
import os
import sys

PLAYER_EXE_NAME = "NeoTJAPlayer.exe"

#: 旧 Player として片付ける版の上限(major, minor)。旧 Player を最後に
#: 配ったのは v15.3.x。これより新しい版(15.4 以降・16.x)の NeoTJAPlayer.exe は
#: 新しい Player かもしれないので残す。
OLD_PLAYER_LAST = (15, 3)


def is_old_player_version(version):
    """version は (major, minor, patch, build) か None。旧 Player なら True。

    None(読めなかった)は False — 分からないものは消さない。"""
    if not version or len(version) < 2:
        return False
    return tuple(version[:2]) <= OLD_PLAYER_LAST


def read_file_version(path):
    """exe のバージョン情報(VS_FIXEDFILEINFO の FileVersion)を読む。

    返り値は (major, minor, patch, build)。読めなければ None。exe は
    build_version.write_version_file で版を埋めてある(v12.0.1 以降)。"""
    if sys.platform != "win32":
        return None
    try:
        ver = ctypes.WinDLL("version", use_last_error=True)
        size = ver.GetFileVersionInfoSizeW(ctypes.c_wchar_p(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(ctypes.c_wchar_p(path), 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not ver.VerQueryValueW(buf, ctypes.c_wchar_p("\\"),
                                  ctypes.byref(ptr), ctypes.byref(length)):
            return None
        if not ptr.value or length.value < 52:
            return None
        # VS_FIXEDFILEINFO: dwSignature, dwStrucVersion, dwFileVersionMS,
        # dwFileVersionLS, ... (すべて DWORD)
        fields = (ctypes.c_uint32 * 4).from_address(ptr.value)
        if fields[0] != 0xFEEF04BD:
            return None
        ms, ls = fields[2], fields[3]
        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:  # noqa: BLE001
        return None


_FO_DELETE = 0x0003
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040        # ごみ箱へ(これが無いと完全に消える)
_FOF_NOERRORUI = 0x0400


def recycle_to_bin(path):
    """path を**ごみ箱へ**移す(SHFileOperationW + FOF_ALLOWUNDO)。

    確認・進捗・エラーの窓は出さない。移せたら True。返り値が 0 でも、
    中断された(fAnyOperationsAborted)・まだ残っているなら失敗とみなす。"""
    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        # shellapi.h は 32bit のときだけ 1 バイト詰め(pshpack1)。64bit は既定。
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            _pack_ = 1
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    full = os.path.abspath(path)
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = _FO_DELETE
    # pFrom は「NUL 区切りの並び + 最後にもう1つ NUL」。c_wchar_p は末尾に
    # NUL を1つ足すので、こちらで1つ足して2つにする。
    op.pFrom = full + "\0"
    op.pTo = None
    op.fFlags = (_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT
                 | _FOF_NOERRORUI)
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    shell32.SHFileOperationW.restype = ctypes.c_int
    rc = shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        return False
    return not os.path.exists(full)


def cleanup_old_player(directory, read_version=read_file_version,
                       recycle=recycle_to_bin):
    """directory にある旧 NeoTJAPlayer.exe をごみ箱へ移す。

    返り値(試験と記録用):
      "absent"   … 無かった
      "unknown"  … 版が読めないので残した
      "newer"    … 旧 Player より新しい版なので残した
      "recycled" … ごみ箱へ移した
      "failed"   … 移せなかった(次の起動でまた試す)
    例外は外へ出さない。"""
    try:
        path = os.path.join(directory, PLAYER_EXE_NAME)
        if not os.path.isfile(path):
            return "absent"
        version = read_version(path)
        if not version:
            return "unknown"
        if not is_old_player_version(version):
            return "newer"
        try:
            ok = recycle(path)
        except Exception:  # noqa: BLE001
            ok = False
        if ok and not os.path.exists(path):
            return "recycled"
        return "failed"
    except Exception:  # noqa: BLE001
        return "failed"


def run_at_startup():
    """起動後に一度呼ぶ。exe 版のときだけ、Editor の隣を見る。

    ソースから動かしているときは何もしない(sys.executable は python.exe で、
    その隣を触る理由が無い)。何が起きても起動は続ける。"""
    try:
        if not getattr(sys, "frozen", False):
            return None
        return cleanup_old_player(os.path.dirname(sys.executable))
    except Exception:  # noqa: BLE001
        return None
