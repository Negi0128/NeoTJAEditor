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
        return Path(sys.executable).resolve().parent
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
