"""Player の中核。譜面を読んで再生ウィンドウへ流すところまで。

画面(一覧・まとめて録画)はこの上に載る。ここには Qt のウィンドウを1つも
作らない — PreviewDock が自前で持っている再生ウィンドウを使うだけ。
"""

import io
import json
import math
import os

from neotja import settings as settings_mod
from neotja.preview_dock import PreviewDock
from neotja import tja_analyzer
from neotja.constants import PLAY_MODE_PLAY, PLAY_MODE_WATCH
from neotja.tja_analyzer import TJACourseAnalyzer

#: Player が書き換えてよい設定のキー。
#:
#: 設定ファイルは Editor と共有する(利用者の選択)。共有で困るのは上書き事故
#: だけ — Player を閉じただけで、エディタ側の「開いていた譜面・ウィンドウ位置・
#: 分割位置」が Player の値で潰れる。そこで保存の直前にファイルを読み直し、
#: **ここに挙げたキーだけ**を書き換えて戻す。再生に関わるものだけを並べてある。
PLAYER_KEYS = (
    "preview_zoom", "preview_speed", "preview_bottom_mode", "waveform_window",
    "preview_volume", "sfx_volume", "master_volume", "audio_output_device",
    "audio_backend", "waveform_stereo", "se_text_enabled",
    "hit_sound_don_path", "hit_sound_ka_path", "hit_sound_use_custom",
    "wireless_offset_enabled", "wireless_offset_ms",
    "record_output_dir", "record_last_dir",
    "player_folders", "player_last_file", "player_select_bgm",
    "player_select_bgm_volume", "gpu_render", "gpu_vsync",
    # 演奏モード(実験的機能)。Player の環境設定で入れても書き戻せるように。
    "player_play_mode",
    # ネームプレート。ここに書き忘れると、Player の環境設定で変えても
    # ファイルへ落ちず、次に描くときに古い値へ戻る(実際にそうなっていた)。
    "nameplate_name", "nameplate_title", "nameplate_title_type",
    "nameplate_title_image", "nameplate_dan", "nameplate_dan_type",
    "nameplate_dan_text_color", "show_tuner", "arrange_ref",
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

    _chart_started = False
    _held = None

    def __init__(self, config_data):
        self.cfg = config_data
        self.analyzer = TJACourseAnalyzer(self.cfg)
        self.current_file = ""
        self.course_override = None
        #: いまの遊び方。**Editor と録画は常に見るだけ。**
        self.play_mode = PLAY_MODE_WATCH
        self.branch_level = "M"
        # 選曲画面で流している音の折り返し位置。None なら何も流していない。
        self._loop = None
        self._demo_active = False
        self._demo_video_duration = None
        self._demo_requested_start = 0.0
        # 「この音源が読めたら、この秒から鳴らす」という予約。
        self._pending = None
        # 最後に読み終わった音源。
        self._ready = None
        #: 本編(play_chart)を流し始めたか。終わりの見張り(chart_finished)用。
        self._chart_started = False
        #: 途中メニューで止めたときに、まだ鳴らしていなかった予約。
        self._held = None
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
            # 曲の終わりの後ろに足す無音は 5 秒(エディタは 10 秒。
            # 利用者の指定 2026-11-13)。
            postroll_sec=5.0,
        )
        self.dock.hide()
        # Player は譜面を書き換えない。作譜ペイン(詳細モード)も読むだけ。
        # 編集の受け口(note_edit_cb・chart_op_cb・header_edit_cb・
        # demostart_cb・undo/redo)は最初から渡していないので、仮に何かが
        # すり抜けても Editor の本文へは届かない。
        self.dock.set_read_only(True)
        # 詳細モードで拡大したとき、作譜ペインも同じ倍率で高くする(下が空かない)。
        self.dock.set_pane_follow_scale(True)
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
        self._demo_active = False
        self._demo_video_duration = None
        if not self.cfg.get("player_select_bgm", True):
            return
        path = settings_mod.skin_dir() / "SelectBgm.ogg"
        if not path.exists():
            return
        self.dock.set_audition(True)
        self._apply_volume(select_bgm=True)
        self._start_audio(str(path), 0.0, loop=True)

    def _apply_volume(self, select_bgm=False):
        """曲の音量を掛け直す。

        選曲画面の BGM だけ、設定の比率を掛けて控えめに鳴らす。待っている
        あいだ流すものなので、譜面と同じ音量だとうるさい。譜面の試聴や本編に
        移るときは掛けるのをやめて、元の音量へ戻す。"""
        vol = float(self.cfg.get("preview_volume", 0.8) or 0.0)
        if select_bgm:
            vol *= float(self.cfg.get("player_select_bgm_volume", 0.6) or 0.0)
        try:
            self.dock.set_volume(max(0.0, min(1.0, vol)))
        except Exception:  # noqa: BLE001
            pass

    def play_demo(self, path, demo_start):
        """譜面の音源を DEMOSTART から流す。終わったらまた同じ所から。

        本家の選曲画面と同じ聞こえ方にするため。DEMOSTART が無ければ頭から。
        """
        self._demo_video_duration = None
        self._pending = None
        self._loop = None
        self._loop_timer.stop()
        self._demo_active = True
        try:
            start = float(demo_start or 0.0)
            start = max(0.0, start) if math.isfinite(start) else 0.0
        except (TypeError, ValueError):
            start = 0.0
        self._demo_requested_start = start
        wave = _find_wave(path)
        if wave:
            self.dock.set_audition(True)
            self._apply_volume()
            self._start_audio(wave, start, loop=True)

    def demo_position(self):
        """試聴用の音声時計（秒）。背景映像もこの時計に従う。"""
        return self.chart_position() if self.demo_ready() else 0.0

    def demo_playing(self):
        return self.demo_ready() and self.chart_playing()

    def demo_ready(self):
        return bool(getattr(self, "_demo_active", False) and self._loop
                    and self._pending is None
                    and self._ready == self._loop[1]
                    and not self.dock.is_decoding())

    def demo_start(self):
        """音源・動画の尺で補正した折り返し位置。"""
        if not getattr(self, "_demo_active", False) or not self._loop:
            return 0.0
        return float(self._loop[0])

    def _demo_loop_duration(self, audio_duration):
        video = getattr(self, "_demo_video_duration", None)
        if getattr(self, "_demo_active", False) and video is not None:
            return min(audio_duration, video) if audio_duration > 0 else video
        return audio_duration

    def set_demo_video_duration(self, seconds):
        """動画が先に読み終わっても、音源の準備完了時に短い方へ揃える。"""
        if not getattr(self, "_demo_active", False):
            return
        try:
            value = float(seconds) if seconds is not None else None
            if value is not None and (not math.isfinite(value) or value <= 0):
                value = None
        except (TypeError, ValueError):
            value = None
        self._demo_video_duration = value
        # 曲名表示へ移る際、動画だけ外しても試聴音声は連続させる。
        # 元の DEMOSTART への復帰は次の play_demo に任せる。
        if value is None:
            return
        if not self.demo_ready():
            return
        dur = self._demo_loop_duration(self.dock.duration_seconds())
        start = self._demo_requested_start
        if dur > 0 and start >= dur - 0.1:
            start = 0.0
        changed = start != self._loop[0]
        self._loop = (start, self._loop[1])
        if changed or (dur > 0 and self.demo_position() >= dur - 0.05):
            self.restart_demo()

    def restart_demo(self):
        """試聴を共通の折り返し位置から再開。本編・選曲BGMには作用しない。"""
        if not self.demo_ready():
            return False
        try:
            self.dock.audio.seek(int(self.demo_start() * 1000))
            self.dock.audio.play()
        except Exception:  # noqa: BLE001
            return False
        return True

    def play_chart(self, at_seconds=0.0):
        """本編を、音源が読めたところで自動的に流す。

        Player は「見る」道具なので、開いてから再生ボタンを押させる理由が
        無い。読み込みは裏で進むので、押せるようになるのを待つのではなく
        「読めたら鳴らす」と予約しておく。
        """
        self._demo_active = False
        self._demo_video_duration = None
        self._loop = None
        # ここから先は本編。試聴モードを解く(譜面も一緒に走らせる)。
        self.dock.set_audition(False)
        self._apply_volume()
        wave = self.dock._current_wave_path
        if not wave:
            return
        self._pending = (wave, float(at_seconds or 0.0), False)
        self._chart_started = True
        self._held = None
        # **読み込みの最中なら待つ。** 同じ音源を試聴で先に読んでいると
        # 「もう読めている」ように見えるが、本編ぶんの読み込みがそのあと
        # 音を差し替えるので、先に鳴らしても止まってしまう。
        if not self.dock.is_decoding() and self._ready == wave:
            self._fire_pending()

    # ------------------------------------------------------------------
    # 本編の一時停止(Player の途中メニュー)
    # ------------------------------------------------------------------
    def pause_chart(self):
        """本編を今の位置で止める。音源・打音・譜面の時計は同じ音源の
        位置で動いているので、音源を止めればまとめて止まる(打音は音源の
        位置に合わせて予定されている。レーンは playingChanged で止まる)。

        背景の映像: 再生画面(PreviewDock)は背景に動画を流していない(静止画
        だけ。動画は録画のときだけ使う)ので、止めるものは無い。

        まだ鳴らす前(読み込み待ちの予約)なら、その予約を預かっておく
        (読めた瞬間に勝手に鳴りださないように)。"""
        if self._pending is not None:
            self._held, self._pending = self._pending, None
        try:
            self.dock.audio.pause()
        except Exception:  # noqa: BLE001
            pass
        try:
            # 状態の反映は playingChanged 経由で1拍遅れるので、時計もここで
            # 止めておく(メニューを開いた瞬間の位置で絵を止める)。
            cp = self.dock.chart_preview
            cp.set_playback(self.dock.audio.position() / 1000.0, False)
        except Exception:  # noqa: BLE001
            pass

    def resume_chart(self):
        """pause_chart で止めた所から続ける。"""
        held, self._held = self._held, None
        if held is not None:
            self._pending = held
            if not self.dock.is_decoding() and self._ready == held[0]:
                self._fire_pending()
            return
        try:
            self.dock._play()
        except Exception:  # noqa: BLE001
            pass

    def chart_position(self):
        """本編の音源の位置(秒・助走込み)。"""
        try:
            return self.dock.audio.position() / 1000.0
        except Exception:  # noqa: BLE001
            return 0.0

    def chart_playing(self):
        try:
            return bool(self.dock.audio.is_playing())
        except Exception:  # noqa: BLE001
            return False

    def chart_finished(self):
        """本編が終わりまで流れきったか(後ろの余韻 5 秒まで鳴り終えて、
        音源が自分で止まった)。止めているだけ・まだ始まっていないときは
        False。"""
        if not self._chart_started or self._pending is not None or self._held is not None:
            return False
        if self.chart_playing():
            return False
        dur = self.dock.duration_seconds()
        return dur > 0 and self.chart_position() >= dur - 0.25

    def stop_audio(self):
        self._pending = None
        self._held = None
        self._chart_started = False
        self._loop = None
        self._demo_active = False
        self._demo_video_duration = None
        self._demo_requested_start = 0.0
        self._loop_timer.stop()
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
            self._loop = None
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
        # DEMOSTART が音源より後ろを指していたら頭から(そのまま飛ぶと、
        # 終わりの見張りが毎回そこへ戻して無音のまま回り続ける)。
        dur = self.dock.duration_seconds()
        if loop:
            dur = self._demo_loop_duration(dur)
        if dur > 0 and at >= dur - 0.1:
            at = 0.0
            if loop and self._loop is not None:
                self._loop = (0.0, self._loop[1])
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
        if self._pending is not None or self.dock.is_decoding():
            return
        dur = self._demo_loop_duration(self.dock.duration_seconds())
        if dur <= 0:
            return
        pos = self.dock.audio.position() / 1000.0
        # 終わりきる**手前**で戻す。鳴り終わってから戻すと、そのぶんの無音が
        # そのまま切れ目になる。0.05 秒手前なら耳では気づかない。
        if pos >= dur - 0.05:
            self.dock.audio.seek(int(self._loop[0] * 1000))
            self.dock.audio.play()

    def chart_wave_path(self, path):
        """本編を読むとき PreviewDock が使う音源の道筋(refresh_from_content と
        同じ作り方)。無ければ ""。"""
        try:
            content = read_text(path)
        except OSError:
            return ""
        from neotja.preview_dock import parse_preview_headers
        wave = parse_preview_headers(content).get("wave") or ""
        return os.path.join(os.path.dirname(path), wave) if wave else ""

    def predecode(self, path):
        """本編の音源を、鳴っている試聴に触らずに読んでおく。"""
        wave = self.chart_wave_path(path)
        if wave:
            self.dock.predecode_wave(wave)

    def use_normal_play_mode(self):
        """再生画面を「通常再生」にする。PreviewDock は開くと作譜で始まる
        (Editor の決まり)が、Player の本編は通常再生で見せる。設定の
        preview_bottom_mode(Editor と共有)には書かない。"""
        d = self.dock
        idx = getattr(d, "MODE_TITLE", None)
        if idx is None:
            return
        prev = getattr(d, "_restoring_mode", False)
        d._restoring_mode = True
        try:
            d.set_bottom_mode(idx)
        finally:
            d._restoring_mode = prev

    def use_detail_mode(self):
        """再生画面を「作譜」(詳細モード: 上にゲーム画面、下に作譜ペイン)に
        する。位置は動かさない。設定(preview_bottom_mode)には書かない。"""
        d = self.dock
        idx = getattr(d, "MODE_EDIT", None)
        if idx is None:
            return False
        prev = getattr(d, "_restoring_mode", False)
        d._restoring_mode = True
        try:
            d.set_bottom_mode(idx)
        finally:
            d._restoring_mode = prev
        try:
            d.chart_edit.set_position(self.chart_position())
        except Exception:  # noqa: BLE001
            pass
        return True

    def drop_hold(self):
        """止めたときに預かった「読めたら鳴らす」の予約を捨てる。モードを
        替えたあと、勝手に頭から鳴りださないように(再生は手で始める)。"""
        self._held = None

    def chart_ready(self):
        """load() した本編の音源が読み終わっているか。"""
        wave = self.dock._current_wave_path
        return bool(wave) and self._ready == wave and not self.dock.is_decoding()

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

    def load(self, path, course_key=None, play_mode=PLAY_MODE_WATCH):
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
        self.play_mode = play_mode
        if course_key:
            self.course_override = course_key
        preview = self.build_preview(content)
        clicks = self.analyzer.build_metronome_clicks(
            content, None, self.dock.duration_seconds())
        self.dock.refresh_from_content(content, path, clicks, preview,
                                       self._course_stats(content, preview))
        # 演奏モードならここで「叩いた記録」を差し込む。**再生モードでは
        # 必ず None に戻す** — 前の曲の記録が残ると、次の曲が叩いた扱いで
        # 始まってしまう。
        if play_mode == PLAY_MODE_PLAY:
            from neotja.player.play_state import PlayState
            self.dock.set_play_state(PlayState(preview))
        else:
            self.dock.set_play_state(None)
        self.cfg["player_last_file"] = path
        return True

    def build_preview(self, content):
        """いま選ばれているコースの譜面データを組む。

        **録画もここを通す。** 以前は録画側が
        build_preview_timeline(course_override) を直に呼んでいたので、
        アレンジのときは TJA に無いコースキーを渡すことになり、別のコース
        (裏)に落ちて合成されないまま録画されていた。組み立てが2か所に
        あったのが原因なので、1か所にまとめてある。"""
        if self.course_override == tja_analyzer.ARRANGE_COURSE_KEY:
            # アレンジ: 中身は裏譜面(アレンジ)そのもの。表譜面(本家)を
            # もう一度組んで突き合わせ、裏にだけある音符の番号を持たせる。
            # 描く側はそれを見て、足した音符だけ薄くする。
            preview = self.analyzer.build_preview_timeline(
                content, None, tja_analyzer.ARRANGE_OVER_COURSE,
                branch_level=self.branch_level)
            base = self.analyzer.build_preview_timeline(
                content, None, tja_analyzer.ARRANGE_BASE_COURSE,
                branch_level=self.branch_level)
            preview["arrange_added"] = tja_analyzer.arrange_added_notes(
                base, preview)
            # course_key は "Edit" のまま(情報モードの集計がこれで課程を
            # 引くため)。代わりに印を立てて、画面側はこれを見る。
            preview["arrange_ref"] = True
            # コースボタンの表記。中身は裏譜面だが、見ているのは
            # 「アレンジ」なので、うら ではなくその名前を出す。
            preview["course_label"] = tja_analyzer.ARRANGE_COURSE_NAME
            preview["course_color"] = tja_analyzer.ARRANGE_COURSE_COLOR
            return preview
        return self.analyzer.build_preview_timeline(
            content, None, self.course_override,
            branch_level=self.branch_level)

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
