import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal

from neotja.child_env import child_env
from neotja.constants import VERSION

RELEASES_API_URL = "https://api.github.com/repos/Negi0128/NeoTJAEditor/releases/latest"
ASSET_NAME = "NeoTJAEditor.exe"
#: 再生・録画専用の NeoTJAPlayer。**Editor と一緒に落として隣へ置く。**
#: 中身は Editor と同じ Qt / ffmpeg / PortAudio を積んだ別の exe で、
#: Player 固有のコードは 110KB しかない(つまり 84MB は重複)。それでも別々に
#: しているのは利用者の選択。片方だけ新しくなると、設定ファイルも再生画面も
#: 共有しているぶん原因の分かりにくい食い違いになるので、**更新のたびに
#: 必ず両方を入れ替える**。
PLAYER_ASSET_NAME = "NeoTJAPlayer.exe"
_USER_AGENT = "NeoTJAEditor-Updater"
#: 1回の読み書きで待つ上限(秒)。**総時間ではなくソケット1操作ぶん。**
#: 30 秒では 84MB の途中で read が切れる回線があった(実測: 3回中2回失敗)。
DOWNLOAD_TIMEOUT = 60
#: 落とし直す回数。回線側の一時的な切断は珍しくないので、1回で諦めない。
DOWNLOAD_TRIES = 3
#: 落とし直す前に置く秒数(回を追うごとに伸ばす)。
RETRY_WAIT = 3.0

#: API の版。付けておくと GitHub 側の既定が変わっても応答の形が揺れない。
_API_VERSION = "2022-11-28"
_API_ROOT = "https://api.github.com/"
#: リリースの添付ファイルを API で落とすときの URL の頭。
#: 非公開リポジトリでは browser_download_url(github.com/.../download/...)が
#: 404 になるので、こちら(releases/assets/<id>)を Accept: octet-stream で叩く。
_ASSET_API_PREFIX = "https://api.github.com/repos/Negi0128/NeoTJAEditor/releases/assets/"
#: 鍵つきで断られたとき、鍵なしでやり直す状態コード。リポジトリがまだ
#: 公開のうちに鍵が失効・取り消しされても、更新が止まらないようにするため。
_AUTH_FALLBACK_CODES = (401, 403, 404)


# ---------------------------------------------------------------------------
# 非公開リポジトリから更新を取るための鍵(アクセストークン)。
#
# 鍵はリポジトリに入れない。組み立てのときに update_token.txt(gitignore 済み)
# から build_version.write_update_token が neotja/_update_token.py(これも
# gitignore 済み)を作り、spec がそれを exe に積む。無ければ鍵なしで動く。
#
# **難読化は「ぱっと見で読めない」程度のもの。** exe を持っている人なら誰でも
# 取り出せる。なので鍵は必ず「このリポジトリ1つだけ・Contents の読み取りだけ」
# の fine-grained token にすること。漏れても読めるのはこのリポジトリだけになる。
# ---------------------------------------------------------------------------

def _decode_token(data: str, key: str) -> str:
    """_update_token.py の中身を元に戻す(XOR + base64)。"""
    import base64
    raw = base64.b64decode(data.encode("ascii"))
    k = key.encode("ascii")
    return bytes(b ^ k[i % len(k)] for i, b in enumerate(raw)).decode("utf-8").strip()


def _load_token() -> str:
    """積まれている鍵を返す。無ければ空文字。"""
    try:
        from neotja import _update_token  # 組み立て時に作られる。普段は無い
    except ImportError:
        return ""
    try:
        return _decode_token(_update_token.DATA, _update_token.KEY)
    except Exception:  # noqa: BLE001 — 壊れていても鍵なしで動けばよい
        return ""


_TOKEN = None


def get_token() -> str:
    """鍵(1回だけ読む)。**画面にもログにも出さないこと。**"""
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = _load_token()
    return _TOKEN


def scrub(message) -> str:
    """エラー文から鍵を消す。利用者に見せる文・記録に残す文は必ずここを通す。"""
    text = str(message)
    token = get_token()
    if token:
        text = text.replace(token, "***")
    return re.sub(r"(?i)\b(bearer|token)(\s+)[A-Za-z0-9_\-.]{8,}", r"\1\2***", text)


class _DropAuthRedirect(urllib.request.HTTPRedirectHandler):
    """転送先へ Authorization を持っていかない。

    API の添付ファイルは S3 などの別ホストへ 302 で飛ばされる。その先に鍵を
    渡す理由は無いし、署名つき URL に Authorization が付くと S3 は 400 を返す。
    鍵は add_unredirected_header で付けているので本来ここで落ちるが、念のため
    両方から消しておく。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            for table in (new.headers, new.unredirected_hdrs):
                for name in list(table):
                    if name.lower() == "authorization":
                        del table[name]
        return new


def _open(url, timeout, token="", accept=None):
    """1回ぶんの GET。token があれば Authorization を付ける(転送先には渡さない)。"""
    headers = {"User-Agent": _USER_AGENT}
    if url.startswith(_API_ROOT):
        headers["X-GitHub-Api-Version"] = _API_VERSION
        headers["Accept"] = accept or "application/vnd.github+json"
    elif accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    if token:
        req.add_unredirected_header("Authorization", "Bearer " + token)
    opener = urllib.request.build_opener(_DropAuthRedirect)
    return opener.open(req, timeout=timeout)


def open_github(url, timeout, accept=None):
    """GitHub への GET。鍵があれば使い、401/403/404 なら鍵なしでやり直す。

    鍵が無ければ最初から鍵なし(今までどおり)。返すのは応答(with で使う)。
    失敗したときの例外文は鍵を消したものにして投げ直す。"""
    token = get_token() if url.startswith(_API_ROOT) else ""
    try:
        if token:
            try:
                return _open(url, timeout, token, accept)
            except urllib.error.HTTPError as e:
                if e.code not in _AUTH_FALLBACK_CODES:
                    raise
                # 公開のうちは鍵なしで通る。非公開になったあとでここへ来るなら
                # 鍵が切れているか、権限が足りない。
        return _open(url, timeout, "", accept)
    except urllib.error.HTTPError as e:
        raise IOError(scrub("HTTP Error %d: %s" % (e.code, e.reason))) from None
    except Exception as e:  # noqa: BLE001
        cleaned = scrub(e)
        if cleaned != str(e):
            raise IOError(cleaned) from None
        raise


def fetch_json(url, timeout=10):
    """API の JSON を取る(鍵があれば使う)。"""
    with open_github(url, timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def asset_url(asset) -> str:
    """リリースの添付ファイルを落とす URL。

    鍵があるときは API の URL(非公開でも落とせる)、無ければ今までどおり
    browser_download_url。"""
    api = asset.get("url", "") or ""
    if get_token() and api.startswith(_ASSET_API_PREFIX):
        return api
    return asset.get("browser_download_url", "") or ""


def open_asset(url, timeout):
    """添付ファイルの GET。API の URL なら Accept: octet-stream を付ける。"""
    if url.startswith(_ASSET_API_PREFIX):
        return open_github(url, timeout, accept="application/octet-stream")
    return open_github(url, timeout)


def _version_tuple(v: str):
    v = v.strip()
    if v.startswith("v") or v.startswith("V"):
        v = v[1:]
    parts = []
    for p in v.split("."):
        # Take only the LEADING run of digits, so a suffixed segment like
        # "0-beta1" reads as 0, not 01(=1). "".join(all digits) used to fuse
        # the pre-release "1" onto the "0" and rank v6.2.0-beta1 above v6.2.0.
        m = re.match(r"\d+", p)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def is_newer(remote_tag: str, local_version: str = VERSION) -> bool:
    return _version_tuple(remote_tag) > _version_tuple(local_version)


class UpdateCheckWorker(QThread):
    """Checks GitHub Releases for a newer tagged version than the running
    build. Network I/O only (urllib, no extra dependency); safe to run at
    startup without blocking the UI."""

    update_available = Signal(str, str, str, str)  # tag, notes, exe_url, player_url
    up_to_date = Signal()
    failed = Signal(str)

    def run(self):
        try:
            data = fetch_json(RELEASES_API_URL)

            tag = data.get("tag_name", "")
            if not tag or not is_newer(tag):
                self.up_to_date.emit()
                return

            urls = {}
            for asset in data.get("assets", []):
                name = asset.get("name")
                if name in (ASSET_NAME, PLAYER_ASSET_NAME):
                    urls[name] = asset_url(asset)

            self.update_available.emit(tag, data.get("body", ""),
                                       urls.get(ASSET_NAME, ""),
                                       urls.get(PLAYER_ASSET_NAME, ""))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(scrub(e))


class UpdateDownloadWorker(QThread):
    """新しい exe を落とす(frozen ビルドのみ)。UI を固めないよう別スレッド。

    **Editor と Player の2本を続けて落とす。** 片方だけ新しくなると、設定
    ファイルも再生画面も共有しているぶん原因の分かりにくい食い違いになる。
    進捗は2本を通した割合で出す(Content-Length が両方分かれば正確な割合、
    分からなければ本数で等分)。

    Player 側だけ失敗しても **Editor の更新は止めない。** Editor が新しく
    なること自体は成立しているし、そこで丸ごと諦めると「更新できません」
    としか言えなくなる。失敗は player_error に残して呼び出し側が伝える。
    """

    progress = Signal(int)          # 0-100、総量が分からなければ -1
    stage = Signal(str)             # いま何を落としているか(進捗ダイアログの文言)
    finished_ok = Signal(str, str)  # 落とした exe のパス (Editor, Player)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, asset_url: str, player_url: str = "", parent=None, require_player=False):
        super().__init__(parent)
        self.asset_url = asset_url
        self.require_player = require_player
        self.player_url = player_url
        #: Player だけ落とせなかったときの理由。Editor は更新される。
        self.player_error = ""
        self._what = ""
        self._cancelled = False

    def cancel(self):
        """Cooperative cancel - the run loop checks this between chunks. Used
        instead of QThread.terminate(), which can strike mid-write and corrupt
        interpreter state or leave the fixed dest path locked (bricking the
        next update attempt with PermissionError). Matches the cancellation
        pattern the other workers in this app already use."""
        self._cancelled = True

    def _fetch(self, url, dest, base, span):
        """url を dest へ落とす。**失敗したら落とし直す。**

        進捗は全体の base%〜(base+span)% に写す。返すのは落としたバイト数。
        中止されたら None。

        回線側の一時的な切断で丸ごと諦めていたが、実測で 84MB の取得は
        3回中2回が read タイムアウトで落ちる回線があった。1回勝負にする
        理由が無いので DOWNLOAD_TRIES 回まで落とし直す。"""
        last = None
        for attempt in range(DOWNLOAD_TRIES):
            if self._cancelled:
                return None
            if attempt:
                # 少し待ってから。詰めて叩き直しても同じ切れ方をする。
                end = time.monotonic() + RETRY_WAIT * attempt
                while time.monotonic() < end:
                    if self._cancelled:
                        return None
                    time.sleep(0.1)
                self.stage.emit("%s (%d 回目)" % (self._what, attempt + 1))
            try:
                return self._fetch_once(url, dest, base, span)
            except Exception as e:  # noqa: BLE001
                if self._cancelled:
                    return None
                last = e
        raise last

    def _fetch_once(self, url, dest, base, span):
        with open_asset(url, DOWNLOAD_TIMEOUT) as resp, open(dest, "wb") as f:
            # Prefer the Content-Length header (resp.length decreases as we
            # read, so capture the expected total up front for validation).
            try:
                expected = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                expected = 0
            total = expected or (resp.length or 0)
            read = 0
            while True:
                if self._cancelled:
                    return None
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                if total:
                    self.progress.emit(int(base + span * read / total))
                else:
                    self.progress.emit(-1)

        # A truncated download (dropped connection) would otherwise be
        # copied over the running exe and brick it - a onefile PyInstaller
        # build stores its archive at the tail, so a short file fails at
        # startup with e.g. "No module named 'PySide6.QtGui'". Validate the
        # size against whatever total we know (Content-Length or the
        # response's initial length) and sanity-check the PE header before
        # letting the caller apply it.
        if total and read != total:
            raise IOError(
                f"ダウンロードが不完全です ({read}/{total} バイト)。"
                "通信状況を確認してもう一度お試しください。"
            )
        with open(dest, "rb") as f:
            if f.read(2) != b"MZ":
                raise IOError("ダウンロードしたファイルが壊れています。もう一度お試しください。")
        return read

    @staticmethod
    def _discard(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def run(self):
        if self.require_player and not self.player_url:
            self.failed.emit('この版にはNeoTJAPlayerがありません。')
            return
        dest = os.path.join(tempfile.gettempdir(), "NeoTJAEditor_update.exe")
        pdest = os.path.join(tempfile.gettempdir(), "NeoTJAPlayer_update.exe")
        # 2本落とすときは前半/後半で半分ずつ。Player が無いリリース(旧版へ
        # 戻したときなど)では Editor だけで 100%。
        span = 50 if self.player_url else 100
        try:
            self._what = "NeoTJAEditor をダウンロード中..."
            self.stage.emit(self._what)
            if self._fetch(self.asset_url, dest, 0, span) is None:
                # The with-block closed both handles; now the partial file can
                # be removed so the fixed path isn't left half-written/locked.
                self._discard(dest)
                self.cancelled.emit()
                return
        except Exception as e:
            if self._cancelled:
                # A read raised because we're tearing down mid-cancel - not a
                # real failure to report to the user.
                self._discard(dest)
                self.cancelled.emit()
                return
            self.failed.emit(scrub(e))
            return

        player_path = ""
        if self.player_url:
            self._what = "NeoTJAPlayer をダウンロード中..."
            self.stage.emit(self._what)
            try:
                if self._fetch(self.player_url, pdest, 50, 50) is None:
                    self._discard(dest)
                    self._discard(pdest)
                    self.cancelled.emit()
                    return
                player_path = pdest
            except Exception as e:  # noqa: BLE001
                # **ここで諦めない。** Editor は落とせているので、そちらの
                # 更新は成立する。Player が入らなかったことだけ伝える。
                if self._cancelled:
                    self._discard(dest)
                    self._discard(pdest)
                    self.cancelled.emit()
                    return
                self._discard(pdest)
                if self.require_player:
                    self._discard(dest)
                    self.failed.emit('NeoTJAPlayerの取得に失敗しました。\n' + scrub(e))
                    return
                self.player_error = scrub(e)

        self.finished_ok.emit(dest, player_path)


def backup_version_settings(settings_path, config):
    """バージョン変更前の設定を退避。元ファイルは書き換えない。"""
    from pathlib import Path
    from datetime import datetime
    folder = Path(settings_path).parent / 'version-backups'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    path = folder / ('settings-v%s-%s.json' % (VERSION, stamp))
    with path.open('x', encoding='utf-8') as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
    return path


class PlayerFetchWorker(QThread):
    """NeoTJAPlayer.exe だけを、いま公開されているリリースから落とす。

    **なぜ要るのか**: 更新のときに2本落とす仕組みを入れても、その処理を走らせる
    のは**利用者の環境に既に入っている古い版の updater** で、そちらは
    NeoTJAEditor.exe しか見ていない。つまり「Player を配り始める版」への更新
    そのものでは Player が届かない。届くのは、その次の更新から。

    そこで「NeoTJAPlayer を別ウィンドウで開く」を押したのに exe が無いときは、
    その場で落とせるようにする。押した人だけが払う形なので、Player を使わない
    人に 84MB を負わせることもない。

    落とし先は **exe の隣**(= 起動時に探す場所)。ここに置けなければ意味が
    無いので、書けないときは素直に失敗として伝える。"""

    progress = Signal(int)
    finished_ok = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, dest_dir: str, parent=None):
        super().__init__(parent)
        self.dest_dir = dest_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            data = fetch_json(RELEASES_API_URL)
            url = ""
            for asset in data.get("assets", []):
                if asset.get("name") == PLAYER_ASSET_NAME:
                    url = asset_url(asset)
                    break
            if not url:
                self.failed.emit("リリースに %s がありませんでした。" % PLAYER_ASSET_NAME)
                return
        except Exception as e:  # noqa: BLE001
            self.failed.emit(scrub(e))
            return

        final = os.path.join(self.dest_dir, PLAYER_ASSET_NAME)
        # 途中で失敗しても中途半端な exe が残らないよう、隣に .part で書いてから
        # 名前を変える。ここで書けなければ置き場所そのものが駄目なので、その
        # 時点で失敗と分かる(%TEMP% へ落としてから気づくより早い)。
        part = final + ".part"
        last = None
        for attempt in range(DOWNLOAD_TRIES):
            if self._cancelled:
                self._rm(part)
                self.cancelled.emit()
                return
            if attempt:
                end = time.monotonic() + RETRY_WAIT * attempt
                while time.monotonic() < end:
                    if self._cancelled:
                        self._rm(part)
                        self.cancelled.emit()
                        return
                    time.sleep(0.1)
            try:
                if self._fetch_once(url, part) is None:   # 中止
                    self._rm(part)
                    self.cancelled.emit()
                    return
                os.replace(part, final)
                self.finished_ok.emit(final)
                return
            except Exception as e:  # noqa: BLE001
                self._rm(part)
                if self._cancelled:
                    self.cancelled.emit()
                    return
                last = e
        self.failed.emit(scrub(last))

    def _fetch_once(self, url, part):
        """1回ぶんの取得。中止されたら None、済んだらバイト数。"""
        with open_asset(url, DOWNLOAD_TIMEOUT) as resp, open(part, "wb") as f:
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            read = 0
            while True:
                if self._cancelled:
                    break
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                self.progress.emit(int(read * 100 / total) if total else -1)
        if self._cancelled:
            return None
        if total and read != total:
            raise IOError("ダウンロードが不完全です (%d/%d バイト)。" % (read, total))
        with open(part, "rb") as f:
            if f.read(2) != b"MZ":
                raise IOError("ダウンロードしたファイルが壊れています。")
        return read

    @staticmethod
    def _rm(path):
        try:
            os.remove(path)
        except OSError:
            pass


ERROR_MARKER_PATH = os.path.join(tempfile.gettempdir(), "neotja_update_error.txt")


def pop_update_error():
    """Return the failure info left behind by a previous update attempt (and
    clear it), or None. The batch that applies the update runs after this
    process is gone, so a failure there can only be reported on the next
    launch."""
    try:
        if not os.path.exists(ERROR_MARKER_PATH):
            return None
        with open(ERROR_MARKER_PATH, "r", encoding="utf-8", errors="replace") as f:
            info = f.read().strip()
        os.remove(ERROR_MARKER_PATH)
        return info or "(詳細不明)"
    except OSError:
        return None


def _is_same_image(pid: int, exe_path: str) -> bool:
    """pid が exe_path と同じ実行ファイルのプロセスか。

    onefile のブートローダ親を見分けるためだけに使う。判定できなければ False
    (= 待ち対象にしない)。ここで誤って無関係な親を待つと、その親が終わるまで
    更新が始まらなくなるので、疑わしいときは待たない側に倒す。"""
    name = os.path.basename(exe_path)
    if not name:
        return False
    try:
        tasklist = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                "System32", "tasklist.exe")
        out = subprocess.run(
            [tasklist, "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.decode("cp932", errors="replace")
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return name.lower() in out.lower()


def apply_update(new_exe_path: str, new_player_path: str = ""):
    """Self-replace pattern for a single-file PyInstaller exe: it can't
    overwrite itself while running, so a tiny batch script waits for this
    process to exit, copies the new exe over it, then relaunches it. Only
    meaningful when frozen; callers should check sys.frozen first.

    The copy is the fragile step - the exe can be locked (antivirus scanning
    the freshly downloaded file, a handle not yet released after exit) or sit
    somewhere unwritable (Program Files). It used to be fired once with its
    output discarded and its exit code ignored, so a failure silently relaunched
    the OLD exe and deleted the download: the update simply never happened and
    said nothing. Now it retries, and on give-up it keeps the download and
    leaves a marker that the relaunched app reports via pop_update_error().

    onefile ビルドは「ブートローダの親」と「実際に動く子」の2プロセスになる。
    _MEIxxxxx への展開を持っているのは親で、終了時にそれを消すのも親。以前は
    子の PID だけを待っていたので、親がまだ後片付けをしている最中に新しい exe を
    起動していた。Windows は PID をすぐ再利用するうえ、_MEIxxxxx の名前は PID
    由来なので、新プロセスが同じ名前のフォルダに展開してしまうと、直後に古い親の
    後片付けがその中身を消してしまう。結果が
    「Failed to load Python DLL ..._MEIxxxxx\\python39.dll」。
    そこで親子ぶんの PID を両方待ち、さらに少し置いてから起動する。

    new_player_path があれば NeoTJAPlayer.exe も Editor の隣へ置く。**Player
    のコピーが失敗しても Editor の更新は成立させる** — Player が起動中だと
    掴まれていて上書きできないことがあるが、そのために Editor まで巻き戻す
    理由が無い。失敗したことだけ印に残して、次の起動で伝える。"""
    current_exe = sys.executable
    bat_path = os.path.join(tempfile.gettempdir(), "neotja_update.bat")
    marker = ERROR_MARKER_PATH
    pids = [os.getpid()]
    try:
        ppid = os.getppid()
        # 親が別物(エクスプローラ等)のときに巻き込まないよう、同じ exe から
        # 起動された親のときだけ待ち対象にする。
        if ppid and ppid != os.getpid() and _is_same_image(ppid, current_exe):
            pids.append(ppid)
    except OSError:
        pass
    # tasklist / find / timeout を裸で書くと PATH 次第で別物に解決される。
    # Git for Windows が入っていると find は GNU find になり、PID を「ファイル
    # 名」と解釈して必ず失敗する = 常に「プロセスはもう無い」と判定される。
    # つまり待ちループが丸ごと空振りし、アプリが動いている真っ最中にコピーして
    # 起動していた。timeout も同様に GNU coreutils 版になって即エラーで返るため、
    # 再試行の間隔も入っていなかった。System32 の実体を絶対パスで呼ぶ。
    # 待ちは timeout ではなく ping にする — timeout は本物でも標準入力が
    # リダイレクトされていると "Input redirection is not supported" で落ちる。
    sysdir = "%SystemRoot%\\System32"
    def sleep_cmd(sec):
        return f'{sysdir}\\ping.exe -n {sec + 1} 127.0.0.1 >nul 2>&1\r\n'

    wait_block = "".join(
        f'{sysdir}\\tasklist.exe /FI "PID eq {pid}" | {sysdir}\\find.exe "{pid}" >nul\r\n'
        "if not errorlevel 1 goto waitmore\r\n"
        for pid in pids
    )
    # Player を隣へ置く一節。Editor のコピーが済んだ**あと**に走らせる
    # (印を消したあとに書くので、失敗がそのまま残る)。Player が起動中だと
    # 掴まれていて上書きできないので、こちらも数回やり直す。失敗しても
    # Editor の更新と再起動はそのまま続ける。
    player_block = ""
    if new_player_path:
        player_dest = os.path.join(os.path.dirname(current_exe), PLAYER_ASSET_NAME)
        player_block = (
            "set NEOTJA_PTRIES=0\r\n"
            ":pcopyloop\r\n"
            "set /a NEOTJA_PTRIES+=1\r\n"
            f'copy /y "{new_player_path}" "{player_dest}" >nul 2>&1\r\n'
            "if not errorlevel 1 goto pcopyok\r\n"
            "if %NEOTJA_PTRIES% GEQ 5 goto pcopyfail\r\n"
            + sleep_cmd(1) +
            "goto pcopyloop\r\n"
            ":pcopyfail\r\n"
            f'echo player_copy_failed>"{marker}"\r\n'
            f'echo update_player={new_player_path}>>"{marker}"\r\n'
            f'echo target_player={player_dest}>>"{marker}"\r\n'
            "goto pdone\r\n"
            ":pcopyok\r\n"
            f'del "{new_player_path}" >nul 2>&1\r\n'
            ":pdone\r\n"
        )

    bat_contents = (
        "@echo off\r\n"
        ":wait\r\n"
        + wait_block +
        "goto waitdone\r\n"
        ":waitmore\r\n"
        + sleep_cmd(1) +
        "goto wait\r\n"
        ":waitdone\r\n"
        # The handle on the exe can linger a moment past process exit, and AV
        # tends to hold the new file briefly - so don't give up on one attempt.
        "set NEOTJA_TRIES=0\r\n"
        ":copyloop\r\n"
        "set /a NEOTJA_TRIES+=1\r\n"
        f'copy /y "{new_exe_path}" "{current_exe}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto copyok\r\n"
        "if %NEOTJA_TRIES% GEQ 10 goto copyfail\r\n"
        + sleep_cmd(1) +
        "goto copyloop\r\n"
        "\r\n"
        ":copyok\r\n"
        f'del "{new_exe_path}" >nul 2>&1\r\n'
        f'del "{marker}" >nul 2>&1\r\n'
        + player_block
        # 古いブートローダの _MEIxxxxx 後片付けは、プロセスが一覧から消えた
        # あともわずかに続く。そこへ新プロセスを被せると展開したての DLL を
        # 消されるので、少し置いてから起動する。
        + sleep_cmd(3) +
        f'start "" "{current_exe}"\r\n'
        'del "%~f0"\r\n'
        "exit /b\r\n"
        "\r\n"
        # ASCII-only marker: the batch's codepage is unpredictable, so let the
        # app do the Japanese wording. The download is deliberately kept so the
        # user can apply it by hand.
        ":copyfail\r\n"
        f'echo copy_failed>"{marker}"\r\n'
        f'echo update_exe={new_exe_path}>>"{marker}"\r\n'
        f'echo target_exe={current_exe}>>"{marker}"\r\n'
        f'start "" "{current_exe}"\r\n'
        'del "%~f0"\r\n'
        "exit /b\r\n"
    )
    with open(bat_path, "w", encoding="cp932") as f:
        f.write(bat_contents)
    # 環境から PyInstaller のブートローダ変数を落として渡す。落とさずに渡すと、
    # この bat が起動し直す **同じパスの** exe が「自分は展開済みの2段目だ」と
    # 思い込み、更新前のプロセスが消したあとの展開先を見に行って
    # "Security validation failure" だけを出して止まる(neotja/child_env.py)。
    subprocess.Popen(["cmd", "/c", bat_path], env=child_env(),
                     creationflags=subprocess.CREATE_NO_WINDOW)
