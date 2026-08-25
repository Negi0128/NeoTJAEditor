import json
import os
import sys
from pathlib import Path

_SETTINGS_KEYS = (
    "run_config", "custom_shortcuts", "theme", "font_family", "font_size",
    "resize_ext", "resize_wrap_16", "resize_wrap_12", "roll_speed", "short_roll_comp",
    "preview_volume", "last_project_folder", "check_updates_on_startup", "auto_save_enabled",
    "hit_sound_don_path", "hit_sound_ka_path", "sfx_volume", "audio_backend",
    "master_volume", "audio_output_device",
    "wireless_offset_enabled", "wireless_offset_ms",
    "waveform_stereo", "se_text_enabled", "note_input_sound",
    "recent_files", "window_geometry", "splitter_state", "preview_max_fps",
    "preview_show_fps", "peepo_chart_edit", "preview_bottom_mode",
    "preview_zoom",
    # 動画書き出しの保存先。ここに書き忘れていたため default_settings() には
    # あるのに load_settings() が読み戻さず、環境設定で指定した保存先が再起動の
    # たびに空へ戻っていた。
    "record_output_dir", "record_last_dir",
)


def default_settings() -> dict:
    return {
        "run_config": {
            "F1": {"name": "えぬいーさん次郎", "path": ""},
            **{k: {"name": f"シミュレータ{k}", "path": ""} for k in ("F2", "F3")},
        },
        "custom_shortcuts": {str(i): "" for i in range(10)},
        # 動画書き出しの保存先。ユーザーが環境設定で決める既定で、書き出しても
        # 勝手には変わらない(空 = 前回使った場所 / TJA と同じフォルダ)。
        "record_output_dir": "",
        # 動画書き出しで最後に実際に使った場所。録画のたびに書き換わる履歴で、
        # 上の record_output_dir(ユーザーの指定)とは別に持つ。一緒にしていた
        # ころは、環境設定で保存先を決めても一度別の場所へ書き出すだけで
        # 上書きされ、二度と戻らなかった。
        "record_last_dir": "",
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
        # マスターボリューム(0.0〜1.0)。preview_volume(曲)/sfx_volume(打音・
        # メトロノーム)は「マスターに対する比率」で、実際に出る音量は
        # マスター × 比率 になる。プレビュー窓の音量行の一番左のスライダー。
        "master_volume": 1.0,
        # 音声出力デバイス。空文字 = OS の既定デバイス。指定するときは
        # sounddevice のデバイス「名前」を入れる(番号は再起動や機器の抜き差しで
        # 変わるため)。名前が見つからなければ黙って既定へ落ちる。環境設定
        # ダイアログ「音声」タブで選ぶ。ミキサー経路でのみ有効。
        "audio_output_device": "",
        # ワイヤレス調整(出力遅延の補正)。Bluetooth イヤホン等で音が遅れて
        # 届くぶんを打ち消すための、曲・打音・メトロノームすべてに一律で効く
        # オフセット。既存の BPM 依存の打音レイテンシ補正とは別物で、そちらに
        # 加算される。正の値 = 出力がその ms だけ遅れているとみなす。
        "wireless_offset_enabled": False,
        "wireless_offset_ms": 0.0,
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
        # 譜面プレビューの最大再描画fps(CPU負荷の上限)。既定120。実際にはパネル
        # のリフレッシュレートの2倍を狙い、この値で頭打ちにする(ソフト描画の
        # ちらつき/カクつき対策。60Hzパネルなら120fps)。下げるほど軽い。
        "preview_max_fps": 120,
        # 譜面プレビュー左上に実測fpsを小さく表示する(描画が本当に出ているか
        # 確認するための目安)。既定True。気になる場合はfalseで消せる。
        "preview_show_fps": True,
        # 譜面プレビュー下部パネルの最後に使っていたモード(Tab / モード切替
        # ボタン)。番号ではなくモード名で持つ — 実験的機能(peepo_chart_edit)の
        # 入り切りで「作譜」が挟まったり抜けたりして番号の意味がずれるため。
        # 既定は本家どおりの「通常再生」。低スペック機は「軽量」を選んでおくと
        # 次回もその状態で開く。
        "preview_bottom_mode": "通常再生",
        # えぬいーさん次郎の表示倍率(%)。100/75/50/25 を循環する。
        # 小さい画面で 720px の絵が入りきらないとき用。
        "preview_zoom": 100,
        # 最近開いた/保存したファイルのパス(新しい順、最大10件)。
        "recent_files": [],
        # ウィンドウのサイズ・位置とサイドバー分割比を次回起動へ引き継ぐための
        # base64 文字列(QMainWindow.saveGeometry / QSplitter.saveState)。空文字
        # なら既定サイズで開く。
        "window_geometry": "",
        "splitter_state": "",
        # 実験的機能: 譜面プレビュー下部パネルの「作譜」モード(波形の上に
        # グリッドとカーソルを出し、キーで音符を直接置ける)を出すかどうか。
        # 既定はオフ。環境設定ダイアログ「実験的機能」タブのチェックボックスで
        # 変更でき、反映はアプリの再起動後(preview_dock.py がここを見て
        # 「作譜」ページを最初から作るかどうかを決めるため)。
        "peepo_chart_edit": False,
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
    """設定を保存する。一時ファイルに書いてから置き換えるので、書き込みの途中で
    失敗しても settings.json が空や壊れた状態にはならない(そうなると次回起動で
    設定が全部初期値へ戻ってしまう)。保存は音量スライダー等からも頻繁に
    呼ばれるぶん、当たる機会も多い。"""
    path = settings_path()
    tmp = None
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix=".neotja_cfg_", suffix=".tmp",
                                   dir=str(Path(path).parent))
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        tmp = None
    except Exception:
        pass
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
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


def skin_dir() -> Path:
    """The optional `skin` folder next to the exe (project root in dev). A
    self-contained pack the author distributes out-of-band: note art plus hit
    sounds. Nothing here is bundled/committed (copyright), and everything is
    optional - the app draws its own 本家風 notes and synths its own hit
    sounds when the folder or a file is absent."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "skin"


def skin_notes_path() -> Path:
    """Note skin the preview uses if present (OpenTaiko-style Notes.png). Kept
    in skin/ rather than the bare `notes.png` the image-export reads, so the
    two never collide on case-insensitive Windows."""
    return skin_dir() / "Notes.png"


def _first_existing(directory: Path, names) -> str:
    for n in names:
        p = directory / n
        if p.exists():
            return str(p)
    return ""


def skin_sound_paths():
    """(don, ka) hit-sound paths from the skin folder if the author packed
    them, else ("", ""). Accepts a few common filenames so the pack is
    forgiving about naming."""
    d = skin_dir()
    don = _first_existing(d, ("don.wav", "dong.wav", "Don.wav", "Dong.wav"))
    ka = _first_existing(d, ("ka.wav", "katsu.wav", "Ka.wav", "Katsu.wav"))
    return don, ka


def effective_hit_sound_paths(cfg):
    """(don, ka) actually used for hit sounds: the user's own files if both
    exist, else the skin pack's, else ("", "") meaning the built-in synth.

    Playback and the video export must agree on this - reading the config
    alone would give the recording the synth whenever the sound comes from a
    skin pack, which is not what the user hears while editing."""
    cfg_don = (cfg or {}).get("hit_sound_don_path", "") or ""
    cfg_ka = (cfg or {}).get("hit_sound_ka_path", "") or ""
    if cfg_don and cfg_ka and os.path.exists(cfg_don) and os.path.exists(cfg_ka):
        return cfg_don, cfg_ka
    skin_don, skin_ka = skin_sound_paths()
    if skin_don and skin_ka:
        return skin_don, skin_ka
    return cfg_don, cfg_ka
