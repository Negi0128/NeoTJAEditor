"""Player の選曲で使う一覧(最近開いた楽曲・保存済み楽曲・登録フォルダ)の
中身の扱い。画面は持たない(neotja/player/library_view.py が並べる)。

設定には MainWindow の config_data をそのまま使う。Editor は保存のたびに
辞書を丸ごと書くので、別の辞書を持つと互いに上書きし合う。ここは辞書の
値を書き換えるだけにして、保存は呼び出し側(save_cb)に頼む。

- player_recent … [{"path", "title", "opened"(epoch 秒)}] 新しい順
- player_saved  … [{"path", "title"}] 登録順。元ファイルへの参照だけで、
  コピーしない。外しても元の TJA は消さない(ファイルには一切触らない)。
- player_folders … [フォルダのパス] シミュレーターの曲フォルダ

同じファイルかどうかは normcase + normpath + abspath で見る(Windows は
大文字小文字と区切りの向きを区別しないため)。
"""

import os
import time

from neotja.player.library import _read_text, _header_value
from neotja.start_screen import COURSE_NAMES, COURSE_ORDER

#: 最近開いた楽曲に残す件数。
RECENT_LIMIT = 30

#: 画面に出す難易度の名前(start_screen の英名 → Player の表記)。
COURSE_LABELS = {
    "Easy": "かんたん", "Normal": "ふつう", "Hard": "むずかしい",
    "Oni": "おに", "Edit": "裏", "Tower": "タワー", "Dan": "段位",
}


def path_key(path):
    """同じファイルかを比べるための鍵。"""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))
    except (TypeError, ValueError):
        return ""


def _entries(cfg, key):
    """設定の一覧を dict のリストへ正規化する。手編集や古い形式(パスの
    文字列だけ)でも落ちない。"""
    raw = cfg.get(key)
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, str) and item:
            out.append({"path": item, "title": ""})
        elif isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"]:
            out.append(dict(item))
    return out


def _dedupe(entries):
    seen = set()
    out = []
    for e in entries:
        k = path_key(e["path"])
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


# ----------------------------------------------------------------------
# 最近開いた楽曲
# ----------------------------------------------------------------------
def recent(cfg):
    return _dedupe(_entries(cfg, "player_recent"))


def add_recent(cfg, path, title=""):
    """先頭へ入れる(あれば先頭へ移す)。"""
    path = os.path.normpath(os.path.abspath(path))
    k = path_key(path)
    rest = [e for e in recent(cfg) if path_key(e["path"]) != k]
    entry = {"path": path, "title": title or song_title(path), "opened": time.time()}
    cfg["player_recent"] = [entry] + rest[:RECENT_LIMIT - 1]
    return entry


def clear_recent(cfg):
    cfg["player_recent"] = []


# ----------------------------------------------------------------------
# 保存済み楽曲
# ----------------------------------------------------------------------
def saved(cfg):
    return _dedupe(_entries(cfg, "player_saved"))


def is_saved(cfg, path):
    k = path_key(path)
    return any(path_key(e["path"]) == k for e in saved(cfg))


def add_saved(cfg, path, title=""):
    """保存済みへ足す。すでにあれば何もしない(False)。"""
    if is_saved(cfg, path):
        return False
    path = os.path.normpath(os.path.abspath(path))
    lst = saved(cfg)
    lst.append({"path": path, "title": title or song_title(path)})
    cfg["player_saved"] = lst
    return True


def remove_saved(cfg, path):
    """一覧から外すだけ。ファイルには触らない。"""
    k = path_key(path)
    lst = saved(cfg)
    keep = [e for e in lst if path_key(e["path"]) != k]
    cfg["player_saved"] = keep
    return len(keep) != len(lst)


def relink_saved(cfg, old_path, new_path):
    """見つからなくなった項目の参照先を差し替える。差し替え先がすでに
    別の項目として登録済みなら、古いほうを外すだけにする(重複させない)。"""
    ko = path_key(old_path)
    new_path = os.path.normpath(os.path.abspath(new_path))
    kn = path_key(new_path)
    lst = saved(cfg)
    if ko == kn:
        return False
    if any(path_key(e["path"]) == kn for e in lst):
        cfg["player_saved"] = [e for e in lst if path_key(e["path"]) != ko]
        return True
    for e in lst:
        if path_key(e["path"]) == ko:
            e["path"] = new_path
            e["title"] = song_title(new_path)
    cfg["player_saved"] = lst
    return True


# ----------------------------------------------------------------------
# 登録フォルダ
# ----------------------------------------------------------------------
def folders(cfg):
    raw = cfg.get("player_folders")
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for p in raw:
        if isinstance(p, str) and p and path_key(p) not in seen:
            seen.add(path_key(p))
            out.append(p)
    return out


def add_folder(cfg, path):
    path = os.path.normpath(os.path.abspath(path))
    lst = folders(cfg)
    if any(path_key(p) == path_key(path) for p in lst):
        return False
    lst.append(path)
    cfg["player_folders"] = lst
    return True


def remove_folder(cfg, path):
    k = path_key(path)
    lst = folders(cfg)
    keep = [p for p in lst if path_key(p) != k]
    cfg["player_folders"] = keep
    return len(keep) != len(lst)


# ----------------------------------------------------------------------
# 譜面の見出し
# ----------------------------------------------------------------------
_INFO_CACHE = {}


def song_title(path):
    return song_info(path)["title"]


def song_info(path):
    """題名・副題・難易度(名前とレベル)を読む。結果は更新日時ごとに覚える。

    戻り値 {"title", "subtitle", "courses": [(名前, レベル or None)]}。
    courses は難しい順(start_screen.COURSE_ORDER)。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"title": stem, "subtitle": "", "courses": []}
    key = (path_key(path), mtime)
    hit = _INFO_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        text = _read_text(path)
    except OSError:
        return {"title": stem, "subtitle": "", "courses": []}
    subtitle = _header_value(text, "SUBTITLE")
    if subtitle.startswith("--") or subtitle.startswith("++"):
        subtitle = subtitle[2:].strip()
    levels = {}
    order = []
    cur = None
    has_start = False
    for raw in text.splitlines():
        s = raw.split("//")[0].strip()
        up = s.upper()
        if up.startswith("COURSE:"):
            v = s[7:].strip()
            cur = COURSE_NAMES.get(v.lower(), v or "?")
            if cur not in order:
                order.append(cur)
        elif up.startswith("LEVEL:"):
            try:
                levels.setdefault(cur or "Oni", int(s[6:].strip()))
            except ValueError:
                pass
        elif up.startswith("#START"):
            has_start = True
            # COURSE: を書かない譜面は仕様上おに。
            if cur is None:
                cur = "Oni"
                if cur not in order:
                    order.append(cur)
    if not order and has_start:
        order.append("Oni")
    order.sort(key=lambda n: (COURSE_ORDER.index(n) if n in COURSE_ORDER
                              else len(COURSE_ORDER), n))
    info = {
        "title": _header_value(text, "TITLE") or stem,
        "subtitle": subtitle,
        "courses": [(n, levels.get(n)) for n in order],
    }
    _INFO_CACHE[key] = info
    return info


def list_dir(path):
    """フォルダの一段ぶん。(サブフォルダ, TJA) の2つのリストを返す。

    シミュレーターの曲フォルダは「ジャンル/曲/曲.tja」の形が多い。中に
    フォルダを持たず TJA だけがあるサブフォルダは、フォルダとしてではなく
    中の TJA を直接並べる(曲1つ開くのに1段余計に潜らせないため)。"""
    dirs, tjas = [], []
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError:
        return dirs, tjas
    for e in entries:
        try:
            if e.is_dir():
                sub_dirs, sub_tjas = _peek(e.path)
                if sub_tjas and not sub_dirs:
                    tjas.extend(sub_tjas)
                elif sub_dirs or sub_tjas:
                    dirs.append(e.path)
            elif e.name.lower().endswith(".tja"):
                tjas.append(e.path)
        except OSError:
            continue
    return dirs, tjas


def _peek(path):
    has_dirs, tjas = False, []
    try:
        for e in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if e.is_dir():
                has_dirs = True
            elif e.name.lower().endswith(".tja"):
                tjas.append(e.path)
    except OSError:
        pass
    return has_dirs, tjas
