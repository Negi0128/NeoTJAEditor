import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal

from neotja.constants import VERSION

RELEASES_API_URL = "https://api.github.com/repos/Negi0128/NeoTJAEditor/releases/latest"
RELEASES_PAGE_URL = "https://github.com/Negi0128/NeoTJAEditor/releases/latest"
ASSET_NAME = "NeoTJAEditor.exe"
_USER_AGENT = "NeoTJAEditor-Updater"


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

    update_available = Signal(str, str, str)  # tag, release_notes, asset_download_url
    up_to_date = Signal()
    failed = Signal(str)

    def run(self):
        try:
            req = urllib.request.Request(RELEASES_API_URL, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            tag = data.get("tag_name", "")
            if not tag or not is_newer(tag):
                self.up_to_date.emit()
                return

            asset_url = ""
            for asset in data.get("assets", []):
                if asset.get("name") == ASSET_NAME:
                    asset_url = asset.get("browser_download_url", "")
                    break

            self.update_available.emit(tag, data.get("body", ""), asset_url)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(str(e))


class UpdateDownloadWorker(QThread):
    """Downloads the new exe asset (frozen builds only) so the caller can
    show progress instead of freezing the UI on a blocking request."""

    progress = Signal(int)      # 0-100, or -1 if content-length is unknown
    finished_ok = Signal(str)   # path to the downloaded exe
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, asset_url: str, parent=None):
        super().__init__(parent)
        self.asset_url = asset_url
        self._cancelled = False

    def cancel(self):
        """Cooperative cancel - the run loop checks this between chunks. Used
        instead of QThread.terminate(), which can strike mid-write and corrupt
        interpreter state or leave the fixed dest path locked (bricking the
        next update attempt with PermissionError). Matches the cancellation
        pattern the other workers in this app already use."""
        self._cancelled = True

    def run(self):
        dest = os.path.join(tempfile.gettempdir(), "NeoTJAEditor_update.exe")
        try:
            req = urllib.request.Request(self.asset_url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as f:
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
                        break
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    read += len(chunk)
                    self.progress.emit(int(read * 100 / total) if total else -1)

            if self._cancelled:
                # The with-block closed both handles; now the partial file can
                # be removed so the fixed path isn't left half-written/locked.
                try:
                    os.remove(dest)
                except OSError:
                    pass
                self.cancelled.emit()
                return

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

            self.finished_ok.emit(dest)
        except Exception as e:
            if self._cancelled:
                # A read raised because we're tearing down mid-cancel - not a
                # real failure to report to the user.
                try:
                    os.remove(dest)
                except OSError:
                    pass
                self.cancelled.emit()
                return
            self.failed.emit(str(e))


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


def apply_update(new_exe_path: str):
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
    leaves a marker that the relaunched app reports via pop_update_error()."""
    current_exe = sys.executable
    bat_path = os.path.join(tempfile.gettempdir(), "neotja_update.bat")
    marker = ERROR_MARKER_PATH
    bat_contents = (
        "@echo off\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {os.getpid()}" | find "{os.getpid()}" >nul\r\n'
        "if not errorlevel 1 (\r\n"
        "  timeout /t 1 /nobreak >nul 2>&1\r\n"
        "  goto wait\r\n"
        ")\r\n"
        # The handle on the exe can linger a moment past process exit, and AV
        # tends to hold the new file briefly - so don't give up on one attempt.
        "set NEOTJA_TRIES=0\r\n"
        ":copyloop\r\n"
        "set /a NEOTJA_TRIES+=1\r\n"
        f'copy /y "{new_exe_path}" "{current_exe}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto copyok\r\n"
        "if %NEOTJA_TRIES% GEQ 10 goto copyfail\r\n"
        "timeout /t 1 /nobreak >nul 2>&1\r\n"
        "goto copyloop\r\n"
        "\r\n"
        ":copyok\r\n"
        f'del "{new_exe_path}" >nul 2>&1\r\n'
        f'del "{marker}" >nul 2>&1\r\n'
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
    subprocess.Popen(["cmd", "/c", bat_path], creationflags=subprocess.CREATE_NO_WINDOW)
