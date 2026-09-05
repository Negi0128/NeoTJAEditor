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
