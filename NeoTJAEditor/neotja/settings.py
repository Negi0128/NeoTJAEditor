import json
import sys
from pathlib import Path

_SETTINGS_KEYS = (
    "run_config", "custom_shortcuts", "theme", "font_family", "font_size",
    "resize_ext", "resize_wrap_16", "resize_wrap_12", "roll_speed", "short_roll_comp",
)


def default_settings() -> dict:
    return {
        "run_config": {k: {"name": f"シミュレータ{k}", "path": ""} for k in ("F1", "F2", "F3")},
        "custom_shortcuts": {str(i): "" for i in range(10)},
        "theme": "dark",
        "font_family": "Consolas",
        "font_size": 12,
        "resize_ext": False,
        "resize_wrap_16": 16,
        "resize_wrap_12": 24,
        "roll_speed": 45,
        "short_roll_comp": "段階的補正 (60fps理論値)",
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
