"""再生画面のあいだ、窓の大きさを 16:9 (ゲーム画面の比)に保つ。

通常の窓(最大化・全画面でない)で再生画面を出していると、窓の比が 16:9 から
ずれたぶんだけ左右(か上下)に黒い帯が出る。そこで再生画面のあいだだけ:

- 窓の縁を引きずっているとき(Windows の WM_SIZING)、引きずっている矩形を
  「ゲーム画面の部分が 16:9 になる」ように直す(sizing_rect)。
- 再生画面へ入るとき、窓を1回だけ 16:9 に合わせる(snap_client_size)。

最大化・F11(全画面)はそのまま。ほかの画面では何もしない。

ここの関数は数字だけを扱う(試験しやすいように Qt にも Windows にも触らない)。
窓へは MainWindow.nativeEvent から handle_native を呼ぶ(Windows のときだけ)。
"""

import sys

ASPECT_W = 16
ASPECT_H = 9

# WM_SIZING の wParam(どの縁を引きずっているか)
WMSZ_LEFT = 1
WMSZ_RIGHT = 2
WMSZ_TOP = 3
WMSZ_TOPLEFT = 4
WMSZ_TOPRIGHT = 5
WMSZ_BOTTOM = 6
WMSZ_BOTTOMLEFT = 7
WMSZ_BOTTOMRIGHT = 8

WM_SIZING = 0x0214


def _h_from_w(w):
    return int(round(w * ASPECT_H / float(ASPECT_W)))


def _w_from_h(h):
    return int(round(h * ASPECT_W / float(ASPECT_H)))


def sizing_rect(rect, edge, extra_w, extra_h, min_inner=(160, 90)):
    """引きずっている窓の矩形 rect=(左, 上, 右, 下) を、ゲーム画面の部分
    (窓の大きさ − extra_w / extra_h)が 16:9 になるように直して返す。

    extra_w / extra_h は窓の枠・タイトルバー・ほかの部品のぶん(画素)。
    引きずっている縁の反対側は動かさない。左右の縁なら高さを、上下の縁なら
    幅を合わせる。角は、幅から出した高さと高さから出した幅のうち大きいほうに
    そろえる(縮めすぎて手から離れないように)。"""
    left, top, right, bottom = (int(v) for v in rect)
    iw = max(int(min_inner[0]), (right - left) - int(extra_w))
    ih = max(int(min_inner[1]), (bottom - top) - int(extra_h))
    if edge in (WMSZ_LEFT, WMSZ_RIGHT):
        ih = _h_from_w(iw)
    elif edge in (WMSZ_TOP, WMSZ_BOTTOM):
        iw = _w_from_h(ih)
    else:
        if _h_from_w(iw) >= ih:
            ih = _h_from_w(iw)
        else:
            iw = _w_from_h(ih)
    w = iw + int(extra_w)
    h = ih + int(extra_h)
    # 幅を変えるときに動かす側
    if edge in (WMSZ_LEFT, WMSZ_TOPLEFT, WMSZ_BOTTOMLEFT):
        left = right - w
    else:
        right = left + w
    # 高さを変えるときに動かす側
    if edge in (WMSZ_TOP, WMSZ_TOPLEFT, WMSZ_TOPRIGHT):
        top = bottom - h
    else:
        bottom = top + h
    return left, top, right, bottom


def snap_client_size(client_w, client_h, page_w, page_h, max_client_h=None):
    """窓の中身の大きさ(client_w, client_h)のうち、ゲーム画面の部分
    (page_w, page_h)を 16:9 にした窓の中身の大きさを返す。

    幅を保って高さを合わせる。高さが max_client_h(画面に入る高さ)を
    超えるときは、高さをそこで止めて幅を縮める。"""
    extra_w = int(client_w) - int(page_w)
    extra_h = int(client_h) - int(page_h)
    pw = max(1, int(page_w))
    ph = _h_from_w(pw)
    if max_client_h is not None and ph + extra_h > int(max_client_h):
        ph = max(1, int(max_client_h) - extra_h)
        pw = _w_from_h(ph)
    return pw + extra_w, ph + extra_h


# ----------------------------------------------------------------------
# Windows の窓へ付ける(WM_SIZING を直す)
#
# アプリ全体の native イベントフィルタには WM_SIZING が来ない(Qt が知って
# いる種類のメッセージしか回さない)。窓の nativeEvent には来るので、
# MainWindow.nativeEvent からここを呼ぶ。
# ----------------------------------------------------------------------
_wt = None


def _wintypes():
    global _wt
    if _wt is None:
        import ctypes
        import ctypes.wintypes
        _wt = (ctypes, ctypes.wintypes, ctypes.windll.user32)
    return _wt


def handle_native(window, message, active_fn, page_fn):
    """window の nativeEvent に来た1通。WM_SIZING で active_fn() が真なら、
    引きずっている矩形を 16:9 に直して True を返す(結果は 1 を返すこと)。"""
    if sys.platform != "win32":
        return False
    try:
        ctypes, wintypes, user32 = _wintypes()
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != WM_SIZING or not active_fn():
            return False
        page = page_fn()
        if page is None:
            return False
        cur = wintypes.RECT()
        if not user32.GetWindowRect(msg.hWnd, ctypes.byref(cur)):
            return False
        dpr = float(window.devicePixelRatioF() or 1.0)
        extra_w = (cur.right - cur.left) - int(round(page.width() * dpr))
        extra_h = (cur.bottom - cur.top) - int(round(page.height() * dpr))
        r = wintypes.RECT.from_address(int(msg.lParam))
        r.left, r.top, r.right, r.bottom = sizing_rect(
            (r.left, r.top, r.right, r.bottom), int(msg.wParam), extra_w, extra_h)
        return True
    except Exception:  # noqa: BLE001  ここで投げると Qt ごと落ちる
        return False
