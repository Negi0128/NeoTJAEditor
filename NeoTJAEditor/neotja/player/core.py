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
    "player_folders", "player_last_file", "player_select_bgm",
    # ネームプレート。ここに書き忘れると、Player の環境設定で変えても
    # ファイルへ落ちず、次に描くときに古い値へ戻る(実際にそうなっていた)。
    "nameplate_name", "nameplate_title", "nameplate_title_type",
    "nameplate_title_image", "nameplate_dan", "nameplate_dan_type",
    "nameplate_dan_text_color", "show_tuner",
    "nameplate_title_dx", "nameplate_title_dy", "nameplate_title_size",
    "nameplate_name_dx", "nameplate_name_dy", "nameplate_name_size",
    "nameplate_dan_dx", "nameplate_dan_dy", "nameplate_dan_size",
    "nameplate_title_image_dx", "nameplate_title_image_dy",
    "nameplate_title_image_dw", "nameplate_title_image_dh",
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
        # 選曲画面で流している音の折り返し位置。None なら何も流していない。
        self._loop = None
        # 「この音源が読めたら、この秒から鳴らす」という予約。
        self._pending = None
        # 最後に読み終わった音源。
        self._ready = None
        # 終わりまで来たかを見る番人。音源の読み込みが裏で進むので、
        # 「頭出し」もこのタイマーが引き受ける(長さが分かるまで待つ)。
        from PySide6.QtCore import QTimer
        self._loop_timer = QTimer()
        # 20ms ごとに見る。250ms にしていたら折り返しが最大 0.25 秒遅れて、
        # 曲の切れ目に無音が空いた。見るだけの処理なので細かくしても軽い。
        from PySide6.QtCore import Qt as _Qt
        self._loop_timer.setTimerType(_Qt.PreciseTimer)
        self._loop_timer.timeout.connect(self._tick_loop)

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
        # 「読めたら鳴らす」の予約を回収する口。
        self.dock.songReady.connect(self._on_song_ready)
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

    # ------------------------------------------------------------------
    # 選曲画面で鳴らすもの
    # ------------------------------------------------------------------
    def click_sound(self):
        """ボタンやカードを押した合図。ドンを1つ鳴らす。"""
        try:
            self.dock.audio.hit_sounds.play_once("don")
        except Exception:  # noqa: BLE001
            pass

    def play_select_bgm(self):
        """譜面を開いていないあいだの BGM。

        曲を1つも選んでいない画面は無音だと止まって見える。環境設定
        (player_select_bgm)で切れる — 録画の下ごしらえ中など、鳴ってほしく
        ない場面があるため。

        鳴らす仕掛けは譜面の音源とまったく同じ(ミキサーに PCM を渡すだけ)。
        BGM 専用の再生経路を作ると、音量・出力デバイスの扱いが二重になる。
        """
        if not self.cfg.get("player_select_bgm", True):
            return
        path = settings_mod.skin_dir() / "SelectBgm.ogg"
        if not path.exists():
            return
        self.dock.set_audition(True)
        self._start_audio(str(path), 0.0, loop=True)

    def play_demo(self, path, demo_start):
        """譜面の音源を DEMOSTART から流す。終わったらまた同じ所から。

        本家の選曲画面と同じ聞こえ方にするため。DEMOSTART が無ければ頭から。
        """
        wave = _find_wave(path)
        if wave:
            self.dock.set_audition(True)
            self._start_audio(wave, max(0.0, float(demo_start or 0.0)),
                              loop=True)

    def play_chart(self, at_seconds=0.0):
        """本編を、音源が読めたところで自動的に流す。

        Player は「見る」道具なので、開いてから再生ボタンを押させる理由が
        無い。読み込みは裏で進むので、押せるようになるのを待つのではなく
        「読めたら鳴らす」と予約しておく。
        """
        # ここから先は本編。試聴モードを解く(譜面も一緒に走らせる)。
        self.dock.set_audition(False)
        wave = self.dock._current_wave_path
        if not wave:
            return
        self._pending = (wave, float(at_seconds or 0.0), False)
        # **読み込みの最中なら待つ。** 同じ音源を試聴で先に読んでいると
        # 「もう読めている」ように見えるが、本編ぶんの読み込みがそのあと
        # 音を差し替えるので、先に鳴らしても止まってしまう。
        if not self.dock.is_decoding() and self._ready == wave:
            self._fire_pending()

    def stop_audio(self):
        self._pending = None
        self._loop = None
        try:
            self.dock.audio.pause()
        except Exception:  # noqa: BLE001
            pass

    def _start_audio(self, wave_path, at_sec, loop=False):
        """音源を読み込んで、読めたところで指定の秒から流す。

        **長さを見て判断しない。** 読み込みは裏で進むので、前の曲の長さが
        残っているあいだに「読めた」と誤判定してしまう(DEMOSTART の頭出しが
        効かなかった原因)。読み終わりは dock.songReady が知らせてくれる。
        """
        self._pending = (wave_path, float(at_sec), bool(loop))
        self._loop = (float(at_sec), wave_path) if loop else None
        if not self.dock.load_wave_only(wave_path):
            self._pending = None
            return
        if not self.dock.is_decoding() and self._ready == wave_path:
            # 既に読み終わっているもの(同じ曲を選び直した等)。
            self._fire_pending()

    def _on_song_ready(self, path):
        self._ready = path
        if self._pending and self._pending[0] == path:
            self._fire_pending()

    def _fire_pending(self):
        wave, at, loop = self._pending
        self._pending = None
        try:
            self.dock.audio.seek(int(at * 1000))
            self.dock.audio.play()
        except Exception:  # noqa: BLE001
            return
        if loop:
            self._loop_timer.start(20)

    def _tick_loop(self):
        """流している音が終わりまで来たら、始めの所へ戻す。"""
        if self._loop is None:
            self._loop_timer.stop()
            return
        dur = self.dock.duration_seconds()
        if dur <= 0:
            return
        pos = self.dock.audio.position() / 1000.0
        # 終わりきる**手前**で戻す。鳴り終わってから戻すと、そのぶんの無音が
        # そのまま切れ目になる。0.05 秒手前なら耳では気づかない。
        if pos >= dur - 0.05:
            self.dock.audio.seek(int(self._loop[0] * 1000))
            self.dock.audio.play()

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


def _find_wave(tja_path):
    """TJA の WAVE: が指す音源。TJA と同じフォルダにある前提。

    拡張子違い(ogg と書いてあるが wav しかない等)も見る — 手元で作った譜面
    ではよくある。"""
    try:
        content = read_text(tja_path)
    except OSError:
        return ""
    folder = os.path.dirname(tja_path)
    for line in content.splitlines():
        t = line.split("//")[0].strip()
        if t.upper().startswith("WAVE:"):
            name = t[5:].strip()
            if not name:
                return ""
            p = name if os.path.isabs(name) else os.path.join(folder, name)
            if os.path.exists(p):
                return p
            stem = os.path.splitext(p)[0]
            for ext in (".ogg", ".wav", ".mp3", ".m4a"):
                if os.path.exists(stem + ext):
                    return stem + ext
            return ""
    return ""


def demo_start_seconds(content):
    """DEMOSTART: の秒数。無ければ 0。"""
    for line in content.splitlines():
        t = line.split("//")[0].strip()
        if t.upper().startswith("DEMOSTART:"):
            try:
                return max(0.0, float(t.split(":", 1)[1].strip()))
            except ValueError:
                return 0.0
    return 0.0


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
