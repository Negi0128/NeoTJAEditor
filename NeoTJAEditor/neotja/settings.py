import json
import sys
from pathlib import Path

_SETTINGS_KEYS = (
    "run_config", "custom_shortcuts", "theme", "font_family", "font_size",
    "resize_ext", "resize_wrap_16", "resize_wrap_12", "roll_speed", "short_roll_comp",
    "preview_volume", "last_project_folder", "check_updates_on_startup", "auto_save_enabled",
    "hit_sound_don_path", "hit_sound_ka_path", "sfx_volume", "audio_backend",
    "waveform_stereo", "se_text_enabled", "note_input_sound",
    "recent_files", "window_geometry", "splitter_state",
)


def default_settings() -> dict:
    return {
        "run_config": {
            "F1": {"name": "えぬいーさん次郎", "path": ""},
            **{k: {"name": f"シミュレータ{k}", "path": ""} for k in ("F2", "F3")},
        },
        "custom_shortcuts": {str(i): "" for i in range(10)},
        "theme": "dark",
        "font_family": "Consolas",
        "font_size": 12,
        "resize_ext": False,
        "resize_wrap_16": 16,
        "resize_wrap_12": 24,
        "roll_speed": 45,
        "short_roll_comp": "段階的補正 (60fps理論値)",
        "preview_volume": 0.8,
        "last_project_folder": "",
        "check_updates_on_startup": True,
        "auto_save_enabled": False,
        "hit_sound_don_path": "",
        "hit_sound_ka_path": "",
        # 効果音(打音/メトロノーム共通)の音量。ミキサー経路の SE 音量スライダー。
        "sfx_volume": 0.9,
        # 再生バックエンド: "mixer"(既定, sounddevice の単一ミキサー)/"qt"(旧
        # QMediaPlayer+QSoundEffect 三点セットを強制)。切替 UI は無く settings.json
        # のみ。"mixer" でもストリームが開けなければ自動的に "qt" 相当へ退避する。
        "audio_backend": "mixer",
        # 波形表示: True = L/R を上下2段で個別表示、False = 合成(モノラル)1段。
        "waveform_stereo": True,
        # 打音表記(ド/ドン/コ/カ/カッ)をゲーム風プレビューのレーン下段に
        # 表示するか。PeepoDrumKit の自動判定を移植したもの(neotja/se_text.py)。
        # 環境設定ダイアログ「エディタ・ツール」タブのチェックボックスで変更。
        "se_text_enabled": True,
        # エディタでノーツ文字(1〜9)を打鍵した瞬間にドン/カツ音を鳴らすか。
        # 譜面本体(#START〜#END内)でのみ発音し、ヘッダ/コメント/コース外や
        # ペースト操作では鳴らない。環境設定ダイアログ「エディタ・ツール」
        # タブのチェックボックスで変更。
        "note_input_sound": True,
        # 最近開いた/保存したファイルのパス(新しい順、最大10件)。
        "recent_files": [],
        # ウィンドウのサイズ・位置とサイドバー分割比を次回起動へ引き継ぐための
        # base64 文字列(QMainWindow.saveGeometry / QSplitter.saveState)。空文字
        # なら既定サイズで開く。
        "window_geometry": "",
        "splitter_state": "",
    }


def settings_path() -> Path:
    # Resolve next to the frozen exe (PyInstaller) or the project root when
    # running from source, instead of the original's bare-relative-path
    # (process-cwd-dependent) behavior.
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "settings.json"


def _coerce(default, loaded):
    """設定値を default の型に合わせて安全に取り込む。JSON は正しくパースでき
    ても型がずれている(手編集で font_size が "12"、run_config の F2 が文字列
    等)ことがあり、そのまま採用すると起動時に QFont(str,str) や
    run_config[k]['name'] で TypeError になってウィンドウ表示前に落ちる。
    変換できない値は default にフォールバックする。"""
    if isinstance(default, bool):
        return loaded if isinstance(loaded, bool) else default
    if isinstance(default, int):
        if isinstance(loaded, bool):
            return default
        try:
            return int(loaded)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        if isinstance(loaded, bool):
            return default
        try:
            return float(loaded)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return loaded if isinstance(loaded, str) else default
    if isinstance(default, dict):
        if not isinstance(loaded, dict):
            return default
        # default の各キーは型を検証しつつ取り込み、既知キーの構造(run_config
        # の各エントリが name/path を持つ dict であること等)を保証する。未知の
        # 追加キーはそのまま通す。
        merged = dict(default)
        for k, v in loaded.items():
            merged[k] = _coerce(default[k], v) if k in default else v
        return merged
    if isinstance(default, list):
        return loaded if isinstance(loaded, list) else default
    return loaded


def load_settings() -> dict:
    data = default_settings()
    path = settings_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for key in _SETTINGS_KEYS:
                    if key in loaded:
                        data[key] = _coerce(data[key], loaded[key])
        except Exception:
            pass
    return data


def save_settings(config_data: dict) -> None:
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def notes_png_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "notes.png"


def icon_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "app_icon.ico"
