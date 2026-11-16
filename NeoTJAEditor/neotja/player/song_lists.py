"""Player の選曲で使う一覧(最近開いた楽曲・保存済み楽曲・登録フォルダ)の
中身の扱い。画面は持たない(neotja/player/library_view.py が並べる)。

設定には MainWindow の config_data をそのまま使う。Editor は保存のたびに
辞書を丸ごと書くので、別の辞書を持つと互いに上書きし合う。ここは辞書の
値を書き換えるだけにして、保存は呼び出し側(save_cb)に頼む。

- player_recent … [{"path", "title", "opened"(epoch 秒)}] 新しい順
- player_saved  … [{"path", "title"}] 登録順。元ファイルへの参照だけで、
  コピーしない。外しても元の TJA は消さない(ファイルには一切触らない)。
- player_folders … [{"simulator", "path"}] シミュレーターごとの曲フォルダ
  (古い形式の文字列だけの項目は「その他」として読む)

同じファイルかどうかは normcase + normpath + abspath で見る(Windows は
大文字小文字と区切りの向きを区別しないため)。
"""

import os
import re
import time
import unicodedata

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
# 登録フォルダ(シミュレーターごと)
# ----------------------------------------------------------------------
#: 分類が分からない・古い形式(パスの文字列だけ)だったときの分類名。
OTHER_SIMULATOR = "その他"

def folders(cfg):
    """[{"simulator", "path"}] を返す。古い形式(文字列)は「その他」へ。"""
    raw = cfg.get("player_folders")
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for item in raw:
        if isinstance(item, str):
            path, sim = item, OTHER_SIMULATOR
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            path = item["path"]
            sim = item.get("simulator")
            if not isinstance(sim, str) or not sim.strip():
                sim = OTHER_SIMULATOR
        else:
            continue
        k = path_key(path)
        if not path or not k or k in seen:
            continue
        seen.add(k)
        out.append({"simulator": sim.strip(), "path": path})
    return out


def migrate_folders(cfg):
    """古い形式の player_folders を新しい形へ書き直す。変えたら True。"""
    new = folders(cfg)
    if cfg.get("player_folders") != new:
        cfg["player_folders"] = new
        return True
    return False


def simulators(cfg):
    """分類名を名前順に(重複なし。数字は数として比べる)。"""
    out = []
    for e in folders(cfg):
        if e["simulator"] not in out:
            out.append(e["simulator"])
    return sorted(out, key=natural_key)


def folders_of(cfg, simulator):
    """その分類の登録フォルダのパスを、フォルダ名の順に。"""
    paths = [e["path"] for e in folders(cfg) if e["simulator"] == simulator]
    return sorted(paths, key=lambda p: (natural_key(os.path.basename(os.path.normpath(p))),
                                        natural_key(p)))


def folder_simulator(cfg, path):
    k = path_key(path)
    for e in folders(cfg):
        if path_key(e["path"]) == k:
            return e["simulator"]
    return None


def add_folder(cfg, path, simulator=OTHER_SIMULATOR):
    path = os.path.normpath(os.path.abspath(path))
    lst = folders(cfg)
    if any(path_key(e["path"]) == path_key(path) for e in lst):
        return False
    lst.append({"simulator": (simulator or "").strip() or OTHER_SIMULATOR,
                "path": path})
    cfg["player_folders"] = lst
    return True


def remove_folder(cfg, path):
    """登録を外すだけ(フォルダの中身には触らない)。"""
    k = path_key(path)
    lst = folders(cfg)
    keep = [e for e in lst if path_key(e["path"]) != k]
    cfg["player_folders"] = keep
    return len(keep) != len(lst)


def set_folder_simulator(cfg, path, simulator):
    simulator = (simulator or "").strip() or OTHER_SIMULATOR
    k = path_key(path)
    lst = folders(cfg)
    changed = False
    for e in lst:
        if path_key(e["path"]) == k and e["simulator"] != simulator:
            e["simulator"] = simulator
            changed = True
    cfg["player_folders"] = lst
    return changed


def rename_simulator(cfg, old, new):
    """分類名を変える。すでにある名前なら、その分類へまとめる。"""
    new = (new or "").strip()
    if not new or new == old:
        return False
    lst = folders(cfg)
    changed = False
    for e in lst:
        if e["simulator"] == old:
            e["simulator"] = new
            changed = True
    cfg["player_folders"] = lst
    return changed


# ----------------------------------------------------------------------
# ジャンル(box.def)
# ----------------------------------------------------------------------
BOX_DEF = "box.def"


def _decode_box(raw):
    for enc in ("utf-8-sig", "cp932", "shift_jis"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp932", errors="replace")


def _box_color(value):
    v = value.strip()
    if not v.startswith("#"):
        v = "#" + v
    hexpart = v[1:]
    if len(hexpart) in (3, 6, 8) and all(c in "0123456789abcdefABCDEF" for c in hexpart):
        if len(hexpart) == 8:
            # #AARRGGBB と書くシミュレーターがある。色だけ使う。
            v = "#" + hexpart[2:]
        return v.lower()
    return None


def parse_box_def(directory):
    """フォルダの box.def を読む。無ければ None。

    戻り値 {"title", "genre", "color"(#rrggbb か None)}。題が無ければ
    フォルダ名。色は #BGCOLOR: を優先、無ければ #BOXCOLOR:。"""
    path = os.path.join(directory, BOX_DEF)
    try:
        with open(path, "rb") as f:
            raw = f.read(64 * 1024)
    except OSError:
        return None
    text = _decode_box(raw).lstrip("\ufeff")
    vals = {}
    for line in text.splitlines():
        s = line.split("//")[0].strip()
        if not s.startswith("#") or ":" not in s:
            continue
        key, _, val = s[1:].partition(":")
        key = key.strip().upper()
        if key not in vals:
            vals[key] = val.strip()
    name = os.path.basename(os.path.normpath(directory)) or directory
    color = None
    for key in ("BGCOLOR", "BOXCOLOR"):
        if vals.get(key):
            color = _box_color(vals[key])
            if color:
                break
    return {
        "title": vals.get("TITLE") or name,
        "genre": vals.get("GENRE", ""),
        "color": color,
    }


def display_name(directory):
    """フォルダの見せる名前。box.def の題があればそれ。"""
    box = parse_box_def(directory)
    if box is not None:
        return box["title"]
    return os.path.basename(os.path.normpath(directory)) or directory


# ----------------------------------------------------------------------
# 譜面の見出し
# ----------------------------------------------------------------------
_INFO_CACHE = {}


def song_title(path):
    return song_info(path)["title"]


def song_info(path):
    """題名・副題・難易度(名前とレベル)を読む。結果は更新日時ごとに覚える。

    戻り値 {"title", "subtitle", "courses": [(名前, レベル or None)],
    "bpm"(ヘッダの BPM: か None)}。
    courses は難しい順(start_screen.COURSE_ORDER)。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"title": stem, "subtitle": "", "courses": [], "bpm": None}
    key = (path_key(path), mtime)
    hit = _INFO_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        text = _read_text(path)
    except OSError:
        return {"title": stem, "subtitle": "", "courses": [], "bpm": None}
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
        "bpm": _bpm(_header_value(text, "BPM")),
    }
    _INFO_CACHE[key] = info
    return info


def _bpm(value):
    try:
        v = float(value.split("//")[0].strip())
    except (AttributeError, ValueError):
        return None
    return v if v > 0 else None


# ----------------------------------------------------------------------
# 並べ替えと検索
# ----------------------------------------------------------------------
def normalize(text):
    """検索・並べ替え用。全角/半角と大文字/小文字の違いを消す。"""
    return unicodedata.normalize("NFKC", text or "").casefold()


_DIGITS = re.compile(r"(\d+)")


def natural_key(text):
    """「Songs2」が「Songs10」より前に来る並べ方の鍵。"""
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t)
                 for t in _DIGITS.split(normalize(text)) if t)


def matches(query, info, path):
    """部分一致。題名・副題・ファイル名のどれかに含まれていれば True。"""
    q = normalize(query).strip()
    if not q:
        return True
    info = info or {}
    return (q in normalize(info.get("title", ""))
            or q in normalize(info.get("subtitle", ""))
            or q in normalize(os.path.basename(path)))


def sort_level(info):
    """難易度順の鍵になるレベル。おに → 無ければ裏 → 無ければいちばん
    高いレベル。どれも無ければ None。"""
    levels = dict((info or {}).get("courses") or [])
    for name in ("Oni", "Edit"):
        if levels.get(name) is not None:
            return levels[name]
    have = [lv for lv in levels.values() if lv is not None]
    return max(have) if have else None


#: 並べ方。最近順は「最近開いた楽曲」だけ。
SORT_MODES = ("recent", "name", "level", "bpm")
SORT_LABELS = {"recent": "最近順", "name": "フォルダ名順", "level": "難易度順", "bpm": "BPM順"}
#: それぞれの並べ方の素直な向き(True = 大きい/新しい順)。
SORT_NATURAL_DESC = {"recent": True, "name": False, "level": True, "bpm": False}


def sort_state(cfg, recent_view=False):
    """設定に残した並べ方 (mode, reverse)。reverse は素直な向きの逆か。"""
    raw = cfg.get("player_sort")
    raw = raw if isinstance(raw, dict) else {}
    key = "recent_mode" if recent_view else "mode"
    mode = raw.get(key)
    allowed = SORT_MODES if recent_view else SORT_MODES[1:]
    if mode not in allowed:
        mode = "recent" if recent_view else "name"
    return mode, bool(raw.get("reverse", False))


def set_sort_state(cfg, mode, reverse, recent_view=False):
    raw = cfg.get("player_sort")
    raw = dict(raw) if isinstance(raw, dict) else {}
    raw["recent_mode" if recent_view else "mode"] = mode
    raw["reverse"] = bool(reverse)
    cfg["player_sort"] = raw


def is_descending(mode, reverse):
    return SORT_NATURAL_DESC.get(mode, False) != bool(reverse)


def _name_key(path, info):
    folder = os.path.basename(os.path.dirname(os.path.normpath(path)))
    return (natural_key(folder), natural_key(os.path.basename(path)),
            natural_key((info or {}).get("title", "")))


def sort_songs(items, mode, reverse=False):
    """曲を並べる。items は (path, info, order) の並び。order は元の並び
    (最近順に使う。小さいほど新しい)。

    - name … 曲のフォルダ名 → ファイル名
    - level … sort_level。同じなら題名順。レベルが無い曲は向きに関係なく最後
    - bpm … ヘッダの BPM:。同じなら題名順。無い曲は最後
    - recent … order
    reverse で素直な向き(SORT_NATURAL_DESC)を逆にする。"""
    items = list(items)
    desc = is_descending(mode, reverse)
    if mode == "recent":
        # order が小さいほど新しい = 素直な向き(新しい順)は order の昇順。
        return sorted(items, key=lambda t: t[2], reverse=not desc)
    if mode in ("level", "bpm"):
        def value(t):
            return sort_level(t[1]) if mode == "level" else (t[1] or {}).get("bpm")
        have = [t for t in items if value(t) is not None]
        rest = [t for t in items if value(t) is None]
        sign = -1 if desc else 1
        have.sort(key=lambda t: (sign * value(t),
                                 natural_key((t[1] or {}).get("title", "")),
                                 _name_key(t[0], t[1])))
        rest.sort(key=lambda t: _name_key(t[0], t[1]))
        return have + rest
    return sorted(items, key=lambda t: _name_key(t[0], t[1]), reverse=desc)


def walk_tjas(root, cancelled=lambda: False):
    """root の下の TJA を全部(深さ優先・名前順)。cancelled() が True に
    なったら途中でやめる。"""
    for cur, dirs, files in os.walk(root):
        if cancelled():
            return
        dirs.sort(key=natural_key)
        for name in sorted(files, key=natural_key):
            if name.lower().endswith(".tja"):
                yield os.path.join(cur, name)


def list_dir(path):
    """フォルダの一段ぶん。(ジャンル, サブフォルダ, TJA) の3つのリストを返す。

    - ジャンル … box.def のあるサブフォルダ。[(パス, parse_box_def の値)]
    - サブフォルダ … それ以外で、中にフォルダか TJA があるもの
    - TJA … このフォルダの TJA と、「曲フォルダ」(中にフォルダを持たず
      TJA だけがある box.def なしのサブフォルダ)の TJA。曲1つ開くのに
      1段余計に潜らせないため。"""
    genres, dirs, tjas = [], [], []
    try:
        entries = sorted(os.scandir(path), key=lambda e: natural_key(e.name))
    except OSError:
        return genres, dirs, tjas
    for e in entries:
        try:
            if e.is_dir():
                box = parse_box_def(e.path)
                if box is not None:
                    genres.append((e.path, box))
                    continue
                sub_dirs, sub_tjas = _peek(e.path)
                if sub_tjas and not sub_dirs:
                    tjas.extend(sub_tjas)
                elif sub_dirs or sub_tjas:
                    dirs.append(e.path)
            elif e.name.lower().endswith(".tja"):
                tjas.append(e.path)
        except OSError:
            continue
    return genres, dirs, tjas


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
