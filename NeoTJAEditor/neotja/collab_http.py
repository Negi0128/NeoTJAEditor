# -*- coding: utf-8 -*-
"""合作の待ち受け口で、WebSocket と一緒に**スマホ用のページも配る**。

利用者の指定 2026-10-22。スマホから作譜するための画面(assets/mobile.html)を、
合作のホストが配る。別のアプリを入れてもらわずに、招待コードの URL を開けば
そのまま繋がる形にするため。

**なぜ口を1つにするか。** ページを別の口で配ると、ブラウザから見て
「ページの出どころ」と「WebSocket の行き先」が別のオリジンになる。トンネル
越しではページが https で届くので、そこから ws:// へは**ブラウザが弾く**
(混在内容)。同じ口から配れば wss:// がそのまま通り、トンネルも1本で足りる。

**どうやって1つの口で2つを捌くか。** QWebSocketServer は自分で listen
しなくても、`handleConnection(QTcpSocket)` で「もう繋がっている相手」を
受け取れる。そこでこちらが QTcpServer として待ち受け、最初の数百バイトを
**覗いて**(読まずに peek して)から振り分ける:

    Upgrade: websocket がある  -> QWebSocketServer へそのまま渡す(今までどおり)
    無い                        -> こちらで HTTP の返事を書いて閉じる

覗くだけなので、渡した先の QWebSocketServer が握手をやり直せる。
"""
import io
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtNetwork import QTcpServer, QTcpSocket

#: 覗く長さ。要求の1行目と見出しが収まればよい。
PEEK_MAX = 4096
#: 要求が来ないまま放っておく相手を切るまで(ms)。トンネルの様子見の接続が
#: 残り続けないように。
IDLE_MS = 15000


def assets_dir() -> Path:
    """同梱したものの置き場(skin_cache._bundled_dir と同じ所)。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "neotja" / "assets"
        if p.is_dir():
            return p
    return Path(__file__).resolve().parent / "assets"


def mobile_page() -> bytes:
    """スマホ用のページ。読めなければ短い断りを返す。"""
    try:
        return assets_dir().joinpath("mobile.html").read_bytes()
    except OSError:
        return ("<!doctype html><meta charset=utf-8>"
                "<p>スマホ用の画面が見つかりませんでした。").encode("utf-8")


def _response(status: str, body: bytes, ctype: str) -> bytes:
    """返事を1つ組み立てる。短いので使い回しはしない。"""
    head = (
        "HTTP/1.1 %s\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %d\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n"
        "\r\n" % (status, ctype, len(body))
    )
    return head.encode("ascii") + body


class FrontDoor(QTcpServer):
    """合作の口。WebSocket は中の QWebSocketServer へ、ほかは HTML を返す。

    `ws_server` は **listen させていない** QWebSocketServer を渡すこと。
    こちらが受けた相手を handleConnection で渡すと、握手が済んだところで
    あちらの newConnection が出る — つまり合作側の作りは1行も変わらない。
    """

    def __init__(self, ws_server, parent=None):
        super().__init__(parent)
        self._ws = ws_server
        #: WebSocket として渡した相手。渡したあとも消えないよう掴んでおく。
        self._handed = set()

    # QTcpServer の受け口。**ここで socket を作って紐づける。**
    def incomingConnection(self, handle):
        sock = QTcpSocket(self)
        if not sock.setSocketDescriptor(handle):
            sock.deleteLater()
            return
        # 要求が来るまで待つ。来ないまま居座る相手は切る。
        timer = QTimer(sock)
        timer.setSingleShot(True)
        timer.timeout.connect(sock.abort)
        timer.start(IDLE_MS)
        sock.readyRead.connect(lambda s=sock, t=timer: self._on_ready(s, t))
        sock.disconnected.connect(sock.deleteLater)

    def _on_ready(self, sock, timer):
        # **読まずに覗く。** 読んでしまうと QWebSocketServer が握手の文面を
        # 見られなくなる(そのまま渡しても相手にされない)。
        head = bytes(sock.peek(PEEK_MAX))
        if b"\r\n\r\n" not in head and len(head) < PEEK_MAX:
            return                      # まだ見出しが揃っていない
        timer.stop()
        try:
            sock.readyRead.disconnect()
        except (RuntimeError, TypeError):
            pass
        if self._is_websocket(head):
            # 以降の面倒は QWebSocketServer が見る。こちらの後始末
            # (切れたら消す)は外しておく。
            try:
                sock.disconnected.disconnect()
            except (RuntimeError, TypeError):
                pass
            # **親は外さない。** 外すと持ち主が Python 側だけになり、この
            # 関数を抜けた拍子に実体ごと消える(実測: 渡した直後に握手が
            # 途切れてゲストが入れなかった)。渡した先が面倒を見るまでの
            # あいだ、こちらでも掴んでおく。
            self._handed.add(sock)
            sock.destroyed.connect(lambda *_a, s=sock: self._handed.discard(s))
            self._ws.handleConnection(sock)
            return
        self._serve(sock, head)

    @staticmethod
    def _is_websocket(head: bytes) -> bool:
        """WebSocket の握手かどうか。見出しの名前は大小を区別しない。"""
        low = head.lower()
        return b"upgrade:" in low and b"websocket" in low

    def _serve(self, sock, head: bytes):
        """ふつうの HTTP。配るのは1枚だけなので、道筋は見るが選ばない。"""
        line = head.split(b"\r\n", 1)[0]
        parts = line.split(b" ")
        method = parts[0] if parts else b""
        path = parts[1] if len(parts) > 1 else b"/"
        if method not in (b"GET", b"HEAD"):
            body = _response("405 Method Not Allowed", b"", "text/plain")
        elif path.split(b"?", 1)[0] in (b"/", b"/index.html", b"/mobile.html"):
            body = _response("200 OK", mobile_page(), "text/html; charset=utf-8")
        elif path.split(b"?", 1)[0] == b"/favicon.ico":
            body = _response("404 Not Found", b"", "text/plain")
        else:
            # 迷ったら入口へ。招待コードを少し間違えても画面が出るように。
            body = _response("200 OK", mobile_page(), "text/html; charset=utf-8")
        if method == b"HEAD":
            body = body.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
        sock.write(QByteArray(body))
        sock.flush()
        sock.disconnectFromHost()
