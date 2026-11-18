"""プレイリストの保存(v15-design/PLAYLIST_SPEC.md)。

設定(settings.json)とは別の playlists.json に置く(user_dirs の
「プレイリスト」フォルダ)。形:

    {"version": 1,
     "playlists": [{"id": "…", "title": "…", "description": "…",
                    "jacket": "ファイル名 または 絶対パス または null",
                    "items": [{"path": "…tja", "course": "Oni"}]}]}

ジャケットは選んだ絵を「ジャケット」フォルダへ写して持つ(元の絵を消しても
残るように)。写したものはファイル名だけを書く(exe のフォルダごと動かしても
切れないように)。それ以外(写せなかった古い値など)は絶対パスのまま。

書くときは一時ファイルに書いてから置き換える(途中で落ちても壊さない)。
読めない(壊れた)ファイルは横へ退かして空から始める(次の保存で上書きして
中身を失わないように)。
"""

import json
import os
import shutil
import time
import uuid

from neotja import user_dirs

FILE_NAME = "playlists.json"
VERSION = 1
#: ジャケットに使える絵。
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")


def file_path(create=True):
    """playlists.json の道。create ならフォルダを作る(読むだけなら作らない)。"""
    return user_dirs.user_dir("playlists", create) / FILE_NAME


def jacket_dir(create=True):
    return user_dirs.user_dir("jackets", create)


# ----------------------------------------------------------------------
# 形をそろえる
def _clean_item(raw):
    if not isinstance(raw, dict):
        return None
    path = raw.get("path")
    if not isinstance(path, str) or not path:
        return None
    course = raw.get("course")
    return {"path": path, "course": course if isinstance(course, str) and course else None}


def _clean(raw):
    if not isinstance(raw, dict):
        return None
    pid = raw.get("id")
    if not isinstance(pid, str) or not pid:
        pid = uuid.uuid4().hex
    jacket = raw.get("jacket")
    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        raw_items = []
    items = [i for i in (_clean_item(x) for x in raw_items) if i]
    return {
        "id": pid,
        "title": str(raw.get("title") or ""),
        "description": str(raw.get("description") or ""),
        "jacket": jacket if isinstance(jacket, str) and jacket else None,
        "items": items,
    }


def new_playlist(title, description="", jacket=None):
    return {"id": uuid.uuid4().hex, "title": str(title or ""),
            "description": str(description or ""), "jacket": jacket or None,
            "items": []}


# ----------------------------------------------------------------------
# 読む・書く
def load(path=None):
    """プレイリストの一覧。無ければ空。壊れていれば退かして空。"""
    path = path or file_path(create=False)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        _set_aside(path)
        return []
    raw = data.get("playlists") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        _set_aside(path)
        return []
    out, seen = [], set()
    for x in raw:
        pl = _clean(x)
        if pl is None:
            continue
        if pl["id"] in seen:
            pl["id"] = uuid.uuid4().hex
        seen.add(pl["id"])
        out.append(pl)
    return out


def _set_aside(path):
    try:
        os.replace(path, "%s.broken-%s" % (path, time.strftime("%Y%m%d-%H%M%S")))
    except OSError:
        pass


def save(playlists, path=None):
    """一時ファイルに書いてから置き換える。"""
    path = str(path or file_path())
    data = {"version": VERSION, "playlists": [c for c in map(_clean, playlists) if c]}
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ----------------------------------------------------------------------
# ジャケット
def jacket_path(pl):
    """プレイリストのジャケットの実際の道。無い・見つからなければ ""。"""
    j = (pl or {}).get("jacket")
    if not j:
        return ""
    p = j if os.path.isabs(j) else os.path.join(str(jacket_dir(create=False)), j)
    return p if os.path.isfile(p) else ""


def import_jacket(src, pid):
    """選んだ絵をジャケットのフォルダへ写す。書く値(ファイル名)を返す。"""
    ext = os.path.splitext(src)[1].lower() or ".png"
    name = "%s_%s%s" % (pid, time.strftime("%Y%m%d%H%M%S"), ext)
    dst = jacket_dir() / name
    n = 1
    while dst.exists():
        dst = jacket_dir() / ("%s_%s-%d%s" % (pid, time.strftime("%Y%m%d%H%M%S"), n, ext))
        n += 1
    shutil.copyfile(src, dst)
    return dst.name


def drop_jacket(value):
    """写して持っていたジャケットを消す(ジャケットのフォルダの中のものだけ)。"""
    if not value or os.path.isabs(value) or os.path.basename(value) != value:
        return
    try:
        os.remove(os.path.join(str(jacket_dir(create=False)), value))
    except OSError:
        pass


# ----------------------------------------------------------------------
class PlaylistStore:
    """プレイリストの一覧を持ち、変えたらすぐ保存する。"""

    def __init__(self, path=None):
        self._path = path
        self.items = load(path)

    def reload(self):
        self.items = load(self._path)

    def save(self):
        save(self.items, self._path)

    def get(self, pid):
        return next((p for p in self.items if p["id"] == pid), None)

    def create(self, title, description="", jacket_src=None):
        pl = new_playlist(title, description)
        if jacket_src:
            pl["jacket"] = import_jacket(jacket_src, pl["id"])
        self.items.append(pl)
        self.save()
        return pl

    def update(self, pid, title, description, jacket_src=None, clear_jacket=False):
        """jacket_src を渡せば絵を替える。clear_jacket なら外す。"""
        pl = self.get(pid)
        if pl is None:
            return None
        pl["title"] = str(title or "")
        pl["description"] = str(description or "")
        if jacket_src or clear_jacket:
            old = pl.get("jacket")
            pl["jacket"] = import_jacket(jacket_src, pid) if jacket_src else None
            if old and old != pl["jacket"]:
                drop_jacket(old)
        self.save()
        return pl

    def delete(self, pid):
        pl = self.get(pid)
        if pl is None:
            return False
        self.items.remove(pl)
        self.save()
        drop_jacket(pl.get("jacket"))
        return True
