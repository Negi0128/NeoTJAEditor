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
#: ジャケットが切り替わるときに重ねて入れ替える(クロスフェード)秒数
#: (利用者の指定 2026-11-13「ジャケット切り替わりの時フェードで切り返して
#: 欲しい」)。切り替わりの時刻を**真ん中**にして、前後へ半分ずつ。
#: パッと替わると目がちらつくが、長すぎると2枚が混ざって誰の絵か
#: 分からない時間が延びる。0.3 秒は「替わったのは分かるが、ちらつかない」
#: くらい。帯がこれより短いときは帯の長さまで縮める(jacket_switches)。
JACKET_FADE_SEC = 0.3
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


def drop_missing(segments, exists=os.path.exists):
    """無い絵の区間を「何も出さない」("")にして、隣り合う同じ絵をつなぐ。

    フェードは「前に見えていた物 → 次に見える物」で決まる。消えた絵の
    区間を残したままだと、見えない絵へ向かって薄くなったり、""どうしの
    あいだで切り替わりを数えたりしてしまう。"""
    out = []
    for start, end, path in segments or ():
        path = path if (path and exists(path)) else ""
        if out and out[-1][2] == path:
            out[-1] = (out[-1][0], end, path)
        else:
            out.append((start, end, path))
    return out


def jacket_switches(segments, fade=JACKET_FADE_SEC):
    """切り替わりごとの [(時刻, フェードの秒数, 前の絵, 次の絵)]。

    フェードは切り替わりの時刻を真ん中にして [時刻 - d/2, 時刻 + d/2]。
    d は fade と、前後の区間の長さのうち一番短いもの — 短い帯でも、
    両側の切り替わりのフェードが半分ずつしか食わないので重ならない
    (多くても端で接するだけ)。頭からの区間(始まり None)・最後までの
    区間(終わり None)は長さ無限として数える。動画の頭には切り替わりが
    無いので、最初の絵はフェードせずにそのまま出る。"""
    out = []
    segs = list(segments or ())
    for i in range(len(segs) - 1):
        s0, t, old = segs[i]
        _t, e1, new = segs[i + 1]
        if t is None or old == new:
            continue
        d = float(fade)
        if s0 is not None:
            d = min(d, float(t) - float(s0))
        if e1 is not None:
            d = min(d, float(e1) - float(t))
        if d <= 0.0:
            continue
        out.append((float(t), d, old, new))
    return out


def blend_at(segments, t, fade=JACKET_FADE_SEC):
    """その時刻に重ねる絵 [(道, 不透明度)]。下から順に描けば録画と同じ。

    フェードの中なら「前の絵を不透明で → 次の絵を a で」(a は 0→1)。
    描いた結果は a*次 + (1-a)*前 で、真ん中(a=0.5)はちょうど半々。
    片方が無い(基本ジャケットも無い)ときは、ある方だけを薄くする。
    フェードの外は image_at と同じ1枚(無ければ空のリスト)。"""
    t = float(t)
    for when, d, old, new in jacket_switches(segments, fade):
        a0 = when - d / 2.0
        if a0 <= t < a0 + d:
            a = (t - a0) / d
            if old and new:
                return [(old, 1.0), (new, a)]
            if new:
                return [(new, a)]
            return [(old, 1.0 - a)] if old else []
    path = image_at(segments, t)
    return [(path, 1.0)] if path else []


# ---- 歌詞の字のクロスフェード ----------------------------------------
# 利用者の指定 2026-11-13「LYRIC の切り替わりもフェードにしてほしい」。
# ジャケットと同じ秒数(JACKET_FADE_SEC)・同じ決まり(切り替わりの時刻を
# 真ん中に、前後の帯が短ければ縮める)で、字の入れ替わりも重ねて替える。
# **時刻だけで決まる純粋な計算**にしてある — 録画は画面外のゲーム画面を
# 動画の時刻で1コマずつ描くので、タイマーや経過時間で薄くすると、
# 見ているときと書き出したものとで違う絵になる。

class LyricTrack:
    """歌詞の並び [(時刻, 文字)] から作った、字の区切りと切り替わり。

    毎コマ作り直さないよう、呼び手は並びが変わったときだけ作る。"""

    def __init__(self, lyrics, fade=JACKET_FADE_SEC):
        # 区切りの作り方はジャケットと同じ(同じ時刻に2つあれば後のもの、
        # 同じ字が続く所はつなぐ)。中身が「絵の道」か「字」かの違いだけ。
        pts = sorted((float(t), i, str(txt or ""))
                     for i, (t, txt) in enumerate(lyrics or ()))
        segs = []
        cur_start, cur = None, ""
        for t, _i, txt in pts:
            if txt == cur:
                continue
            if cur_start is None or t > cur_start:
                segs.append((cur_start, t, cur))
            cur_start, cur = t, txt
        segs.append((cur_start, None, cur))
        out = []
        for s in segs:
            if out and out[-1][2] == s[2]:
                out[-1] = (out[-1][0], s[1], s[2])
            else:
                out.append(s)
        self.segments = out
        self.switches = jacket_switches(out, fade)
        #: フェードの始まり(時刻 - d/2)の並び。bisect で引くため。
        self._fade_starts = [w - d / 2.0 for w, d, _o, _n in self.switches]
        self._seg_starts = [s if s is not None else float("-inf")
                            for s, _e, _p in out]

    def layers_at(self, t):
        """その時刻に描く字 [(文字, 不透明度)]。

        フェードの中なら前の字が 1-a、次の字が a(a は 0→1)で、真ん中は
        どちらも半分。字はジャケットと違って重ねても下が透けて構わない
        (前の字を不透明のまま残すと、次の字と重なって読めない)。
        空の歌詞へ替わる所は前の字が薄くなって消え、最初の歌詞は薄い所から
        出てくる。フェードの外は1つ(無ければ空のリスト)。"""
        import bisect
        t = float(t)
        i = bisect.bisect_right(self._fade_starts, t) - 1
        if i >= 0:
            when, d, old, new = self.switches[i]
            a0 = self._fade_starts[i]
            if t < a0 + d:
                a = (t - a0) / d
                out = []
                if old:
                    out.append((old, 1.0 - a))
                if new:
                    out.append((new, a))
                return out
        k = bisect.bisect_right(self._seg_starts, t) - 1
        if k < 0:
            return []
        text = self.segments[k][2]
        return [(text, 1.0)] if text else []
