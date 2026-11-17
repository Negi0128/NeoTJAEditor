"""Editor / Player が共有する曲別の背景・基本ジャケット。

設定辞書の保存は呼び手が行う。rec_bg_*（全曲の既定）は変更しない。
基本ジャケットは既存 .tja.jacket.json の base を正とし、歌詞対応は保持する。
"""
import json
import os
import tempfile

from neotja.lyric_jacket import sidecar_path, from_stored, to_stored

ART_KINDS = ("jacket", "background")


def path_key(path):
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))
    except (TypeError, ValueError):
        return ""


def _entry(cfg, path):
    raw = cfg.get("player_chart_art")
    entry = raw.get(path_key(path)) if isinstance(raw, dict) else None
    return entry if isinstance(entry, dict) else {}


def _sidecar(path, strict=False):
    if not path:
        return None
    try:
        with open(sidecar_path(path), "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("ジャケット設定がオブジェクトではありません")
        return raw
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        if strict:
            raise
        return None


def has_chart_art(cfg, path, kind):
    """空文字による明示的な非表示も「指定あり」。"""
    if kind not in ART_KINDS:
        return False
    if kind == "jacket":
        raw = _sidecar(path)
        if raw is not None and isinstance(raw.get("base"), str):
            return True
    return isinstance(_entry(cfg, path).get(kind), str)


def chart_art(cfg, path):
    entry = _entry(cfg, path)
    out = {k: entry.get(k) if isinstance(entry.get(k), str) else ""
           for k in ART_KINDS}
    raw = _sidecar(path)
    if raw is not None and isinstance(raw.get("base"), str):
        out["jacket"] = from_stored(raw["base"], os.path.dirname(os.path.abspath(path)))
    return out


def existing_art(cfg, path, kind):
    value = chart_art(cfg, path).get(kind, "")
    return value if value and os.path.isfile(value) else ""


def _write_base(path, jacket):
    if not path:
        raise ValueError("譜面の保存先がありません")
    raw = _sidecar(path, strict=True)
    if raw is None:
        raw = {"version": 1, "lyrics": {}}
    folder = os.path.dirname(os.path.abspath(path))
    raw["base"] = to_stored(jacket, folder)
    fd, tmp = tempfile.mkstemp(prefix=".neotja_jk_", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, sidecar_path(path))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def set_chart_art(cfg, path, save_jacket=True, **kinds):
    """指定されたパスのみ更新。保存失敗なら設定辞書も更新しない。

    save_jacket=False は Editor の未保存 LYRIC 対応を既存 setter で扱う場合。
    """
    values = {k: os.path.normpath(os.path.abspath(v)) if v else ""
              for k, v in kinds.items() if k in ART_KINDS}
    if "jacket" in values and save_jacket:
        _write_base(path, values["jacket"])
    raw = cfg.get("player_chart_art")
    raw = dict(raw) if isinstance(raw, dict) else {}
    entry = dict(_entry(cfg, path))
    entry.update(values)
    entry["path"] = os.path.normpath(os.path.abspath(path))
    # パスだけ変更した場合も見た目設定内の古いパスを残さない。
    nested = entry.get("record_background")
    if isinstance(nested, dict):
        nested = dict(nested)
        for key, field in (("jacket", "art"), ("background", "path")):
            if key in values:
                nested[field] = values[key]
        entry["record_background"] = nested
    raw[path_key(path)] = entry
    cfg["player_chart_art"] = raw


def record_background(cfg, path, fallback=None):
    """既定のコピーへ曲別値を適用する。fallback 本体は変更しない。"""
    from neotja.recorder import RecordBackground, as_background
    base = as_background(fallback)
    keys = ("path", "offset", "art") + RecordBackground.DEFAULT_KEYS
    values = {k: getattr(base, k) for k in keys}
    nested = _entry(cfg, path).get("record_background")
    if isinstance(nested, dict):
        values.update({k: nested[k] for k in keys if k in nested})
    art = chart_art(cfg, path)
    for kind, field in (("jacket", "art"), ("background", "path")):
        if has_chart_art(cfg, path, kind):
            values[field] = art[kind]
    values["art_track"] = list(base.art_track)
    return RecordBackground(**values)


def set_record_background(cfg, path, bg, save_jacket=True):
    """調整値を曲別に記憶。動的な LYRIC 時間区間は保存しない。"""
    from neotja.recorder import RecordBackground, as_background
    bg = as_background(bg)
    set_chart_art(cfg, path, save_jacket=save_jacket,
                  jacket=bg.art, background=bg.path)
    keys = ("path", "offset", "art") + RecordBackground.DEFAULT_KEYS
    _entry(cfg, path)["record_background"] = {k: getattr(bg, k) for k in keys}
