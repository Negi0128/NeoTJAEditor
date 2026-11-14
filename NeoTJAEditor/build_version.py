# -*- coding: utf-8 -*-
"""exe に埋めるバージョン情報リソースを作る。

**なぜ要るのか**
会社名も製品名もバージョンも空の未署名 exe は、Windows Defender の機械学習
判定(Trojan:Win32/Sabsik.TE.A!ml 等)で不利に働く。実際に v12.0.0 の
NeoTJAPlayer.exe がそれで隔離された。中身が変わるわけではないので誤検知が
必ず消えるとは限らないが、素性を書いておくのは正しいことでもある。

番号は neotja/constants.VERSION から作る。spec に直書きすると上げ忘れる。
"""
import os

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo,
)

#: 作者。exe のプロパティに出る。
COMPANY = "Negi0128"


def _quad(version: str):
    """"12.0.0" → (12, 0, 0, 0)。Windows は4つ組しか受け付けない。"""
    parts = []
    for chunk in str(version).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    parts = (parts + [0, 0, 0, 0])[:4]
    return tuple(parts)


def write_version_file(name: str, description: str, version: str,
                       out_dir: str) -> str:
    """バージョン情報を書き出して、そのパスを返す。

    name        … NeoTJAPlayer など。ファイル名と製品名に使う
    description … エクスプローラの「説明」に出る文
    """
    quad = _quad(version)
    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=quad, prodvers=quad,
                          mask=0x3F, flags=0x0, OS=0x40004,
                          fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", COMPANY),
                StringStruct("FileDescription", description),
                StringStruct("FileVersion", version),
                StringStruct("InternalName", name),
                StringStruct("LegalCopyright", "Copyright (c) " + COMPANY),
                StringStruct("OriginalFilename", name + ".exe"),
                StringStruct("ProductName", name),
                StringStruct("ProductVersion", version),
            ])]),
            # 0x0409 = 英語(米国) / 1200 = Unicode
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "version_%s.txt" % name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(info))
    return path


# ---------------------------------------------------------------------------
# 更新用の鍵(非公開リポジトリから更新を取るためのアクセストークン)
#
# 鍵はリポジトリに入れない。組み立てる人の手元にだけ置く:
#   - NeoTJAEditor/update_token.txt に1行で書く(gitignore 済み)か、
#   - 環境変数 NEOTJA_UPDATE_TOKEN に入れる。
# どちらも無ければ鍵なしの exe になる(公開リポジトリのうちはそれで動く)。
#
# ここで neotja/_update_token.py(gitignore 済み)を作り、spec がそれを積む。
# 難読化(XOR + base64)は「ぱっと見で読めない」程度のもので、exe を持って
# いる人なら誰でも取り出せる。なので鍵は必ず
#   fine-grained / このリポジトリだけ / Contents: Read-only
# のものにすること。
# ---------------------------------------------------------------------------

TOKEN_FILE = "update_token.txt"
TOKEN_MODULE = os.path.join("neotja", "_update_token.py")
TOKEN_ENV = "NEOTJA_UPDATE_TOKEN"


def _read_token(spec_dir: str) -> str:
    path = os.path.join(spec_dir, TOKEN_FILE)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    return os.environ.get(TOKEN_ENV, "").strip()


def write_update_token(spec_dir: str) -> bool:
    """鍵があれば neotja/_update_token.py を作って True。無ければ古いものを消して False。

    **古いものを消すのが大事。** 前の組み立てで作られたまま残っていると、
    鍵を外したつもりでも積まれてしまう。鍵そのものは表示しない。"""
    import base64
    import secrets

    module = os.path.join(spec_dir, TOKEN_MODULE)
    token = _read_token(spec_dir)
    if not token:
        if os.path.exists(module):
            os.remove(module)
        print("[spec] 更新用の鍵が無いので、鍵なしで組み立てます(公開リポジトリ向け)")
        return False
    key = secrets.token_hex(16)
    raw = token.encode("utf-8")
    k = key.encode("ascii")
    data = base64.b64encode(bytes(b ^ k[i % len(k)] for i, b in enumerate(raw))).decode("ascii")
    with open(module, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 組み立て時に build_version.write_update_token が作るファイル。\n"
                "# コミットしないこと(.gitignore 済み)。難読化はぱっと見よけだけ。\n"
                "KEY = %r\nDATA = %r\n" % (key, data))
    print("[spec] 更新用の鍵を積みます(%d 文字)" % len(token))
    return True
