import json
import sys
from pathlib import Path

_SETTINGS_KEYS = (
    "run_config", "custom_shortcuts", "theme", "font_family", "font_size",
    "resize_ext", "resize_wrap_16", "resize_wrap_12", "roll_speed", "short_roll_comp",
    "preview_volume", "last_project_folder", "check_updates_on_startup", "auto_save_enabled",
    "hit_sound_don_path", "hit_sound_ka_path", "sfx_volume", "audio_backend",
    "waveform_stereo",
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


def load_settings() -> dict:
    data = default_settings()
    path = settings_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for key in _SETTINGS_KEYS:
                if key in loaded:
                    if isinstance(loaded[key], dict) and isinstance(data[key], dict):
                        data[key].update(loaded[key])
                    else:
                        data[key] = loaded[key]
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
