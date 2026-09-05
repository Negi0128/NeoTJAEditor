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
    "preview_zoom", "preview_speed", "waveform_window",
    "player_select_bgm",
    # 更新で NeoTJAPlayer だけ入れ替えられなかったときの印。**次の起動で
    # 読み戻せないと意味が無い** — Editor 側は最新なので「更新の確認」は
    # 「最新です」と答えるだけで、Player を取りに行く道が無くなる。
    "player_update_pending",
    # 動画書き出しの保存先。ここに書き忘れていたため default_settings() には
    # あるのに load_settings() が読み戻さず、環境設定で指定した保存先が再起動の
    # たびに空へ戻っていた。
    "record_output_dir", "record_last_dir",
    # 利用者が用意した TNDE の System フォルダ。ここと default_settings() の
    # 両方に書くこと(片方だけだと再起動のたびに空へ戻る。上の
    # record_output_dir が実際にそうなっていた)。
    "system_dir",
    # System が見つからないときに起動時の案内を出すか。これも
    # default_settings() と両方に要る — ここに書き忘れると「次回から
    # 表示しない」を押しても再起動で戻り、案内が永遠に出続ける。
    "warn_missing_system",
    # 銘板。これも default_settings() と両方に要る。
    "nameplate_name", "nameplate_title", "nameplate_title_type",
    "nameplate_title_image", "nameplate_dan", "nameplate_dan_type",
    "nameplate_dan_text_color", "show_tuner",
)


def default_settings() -> dict:
    return {
        "run_config": {
            "F1": {"name": "NeoTJAPlayer", "path": ""},
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
        # 利用者が用意した TNDE の System フォルダ。絵・音・フォントはすべて
        # ここから取り出してキャッシュへ展開する(neotja/skin_cache.py)。
        # 空 = 未指定で、そのときは exe の隣だけを見る(デスクトップ配下などの
        # 自動探索はやめた — 理由は skin_cache.find_system_dir の説明)。
        # 見つからなくても起動はする。案内を出したうえで、素材を使わない
        # 「内蔵スキン」(描画側の自前の絵と合成打音)で動く。
        "system_dir": "",
        # System が見つからないときに、起動時の案内を出すか。案内の
        # 「次回から表示しない」で False になる。素材を置くつもりが無い人に
        # 毎回同じダイアログを見せても読まれないだけなので、逃げ道を用意した。
        # 環境設定「エディタ・ツール」タブの素材の枠から戻せる。
        "warn_missing_system": True,
        # --- 銘板(ゲーム画面の左下の名前板) ---
        # 中身はここが正。TNDE 形式の NamePlate.json は環境設定の
        # 「読み込む」から取り込む(load_nameplate)。
        "nameplate_name": "どんちゃん",
        "nameplate_title": "NeoTJAPlayer",
        # 称号バーの色。NAMEPLATE_TITLE_TYPES の値。
        "nameplate_title_type": 2,
        # 称号バーを丸ごと差し替える絵。空 = 使わない。帯も文字も焼き込んだ
        # 1枚として貼るので、指定すると上の色と文字は出ない。
        "nameplate_title_image": "",
        # 段位。空 = 段位を出さない。NAMEPLATE_DAN_NAMES から選ぶ。
        "nameplate_dan": "",
        # 段位の背景。NAMEPLATE_DAN_TYPES の値。
        "nameplate_dan_type": 2,
        # 段位の文字色。NAMEPLATE_DAN_TEXT_COLORS の値。
        "nameplate_dan_text_color": "gold",
        # 位置合わせ用のキー(Ctrl+Shift+…)を効かせるか。ふだんは切っておく。
        # 作る側が絵の位置を詰めるための道具で、遊ぶ人には要らないため。
        "show_tuner": False,
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
        # NeoTJAPlayer の表示倍率(%)。100/75/50/25 を循環する。
        # 小さい画面で 720px の絵が入りきらないとき用。
        "preview_zoom": 100,
        # 再生速度の倍率。0.25 / 0.50 / 0.75 / 1.00 の 4 段階だけを取り、
        # 等倍より速い再生は無い。旧版で 1.50 や 2.00 が保存されていても、
        # 読み込み時に chart_preview_widget.snap_speed() が段階へ丸める。
        "preview_speed": 1.00,
        # 音声波形モードで一度に見せる秒数(表示幅)。Alt/Ctrl+ホイールで
        # 1〜60秒のあいだを変えられ、変えた値がそのまま次回の既定になる。
        # 表示倍率(preview_zoom)やモードと同じ「触ったら覚える」扱い。
        "waveform_window": 6.0,
        # NeoTJAPlayer の選曲画面で、譜面を開いていないあいだ BGM を流すか。
        # 音源は System の TNDE-R/Sounds/BGM/SongSelect.ogg。
        "player_select_bgm": True,
        # 最近開いた/保存したファイルのパス(新しい順、最大10件)。
        "recent_files": [],
        "player_update_pending": False,
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


def _primary_settings_path() -> Path:
    # Resolve next to the frozen exe (PyInstaller) or the project root when
    # running from source, instead of the original's bare-relative-path
    # (process-cwd-dependent) behavior.
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "settings.json"


def _fallback_settings_path() -> Path:
    """exe の隣へ書けなかったときの退避先。%LOCALAPPDATA%\\NeoTJAEditor\\。

    素材キャッシュ(skin_cache.cache_dir())と同じ親を使う — 利用者から見て
    「このアプリの持ち物」が1か所にまとまるほうが、消したいときに分かりやすい。"""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local)
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "NeoTJAEditor" / "settings.json"


def crash_log_path() -> Path:
    """落ちた理由を書き残す先。%LOCALAPPDATA%\\NeoTJAEditor\\crash.log。

    設定や素材キャッシュと同じ親に置く — 利用者から見て「このアプリの
    持ち物」が1か所にまとまるほうが、送ってもらうときに案内しやすい。
    exe の隣にしないのは、Program Files 配下だと書けないため(そこで
    書けないと、いちばん記録が欲しい環境で何も残らないことになる)。"""
    return _fallback_settings_path().parent / "crash.log"


def settings_path() -> Path:
    """設定ファイルの在りか。ふだんは exe の隣。

    **なぜ退避先があるのか**
    exe を Program Files 配下へ入れられていると、その隣には書けない。以前は
    save_settings() が例外を握りつぶして黙って何もしなかったので、起動時に
    System フォルダを選び直しても保存されず、**次の起動でまた同じダイアログ**
    という抜け出せない状態になっていた。

    「保存できませんでした」と伝えるだけにする案もあったが、利用者にできる
    ことが「アプリを別の場所へ入れ直す」しかなく、逃げ道になっていない。
    そこで書ける場所へ退避する方を採った。

    退避先が一度できたら、以降は読み書きともそちらを見る。両方あるときに
    exe の隣を優先すると、書き込みは退避先・読み込みは隣という食い違いが
    起きて、保存したはずの設定が戻らなくなる。"""
    fallback = _fallback_settings_path()
    if fallback.exists():
        return fallback
    return _primary_settings_path()


#: 称号バーの見た目。値は NamePlate_Parts.png の何番目の帯かで、
#: game_screen.NAMEPLATE_PART_TITLES の並びと同じ。
NAMEPLATE_TITLE_TYPES = (("木", 0), ("金", 1), ("紫", 2))
#: 段位の背景。0 は素材では銀色だが、見た目どおり「白」と呼ぶ。
NAMEPLATE_DAN_TYPES = (("白", 0), ("金", 1), ("虹", 2))
#: 段位の選択肢。空 = 段位を出さない。
NAMEPLATE_DAN_NAMES = (
    "", "五級", "四級", "三級", "二級", "一級",
    "初段", "二段", "三段", "四段", "五段",
    "六段", "七段", "八段", "九段", "十段",
    "玄人", "名人", "超人", "達人",
)
#: 段位の文字色。
NAMEPLATE_DAN_TEXT_COLORS = (("白", "white"), ("金", "gold"))


def nameplate_path() -> Path:
    """銘板の内容を書いた NamePlate.json の在りか。settings.json と同じ場所。

    形式は TJAPlayer3-Develop-ReWrite と同じで、TNDE に付いてくる
    「NamePlate to JSON」で作ったものをそのまま置ける:

        {"name": ["1Pの名前", "2Pの名前"], "title": [...], "dan": [...],
         "danGold": [true, false], "danType": [2, 0], "titleType": [2, 0]}

    設定と同じ場所にしてあるのは、退避先(%LOCALAPPDATA%)へ逃げたときに
    片方だけ取り残されないようにするため。"""
    return settings_path().parent / "NamePlate.json"


#: NamePlate.json が無い / 読めないときの中身。
NAMEPLATE_DEFAULT = {
    "name": ["どんちゃん", "かつくん"],
    "title": ["", ""],
    "dan": ["", ""],
    "danGold": [False, False],
    "danType": [0, 0],
    "titleType": [0, 0],
}


def load_nameplate(path=None) -> dict:
    """NamePlate.json を読む。無ければ・壊れていれば既定値を返す。

    **例外は投げない。** 銘板が出ないだけで演奏はできるので、手で書き換えた
    JSON が壊れていても起動は妨げない。項目ごとに型を見て、駄目なものだけ
    既定値に戻す(全部捨てると、1文字の書き間違いで名前まで消える)。"""
    out = {k: list(v) if isinstance(v, list) else v
           for k, v in NAMEPLATE_DEFAULT.items()}
    p = Path(path) if path is not None else nameplate_path()
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return out
    if not isinstance(data, dict):
        return out
    for key, default in NAMEPLATE_DEFAULT.items():
        got = data.get(key)
        if not isinstance(got, list):
            continue
        want_bool = isinstance(default[0], bool)
        want_int = isinstance(default[0], int) and not want_bool
        for i in range(len(out[key])):
            if i >= len(got):
                break
            v = got[i]
            if want_bool:
                if isinstance(v, bool):
                    out[key][i] = v
            elif want_int:
                if isinstance(v, int) and not isinstance(v, bool):
                    out[key][i] = v
            elif isinstance(v, str):
                out[key][i] = v
    return out


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


def _write_settings(path: Path, config_data: dict) -> bool:
    """1か所へ書く。一時ファイルに書いてから置き換えるので、書き込みの途中で
    失敗しても settings.json が空や壊れた状態にはならない(そうなると次回起動で
    設定が全部初期値へ戻ってしまう)。保存は音量スライダー等からも頻繁に
    呼ばれるぶん、当たる機会も多い。"""
    tmp = None
    try:
        import tempfile
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".neotja_cfg_", suffix=".tmp",
                                   dir=str(path.parent))
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        tmp = None
        return True
    except Exception:  # noqa: BLE001 — 呼び出し側へは戻り値で伝える。
        return False
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def save_settings(config_data: dict) -> bool:
    """設定を保存する。書けたかどうかを返す。

    ふだんの置き場(exe の隣)へ書けなかったときは %LOCALAPPDATA% 側へ退避する
    — 理由は settings_path() の説明のとおり。退避が起きたあとは
    settings_path() もそちらを返すので、読み書きが食い違うことはない。

    戻り値を見ない呼び出しがほとんどだが(音量を動かすたびに知らせても
    仕方がない)、起動時の System フォルダの選び直しのように「保存できないと
    利用者が同じところで足止めされる」場面だけは確かめている。"""
    if _write_settings(settings_path(), config_data):
        return True
    fallback = _fallback_settings_path()
    if fallback == settings_path():
        return False  # 退避先そのものが書けなかった。もう行き先が無い。
    return _write_settings(fallback, config_data)


def notes_png_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "notes.png"


def icon_path(player: bool = False) -> Path:
    """窓に付けるアイコン。Editor は E、Player は P。

    見分けが付かないと、並んで開いているときにどちらの窓か分からない
    (どちらも同じ青い丸だった)。

    frozen では **exe の隣ではなく _MEIPASS** を見る。datas に入れた
    ファイルはそこへ展開されるので、exe の隣を見ても見つからず、
    setWindowIcon が黙って飛ばされていた。"""
    name = "app_icon_player.ico" if player else "app_icon.ico"
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).parent)
    else:
        base = Path(__file__).resolve().parent.parent
    return base / name


def skin_dir() -> Path:
    """素材(音符・背景・打音・フォント)を読むフォルダ。

    中身は TNDE の素材そのもので再配布できないため、アプリには一切同梱せず、
    **利用者が用意した System フォルダから起動時に取り出したキャッシュ**を
    ここで返す(展開は neotja/skin_cache.py、実体は
    %LOCALAPPDATA%\\NeoTJAEditor\\skin_cache)。中の並びは以前 exe の隣に
    置いてもらっていた skin/ とまったく同じなので、これを読む側
    (game_screen.py / chart_preview_widget.py / chara.py)は何も変わらない。

    ファイルが欠けていても描画側は自前の本家風の絵と合成音へ落ちるので、
    1枚足りないだけで落ちることはない。"""
    from neotja import skin_cache
    return skin_cache.cache_dir()


def skin_notes_path() -> Path:
    """プレビューが使う音符の絵(Notes.png)。画像書き出しが読む素の
    `notes.png` とは別物で、大文字小文字を区別しない Windows でぶつからない
    ようキャッシュ側に置いてある。"""
    return skin_dir() / "Notes.png"


def _first_existing(directory: Path, names) -> str:
    for n in names:
        p = directory / n
        if p.exists():
            return str(p)
    return ""


def skin_sound_paths():
    """キャッシュから取れる打音 (don, ka) のパス。無ければ ("", "")。

    System の ogg をデコードしたものが入る想定だが、ffmpeg が使えない等で
    展開できていないこともあるので、存在しない場合は空を返して呼び出し側の
    合成音へ落とす。名前は数通り許す(以前 exe の隣に置いてもらっていた
    skin/ の流儀をそのまま残してある)。"""
    d = skin_dir()
    don = _first_existing(d, ("don.wav", "dong.wav", "Don.wav", "Dong.wav"))
    ka = _first_existing(d, ("ka.wav", "katsu.wav", "Ka.wav", "Katsu.wav"))
    return don, ka


def effective_hit_sound_paths(cfg):
    """実際に鳴らす打音 (don, ka)。環境設定で指定した自前の WAV が両方とも
    実在すればそれ、無ければキャッシュのもの、それも無ければ ("", "") =
    内蔵の合成音。

    再生と動画書き出しはここで足並みを揃える必要がある。設定だけを見ると、
    音がキャッシュ由来のときに録画だけ合成音になってしまい、編集中に
    聞こえている音と違うものが書き出される。"""
    cfg_don = (cfg or {}).get("hit_sound_don_path", "") or ""
    cfg_ka = (cfg or {}).get("hit_sound_ka_path", "") or ""
    if cfg_don and cfg_ka and os.path.exists(cfg_don) and os.path.exists(cfg_ka):
        return cfg_don, cfg_ka
    skin_don, skin_ka = skin_sound_paths()
    if skin_don and skin_ka:
        return skin_don, skin_ka
    return cfg_don, cfg_ka
