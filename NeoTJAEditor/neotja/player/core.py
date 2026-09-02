"""Player の中核。譜面を読んで再生ウィンドウへ流すところまで。

画面(一覧・まとめて録画)はこの上に載る。ここには Qt のウィンドウを1つも
作らない — PreviewDock が自前で持っている再生ウィンドウを使うだけ。
"""

import io
import json
import os

from neotja import settings as settings_mod
from neotja.preview_dock import PreviewDock
from neotja.tja_analyzer import TJACourseAnalyzer

#: Player が書き換えてよい設定のキー。
#:
#: 設定ファイルは Editor と共有する(利用者の選択)。共有で困るのは上書き事故
#: だけ — Player を閉じただけで、エディタ側の「開いていた譜面・ウィンドウ位置・
#: 分割位置」が Player の値で潰れる。そこで保存の直前にファイルを読み直し、
#: **ここに挙げたキーだけ**を書き換えて戻す。재生に関わるものだけを並べてある。
PLAYER_KEYS = (
    "preview_zoom", "preview_speed", "preview_bottom_mode", "waveform_window",
    "preview_volume", "sfx_volume", "master_volume", "audio_output_device",
    "audio_backend", "waveform_stereo", "se_text_enabled",
    "hit_sound_don_path", "hit_sound_ka_path",
    "wireless_offset_enabled", "wireless_offset_ms",
    "record_output_dir", "record_last_dir",
    "player_folders", "player_last_file",
)


def save_shared_settings(cfg):
    """Player の設定だけをファイルへ書き戻す(読み直してから混ぜる)。

    そのまま save_settings(cfg) を呼ぶと、Player が持っていない/古い値まで
    まとめて書いてしまい、裏で開いている Editor の設定を潰す。
    """
    try:
        path = settings_mod.settings_path()
        base = {}
        if os.path.exists(path):
            try:
                base = json.load(io.open(str(path), encoding="utf-8"))
            except Exception:  # noqa: BLE001
                base = {}
        if not isinstance(base, dict):
            base = {}
        for key in PLAYER_KEYS:
            if key in cfg:
                base[key] = cfg[key]
        settings_mod.save_settings(base)
    except Exception:  # noqa: BLE001
        # 設定が保存できなくても再生も録画もできる。ここで落とさない。
        pass


def read_courses(content, analyzer):
    """譜面に入っているコースを [{"key","label","level"}, ...] で返す。

    難易度選択画面へ渡すためのもの。レベルは parse_courses が持っていない
    ので、library と同じ手(ヘッダの LEVEL: を拾う)を使う。
    """
    from neotja.player.library import _levels_by_course
    try:
        courses = analyzer.parse_courses(content) or []
    except Exception:  # noqa: BLE001
        return []
    levels = _levels_by_course(content, analyzer)
    out = []
    for c in courses:
        key = c.get("key")
        if not key:
            continue
        out.append({"key": key, "label": c.get("label", key),
                    "level": levels.get(key)})
    return out


class PlayerCore:
    """PreviewDock を1つ抱えて、譜面を読み込む係。

    PreviewDock 自体(ドックの見た目)は一度も見せない。使うのはその中の
    再生ウィンドウ(game_preview_window)だけ。
    """

    def __init__(self, config_data):
        self.cfg = config_data
        self.analyzer = TJACourseAnalyzer(self.cfg)
        self.current_file = ""
        self.course_override = None
        self.branch_level = "M"

        self.dock = PreviewDock(
            # OFFSET を書き戻す先(エディタのヘッダ行)が無いので受け捨てる。
            # Player は譜面を書き換えない。
            apply_offset_cb=lambda _text: None,
            config_data=self.cfg,
            save_settings_cb=lambda: save_shared_settings(self.cfg),
            audio_backend=self.cfg.get("audio_backend", "mixer"),
            audio_output_device=self.cfg.get("audio_output_device", ""),
            waveform_stereo=bool(self.cfg.get("waveform_stereo", True)),
            se_text_enabled=bool(self.cfg.get("se_text_enabled", True)),
        )
        self.dock.hide()
        self.dock.set_master_volume(float(self.cfg.get("master_volume", 1.0)))
        self.dock.set_volume(float(self.cfg.get("preview_volume", 0.8)))
        self.dock.set_sfx_volume(float(self.cfg.get("sfx_volume", 0.9)))
        don, ka = settings_mod.effective_hit_sound_paths(self.cfg)
        self.dock.set_hit_sound_files(don, ka)

    # ------------------------------------------------------------------
    @property
    def window(self):
        """再生ウィンドウ。Player の主役。"""
        return self.dock.game_preview_window

    def peek(self, path):
        """譜面を**再生せずに**覗く。難易度選択画面へ出す材料を返す。

        (曲名, サブタイトル, コース一覧) を返し、読めなければ None。
        ここで音源を読み込まないのは、コースを選ぶ前に曲を鳴らし始めても
        意味が無く、選び直すたびに読み直しが起きるため。"""
        try:
            content = read_text(path)
        except OSError:
            return None
        from neotja.preview_dock import parse_preview_headers
        h = parse_preview_headers(content)
        sub = (h.get("subtitle") or "").lstrip("-")
        return h.get("title") or "", sub, read_courses(content, self.analyzer)

    def load(self, path, course_key=None):
        """TJA を1つ読み込んで再生ウィンドウへ流す。成功したら True。

        エディタの _apply_preview_payload と同じ手順(解析 → メトロノーム →
        preview_data → refresh_from_content)。違うのは、内容がエディタでは
        なくファイルから来ることだけ。
        """
        try:
            content = read_text(path)
        except OSError:
            return False
        self.current_file = path
        if course_key:
            self.course_override = course_key
        preview = self.analyzer.build_preview_timeline(
            content, None, self.course_override, branch_level=self.branch_level)
        clicks = self.analyzer.build_metronome_clicks(
            content, None, self.dock.duration_seconds())
        self.dock.refresh_from_content(content, path, clicks, preview,
                                       self._course_stats(content, preview))
        self.cfg["player_last_file"] = path
        return True

    def _course_stats(self, content, preview):
        """情報モードが出すコースごとの集計(ノーツ数・連打数など)。

        エディタは解析パスの副産物として持っているものを渡している
        (MainWindow._find_course_stats)。Player はその場で数え直す。
        取れなくても表示が「-」になるだけで、再生には影響しない。"""
        try:
            key = preview.get("course_key")
            for c in self.analyzer.parse_courses(content):
                if c.get("key") == key:
                    return c
        except Exception:  # noqa: BLE001
            pass
        return None

    def show(self):
        self.dock.set_game_preview_visible(True)

    def shutdown(self):
        """音声デバイスを確定的に閉じる。プロセス終了任せにしない。"""
        try:
            self.dock.shutdown_audio()
        except Exception:  # noqa: BLE001
            pass


def read_text(path):
    """TJA を読む。cp932 の譜面が多いので、UTF-8 で読めなければそちらへ。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
