"""保存したネームプレートの一覧と、その書き出し・読み込み。

ゲーム画面が読むのは今までどおり設定の平らなキー(nameplate_name など)で、
これは「いま使っている1枚」。ここで扱う nameplate_profiles は、それとは別に
取っておく控えの一覧。描く側を一切触らずに「何枚も持って切り替える」を
足すため、こういう二段の形にしてある。

画面(環境設定)から切り離してあるのは、読み込みの検査を試験で確かめられる
ようにするため。読み込むファイルはよその PC から来るので、何が入っていても
落ちずに弾けることが要る。
"""
import base64
import binascii
import copy
import hashlib
import json
import os
import re
from pathlib import Path

from neotja import settings as settings_mod

#: 書き出したファイルの印。違うものを読み込もうとしたら弾く。
FORMAT_NAME = "NeoTJAEditor-nameplate"
FORMAT_VERSION = 1

#: 1枚のネームプレートを作っている設定のキー。並びは環境設定の入力欄と同じ。
PLATE_KEYS = (
    "nameplate_name", "nameplate_title", "nameplate_title_type",
    "nameplate_title_image",
    "nameplate_title_image_dx", "nameplate_title_image_dy",
    "nameplate_title_image_dw", "nameplate_title_image_dh",
    "nameplate_dan", "nameplate_dan_type", "nameplate_dan_text_color",
    "nameplate_title_dx", "nameplate_title_dy", "nameplate_title_size",
    "nameplate_name_dx", "nameplate_name_dy", "nameplate_name_size",
    "nameplate_dan_dx", "nameplate_dan_dy", "nameplate_dan_size",
)

#: 文字の長さの上限。入力欄の setMaxLength と同じ数字にしてある —
#: 読み込みで長いものが入ると、入力欄に入れた時点で黙って切れてしまうため。
LABEL_MAX = 32
TITLE_MAX = 64
NAME_MAX = 32
#: 画像の道の上限(手元の道なので、普通はこれで足りる)。
PATH_MAX = 1024
#: 埋め込む画像の上限(元のバイト数)。称号の帯1枚なので、これを超えるものは
#: 何かの間違いとみなす。
IMAGE_MAX_BYTES = 5 * 1024 * 1024
#: 1つのファイルから読むネームプレートの数の上限。
PLATES_MAX = 100
#: 読み込むファイルそのものの上限。画像を base64 で埋めると 4/3 倍に膨らむ
#: ので、画像の上限より大きめに取ってある。
FILE_MAX_BYTES = 32 * 1024 * 1024

#: 数値の取りうる幅。環境設定の入力欄(_text_rows / _rect_rows)と同じ。
_RANGES = {
    "nameplate_title_image_dx": (-400, 400),
    "nameplate_title_image_dy": (-400, 400),
    "nameplate_title_image_dw": (-400, 400),
    "nameplate_title_image_dh": (-400, 400),
    "nameplate_title_dx": (-200, 200),
    "nameplate_title_dy": (-200, 200),
    "nameplate_title_size": (6, 60),
    "nameplate_name_dx": (-200, 200),
    "nameplate_name_dy": (-200, 200),
    "nameplate_name_size": (6, 60),
    "nameplate_dan_dx": (-200, 200),
    "nameplate_dan_dy": (-200, 200),
    "nameplate_dan_size": (6, 60),
}
_STR_MAX = {
    "nameplate_name": NAME_MAX,
    "nameplate_title": TITLE_MAX,
    "nameplate_title_image": PATH_MAX,
}
#: 選択肢から選ぶ項目。外れた値は既定へ戻す(描く側が知らない値を渡さない)。
_CHOICES = {
    "nameplate_title_type": tuple(v for _, v in settings_mod.NAMEPLATE_TITLE_TYPES),
    "nameplate_dan_type": tuple(v for _, v in settings_mod.NAMEPLATE_DAN_TYPES),
    "nameplate_dan_text_color": tuple(
        v for _, v in settings_mod.NAMEPLATE_DAN_TEXT_COLORS),
    "nameplate_dan": tuple(settings_mod.NAMEPLATE_DAN_NAMES),
}

#: NamePlate フォルダで意味を持つファイル名。読み込んだ画像がこの名前だと、
#: 部品シートや称号の差し替えとして拾われてしまうので避ける(小文字で比べる)。
_RESERVED_NAMES = {"nameplate.png", "nameplate_parts.png", "nameplate_title.png"}
_IMAGE_EXTS = (".png", ".webp", ".jpg", ".jpeg")


class NameplateImportError(ValueError):
    """読み込めないファイル。メッセージはそのまま利用者に見せる。"""


_DEFAULTS = None


def _defaults() -> dict:
    # 一度だけ作って使い回す。譜面プレビューは load_settings() を毎コマ呼び、
    # そこから ensure_profiles が1枚ごとにここを通るため。
    global _DEFAULTS
    if _DEFAULTS is None:
        d = settings_mod.default_settings()
        _DEFAULTS = {k: d[k] for k in PLATE_KEYS}
    return _DEFAULTS


def _clean_text(value, limit: int) -> str:
    # 改行やタブは1行の入力欄に入らないので空白へ寄せる。
    text = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return text[:limit]


def sanitize_values(raw) -> dict:
    """ネームプレートの値を、描く側が受け取れる形へそろえる。

    知らないキーは捨て、足りないキーは既定で埋め、型と幅を直す。
    手で書き換えた設定ファイルや、よその PC から来たファイルを
    そのまま描く側へ渡さないための関所。"""
    defaults = _defaults()
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key in PLATE_KEYS:
        default = defaults[key]
        value = raw.get(key, default)
        if isinstance(default, int):
            if isinstance(value, bool):
                value = default
            try:
                value = int(round(float(value)))
            except (TypeError, ValueError, OverflowError):
                value = default
            lo, hi = _RANGES.get(key, (None, None))
            if lo is not None:
                value = max(lo, min(hi, value))
        else:
            value = _clean_text(value, _STR_MAX.get(key, 64)) \
                if isinstance(value, (str, int, float)) and not isinstance(value, bool) \
                else default
        if key in _CHOICES and value not in _CHOICES[key]:
            value = default
        out[key] = value
    return out


def clean_label(label, fallback: str = "ネームプレート") -> str:
    text = _clean_text(label, LABEL_MAX).strip() if isinstance(label, str) else ""
    return text or fallback


def profile_from_values(label, values) -> dict:
    """入力欄の値(または設定の平らなキー)から、保存用の1枚を作る。"""
    vals = sanitize_values(values)
    prof = {"label": clean_label(label, vals["nameplate_name"] or "ネームプレート")}
    prof.update(vals)
    return prof


def values_of(profile) -> dict:
    """保存した1枚から、設定の平らなキーと同じ形の辞書を取り出す。"""
    return sanitize_values(profile)


def unique_label(label: str, existing) -> str:
    """一覧に同じ名前があれば「 (2)」「 (3)」… を付けて重ならないようにする。
    同じ名前が並ぶと、選ぶ欄でどれがどれか見分けられないため。"""
    taken = set(existing)
    if label not in taken:
        return label
    n = 2
    while True:
        suffix = " (%d)" % n
        cand = label[:max(1, LABEL_MAX - len(suffix))] + suffix
        if cand not in taken:
            return cand
        n += 1


def ensure_profiles(cfg: dict) -> None:
    """設定の一覧を整える。空なら、いまの平らな値から1枚作る。

    この機能より前から使っている人は一覧を持っていないので、最初に開いた
    ときにいまのネームプレートを1枚目として取っておく(何も失わないように)。"""
    profiles = cfg.get("nameplate_profiles")
    cleaned = []
    if isinstance(profiles, list):
        for p in profiles[:PLATES_MAX]:
            if isinstance(p, dict):
                prof = profile_from_values(p.get("label"), p)
                prof["label"] = unique_label(prof["label"],
                                             [q["label"] for q in cleaned])
                cleaned.append(prof)
    if not cleaned:
        current = {k: cfg[k] for k in PLATE_KEYS if k in cfg}
        cleaned = [profile_from_values(current.get("nameplate_name"), current)]
    cfg["nameplate_profiles"] = cleaned
    index = cfg.get("nameplate_profile_index", 0)
    if not isinstance(index, int) or isinstance(index, bool):
        index = 0
    cfg["nameplate_profile_index"] = max(0, min(len(cleaned) - 1, index))


# ---------------------------------------------------------------- 書き出し

def export_dict(profile) -> dict:
    """1枚をファイルへ書く形にする。

    称号の画像は手元の道のままだと別の PC では意味が無いので、
    ファイルがあれば中身を base64 で埋め込み、道は書かない。"""
    vals = values_of(profile)
    plate = {"label": clean_label(profile.get("label"), vals["nameplate_name"])}
    image_path = vals.pop("nameplate_title_image")
    plate.update(vals)
    if image_path:
        try:
            p = Path(image_path)
            if p.is_file() and p.stat().st_size <= IMAGE_MAX_BYTES:
                data = p.read_bytes()
                plate["title_image"] = {
                    "filename": p.name,
                    "data": base64.b64encode(data).decode("ascii"),
                }
        except OSError:
            pass   # 読めない画像は付けずに書き出す(ネームプレートそのものは渡せる)
    return {"format": FORMAT_NAME, "version": FORMAT_VERSION, "plates": [plate]}


def export_json(profile) -> str:
    return json.dumps(export_dict(profile), ensure_ascii=False, indent=2)


def safe_file_stem(label: str) -> str:
    """書き出しの既定のファイル名。Windows で使えない文字を除く。"""
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", label).strip(" .")
    return stem or "nameplate"


# ---------------------------------------------------------------- 読み込み

def _is_image(data: bytes) -> bool:
    from PySide6.QtGui import QImage
    img = QImage()
    return img.loadFromData(data) and not img.isNull()


def _decode_image(obj, is_image) -> tuple:
    """埋め込み画像を (ファイル名, 中身) にする。おかしければ例外。"""
    if not isinstance(obj, dict):
        raise NameplateImportError("称号の画像の形が正しくありません。")
    data = obj.get("data")
    if not isinstance(data, str) or len(data) > IMAGE_MAX_BYTES * 4 // 3 + 16:
        raise NameplateImportError("称号の画像が大きすぎるか、形が正しくありません。")
    try:
        raw = base64.b64decode(data.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise NameplateImportError("称号の画像を読めませんでした。") from None
    if not raw or len(raw) > IMAGE_MAX_BYTES:
        raise NameplateImportError("称号の画像が大きすぎます(5MB まで)。")
    if not is_image(raw):
        raise NameplateImportError("称号の画像が壊れているか、画像ではありません。")
    name = obj.get("filename")
    return (name if isinstance(name, str) else ""), raw


def parse_import(data, is_image=None) -> list:
    """読み込んだ JSON(文字列か辞書)を検査して、ネームプレートの一覧にする。

    返すのは [(1枚の辞書, 画像 or None), ...]。画像は (ファイル名, 中身)。
    1つでもおかしい所があればファイルごと弾く — 半分だけ入ると、どこまで
    入ったのか利用者から分からないため。"""
    if is_image is None:
        is_image = _is_image
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise NameplateImportError("文字コードが UTF-8 ではありません。") from None
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, RecursionError):
            raise NameplateImportError("JSON として読めませんでした。") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT_NAME:
        raise NameplateImportError(
            "NeoTJAEditor で書き出したネームプレートのファイルではありません。")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) \
            or version > FORMAT_VERSION:
        raise NameplateImportError(
            "新しい版の NeoTJAEditor で書き出されたファイルです。"
            "アプリを更新してから読み込んでください。")
    plates = data.get("plates")
    if not isinstance(plates, list) or not plates:
        raise NameplateImportError("ネームプレートが1枚も入っていません。")
    if len(plates) > PLATES_MAX:
        raise NameplateImportError("ネームプレートが多すぎます(%d 枚まで)。" % PLATES_MAX)
    result = []
    for raw in plates:
        if not isinstance(raw, dict):
            raise NameplateImportError("ネームプレートの形が正しくありません。")
        vals = dict(raw)
        # 手元の道は別の PC では意味が無いので、書いてあっても使わない。
        vals["nameplate_title_image"] = ""
        prof = profile_from_values(raw.get("label"), vals)
        image = None
        if raw.get("title_image") is not None:
            image = _decode_image(raw["title_image"], is_image)
        result.append((prof, image))
    return result


def _safe_image_name(filename: str, data: bytes) -> str:
    name = os.path.basename(filename.replace("\\", "/")) if filename else ""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    stem, ext = os.path.splitext(name)
    if ext.lower() not in _IMAGE_EXTS:
        # 拡張子が無い・おかしいときは中身から決める(Qt は中身で読むので
        # 実害は無いが、利用者がフォルダで見たときに分かるように)。
        ext = ".jpg" if data[:3] == b"\xff\xd8\xff" else \
            ".webp" if data[8:12] == b"WEBP" else ".png"
        stem = stem or name
    stem = (stem or "title")[:80]
    name = stem + ext
    if name.lower() in _RESERVED_NAMES:
        name = "title_" + name
    return name


def store_image(filename: str, data: bytes, dest_dir) -> Path:
    """埋め込み画像を NamePlate フォルダへ書く。

    同じ名前の別の絵がすでにあれば「 (2)」を付けて書く(手元の絵を消さない)。
    中身まで同じなら、それをそのまま使う(読み込むたびに複製が増えないように)。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_image_name(filename, data)
    stem, ext = os.path.splitext(name)
    digest = hashlib.sha256(data).digest()
    n = 1
    while True:
        cand = dest_dir / (name if n == 1 else "%s (%d)%s" % (stem, n, ext))
        if not cand.exists():
            cand.write_bytes(data)
            return cand
        try:
            if cand.is_file() and hashlib.sha256(cand.read_bytes()).digest() == digest:
                return cand
        except OSError:
            pass
        n += 1


def import_plates(data, existing_labels, dest_dir, is_image=None) -> list:
    """読み込んで、一覧へ足す新しいネームプレートを返す(元の一覧は触らない)。

    名前が重なれば「 (2)」を付け、画像は dest_dir へ書いてその道を入れる。
    上書きは決してしない — 読み込みで手元のネームプレートが消えると取り返せないため。"""
    parsed = parse_import(data, is_image)
    taken = list(existing_labels)
    out = []
    for prof, image in parsed:
        prof = copy.deepcopy(prof)
        prof["label"] = unique_label(prof["label"], taken)
        taken.append(prof["label"])
        if image is not None:
            path = store_image(image[0], image[1], dest_dir)
            prof["nameplate_title_image"] = str(path)
        out.append(prof)
    return out
