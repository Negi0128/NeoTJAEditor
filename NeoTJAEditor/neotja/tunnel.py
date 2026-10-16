"""合作モード: 離れた相手と繋ぐための入口(トンネル)。

**相手に何かを入れてもらわない**ための仕掛け(利用者の指定 2026-10-16
「別のアプリダウンロードするの手間じゃないかな」)。ホストのアプリが
cloudflared を起こし、手元の待ち受け口への入口を外に1つ作る。相手は
出てきた URL を合言葉として貼るだけでよく、ルーターも触らない
(どちらも外へ繋ぎに行くだけなので、穴を開ける側が誰も居ない)。

    [ホストのアプリ] ←ws← [cloudflared] ←https← Cloudflare ←── [ゲスト]
         127.0.0.1:7391

cloudflared は同梱する(tools/cloudflared.exe)。無ければ同じ家の中だけの
合作になる — 入口が作れないだけで、ほかは何も変わらない。

**ここは譜面に触らない。** 入口の URL を1つ返すだけの係で、合作の中身
(本文・編集モード・相手のカーソル)は collab.py が持っている。
"""

import os
import re
import shutil
import sys
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtNetwork import QDnsLookup

#: 同梱する実行ファイルの置き場(リポジトリでも exe の中でも同じ並び)。
TOOLS_DIR = "tools"
EXE_NAME = "cloudflared.exe" if os.name == "nt" else "cloudflared"
#: 出てくる URL。cloudflared が標準エラーへ枠付きで出す。
def _now():
    return time.monotonic()


URL_RE = re.compile(r"https://[a-z0-9][a-z0-9\-]*\.trycloudflare\.com")
#: 入口ができるまで待つ時間(秒)。これを過ぎたら諦めて家の中だけにする。
#:
#: **URL が出た≠まだ使えない。** cloudflared は URL を数秒で出すが、その
#: 名前が世界の DNS に載るのはもっと後(実測 2026-10-16: URL が出てから
#: 60.4 秒後にようやく引けた。引けた瞬間 443 は即繋がった)。出た瞬間の
#: URL を相手に渡すと「繋がりません」になるので、**引けるまで待ってから**
#: 合言葉として出す。余裕を見て3分。
READY_TIMEOUT_SEC = 180.0
#: 名前が引けるか試す間合い(秒)。
DNS_RETRY_SEC = 3.0


def _candidates():
    """cloudflared を探す場所を、優先する順に並べる。"""
    out = []
    mei = getattr(sys, "_MEIPASS", None)
    if mei:                                   # 凍結した exe の中(同梱)
        out.append(os.path.join(mei, TOOLS_DIR, EXE_NAME))
    here = os.path.dirname(os.path.abspath(__file__))
    out.append(os.path.join(os.path.dirname(here), TOOLS_DIR, EXE_NAME))
    out.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                            TOOLS_DIR, EXE_NAME))
    return out


def find_cloudflared():
    """同梱 → アプリの隣 → PATH の順に探す。無ければ None。"""
    for p in _candidates():
        if os.path.isfile(p):
            return p
    return shutil.which("cloudflared")


def parse_url(text):
    """cloudflared の吐き出しから入口の URL を拾う。無ければ None。"""
    m = URL_RE.search(text or "")
    return m.group(0) if m else None


class Tunnel(QObject):
    """cloudflared を1つ飼う。

    使い方は start(port) → ready(URL) か failed(理由)。stop() で必ず止める
    (止め忘れると、アプリを閉じても入口が開いたままになる)。
    """

    #: 入口ができて、**外から本当に引ける**ようになった。(URL)
    ready = Signal(str)
    #: 作れなかった / 途中で落ちた。(理由)
    failed = Signal(str)
    #: URL は出たが、まだ名前が引けない。(URL, 待っている秒数)
    waiting = Signal(str, float)

    def __init__(self, parent=None, cmd=None):
        """cmd を渡すとそれを起こす(試験で偽物を使うため)。"""
        super().__init__(parent)
        self._cmd = cmd
        self._proc = None
        self._url = None
        self._buf = ""
        #: 名前が引けたか(引けて初めて相手に渡せる)。
        self._ok = False
        self._dns = None
        self._t_url = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(READY_TIMEOUT_SEC * 1000))
        self._timer.timeout.connect(self._on_timeout)

    def url(self):
        return self._url

    def is_running(self):
        return (self._proc is not None
                and self._proc.state() != QProcess.NotRunning)

    def start(self, port):
        """手元の port への入口を作る。起こせたら True。"""
        self.stop()
        cmd = self._cmd
        if cmd is None:
            exe = find_cloudflared()
            if not exe:
                self.failed.emit(
                    "トンネルの実行ファイル(%s)が見つかりません。"
                    "同じ家の中だけの合作になります。" % EXE_NAME)
                return False
            cmd = [exe, "tunnel", "--no-autoupdate",
                   "--url", "http://127.0.0.1:%d" % int(port)]
        self._buf = ""
        self._url = None
        self._ok = False
        proc = QProcess(self)
        # cloudflared は URL を標準エラーへ出す。分けずにまとめて読む。
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_output)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start(cmd[0], cmd[1:])
        self._timer.start()
        return True

    def stop(self):
        self._timer.stop()
        proc, self._proc = self._proc, None
        self._url = None
        self._ok = False
        dns, self._dns = self._dns, None
        if dns is not None:
            try:
                dns.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            dns.abort()
            dns.deleteLater()
        if proc is None:
            return
        try:
            proc.readyReadStandardOutput.disconnect()
            proc.finished.disconnect()
            proc.errorOccurred.disconnect()
        except (RuntimeError, TypeError):
            pass
        if proc.state() != QProcess.NotRunning:
            proc.kill()
            proc.waitForFinished(2000)
        proc.deleteLater()

    # ------------------------------------------------------------------
    def _on_output(self):
        proc = self._proc
        if proc is None:
            return
        try:
            chunk = bytes(proc.readAllStandardOutput()).decode(
                "utf-8", "replace")
        except Exception:  # noqa: BLE001
            return
        # **URL が行をまたぐことがある**(枠線付きで出すため)。繋げて見る。
        self._buf = (self._buf + chunk)[-8000:]
        if self._url is not None:
            return
        url = parse_url(self._buf)
        if url:
            self._url = url
            # まだ渡せない。名前が引けるまで待つ(_check_dns)。
            self._t_url = _now()
            self._check_dns()

    # ------------------------------------------------------------------
    # 名前が引けるまで待つ
    #
    # URL が出ても、その名前が世界の DNS に載るまでは誰も辿り着けない
    # (実測 60 秒前後)。**引けるのを確かめてから**合言葉として出す。
    # ------------------------------------------------------------------
    def _check_dns(self):
        url = self._url
        if not url or self._ok:
            return
        host = url.split("://", 1)[1].split("/", 1)[0]
        lk = QDnsLookup(QDnsLookup.A, host, self)
        lk.finished.connect(lambda l=lk: self._on_dns(l))
        self._dns = lk
        lk.lookup()

    def _on_dns(self, lk):
        err = lk.error()
        recs = lk.hostAddressRecords()
        lk.deleteLater()
        if self._dns is lk:
            self._dns = None
        if self._url is None or self._ok:
            return
        if err == QDnsLookup.NoError and recs:
            self._ok = True
            self._timer.stop()
            self.ready.emit(self._url)
            return
        # まだ。少し待ってもう一度。
        self.waiting.emit(self._url, _now() - self._t_url)
        QTimer.singleShot(int(DNS_RETRY_SEC * 1000), self._check_dns)

    def _on_timeout(self):
        if self._ok:
            return
        url, self._url = self._url, None
        self.stop()
        if url:
            self.failed.emit(
                "入口の名前が %g 秒たっても世界に広まりませんでした。"
                "同じ家の中だけの合作になります。" % READY_TIMEOUT_SEC)
        else:
            self.failed.emit(
                "トンネルの入口が %g 秒で作れませんでした。"
                "同じ家の中だけの合作になります。" % READY_TIMEOUT_SEC)

    def _on_finished(self, _code, _status):
        if self._ok:
            self._url = None
            self._ok = False
            self.failed.emit("トンネルが切れました。合言葉は使えません。")

    def _on_error(self, _err):
        proc = self._proc
        self._timer.stop()
        self.failed.emit("トンネルを起こせませんでした(%s)"
                         % (proc.errorString() if proc else "不明"))
