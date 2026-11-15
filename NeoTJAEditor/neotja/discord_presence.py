"""Discord の「NeoTJAEditor をプレイ中」表示(Rich Presence)。

PC で動いている Discord に、名前付きパイプ(\\\\.\\pipe\\discord-ipc-N)で
今の状態を送る。Discord 公式の仕組みで、外部のライブラリは使わない
(pypresence 等を足すと exe が太るうえ、使うのは送るだけの数十行で済む)。

送るのは「作譜中」の一言・経過時間・アイコンだけ。曲名や難易度は
どの設定でも送らない(利用者の指定 2026-11-15「曲名とかは一切出さない」)。

Discord が起動していない・閉じられた・パイプが壊れた、はどれも黙って
やり過ごし、少し待ってからつなぎ直す。送る処理は裏の糸で回すので、
エディタの描画は待たせない。
"""
import json
import os
import struct
import threading
import time
import uuid

#: Discord の開発者ページで作ったアプリの ID。秘密の値ではない
#: (クライアントに埋め込む前提の公開の番号)。名前とアイコンはそちら側で決まる。
CLIENT_ID = "1571481093914501224"
#: 開発者ページの Art Assets に登録した絵の名前。
LARGE_IMAGE = "neotja"

_OP_HANDSHAKE = 0
_OP_FRAME = 1
_OP_CLOSE = 2

#: つながらなかったとき、次に試すまでの秒数。
RETRY_SEC = 15.0


def _pipe_paths():
    return [r"\\.\pipe\discord-ipc-%d" % i for i in range(10)]


def build_activity(show_editing, start_ts):
    """Discord に送る activity。曲名などは入れない(ここ以外で足さないこと)。"""
    act = {
        "timestamps": {"start": int(start_ts)},
        "assets": {"large_image": LARGE_IMAGE, "large_text": "NeoTJAEditor"},
    }
    if show_editing:
        act["details"] = "作譜中"
    return act


class DiscordPresence:
    """裏の糸で Discord とつながり、表示を保つ係。

    update(enabled, show_editing) で状態を渡すだけでよい。同じ状態を何度
    渡しても送り直さない。stop() で表示を消して糸を止める。"""

    def __init__(self, client_id=CLIENT_ID):
        self.client_id = client_id
        self._pipe = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._want = (False, True)      # (enabled, show_editing)
        self._sent = None               # 最後に送れた状態(None = 未送信/切断)
        self._start_ts = time.time()
        self._thread = threading.Thread(
            target=self._run, name="discord-presence", daemon=True)
        self._thread.start()

    # --- 外から呼ぶ ---
    def update(self, enabled, show_editing=True):
        with self._lock:
            self._want = (bool(enabled), bool(show_editing))
        self._wake.set()

    def stop(self):
        self._stop = True
        self._wake.set()
        self._thread.join(timeout=2.0)

    # --- 糸の中 ---
    def _run(self):
        while not self._stop:
            with self._lock:
                want = self._want
            try:
                self._sync(want)
            except OSError:
                self._close()
            # 送れていればずっと寝て、状態が変わったら起きる。
            # つながっていないなら少し待ってつなぎ直す。
            timeout = None if self._sent == want else RETRY_SEC
            self._wake.wait(timeout)
            self._wake.clear()
        try:
            if self._pipe is not None:
                self._set_activity(None)
        except OSError:
            pass
        self._close()

    def _sync(self, want):
        enabled, show_editing = want
        if not enabled:
            if self._pipe is not None:
                # 表示を消してから切る(切るだけでも消えるが、確実にするため)。
                self._set_activity(None)
                self._close()
            self._sent = want
            return
        if self._sent == want and self._pipe is not None:
            return
        if self._pipe is None and not self._connect():
            self._sent = None
            return
        self._set_activity(build_activity(show_editing, self._start_ts))
        self._sent = want

    def _connect(self):
        for path in _pipe_paths():
            try:
                pipe = open(path, "r+b", buffering=0)
            except OSError:
                continue
            self._pipe = pipe
            try:
                self._send(_OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
                op, data = self._recv()
            except OSError:
                self._close()
                continue
            if op == _OP_FRAME and data.get("evt") == "READY":
                return True
            self._close()
        return False

    def _set_activity(self, activity):
        self._send(_OP_FRAME, {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": activity},
            "nonce": uuid.uuid4().hex,
        })
        # 返事を読んで捨てる(読まないとパイプが詰まる)。
        self._recv()

    def _send(self, op, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._pipe.write(struct.pack("<II", op, len(body)) + body)

    def _recv(self):
        head = self._read_exact(8)
        op, length = struct.unpack("<II", head)
        body = self._read_exact(length)
        if op == _OP_CLOSE:
            raise OSError("discord closed the connection")
        try:
            return op, json.loads(body.decode("utf-8"))
        except ValueError:
            return op, {}

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self._pipe.read(n - len(buf))
            if not chunk:
                raise OSError("discord pipe closed")
            buf += chunk
        return buf

    def _close(self):
        if self._pipe is not None:
            try:
                self._pipe.close()
            except OSError:
                pass
        self._pipe = None
        self._sent = None
