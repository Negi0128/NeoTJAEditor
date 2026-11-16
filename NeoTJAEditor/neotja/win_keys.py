"""Windows の Alt+Space(窓のシステムメニュー)を止める仕掛け。

利用者の報告 2026-11-16: Alt を押したまま Space(再生/停止)を押すと、
窓の左上にシステムメニュー(元のサイズに戻す・移動・閉じる…)が開く。
開くと以後のキーをメニューが食うので、作譜や演奏の途中で困る。

**なぜウィジェットの keyPressEvent ではなく native のフィルタなのか**
Alt+Space は Windows が WM_SYSKEYDOWN → WM_SYSCHAR → WM_SYSCOMMAND
(SC_KEYMENU) と回して開くもので、Qt のキーイベントになる前に決まって
しまう(Qt 自身も WM_SYSKEYDOWN の Space を見てメニューを出す)。
QApplication に付けておけば Editor / Player のどの窓でも効く。

止めるのは Space の3通だけ。WM_SYSKEYUP は通す(離したことが Qt に
伝わらないと、Alt が押されたままの扱いになる)。Alt 単独や Alt+F4 など
ほかの組み合わせも今までどおり。"""
import sys

WM_SYSKEYDOWN = 0x0104
WM_SYSCHAR = 0x0106
WM_SYSCOMMAND = 0x0112
SC_KEYMENU = 0xF100
VK_SPACE = 0x20

#: install() で付けたフィルタ。Python 側で持っていないと GC で消え、
#: Qt が消えたオブジェクトを呼んで落ちる。
_installed = None


def should_swallow(msg_id, wparam, lparam):
    """この1通を握りつぶすか(Windows のメッセージの番号と引数で決める)。"""
    if msg_id in (WM_SYSKEYDOWN, WM_SYSCHAR):
        return wparam == VK_SPACE
    if msg_id == WM_SYSCOMMAND:
        # SC_KEYMENU の lParam はメニューを開いた文字。Space(0x20)の
        # ときだけ止める(0 は Alt 単独・F10 でメニュー列へ行くほう)。
        return (wparam & 0xFFF0) == SC_KEYMENU and lparam == VK_SPACE
    return False


def _make_filter_class():
    import ctypes.wintypes
    from PySide6.QtCore import QAbstractNativeEventFilter

    class AltSpaceFilter(QAbstractNativeEventFilter):
        def nativeEventFilter(self, event_type, message):
            if bytes(event_type) != b"windows_generic_MSG":
                return False, 0
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                hit = should_swallow(msg.message, msg.wParam or 0,
                                     msg.lParam or 0)
            except Exception:  # noqa: BLE001  ここで投げると Qt ごと落ちる
                return False, 0
            return (True, 0) if hit else (False, 0)

    return AltSpaceFilter


def install(app):
    """app(QApplication)に付ける。Windows 以外では何もしない。"""
    global _installed
    if sys.platform != "win32" or _installed is not None:
        return _installed
    filt = _make_filter_class()()
    app.installNativeEventFilter(filt)
    _installed = filt
    return filt
