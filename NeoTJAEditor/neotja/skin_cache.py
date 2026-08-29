# -*- coding: utf-8 -*-
"""利用者が用意した TNDE の System フォルダから、描画側が読む素材一式を
ローカルのキャッシュへ展開する。

**なぜキャッシュへ展開するのか**
TNDE の素材は再配布できないのでアプリには一切同梱できない。かといって
描画側(game_screen.py / chart_preview_widget.py / chara.py の計11か所)を
「System のどのファイルか」で書き換えて回ると、chara.py のようにフォルダを
丸ごと走査している箇所まで作り直すことになる。

そこで、起動時に skin_map.SKIN_MAP のとおりに System から取り出したものを
**旧 skin/ とまったく同じ木構造**でキャッシュへ並べ、settings.skin_dir() が
そちらを指すようにした。描画側は1行も変わらない。

展開は毎回やると起動が目に見えて遅くなるので、System のパス・**System の中身
(全 source の大きさと更新時刻)**・SKIN_MAP の中身・アプリのバージョンから
作った指紋をキャッシュに書いておき、前回と変わっていなければ丸ごと省く
(2回目以降は実測 10ms 弱)。

指紋には「実際に置けた件数」も入れてあり、省く前に実数と突き合わせる。
指紋だけを信じていたころは、素材が消えていても(ウイルス対策の隔離、
ディスク不足、利用者の手による削除)二度と作り直されなかった。

なお、うまく展開できなかった回は**指紋を書かない**。書いてしまうと空同然の
キャッシュが「展開済み」になり、利用者は自力で復帰できなくなる。手動で
やり直したいときは環境設定の「素材を再展開する」(= ensure_cache(force=True))。
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from neotja import skin_map as skin_map_mod
from neotja.constants import VERSION

_log = logging.getLogger(__name__)

#: 妥当な System フォルダなら必ず持っているもの。TNDE の System は
#: TNDE-R/{Graphics,Sounds} を含む — この2つが揃っていれば、SKIN_MAP の
#: source が指す先も基本的にそこにある。
_REQUIRED_SUBDIRS = ("TNDE-R/Graphics", "TNDE-R/Sounds")

#: 展開の手順そのものを変えたときに上げる番号。指紋に混ぜてあるので、
#: これを変えるとキャッシュが作り直される(SKIN_MAP は同じでも、こちらの
#: 取り出し方を直したときに古い結果が残らないようにするため)。
#: 2 … 指紋に System の中身(sources)と書いた件数(files)を持たせた版。
_EXTRACT_FORMAT = 2

_FINGERPRINT_NAME = ".fingerprint.json"

#: 展開が「使えた」とみなす最低の成功率。これを下回ったときは**指紋を書かない**。
#:
#: 以前は1件も成功しなくても指紋を書いていたので、TNDE-R/{Graphics,Sounds} の
#: 殻だけがある System(別バージョン・コピー途中の配布物)を渡すと
#: ok=2 / failed=418 のまま「展開済み」になり、2回目以降は展開そのものを飛ばして
#: **永久に空のスキンで起動する**。しかも利用者にはキャッシュを消す以外の
#: 復帰手段が無かった。
#:
#: 一方で「1件でも失敗したら指紋を書かない」にすると、TNDE の版違いで数枚
#: 足りないだけの利用者が毎回2秒の展開をやり直すことになる。そこで
#: 「ほぼ揃っていれば指紋を書いて、足りないぶんは警告で伝える」(呼び出し側の
#: 責務)という線引きにした。
_MIN_OK_RATIO = 0.8

#: KIND_UNRESOLVED(System からの作り方が特定できなかったもの)を、近い候補で
#: 代用するかどうか。True にしてあるのは、絵が1枚も無いとレーンの土台や音符が
#: まるごと描かれなくなるため。ただし見た目は現物と一致しない。代用が悪目立ち
#: するようなら、ここを False にすれば「ファイル無し」= 描画側の自前の絵へ
#: 落ちる。skin_map 側が「扱いは呼び出し側で決めること」としている部分。
SUBSTITUTE_UNRESOLVED = True


# ----------------------------------------------------------------------
# System フォルダの在りか
# ----------------------------------------------------------------------

def _base_dir() -> Path:
    """exe の隣(凍結時)/ プロジェクトルート(開発時)。settings.skin_dir() と
    同じ流儀で求める — 「exe と同じ場所に置いてください」と案内する以上、
    settings 側とずれると案内した場所を見に行かないことになる。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def is_valid_system_dir(path) -> bool:
    """そのフォルダが TNDE の System として使えるか。

    名前が "System" かどうかでは見ない(利用者が改名していることがある)。
    中身に TNDE-R/Graphics と TNDE-R/Sounds が揃っているかだけで判定する。"""
    if not path:
        return False
    try:
        base = Path(path)
    except (TypeError, ValueError):
        return False
    if not base.is_dir():
        return False
    for rel in _REQUIRED_SUBDIRS:
        if not (base / rel).is_dir():
            return False
    return True


def find_system_dir(cfg=None):
    """System フォルダを探す。

    戻り値は (見つかった Path または None, 探した場所, 使えなかった指定 or None)。

    探すのは「利用者がはっきり指定したもの」と「案内している置き場(exe の
    隣)」の2つだけ。探した場所の一覧は、見つからなかったときのダイアログで
    そのまま並べる(「どこを見たのか」が分からないと利用者は直しようがない)。

    **なぜデスクトップ等の自動探索をやめたのか**
    以前はここに3段目として、デスクトップ・ダウンロード・OneDrive 配下を
    幅優先で掘って System を探す処理があった。やめた理由は3つ:
      * 起動のたびに、まだウィンドウが1つも出ていない状態でディスクを
        なめることになる。オンラインのみの OneDrive や切断済みの UNC が
        混ざると 1回の is_dir() が数秒ブロックし、利用者からは
        「起動しない」ようにしか見えなかった(打ち切りの仕掛けを何段も
        足してもこの気持ち悪さは消えない)。
      * 拾ってくるのが**利用者の意図しない System** でありうる。TNDE を
        複数版デスクトップに置いている人は、どれが使われたのか分からない。
      * 見つからなくても起動できるようになった(呼び出し側が内蔵スキンへ
        落とす)ので、無理に探し当てる必要そのものが無くなった。
    置き場が違うなら、案内のダイアログか環境設定から選んでもらう。

    3つ目の戻り値は「環境設定で指定されているのに使えなかったパス」。以前は
    これを黙って読み飛ばして次の候補へ進んでいたので、外付けや NAS が
    繋がっていない起動で、警告も無く別の System の素材に差し替わっていた
    (絵が変わった理由が利用者に分からない)。呼び出し側で伝えられるよう返す。"""
    searched = []
    unusable = None

    # 1. 環境設定で指定されたパス。
    configured = ((cfg or {}).get("system_dir") or "").strip()
    if configured:
        searched.append(configured)
        if is_valid_system_dir(configured):
            return Path(configured), searched, None
        unusable = configured

    # 2. exe の隣(開発時はプロジェクトルート)。案内している置き場。
    base = _base_dir()
    for cand in (base / "System", base.parent / "System"):
        searched.append(str(cand))
        if is_valid_system_dir(cand):
            return cand, searched, unusable

    return None, searched, unusable


# ----------------------------------------------------------------------
# キャッシュ
# ----------------------------------------------------------------------

def cache_dir() -> Path:
    """展開先。%LOCALAPPDATA%\\NeoTJAEditor\\skin_cache。

    LOCALAPPDATA を選んだ理由:
      * 中身は System から**いつでも作り直せる**派生物なので、漫遊
        (Roaming)させる意味が無い。20MB 超をローミングに置くと、ドメイン
        環境でログオンのたびに同期されて迷惑になる。
      * exe の隣には置けない。Program Files 配下に入れられていると書き込め
        ないし、そもそも再配布できない素材を配布物と同じ場所に増やしたくない。
      * ユーザーごとに分かれるので、共有 PC でも取り違えない。
    LOCALAPPDATA が無い環境(Windows 以外)では ~/.cache 相当へ落とす。"""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local)
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "NeoTJAEditor" / "skin_cache"


def _bundled_dir() -> Path:
    """同梱素材(作者の自作物)の置き場。PyInstaller で固めると
    sys._MEIPASS 配下へ展開されるので、そちらも見る。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "neotja" / "assets"
        if p.is_dir():
            return p
    return Path(__file__).resolve().parent / "assets"


def _skin_map_digest() -> str:
    """SKIN_MAP の中身のハッシュ。表が更新されたら展開もやり直す必要がある
    ので、指紋に混ぜる。dict の並び順に左右されないよう sort_keys で固める。"""
    blob = json.dumps(skin_map_mod.SKIN_MAP, sort_keys=True, ensure_ascii=False,
                      default=str)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


def _norm_path_for_fp(text: str) -> str:
    """指紋に入れるパスの正規化。

    Windows は大文字小文字を区別しないので、`C:\\TNDE\\System` と
    `c:\\tnde\\system` を別物と数えると意味の無い作り直しが起きる。逆に
    Linux / macOS(大文字小文字を区別する)では別のフォルダになりうるので、
    小文字化は Windows のときだけにする。以前は無条件に .lower() していた。"""
    if os.name == "nt":
        return text.lower()
    return text


def _iter_source_rels():
    """SKIN_MAP が System 側で読むファイルを、重複を除いて列挙する。
    compose の原本は entry ではなく layers 側が持っているので、そちらも拾う。"""
    rels = set()
    for entry in skin_map_mod.SKIN_MAP.values():
        src = entry.get("source")
        if src:
            rels.add(src)
        for layer in (entry.get("layers") or []):
            lsrc = layer.get("source")
            if lsrc:
                rels.add(lsrc)
    return rels


def _sources_digest(system_dir: Path) -> str:
    """System 側の素材そのものの指紋。全 source の (相対パス, 大きさ, 更新時刻)
    を集計してハッシュにする。

    **なぜ要るのか**
    以前の指紋は System の「パス」しか見ていなかったので、同じ場所のまま
    スキンを差し替えても、TNDE を上書き更新しても気づけず、古いキャッシュが
    そのまま使われ続けた。利用者から見ると「入れ替えたのに反映されない」。

    **なぜ os.scandir なのか**
    409件を1件ずつ os.stat すると実測 55ms かかり、展開を省く回(実測 1.8ms)の
    横に置くには重すぎる。source が入っているフォルダは 22 個しかないので、
    フォルダ単位で os.scandir する。Windows の scandir は列挙のついでに
    大きさと更新時刻を返すため、ファイル1件あたりの追加の問い合わせが要らない
    (実測 5ms)。

    更新時刻は st_mtime_ns で取る。秒の float だと環境によって丸めが入り、
    書き戻しただけのファイルを見逃すことがある。"""
    system_dir = Path(system_dir)
    by_dir = {}
    for rel in _iter_source_rels():
        head, _, name = rel.rpartition("/")
        by_dir.setdefault(head, set()).add(name)

    parts = []
    for head, names in by_dir.items():
        found = {}
        try:
            with os.scandir(str(system_dir / head)) as it:
                for ent in it:
                    if ent.name in names:
                        try:
                            st = ent.stat()
                        except OSError:
                            continue
                        found[ent.name] = (st.st_size, st.st_mtime_ns)
        except OSError:
            # フォルダごと無い。「無い」ことも指紋の一部なので、下で 0 として入る。
            pass
        for name in names:
            size, mtime = found.get(name, (-1, -1))
            parts.append("%s/%s|%d|%d" % (head, name, size, mtime))

    parts.sort()
    return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()


def _fingerprint(system_dir: Path) -> dict:
    return {
        "system_dir": _norm_path_for_fp(str(Path(system_dir).resolve())),
        "skin_map": _skin_map_digest(),
        "app_version": VERSION,
        "format": _EXTRACT_FORMAT,
        "sources": _sources_digest(system_dir),
    }


def _read_fingerprint(dest: Path):
    try:
        with open(dest / _FINGERPRINT_NAME, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 — 壊れていれば「指紋なし」= 作り直し。
        return None


def _count_cache_files(dest: Path) -> int:
    """キャッシュに実在するファイルの数(指紋そのものは数えない)。

    os.scandir で一度なめるだけなので実測 2.5ms(421件)。1件ずつ
    os.path.exists で確かめるより速く、フォルダの数も知らずに済む。"""
    n = 0
    stack = [str(dest)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for ent in it:
                    try:
                        if ent.is_dir(follow_symlinks=False):
                            stack.append(ent.path)
                            continue
                    except OSError:
                        continue
                    if ent.name != _FINGERPRINT_NAME:
                        n += 1
        except OSError:
            continue
    return n


def cached_file_count() -> int:
    """いまキャッシュに残っている素材の数。

    System が見つからないときの案内で使う。0 なら描画側は本当に自前の絵
    (= 内蔵スキン)だけで動くが、前に別の System から展開したものが残って
    いれば、それはそのまま使われる。どちらなのかを黙っていると
    「内蔵スキンで起動したはずなのに絵が出ている」ことになるので、
    文面を出し分けるために数える。"""
    return _count_cache_files(cache_dir())


def _cache_is_usable(dest: Path, want: dict) -> bool:
    """展開を省いてよいか。指紋の一致だけでなく、**中身が本当に在るか**まで見る。

    指紋だけを信じていたころは、ウイルス対策に1枚隔離された・ディスクが
    一杯で後半が書けなかった・利用者が一部を消した、のどれでも
    「展開済み」と判断され、二度と作り直されなかった。そこで指紋に書いた
    件数(files)を持たせ、起動のたびに実数と突き合わせる。

    実数が指紋より**少ない**ときだけ作り直す。多いぶんは、利用者が何かを
    置いた程度のことで毎回2秒の展開をやり直させたくないので許す。"""
    have = _read_fingerprint(dest)
    if not have:
        return False
    for key in want:
        if have.get(key) != want[key]:
            return False
    n = have.get("files")
    if not isinstance(n, int) or n <= 0:
        # 件数を持たない古い版の指紋。作り直して新しい形にする。
        return False
    return _count_cache_files(dest) >= n


# ----------------------------------------------------------------------
# 1件ぶんの取り出し
# ----------------------------------------------------------------------

def _extract_copy(system_dir: Path, entry, out: Path) -> bool:
    src = system_dir / entry["source"]
    if not src.is_file():
        _log.warning("System に見当たらない: %s", entry["source"])
        return False
    shutil.copyfile(str(src), str(out))
    return True


def _extract_crop(system_dir: Path, entry, out: Path) -> bool:
    rect = entry.get("rect")
    if not rect:
        _log.warning("crop なのに矩形が無い: %s", entry.get("source"))
        return False
    src = system_dir / entry["source"]
    if not src.is_file():
        _log.warning("System に見当たらない: %s", entry["source"])
        return False
    # Pillow はここでしか要らないので遅延 import。起動のたびに読み込むと
    # 展開を省いた回でも数十msぶん損をする。
    from PIL import Image
    left, top, width, height = rect
    with Image.open(str(src)) as im:
        im.crop((left, top, left + width, top + height)).save(str(out))
    return True


def _extract_compose(system_dir: Path, entry, out: Path) -> bool:
    """System の複数の絵を重ねて1枚に焼く。

    旧 skin/ の何枚か(上背景・音符・コンボ文字・ネームプレート)は、System の
    どの1枚とも一致せず、複数の素材を重ねた合成物だった。skin_map がその
    重ね方(下から順の layers、各層の切り出し矩形 rect と貼る位置 pos、
    横方向に敷き詰めるかの tile_x)を持っているので、そのとおりに作る。"""
    size = entry.get("size")
    layers = entry.get("layers")
    if not size or not layers:
        _log.warning("compose なのに size / layers が無い: %s", out.name)
        return False
    from PIL import Image
    canvas = Image.new("RGBA", (int(size[0]), int(size[1])), (0, 0, 0, 0))

    def put(piece, x, y):
        """canvas からはみ出すぶんを落として重ねる。alpha_composite は
        はみ出しを許さず例外になるので、先に切っておく(横に敷き詰めると
        最後の1枚がほぼ必ずはみ出す)。"""
        sx = max(0, -x)
        sy = max(0, -y)
        w = min(piece.width - sx, canvas.width - max(x, 0))
        h = min(piece.height - sy, canvas.height - max(y, 0))
        if w <= 0 or h <= 0:
            return
        clipped = piece.crop((sx, sy, sx + w, sy + h)) \
            if (sx or sy or w != piece.width or h != piece.height) else piece
        canvas.alpha_composite(clipped, (max(x, 0), max(y, 0)))

    for layer in layers:
        src = system_dir / layer["source"]
        if not src.is_file():
            _log.warning("System に見当たらない: %s", layer["source"])
            return False
        with Image.open(str(src)) as im:
            piece = im.convert("RGBA")
            rect = layer.get("rect")
            if rect:
                left, top, width, height = rect
                piece = piece.crop((left, top, left + width, top + height))
            x, y = layer.get("pos") or (0, 0)
            if layer.get("tile_x") and piece.width > 0:
                # 流れる背景は横に継ぎ目なく続く絵なので、幅ぶんだけ敷き詰める。
                while x < canvas.width:
                    put(piece, int(x), int(y))
                    x += piece.width
            else:
                put(piece, int(x), int(y))
    canvas.save(str(out))
    return True


def _extract_bundled(entry, rel: str, out: Path) -> bool:
    src = _bundled_dir() / Path(rel).name
    if not src.is_file():
        _log.warning("同梱素材が見つからない: %s", src)
        return False
    shutil.copyfile(str(src), str(out))
    return True


def _extract_decode(system_dir: Path, entry, out: Path) -> bool:
    """ogg を wav へデコードする。imageio-ffmpeg が抱えている ffmpeg を使う
    (動画書き出しで既に依存しているので、新しい要求は増えない)。

    なお skin_map の但し書きどおり、これで得られる波形は旧 skin/ の wav とは
    一致しない(別録りの音)。役割の上での代用であって、同じ音にはならない。"""
    src = system_dir / entry["source"]
    if not src.is_file():
        _log.warning("System に見当たらない: %s", entry["source"])
        return False
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        _log.warning("ffmpeg が使えないので音のデコードを飛ばす: %s", exc)
        return False
    cmd = [exe, "-y", "-loglevel", "error", "-i", str(src),
           "-acodec", "pcm_s16le", str(out)]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:  # noqa: BLE001
        _log.warning("デコードに失敗: %s (%s)", entry["source"], exc)
        return False
    return True


def _extract_unresolved(system_dir: Path, entry, out: Path) -> bool:
    """System からの作り方が特定できなかったもの。skin_map は「扱いは
    呼び出し側で決めること」としているので、ここでは**近い候補で代用する**。

    絵が1枚も無いと描画側がその要素をまるごと描けなくなる(レーンの土台や
    音符の絵が欠ける)ため、見た目が多少違っても出しておくほうがましと判断
    した。候補すら無いものは黙って諦める — 描画側はファイルが無い場合に
    自前の絵へ落ちるようにできている。"""
    if not SUBSTITUTE_UNRESOLVED or not entry.get("source"):
        return False
    if entry.get("rect"):
        return _extract_crop(system_dir, entry, out)
    return _extract_copy(system_dir, entry, out)


# ----------------------------------------------------------------------
# 展開
# ----------------------------------------------------------------------

def ensure_cache(system_dir, force: bool = False) -> dict:
    """System からキャッシュへ展開する。既に同じ指紋で展開済みなら何もしない。

    戻り値は結果の要約 dict:
      skipped  … 展開を省いたか
      elapsed  … かかった秒数
      ok/failed/unknown … 成功・失敗・未知の種別だった件数
      total    … SKIN_MAP の件数
      trusted  … 指紋を書いたか(= 次回この結果を信用して省いてよいか)
      error    … キャッシュへ書けなかったときの説明。無事なら None
      dest     … 展開先

    **例外は投げない。** 素材が用意できなくても、呼び出し側が案内を出して
    穏やかに終われるようにするため、書き込みの失敗も error に載せて返す。
    """
    started = time.perf_counter()
    dest = cache_dir()
    total = len(skin_map_mod.SKIN_MAP)

    def result(**kw):
        base = {"skipped": False, "elapsed": time.perf_counter() - started,
                "ok": 0, "failed": 0, "unknown": 0, "total": total,
                "trusted": False, "error": None, "dest": dest}
        base.update(kw)
        return base

    want = _fingerprint(system_dir)

    if not force and _cache_is_usable(dest, want):
        return result(skipped=True, trusted=True)

    system_dir = Path(system_dir)

    # 作り直す前に古いキャッシュを丸ごと捨てる。残しておくと前回の残骸が
    # 新しい素材に混ざる — chara.py は 0.png からの連番を数えてコマ数を決めて
    # いるので、前のキャラのほうがコマ数が多いと**古い絵が続きとして再生される**。
    # 指紋も一緒に消えるので、途中で落ちても「展開済み」と誤認しない。
    if dest.exists():
        shutil.rmtree(str(dest), ignore_errors=True)

    # ここから先は %LOCALAPPDATA% への書き込み。読み取り専用にされている・
    # ディスクが一杯・別ユーザーの持ち物、といった事情で失敗しうるが、以前は
    # 例外がそのまま呼び出し側へ抜けて**起動そのものができなかった**
    # (ウィンドウも案内も出ないまま落ちる)。何が起きたかを返して、呼び出し側で
    # 案内を出せるようにする。
    try:
        dest.mkdir(parents=True, exist_ok=True)
        # 420件を2秒かけて全部失敗してから気づくのは無駄なので、先に1回試す。
        probe = dest / ".writetest"
        with open(probe, "w", encoding="utf-8") as f:
            f.write("")
        probe.unlink()
    except OSError as exc:
        _log.warning("キャッシュへ書き込めない: %s (%s)", dest, exc)
        return result(error="%s\n\n%s" % (dest, exc))

    handlers = {
        skin_map_mod.KIND_COPY: lambda e, r, o: _extract_copy(system_dir, e, o),
        skin_map_mod.KIND_CROP: lambda e, r, o: _extract_crop(system_dir, e, o),
        skin_map_mod.KIND_BUNDLED: lambda e, r, o: _extract_bundled(e, r, o),
        skin_map_mod.KIND_DECODE: lambda e, r, o: _extract_decode(system_dir, e, o),
        skin_map_mod.KIND_UNRESOLVED:
            lambda e, r, o: _extract_unresolved(system_dir, e, o),
    }
    # KIND_COMPOSE は後から skin_map に増えた種別。表の側がまだ持っていない
    # 版でも動くよう getattr で拾う(無ければ登録しない = 未知として飛ばす)。
    _compose = getattr(skin_map_mod, "KIND_COMPOSE", None)
    if _compose:
        handlers[_compose] = lambda e, r, o: _extract_compose(system_dir, e, o)

    ok = failed = unknown = 0
    made_dirs = set()
    for rel, entry in skin_map_mod.SKIN_MAP.items():
        kind = entry.get("kind")
        handler = handlers.get(kind)
        if handler is None:
            # 種別は今後増えうる。知らないものが来ても展開全体を止めず、
            # その1件だけ飛ばして残りを揃える(絵が1枚欠けるだけで済む)。
            unknown += 1
            _log.warning("未知の種別 %r なので飛ばす: %s", kind, rel)
            continue
        out = dest / rel
        parent = out.parent
        if parent not in made_dirs:
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                # フォルダが作れないなら、その下の1件も置けない。展開全体は
                # 止めず(他のフォルダは書けるかもしれない)、失敗として数える。
                failed += 1
                _log.warning("フォルダが作れない: %s (%s)", parent, exc)
                continue
            made_dirs.add(parent)
        try:
            if handler(entry, rel, out):
                ok += 1
            else:
                failed += 1
        except Exception as exc:  # noqa: BLE001 — 1件の失敗で起動を止めない。
            failed += 1
            _log.warning("展開に失敗: %s (%s)", rel, exc)

    # 指紋を書くのは「使える結果になった」ときだけ。書かなければ次の起動でも
    # もう一度展開を試すので、System を直せばそれだけで直る(利用者が
    # キャッシュフォルダを探して消す必要がない)。_MIN_OK_RATIO 参照。
    attempted = ok + failed
    trusted = attempted > 0 and ok >= attempted * _MIN_OK_RATIO
    if trusted:
        want = dict(want)
        # 実際に置けた件数。次の起動で実数と突き合わせて、消えていたら作り直す。
        want["files"] = ok
        try:
            with open(dest / _FINGERPRINT_NAME, "w", encoding="utf-8") as f:
                json.dump(want, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            # 書けなくても致命ではない(素材は揃っている)。次の起動で
            # 展開をやり直すだけなので、起動は続けさせる。
            _log.warning("指紋が書けない: %s", exc)
            trusted = False
    else:
        _log.warning("展開がほとんど成功しなかったので指紋を書かない: "
                     "ok=%d failed=%d", ok, failed)

    return result(ok=ok, failed=failed, unknown=unknown, trusted=trusted)
