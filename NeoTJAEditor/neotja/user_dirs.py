"""利用者のデータを置くフォルダ(exe の横)。

置き場所は exe(凍結した版)なら sys.executable のフォルダ、ソースから
動かしているならアプリのフォルダ(neotja の1つ上)。その下の
「ユーザーデータ」に種類ごとのフォルダを作る(使うときに初めて作る)。

    ユーザーデータ/
        録画/          録画の書き出し(まだ使っていない)
        YouTube/       YouTube から落とした動画(まだ使っていない)
        プレイリスト/  playlists.json
        ジャケット/    プレイリストのジャケット

目的は、Temp や譜面のフォルダに置いて間違えて消す事故を防ぐこと。
既にあるファイルは移さない。

試験では NEOTJA_USER_DIR(環境変数)か base_override(この部品の属性)で
置き場所を差し替える。差し替えた先にも「ユーザーデータ」は作らず、その
フォルダの直下に種類ごとのフォルダを作る。
exe の横に書けないとき(Program Files など)は %LOCALAPPDATA%\\NeoTJAEditor\\
ユーザーデータ へ退避する(settings.json と同じ考え方)。
"""

import os
import sys
from pathlib import Path

#: exe の横に作る専用フォルダ。
ROOT_NAME = "ユーザーデータ"
#: 種類 → フォルダ名。
SUBDIRS = {
    "recordings": "録画",
    "youtube": "YouTube",
    "playlists": "プレイリスト",
    "jackets": "ジャケット",
}
#: 置き場所を差し替える環境変数。
ENV_VAR = "NEOTJA_USER_DIR"
#: 置き場所の差し替え(試験用)。None なら使わない。
base_override = None


def app_base():
    """exe(凍結した版)またはアプリのフォルダ。"""
    if getattr(sys, "frozen", False):
        # resolve() は使わない: 抜いた USB など用意できていない場所では
        # 例外になる(settings.json の置き場所と同じ決め方にする)。
        return Path(os.path.abspath(sys.executable)).parent
    return Path(__file__).resolve().parent.parent


def _fallback_root():
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "NeoTJAEditor" / ROOT_NAME


def root():
    """種類ごとのフォルダを並べる親(作らない)。"""
    over = base_override if base_override is not None else os.environ.get(ENV_VAR)
    if over:
        return Path(over)
    return app_base() / ROOT_NAME


def user_dir(kind, create=True):
    """種類 kind("recordings" / "youtube" / "playlists" / "jackets")の
    フォルダ。create なら無ければ作る。作れなければ退避先を使う。"""
    name = SUBDIRS[kind]
    path = root() / name
    if not create:
        return path
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        if base_override is not None or os.environ.get(ENV_VAR):
            raise          # 差し替えた先に作れないのは試験の誤り
    path = _fallback_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def copy_into(kind, src):
    """src を種類 kind のフォルダへ写し、写した先の道を返す。元を消しても
    困らないようにするため。すでにそのフォルダの中なら何もしない。同じ絵
    (同じ中身)を何度選んでも1枚だけ。写せなければ src のまま返す。"""
    import hashlib
    import shutil
    if not src or not os.path.isfile(src):
        return src
    try:
        folder = user_dir(kind)
        src_abs = Path(src).resolve()
        if src_abs.parent == folder.resolve():
            return str(src_abs)
        h = hashlib.sha1()
        with open(src_abs, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        dst = folder / ("%s_%s%s" % (src_abs.stem, h.hexdigest()[:10],
                                     src_abs.suffix.lower()))
        if not dst.exists():
            tmp = dst.with_name(dst.name + ".part")
            shutil.copyfile(src_abs, tmp)
            os.replace(tmp, dst)
        return str(dst)
    except OSError:
        return src
