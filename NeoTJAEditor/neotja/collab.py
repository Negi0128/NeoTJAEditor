"""合作モード: 同じ譜面を複数の端末で一緒に書く(利用者の案 2026-10-16)。

**ペンは1本しか回らない。** 同時に書けるのは常に1人で、ほかの人は同じ譜面を
見ている。「これどう?」「いやこっちかも」と相談しながら交代する、という
使い方を狙っているため(利用者の指定)。こうすると衝突が原理的に起きないので、
OT も CRDT も要らない — 本文の同期は「○文字目から○文字を、この文字列へ
差し替える」を順に流すだけで足りる。

    ホスト(待ち受ける側)            ゲスト(繋ぐ側)
      本文の持ち主                    繋いだ時に本文を丸ごと貰う
      ペンの行き先を決める            「ちょうだい」と言える
      みんなへ配り直す                ペンを持っている間だけ書ける

運び方は **WebSocket 1本**。同じ家なら相手の IP へ直接、離れていれば
トンネル(後の段で同梱する)の入口へ。どちらも繋ぎ先の文字列が変わるだけで、
ここから下は1行も変わらない。

ずれの検出: 本文を変えるたびに通し番号(rev)を1つ増やし、ときどき本文の
ハッシュを添えて配る。食い違ったゲストには本文を丸ごと送り直す。ペンが
1本である以上ずれないはずだが、**ずれていないことを確かめずに使わせない**
ための保険。
"""

import hashlib
import json
import os
import sys
import time

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import (QAbstractSocket, QHostAddress,
                               QNetworkInterface)
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

#: 既定の待ち受け口。
DEFAULT_PORT = 7391
#: 本文のハッシュを添えて配る間合い(秒)。
SUM_EVERY_SEC = 5.0
#: カーソル位置を送る間合い(ms)。打つたびに送ると数が多すぎる。
CURSOR_MS = 80
#: 音源を切って送るときの1回ぶん(バイト)。大きすぎると画面が止まる。
SONG_CHUNK = 256 * 1024

#: 繋ぎそこねたときに試し直す回数と間合い(秒)。
#:
#: トンネルの名前は出来てすぐは引けない(実測 60 秒前後)。合言葉を
#: 貰った相手がせっかちに貼ることはあるので、1回で諦めない。
JOIN_TRIES = 6
JOIN_RETRY_SEC = 5.0

#: 合言葉(ホストが出す文字列)の頭。種類を取り違えないように付ける。
CODE_PREFIX = "neotja1:"


#: 合作で作る譜面を置くフォルダの名前(アプリの隣)。
COLLAB_DIR_NAME = "合作"


def collab_dir():
    """合作で使う譜面と音源の置き場(アプリの隣の「合作」フォルダ)。

    **合作中はここの中しか触らない**(利用者の指定 2026-10-16)。こうして
    おくと、

      ・相手から貰った譜面が、自分の譜面置き場に紛れ込まない
      ・うっかり別の曲を開いたまま打って、相手の譜面を壊す事故が起きない
      ・音源も同じ所へ置けるので、2人の持ち物がそろう

    無ければ作る。作れなければ None を返す(その場合は合作を始めない)。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, COLLAB_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return None
    return path


def in_collab_dir(path):
    """その道筋が合作フォルダの中か。"""
    root = collab_dir()
    if not root or not path:
        return False
    try:
        return os.path.commonpath([os.path.abspath(path), root]) == root
    except ValueError:          # ドライブが違うと比べられない
        return False


def collab_file_name(base, fallback="合作"):
    """合作で作るファイルの名前。

    **元の名前を残して、いつのものか分かるようにする**
    (利用者の指定 2026-10-17: 「ファイル名_合作_YYYYMMDD_HHMM.tja」)。
    曲名だけだと、同じ曲で何度も合作したときに見分けが付かない。"""
    import datetime
    stem = safe_file_name(base, fallback)
    return "%s_合作_%s" % (stem,
                           datetime.datetime.now().strftime("%Y%m%d_%H%M"))


def safe_file_name(title, fallback="合作"):
    """曲名からファイル名を作る。Windows で使えない字は落とす。"""
    name = "".join(c for c in str(title or "").strip()
                   if c not in '\\/:*?"<>|' and ord(c) >= 32)
    name = name.strip(" .")
    return name[:60] or fallback


def text_hash(text: str) -> str:
    """本文の指紋。短くてよい(ずれたことが分かればいい)。"""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def encode(msg: dict) -> str:
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":"))


def decode(raw: str):
    """届いた1通を dict にする。読めなければ None。

    **相手から来たものは信じない。** 種類(t)の無いもの、dict でないものは
    捨てる。ここを通ったあとの受け手は t で振り分けるだけにする。"""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
        return None
    return msg


def local_addresses():
    """この PC の、相手に教えられるアドレス一覧。

    127.0.0.1 と、繋がっていない口は外す。Tailscale を入れていれば
    100.x.x.x もここに出てくるので、遠くの相手にはそれを渡せばよい。"""
    out = []
    for ifc in QNetworkInterface.allInterfaces():
        flags = ifc.flags()
        if not (flags & QNetworkInterface.IsUp):
            continue
        if flags & QNetworkInterface.IsLoopBack:
            continue
        for ent in ifc.addressEntries():
            ip = ent.ip()
            if ip.protocol() != QHostAddress.IPv4Protocol:
                continue
            s = ip.toString()
            if s.startswith("169.254."):      # 相手に届かない自動割り当て
                continue
            out.append(s)
    return out


def make_code(host: str, port: int) -> str:
    """合言葉を作る。いまは「どこへ繋ぐか」をそのまま包んだだけ。

    トンネルを足す段で中身が URL に変わるが、**貼る側の手順は変えない**
    ようにここで包んでおく。"""
    return "%s%s:%d" % (CODE_PREFIX, host, int(port))


def parse_code(code: str):
    """合言葉を (繋ぎ先の URL, 表示用の名前) にする。読めなければ None。

    頭が無いもの、ただの IP、ws:// や https:// の URL も受ける — 貼り間違い
    より「そのまま貼った」ほうが多いため。"""
    s = (code or "").strip()
    if not s:
        return None
    if s.startswith(CODE_PREFIX):
        s = s[len(CODE_PREFIX):]
    if s.startswith("ws://") or s.startswith("wss://"):
        return s, s
    if s.startswith("https://"):
        return "wss://" + s[len("https://"):].rstrip("/"), s
    if s.startswith("http://"):
        return "ws://" + s[len("http://"):].rstrip("/"), s
    if ":" not in s:
        s = "%s:%d" % (s, DEFAULT_PORT)
    return "ws://" + s, s


class CollabSession(QObject):
    """合作の1回ぶん。ホストでもゲストでも、これ1つを使う。

    **本文そのものはここが持たない。** 持っているのは「誰が居るか」
    「誰がペンを持っているか」「いま何番目の変更か」だけ。本文の出し入れは
    繋いだ側(CollabBridge)が受け持つ — こちらを画面から切り離しておけば、
    窓を出さずに試せる。
    """

    #: 繋がった/切れた/人が増えた。画面の表示を直す合図。
    stateChanged = Signal()
    #: 本文を丸ごと受け取った(入った直後・ずれを直すとき)。(本文, rev)
    fullTextReceived = Signal(str, int)
    #: 差し替えを受け取った。(位置, 消す数, 入れる文字, rev)
    patchReceived = Signal(int, int, str, int)
    #: 相手の居場所が動いた。(名前, 文字の位置, 譜面時刻)
    #: 譜面時刻は作譜ペインを見ていないとき -1。
    presenceReceived = Signal(str, int, float)
    #: ペンの持ち主が変わった。(名前)
    penChanged = Signal(str)
    #: 誰かがペンを欲しがっている。(名前)
    penRequested = Signal(str)
    #: 画面の隅に出す知らせ。
    notice = Signal(str)
    #: 音源の受け渡しの進み具合。(済んだバイト, 全部, 送る側か)
    songProgress = Signal(int, int, bool)
    #: 音源を受け取り終えた。(保存した道筋)
    songReceived = Signal(str)
    #: 編集者が再生/一時停止した。(譜面時刻, 再生中か)
    playbackReceived = Signal(float, bool)

    def __init__(self, name="", parent=None):
        super().__init__(parent)
        #: 自分の名前(画面に出すだけ。取り違え防止)。
        self.name = name or "だれか"
        #: "off" / "host" / "guest"
        self.role = "off"
        #: ホスト: 繋がっているゲスト {QWebSocket: 名前}
        self._peers = {}
        #: ゲスト: ホストへの1本。
        self._sock = None
        self._server = None
        self._port = 0
        #: 本文の通し番号。本文を変えた人が1つ増やす。
        self.rev = 0
        #: ペンの持ち主の名前。
        self.pen = ""
        #: ずれを見張る札。
        self._sum_timer = QTimer(self)
        self._sum_timer.setInterval(int(SUM_EVERY_SEC * 1000))
        self._sum_timer.timeout.connect(self._send_sum)
        #: 本文を外から読むための呼び出し(CollabBridge が差す)。
        self.read_text = None
        #: 曲の見分け(名前と大きさ)。違う音源なら知らせるために持つ。
        self.song = ""
        #: 相手の居場所 {名前: (文字の位置, 譜面時刻 or None, 届いた時刻)}。
        self._here = {}
        #: 繋ぎ直しの残り回数と繋ぎ先。
        self._join_left = 0
        self._join_url = ""
        #: ホスト: 送れる音源の道筋 / 送っている途中の仕事。
        self._song_path = ""
        self._send_jobs = {}
        #: ゲスト: 受け取っている途中 [開いたファイル, 済, 全部, 名前]。
        self._recv = None
        #: ゲスト: 譜面が欲しがっている音源の名前。
        self._want_song = ""

    # ------------------------------------------------------------------
    # 出入り
    # ------------------------------------------------------------------
    def is_on(self) -> bool:
        return self.role != "off"

    def holds_pen(self) -> bool:
        """自分がいま書いていいか。合作していないときは当然いい。"""
        return (not self.is_on()) or (self.pen == self.name)

    def peer_names(self):
        if self.role == "host":
            return list(self._peers.values())
        return ["ホスト"] if self._sock is not None else []

    def start_host(self, port=DEFAULT_PORT):
        """待ち受ける。開けたら合言葉を返す。駄目なら None。"""
        self.stop()
        srv = QWebSocketServer("NeoTJAEditor", QWebSocketServer.NonSecureMode,
                               self)
        if not srv.listen(QHostAddress.Any, int(port)):
            self.notice.emit("合作: %d 番の口を開けませんでした(%s)"
                             % (port, srv.errorString()))
            srv.deleteLater()
            return None
        srv.newConnection.connect(self._on_new_peer)
        self._server = srv
        self._port = srv.serverPort()
        self.role = "host"
        self.pen = self.name          # 始めた人が最初に持つ
        self._sum_timer.start()
        self.stateChanged.emit()
        self.penChanged.emit(self.pen)
        return self.code()

    def port(self):
        """待ち受けている口。トンネルを立てる側が要る。"""
        return self._port

    def code(self):
        """ゲストに渡す合言葉。いちばん外に出せそうなアドレスで作る。"""
        if self.role != "host":
            return None
        addrs = local_addresses()
        return make_code(addrs[0] if addrs else "127.0.0.1", self._port)

    def codes(self):
        """アドレスが複数あるときの候補(どれが相手に届くかは分からない)。"""
        if self.role != "host":
            return []
        return [make_code(a, self._port) for a in local_addresses()]

    def join(self, code):
        """ホストへ繋ぎに行く。"""
        got = parse_code(code)
        if got is None:
            self.notice.emit("合作: 合言葉が読めません")
            return False
        url, shown = got
        self.stop()
        sock = QWebSocket()
        sock.connected.connect(self._on_connected)
        sock.disconnected.connect(self._on_guest_closed)
        sock.textMessageReceived.connect(
            lambda raw, s=sock: self._on_message(s, raw))
        sock.binaryMessageReceived.connect(self._on_binary)
        sock.errorOccurred.connect(
            lambda _e, s=sock: self._on_join_error(s))
        self._sock = sock
        self._join_url = url
        self._join_left = JOIN_TRIES
        self.role = "guest"
        self.pen = ""                 # ホストが教えてくれるまで持っていない
        self.notice.emit("合作: %s へ繋いでいます…" % shown)
        sock.open(QUrl(url))
        self.stateChanged.emit()
        return True

    def _on_join_error(self, sock):
        """繋がらなかった。トンネルが広まる前かもしれないので試し直す。"""
        if sock is not self._sock or self.role != "guest":
            return
        self._join_left -= 1
        if self._join_left <= 0:
            self.notice.emit("合作: 繋がりません(%s)"
                             % sock.errorString())
            return
        self.notice.emit(
            "合作: まだ繋がりません。%g 秒後にもう一度試します"
            "(あと %d 回)" % (JOIN_RETRY_SEC, self._join_left))
        QTimer.singleShot(int(JOIN_RETRY_SEC * 1000), self._retry_join)

    def _retry_join(self):
        sock = self._sock
        if sock is None or self.role != "guest" or not self._join_url:
            return
        if sock.state() != QAbstractSocket.UnconnectedState:
            return                    # もう繋がっている/繋ぎ中
        sock.open(QUrl(self._join_url))

    def stop(self):
        """やめる。繋がっているものは全部閉じる。"""
        self._sum_timer.stop()
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        for sock in list(self._peers):
            sock.close()
        self._peers.clear()
        if self._sock is not None:
            self._sock.close()
            self._sock.deleteLater()
            self._sock = None
        self._here.clear()
        self._abort_recv()
        for job in list(self._send_jobs.values()):
            try:
                job[0].close()
            except OSError:
                pass
        self._send_jobs.clear()
        self._join_left = 0
        self._join_url = ""
        # **送れる音源の覚え(_song_path)はここで消す。** 部屋ごとの持ち物
        # なので、出たら忘れるのが正しい。ただし start_host() は中で stop()
        # を呼ぶので、**登録は start_host() のあと**でないと消される
        # (2026-10-17: 先に登録していたため、ゲストが「ください」と言っても
        #  ホストに送るものが無かった)。
        self._song_path = ""
        was = self.role
        self.role = "off"
        self.pen = ""
        if was != "off":
            self.stateChanged.emit()
            self.penChanged.emit(self.pen)

    # ------------------------------------------------------------------
    # 送る
    # ------------------------------------------------------------------
    def _send(self, msg, skip=None):
        """全員へ1通。skip に渡した相手だけ飛ばす(言ってきた本人など)。"""
        raw = encode(msg)
        if self.role == "host":
            for sock in list(self._peers):
                if sock is not skip:
                    sock.sendTextMessage(raw)
        elif self._sock is not None and self._sock.isValid():
            self._sock.sendTextMessage(raw)

    def send_patch(self, pos, removed, inserted):
        """自分が本文を変えた。みんなへ配る。

        ペンを持っている人しか本文を変えないので、ここで番号を増やして
        よい(同じ番号が2人から出ることがない)。"""
        if not self.is_on():
            return
        self.rev += 1
        self._send({"t": "patch", "pos": int(pos), "rm": int(removed),
                    "ins": inserted, "rev": self.rev})

    def send_here(self, pos, chart_t=None):
        """「いま自分はここに居る」。文字の位置と譜面の時刻を一緒に流す。

        **譜面には一切効かない飾り。** 落ちても困らないので、数が多すぎる
        ときは黙って間引く(呼ぶ側が間合いを決める)。"""
        if not self.is_on():
            return
        msg = {"t": "here", "who": self.name, "pos": int(pos)}
        if chart_t is not None:
            msg["ct"] = float(chart_t)
        self._send(msg)

    def peers_here(self):
        """相手の居場所 {名前: (文字の位置, 譜面時刻 or None, 届いた時刻)}。"""
        return dict(self._here)

    def send_playback(self, seconds, playing):
        """編集者が再生/一時停止した。みんなへ配る。

        **編集者だけが配る。** 全員が配ると、受けた側がまた配り返して
        永遠に止まらなくなる(利用者の指定 2026-10-17: 編集者の
        再生/一時停止に合わせたい)。"""
        if self.is_on() and self.holds_pen():
            self._send({"t": "play", "at": float(seconds),
                        "on": bool(playing)})

    def request_pen(self):
        """「自分が編集モードになりたい」と言う。

        **誰も編集していなければ、断りなく自分のものになる。** 交代しながら
        書く使い方なので、相手がプレビューに下りているのに許可を待たされる
        のはただの邪魔(利用者の指定 2026-10-16)。誰かが編集中のときだけ、
        その人に代われるか尋ねる。"""
        if not self.is_on() or self.holds_pen():
            return
        if self.role == "host":
            if not self.pen:
                self.grant_pen(self.name)
            else:
                self.penRequested.emit(self.name)
        else:
            self._send({"t": "pen_req", "who": self.name})

    def give_pen_to(self, who):
        """**いまの編集者が**、誰かへ編集を渡す。

        ホストなら自分で決める。ゲストならホストへ頼む(決めるのは
        いつもホスト1人という形は崩さない)。"""
        if not self.is_on() or not self.holds_pen():
            return
        who = str(who or "")
        if self.role == "host":
            self.grant_pen(who)
        else:
            self._send({"t": "pen_give", "from": self.name, "to": who})

    def everyone(self):
        """いま部屋に居る人の名前(自分を含む)。"""
        return [self.name] + self.peer_names()

    def release_pen(self):
        """編集をやめてプレビューへ下りる。誰も編集していない状態になる。"""
        if not self.is_on() or not self.holds_pen():
            return
        if self.role == "host":
            self.grant_pen("")
        else:
            self._send({"t": "pen_free", "who": self.name})

    def grant_pen(self, who):
        """ペンの持ち主を決める。**ホストだけが決められる。**

        誰が持っているかを1箇所で決めないと、2人が同時に「自分が持って
        いる」と思い込んで本文が分岐する。"""
        if self.role != "host":
            return
        who = str(who or "")        # "" は「誰も編集していない」
        if who and who != self.name and who not in self._peers.values():
            return
        self.pen = who
        self._send({"t": "pen", "who": who})
        self.penChanged.emit(who)

    # ------------------------------------------------------------------
    # 音源の受け渡し
    #
    # 譜面だけ合っても、相手が同じ曲を持っていなければ何も聞こえない。
    # ホストが持っているものを、合作フォルダへそのまま送る(利用者の指定
    # 2026-10-16「ogg をそこにダウンロードしたらいい」)。
    #
    # **切って送る。** 10MB を1回で渡すと、その間ずっと画面が止まる。
    # 少しずつ送って、合間に画面を返す。
    # ------------------------------------------------------------------
    def song_offer(self, path):
        """ホスト: 送れる音源を覚えておく(入ってきた人に知らせる)。"""
        self._song_path = path or ""
        if path and os.path.exists(path):
            self.song = "%s/%d" % (os.path.basename(path),
                                   os.path.getsize(path))
        else:
            self.song = ""

    def _song_info(self):
        p = self._song_path
        if not p or not os.path.exists(p):
            return None
        return {"name": os.path.basename(p), "size": os.path.getsize(p)}

    def request_song(self, want_name=""):
        """ゲスト: 音源をくださいと言う。

        want_name は譜面の WAVE: が指している名前。**その名前で置く。**
        送り主の手元の名前で置くと、譜面が指す先と食い違って鳴らない
        (2026-10-17 の実測で実際に食い違った)。"""
        if self.role == "guest" and self._sock is not None:
            self._want_song = str(want_name or "")
            self._send({"t": "song_req"})

    def _start_song_send(self, sock):
        """ホスト: 音源を切りながら送り始める。"""
        info = self._song_info()
        if info is None or sock is None:
            return
        try:
            fh = open(self._song_path, "rb")
        except OSError:
            return
        sock.sendTextMessage(encode({"t": "song_begin", "name": info["name"],
                                     "size": info["size"]}))
        self._send_jobs[sock] = [fh, 0, info["size"]]
        QTimer.singleShot(0, lambda s=sock: self._pump_song(s))

    def _pump_song(self, sock):
        job = self._send_jobs.get(sock)
        if job is None:
            return
        fh, done, total = job
        if sock not in self._peers or not sock.isValid():
            fh.close()
            self._send_jobs.pop(sock, None)
            return
        chunk = fh.read(SONG_CHUNK)
        if not chunk:
            fh.close()
            self._send_jobs.pop(sock, None)
            sock.sendTextMessage(encode({"t": "song_end"}))
            self.notice.emit("合作: 音源を送り終えました")
            return
        sock.sendBinaryMessage(chunk)
        job[1] = done + len(chunk)
        self.songProgress.emit(job[1], total, True)
        QTimer.singleShot(0, lambda s=sock: self._pump_song(s))

    def _song_dest(self, name):
        root = collab_dir()
        return os.path.join(root, safe_file_name(name, "song")) if root else ""

    def _send_sum(self):
        """本文の指紋を配る(ホストだけ)。ずれていたら相手が言ってくる。"""
        if self.role != "host" or not self._peers:
            return
        if self.read_text is None:
            return
        try:
            text = self.read_text()
        except Exception:  # noqa: BLE001
            return
        self._send({"t": "sum", "rev": self.rev, "hash": text_hash(text)})

    def send_full(self, text, to=None):
        """本文を丸ごと送る(入った直後・ずれを直すとき)。"""
        msg = {"t": "full", "text": text, "rev": self.rev, "pen": self.pen,
               "song": self.song}
        if to is not None:
            to.sendTextMessage(encode(msg))
        else:
            self._send(msg)

    # ------------------------------------------------------------------
    # 受ける
    # ------------------------------------------------------------------
    def _on_binary(self, data):
        """音源の中身が届いた。受け取っている最中でなければ捨てる。"""
        rec = self._recv
        if rec is None:
            return
        fh, done, total, name = rec
        try:
            fh.write(bytes(data))
        except OSError:
            self._abort_recv("書き込めませんでした")
            return
        rec[1] = done + len(data)
        self.songProgress.emit(rec[1], total, False)

    def _abort_recv(self, why=""):
        rec, self._recv = self._recv, None
        if rec is not None:
            try:
                rec[0].close()
            except OSError:
                pass
        if why:
            self.notice.emit("合作: 音源を受け取れませんでした(%s)" % why)

    def _on_new_peer(self):
        while self._server is not None:
            sock = self._server.nextPendingConnection()
            if sock is None:
                break
            sock.textMessageReceived.connect(
                lambda raw, s=sock: self._on_message(s, raw))
            sock.disconnected.connect(lambda s=sock: self._on_peer_closed(s))
            self._peers[sock] = "だれか"
            self.stateChanged.emit()

    def _on_connected(self):
        self.notice.emit("合作: 繋がりました")
        self._send({"t": "hello", "who": self.name, "song": self.song})
        self.stateChanged.emit()

    def _on_peer_closed(self, sock):
        name = self._peers.pop(sock, None)
        self._here.pop(name, None)
        sock.deleteLater()
        if name:
            self.notice.emit("合作: %s が抜けました" % name)
        if self.pen and self.pen not in self.peer_names() + [self.name]:
            # 編集していた人が居なくなった。空ける(勝手にこちらが編集モードへ
            # 切り替わると驚くので、取りに行くのは人の操作に任せる)。
            self.grant_pen("")
            self.notice.emit("合作: 編集権は空いています"
                             "(メニューの「編集モードに切り替え」で取得できます)")
        self.stateChanged.emit()

    def _on_guest_closed(self):
        self.notice.emit("合作: ホストとの繋がりが切れました")
        self.role = "off"
        self.pen = ""
        self._sock = None
        self.stateChanged.emit()
        self.penChanged.emit(self.pen)

    def _on_message(self, sock, raw):
        msg = decode(raw)
        if msg is None:
            return
        t = msg.get("t")
        if t == "hello" and self.role == "host":
            self._peers[sock] = str(msg.get("who") or "だれか")
            self.notice.emit("合作: %s が入りました" % self._peers[sock])
            if self.read_text is not None:
                self.send_full(self.read_text(), to=sock)
            # いま誰が持っているかも教える。
            sock.sendTextMessage(encode({"t": "pen", "who": self.pen}))
            self.stateChanged.emit()
            return
        if t == "full":
            self.rev = int(msg.get("rev") or 0)
            self.pen = str(msg.get("pen") or "")
            self.fullTextReceived.emit(str(msg.get("text") or ""), self.rev)
            self.penChanged.emit(self.pen)
            return
        if t == "patch":
            self.rev = int(msg.get("rev") or (self.rev + 1))
            self.patchReceived.emit(int(msg.get("pos") or 0),
                                    int(msg.get("rm") or 0),
                                    str(msg.get("ins") or ""), self.rev)
            if self.role == "host":
                self._send(msg, skip=sock)      # ほかのゲストへも配る
            return
        if t == "here":
            who = str(msg.get("who") or "")
            if who and who != self.name:
                ct = msg.get("ct")
                self._here[who] = (int(msg.get("pos") or 0),
                                   None if ct is None else float(ct),
                                   time.monotonic())
                self.presenceReceived.emit(
                    who, int(msg.get("pos") or 0),
                    -1.0 if ct is None else float(ct))
            if self.role == "host":
                self._send(msg, skip=sock)   # ほかのゲストへも配る
            return
        if t == "play":
            self.playbackReceived.emit(float(msg.get("at") or 0.0),
                                       bool(msg.get("on")))
            if self.role == "host":
                self._send(msg, skip=sock)
            return
        if t == "pen_give":
            # **いまの編集者からの受け渡し。** ホストでなくても
            # 自分の番を人に譲れるようにする(利用者の指定
            # 2026-10-17)。決めるのはホストのままなので、ホストが
            # 受けて配り直す — 2人が同時に「自分が編集者」と
            # 思い込む形は作らない。
            if self.role == "host":
                who_from = str(msg.get("from") or "")
                if who_from == self.pen:
                    self.grant_pen(str(msg.get("to") or ""))
            return
        if t == "pen_req" and self.role == "host":
            who = str(msg.get("who") or "")
            if not self.pen:
                self.grant_pen(who)     # 空いているなら断りなく渡す
            else:
                self.penRequested.emit(who)
            return
        if t == "pen_free" and self.role == "host":
            # ゲストが自分からプレビューへ下りた。
            if self.pen == str(msg.get("who") or ""):
                self.grant_pen("")
            return
        if t == "pen":
            self.pen = str(msg.get("who") or "")
            self.penChanged.emit(self.pen)
            return
        if t == "song_begin" and self.role == "guest":
            self._abort_recv()
            # 置く名前は**譜面が指している名前**を優先する。
            name = self._want_song or str(msg.get("name") or "song")
            dst = self._song_dest(name)
            if not dst:
                return
            try:
                fh = open(dst + ".part", "wb")
            except OSError:
                self.notice.emit("合作: 音源を置く場所がありません")
                return
            self._recv = [fh, 0, int(msg.get("size") or 0), name]
            self.notice.emit("合作: 音源を受け取っています…")
            return
        if t == "song_end" and self.role == "guest":
            rec, self._recv = self._recv, None
            if rec is None:
                return
            fh, done, total, name = rec
            fh.close()
            dst = self._song_dest(name)
            try:
                if os.path.exists(dst):
                    os.remove(dst)
                os.replace(dst + ".part", dst)
            except OSError as e:
                self.notice.emit("合作: 音源を置けませんでした(%s)" % e)
                return
            self.songReceived.emit(dst)
            return
        if t == "song_req" and self.role == "host":
            self._start_song_send(sock)
            return
        if t == "sum" and self.role == "guest":
            if self.read_text is None:
                return
            want = str(msg.get("hash") or "")
            if want and text_hash(self.read_text()) != want:
                self.notice.emit("合作: ずれを見つけたので貰い直します")
                self._send({"t": "resync"})
            return
        if t == "resync" and self.role == "host":
            if self.read_text is not None:
                self.send_full(self.read_text(), to=sock)
            return
