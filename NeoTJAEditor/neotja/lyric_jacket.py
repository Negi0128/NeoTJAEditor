# -*- coding: utf-8 -*-
"""歌詞(#LYRIC)に合わせて切り替えるジャケットの対応表(利用者の承認
2026-11-13「LYRIC に合わせたジャケット切り替え」)。

合作譜面で担当者の名前を歌詞に出しているあいだ、その人のアイコンを
ジャケットとして出すためのもの。普通の歌詞にも使える。

**どこに覚えるか**: 譜面の隣の `<名前>.tja.jacket.json`。TJA の本文には
1文字も書かない — ほかの太鼓シミュが知らない命令を足すと読めなくなる
おそれがあり、歌詞の文字そのものも利用者のものなので触りたくない。

    {"version": 1, "base": "<基本ジャケット>",
     "lyrics": {"<歌詞の文字>": "<画像>"}}

**何で引くか**: 歌詞の文字そのもの(解析と同じ切り方をした後の文字)。
同じ文字の歌詞はどこに何度出てきても同じ絵になる。帯ごとに覚えないので、
帯を動かしても伸ばしても対応はずれず、帯を消せばその区間には出ない。

**道の書き方**: 画像が譜面のフォルダ(とその下)にあれば譜面からの相対、
それ以外は絶対。フォルダごと渡したり動かしたりしても切れないように。
メモリの中では常に絶対の道で持ち、書くときだけ相対へ直す — 「名前を
付けて保存」で別のフォルダへ移っても、そこから数え直せば済むため。
"""
import json
import os
import tempfile

#: 譜面の道の後ろに付ける名前。
SIDECAR_SUFFIX = ".jacket.json"
#: 書く形の版。
VERSION = 1
#: 選べる画像。録画(ffmpeg)と作譜ペイン(Qt)のどちらでも読める形だけ。
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
IMAGE_FILTER = "画像 (*.png *.jpg *.jpeg *.webp *.bmp)"


def sidecar_path(tja_path):
    """譜面の隣の対応表の道。"""
    return str(tja_path) + SIDECAR_SUFFIX


def lyric_key(text):
    """対応表の鍵。解析(tja_analyzer)が歌詞に掛けるのと同じ前後の空白落とし。"""
    return str(text or "").strip()


def lyric_of_line(line):
    """TJA の1行が #LYRIC なら歌詞の文字を、違えば None を返す。

    解析と同じ切り方(// から先はコメント、命令名のあと行末までが歌詞、
    前後の空白を落とす)。文字の無い #LYRIC は "" を返す。"""
    s = str(line).split("//")[0].strip()
    if not s.startswith("#LYRIC"):
        return None
    return s[len("#LYRIC"):].strip()


def count_lyric(tja_text, text):
    """本文の中に同じ歌詞が何か所あるか(全コース)。窓の「N か所に効きます」用。

    対応表は譜面ごと(コースをまたいで共通)なので、数えるのも全コース。"""
    key = lyric_key(text)
    if not key:
        return 0
    n = 0
    for line in str(tja_text or "").splitlines():
        if lyric_of_line(line) == key:
            n += 1
    return n


def _norm(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def to_stored(path, folder):
    """書くときの道。folder(譜面のフォルダ)の中なら相対、外なら絶対。"""
    path = str(path or "")
    if not path:
        return ""
    ap = os.path.abspath(path)
    if folder:
        af = os.path.abspath(folder)
        try:
            inside = os.path.commonpath([_norm(ap), _norm(af)]) == _norm(af)
        except ValueError:
            # ドライブが違う(C: と D:)と commonpath が投げる。外扱い。
            inside = False
        if inside:
            # 区切りは / にそろえる。手で開いて読んだときに見やすく、
            # Windows でもそのまま通る。
            return os.path.relpath(ap, af).replace(os.sep, "/")
    return ap


def from_stored(stored, folder):
    """読んだ道を絶対の道へ。相対なら folder から数える。"""
    s = str(stored or "").strip()
    if not s:
        return ""
    if os.path.isabs(s) or not folder:
        return os.path.normpath(s)
    return os.path.normpath(os.path.join(folder, s))


class LyricJackets:
    """基本ジャケットと、歌詞ごとの絵。中の道はすべて絶対。"""

    def __init__(self, base="", lyrics=None):
        self.base = str(base or "")
        self.lyrics = {}
        for k, v in dict(lyrics or {}).items():
            key, val = lyric_key(k), str(v or "")
            if key and val:
                self.lyrics[key] = val

    def __eq__(self, other):
        return (isinstance(other, LyricJackets) and self.base == other.base
                and self.lyrics == other.lyrics)

    def is_empty(self):
        return not self.base and not self.lyrics

    def copy(self):
        return LyricJackets(self.base, dict(self.lyrics))

    def image_for(self, text):
        """その歌詞に付けた絵。無ければ ""。空の歌詞には付かない。"""
        key = lyric_key(text)
        return self.lyrics.get(key, "") if key else ""

    def set_image(self, text, path):
        """付ける/外す(path が空なら外す)。変わったら True。"""
        key = lyric_key(text)
        if not key:
            return False
        path = str(path or "")
        if not path:
            return self.lyrics.pop(key, None) is not None
        if self.lyrics.get(key) == path:
            return False
        self.lyrics[key] = path
        return True

    def set_base(self, path):
        """基本ジャケットを替える。変わったら True。"""
        path = str(path or "")
        if path == self.base:
            return False
        self.base = path
        return True

    # ---- 書く / 読む --------------------------------------------------
    def to_dict(self, folder):
        return {"version": VERSION,
                "base": to_stored(self.base, folder),
                "lyrics": {k: to_stored(v, folder)
                           for k, v in sorted(self.lyrics.items())}}

    @classmethod
    def from_dict(cls, data, folder):
        """形が崩れていれば、読める所だけ読む(読めなければ空)。"""
        if not isinstance(data, dict):
            return cls()
        base = data.get("base", "")
        base = from_stored(base, folder) if isinstance(base, str) else ""
        lyrics = {}
        raw = data.get("lyrics")
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, str) and v.strip():
                    lyrics[k] = from_stored(v, folder)
        return cls(base, lyrics)

    @classmethod
    def load(cls, tja_path):
        """譜面の隣から読む。無い・壊れている → 空(落とさない)。"""
        if not tja_path:
            return cls()
        p = sidecar_path(tja_path)
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            # 無い(ふつう)・読めない・JSON でない。どれも「何も付いて
            # いない」として開く — 譜面が開けないほうがずっと困る。
            return cls()
        return cls.from_dict(data, os.path.dirname(os.path.abspath(tja_path)))

    def save(self, tja_path):
        """譜面の隣へ書く。書いたら True。

        **空なら書かない** — ただし前に書いた物があれば空で上書きする。
        黙って消すと、消したつもりのない人のファイルまで無くなったように
        見えるので、消すことはしない(利用者の指定 2026-11-13)。"""
        if not tja_path:
            return False
        p = sidecar_path(tja_path)
        if self.is_empty() and not os.path.exists(p):
            return False
        folder = os.path.dirname(os.path.abspath(tja_path))
        text = json.dumps(self.to_dict(folder), ensure_ascii=False, indent=2)
        # 譜面と同じく、一時ファイルに書いてから置き換える(途中で落ちても
        # 前の中身が残る)。
        fd, tmp = tempfile.mkstemp(prefix=".neotja_jk_", suffix=".tmp",
                                   dir=folder)
        os.close(fd)
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            os.replace(tmp, p)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return True


# ---- いつ、どの絵か ----------------------------------------------------
def jacket_segments(lyrics, jackets):
    """時間の区切りごとの絵 [(始まり, 終わり, 道)] を返す。

    lyrics は解析の [(時刻, 歌詞)](空の歌詞 = そこで消す合図)。時刻の
    基準は渡したとおり(譜面時刻でも音源時刻でも)で、返す時刻も同じ基準。
    始まりの None は「頭から」、終わりの None は「最後まで」。道の "" は
    「何も出さない」(基本ジャケットも無いとき)。

    決まり(企画案の表):
      - 最初の歌詞より前、空の歌詞、絵の無い歌詞 → 基本ジャケット
      - 絵を付けた歌詞の帯の内側 → その絵
      - 帯の始まりで切り替わり、次の #LYRIC で終わる。絵の付いた帯が
        続けば絵から絵へそのまま替わる。
    区間は [始まり, 終わり) — 始まりを含み、終わりは次の区間のもの。
    同じ絵が続く区切りはまとめる(録画のフィルタを短くするため)。
    """
    base = jackets.base if jackets is not None else ""
    pts = sorted((float(t), i, str(txt or ""))
                 for i, (t, txt) in enumerate(lyrics or ()))
    segs = []
    cur_start, cur_path = None, base
    for t, _i, txt in pts:
        path = (jackets.image_for(txt) if (jackets and txt) else "") or base
        if path == cur_path:
            continue
        if cur_start is None or t > cur_start:
            segs.append((cur_start, t, cur_path))
        cur_start, cur_path = t, path
    segs.append((cur_start, None, cur_path))
    # 同じ時刻に歌詞が2つあった等で、同じ絵が隣り合ったらつなぐ。
    out = []
    for s in segs:
        if out and out[-1][2] == s[2]:
            out[-1] = (out[-1][0], s[1], s[2])
        else:
            out.append(s)
    return out


def image_at(segments, t):
    """その時刻の絵。途中から見ても巻き戻しても、区切りだけで決まる。"""
    t = float(t)
    for start, end, path in segments or ():
        if (start is None or start <= t) and (end is None or t < end):
            return path
    return ""
