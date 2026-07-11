import json
import os
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
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
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

    def __init__(self, asset_url: str, parent=None):
        super().__init__(parent)
        self.asset_url = asset_url

    def run(self):
        try:
            dest = os.path.join(tempfile.gettempdir(), "NeoTJAEditor_update.exe")
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
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    read += len(chunk)
                    self.progress.emit(int(read * 100 / total) if total else -1)

            # A truncated download (dropped connection) would otherwise be
            # copied over the running exe and brick it - a onefile PyInstaller
            # build stores its archive at the tail, so a short file fails at
            # startup with e.g. "No module named 'PySide6.QtGui'". Validate the
            # size against Content-Length and sanity-check the PE header before
            # letting the caller apply it.
            if expected and read != expected:
                raise IOError(
                    f"ダウンロードが不完全です ({read}/{expected} バイト)。"
                    "通信状況を確認してもう一度お試しください。"
                )
            with open(dest, "rb") as f:
                if f.read(2) != b"MZ":
                    raise IOError("ダウンロードしたファイルが壊れています。もう一度お試しください。")

            self.finished_ok.emit(dest)
        except Exception as e:
            self.failed.emit(str(e))


def apply_update(new_exe_path: str):
    """Self-replace pattern for a single-file PyInstaller exe: it can't
    overwrite itself while running, so a tiny batch script waits for this
    process to exit, copies the new exe over it, then relaunches it. Only
    meaningful when frozen; callers should check sys.frozen first."""
    current_exe = sys.executable
    bat_path = os.path.join(tempfile.gettempdir(), "neotja_update.bat")
    bat_contents = (
        "@echo off\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {os.getpid()}" | find "{os.getpid()}" >nul\r\n'
        "if not errorlevel 1 (\r\n"
        "  timeout /t 1 /nobreak >nul\r\n"
        "  goto wait\r\n"
        ")\r\n"
        f'copy /y "{new_exe_path}" "{current_exe}" >nul\r\n'
        f'start "" "{current_exe}"\r\n'
        f'del "{new_exe_path}"\r\n'
        'del "%~f0"\r\n'
    )
    with open(bat_path, "w", encoding="cp932") as f:
        f.write(bat_contents)
    subprocess.Popen(["cmd", "/c", bat_path], creationflags=subprocess.CREATE_NO_WINDOW)
