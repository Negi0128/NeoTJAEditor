"""NeoTJAPlayer の入れ物(v15.5〜)。

ランチャーの「Player を起動」で MainWindow の中央(central_stack)へ差し込む
1ページ。別の窓・別のプロセスにはしない(利用者の確定 — Editor と同じ
NeoTJAEditor.exe に同居させる)。

上の段に「戻る」とワードマーク、その下に画面を重ねる(QStackedWidget)。
選曲(LibraryView)で曲が選ばれたら song_chosen を出し、難易度選択
(DifficultyPage)へ進む。難易度選択は窓いっぱいに背景を見せるので、
そのあいだは上の段を隠す(戻る・設定は難易度選択が自分で持つ)。

設定は MainWindow の config_data と保存の口(save_cb)をそのまま借りる。
自分で別の辞書を持つと、Editor が丸ごと書くたびに Player の値が消えるため。
"""

import os
import time

from PySide6.QtCore import Qt, Signal, QPointF, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from neotja.player.difficulty import DifficultyPage
from neotja.player.intro import IntroPage, IntroSequence
from neotja.player.opened import OpenedPage
from neotja.player.play_page import PlayPage
from neotja.player.results import ResultsPage
from neotja.player.transitions import FadeStack
from neotja.player.library_view import LibraryView, library_qss
from neotja.start_screen import StartScreen, dropped_tja_path, wordmark_family
from neotja.theme import COLORS, glass_colors
from neotja.theme import with_alpha as _c, rgba as _rgba


class PlayerHost(QWidget):
    back_requested = Signal()
    song_chosen = Signal(str)
    play_requested = Signal(str, str)     # (tja, コースキー)
    record_requested = Signal(str, str)
    #: 通常プレイで譜面が終わりまで流れた(tja, コースキー)。結果画面の入口。
    play_finished = Signal(str, str)
    #: 再生画面へ入るところ(窓を 16:9 に1回合わせる合図。MainWindow が受ける)。
    play_entering = Signal()

    #: 譜面の終わりを見張る間合い(ms)。
    FINISH_POLL_MS = 100
    #: 選曲で行を選んでから試聴を始めるまでの間(ms)。続けて押したら最後の曲だけ。
    SELECT_PREVIEW_DELAY_MS = 150

    def __init__(self, config_data, save_cb, parent=None, audio_provider=None,
                 editor_audio_cb=None):
        super().__init__(parent)
        self.setObjectName("playerHost")
        #: Player で音量を変えたとき、Editor の音声エンジンと欄へ当て直す口
        #: (MainWindow から。操作音は Editor のエンジンで鳴るので、すぐ要る)。
        self._editor_audio_cb = editor_audio_cb
        # 操作音。鳴らす道は呼び出し側(MainWindow)の音声エンジンを借りる
        # (neotja/player/ui_sound.py の説明)。
        from neotja.player.ui_sound import UiSounds
        self.sounds = UiSounds(audio_provider)
        self.setAcceptDrops(True)
        self.cfg = config_data
        self._save_cb = save_cb
        self._art_dialog = None
        self._art_pending = None
        self._demo_video = None
        #: 再生画面の背景・ジャケット(backdrop.ChartBackdrop)。再生中だけ。
        self._backdrop = None
        self._shutting_down = False
        self.current_song = ""

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        top = QWidget()
        self.top_bar = top
        top.setObjectName("playerTop")
        th = QHBoxLayout(top)
        th.setContentsMargins(16, 12, 28, 10)
        th.setSpacing(14)
        self.btn_back = QPushButton("‹   戻る")
        self.btn_back.setObjectName("back")
        self.btn_back.setCursor(Qt.PointingHandCursor)
        self.btn_back.clicked.connect(lambda: self.sounds.pi())
        self.btn_back.clicked.connect(self.back_requested)
        th.addWidget(self.btn_back, 0, Qt.AlignVCenter)
        self.lbl_name = QLabel()
        self.lbl_name.setObjectName("playerName")
        self.lbl_name.setTextFormat(Qt.RichText)
        th.addWidget(self.lbl_name, 0, Qt.AlignVCenter)
        th.addStretch(1)
        from neotja.player.volume import SpeakerButton, VolumePopover
        self.btn_volume = SpeakerButton()
        self.btn_volume.toggled.connect(self._toggle_volume_popover)
        th.addWidget(self.btn_volume, 0, Qt.AlignVCenter)
        v.addWidget(top)
        rule = QFrame()
        self.top_rule = rule
        rule.setObjectName("rule")
        rule.setFixedHeight(1)
        v.addWidget(rule)

        # 画面の切り替えはフェードで(transitions.FadeStack)。
        self.body = FadeStack()
        v.addWidget(self.body, 1)
        self.library = LibraryView(config_data, save_cb)
        self.library.song_chosen.connect(self._on_song_chosen)
        # 1回目の押下は選ぶだけで、その曲の DEMOSTART から試聴する(少し待って
        # から。連打で何曲も読みにいかない)。
        self.library.song_selected.connect(self._on_song_selected)
        self._select_timer = QTimer(self)
        self._select_timer.setSingleShot(True)
        self._select_timer.setInterval(self.SELECT_PREVIEW_DELAY_MS)
        self._select_timer.timeout.connect(self._start_selected_preview)
        self._select_path = str()
        self.library.sound_requested.connect(self.sounds.play)
        self.library.file_opened.connect(self.show_opened)
        self.library.list_switched.connect(self._on_list_switched)
        self.library.preview_toggled.connect(self._on_preview_toggled)
        self.body.addWidget(self.library)
        # 「TJAを開く」(選ぶ・落とす)のあとの、その曲の画面。
        self.opened = OpenedPage(config_data)
        self.opened.sound_requested.connect(self.sounds.play)
        self.opened.back_requested.connect(self._opened_back)
        self.opened.add_requested.connect(self._opened_add)
        self.opened.play_requested.connect(self._opened_play)
        self.body.addWidget(self.opened)
        self.difficulty = DifficultyPage(config_data, save_cb)
        self.difficulty.back_requested.connect(self.show_library)
        self.difficulty.sound_requested.connect(self.sounds.play)
        self.difficulty.play_requested.connect(self._on_play)
        self.difficulty.record_requested.connect(self._on_record)
        self.difficulty.art_requested.connect(self._on_art)
        self.difficulty.settings.changed.connect(self._on_demo_setting)
        #: 試聴を鳴らす係(PlayerCore)。初めて難易度選択を開いたときに作る。
        #: PlayerCore は裏に PreviewDock(音声エンジン一式)を持つので、
        #: 選曲だけで終わる人にまで音声デバイスを開かせない。
        self._core = None
        self.body.addWidget(self.difficulty)
        # 曲名表示(5 秒・飛ばせない)と再生画面。
        self.intro = IntroPage(config_data)
        self.body.addWidget(self.intro)
        # プレイリストの連続再生(曲名表示の右の板から)。
        self.intro.sound_requested.connect(lambda kind: self.sounds.play(kind))
        self.intro.play_requested.connect(self._playlist_play)
        self.intro.quit_requested.connect(self.end_playlist)
        self.intro.course_changed.connect(self._playlist_course_changed)
        self.library.playlist_play_requested.connect(
            lambda pid, opts: self.start_playlist(pid, **(opts or {})))
        #: 連続再生のいま(None なら普段)。{"pid", "items": [{path, course}], "index"}。
        self._pl_session = None
        # 連続再生の最初のプレイリストの照会画面。
        from neotja.player.playlist_overview import PlaylistOverviewPage
        self.playlist_overview = PlaylistOverviewPage(config_data)
        self.playlist_overview.sound_requested.connect(lambda kind: self.sounds.play(kind))
        self.playlist_overview.play_requested.connect(self._playlist_overview_play)
        self.playlist_overview.quit_requested.connect(self.end_playlist)
        self.body.addWidget(self.playlist_overview)
        self.play_page = PlayPage()
        self.body.addWidget(self.play_page)
        self.play_page.esc_pressed.connect(self.open_pause_menu)
        self.play_page.menu.chosen.connect(self._on_menu)
        self.play_page.menu.sound_requested.connect(self.sounds.play)
        # 結果画面(通常プレイで譜面が終わったとき)。
        self.results = ResultsPage(config_data)
        self.body.addWidget(self.results)
        self.results.sound_requested.connect(self.sounds.play)
        self.results.retry_requested.connect(self._on_results_retry)
        self.results.library_requested.connect(self._on_results_library)
        #: 結果画面に出している曲 (tja, コースキー)。
        self._result_ctx = None
        #: いま進んでいる曲名表示の段取り(IntroSequence)。無ければ None。
        self._seq = None
        #: 再生画面で流している曲 (tja, コースキー)。流していなければ None。
        self._play_ctx = None
        #: 再生画面のモード。"normal"(通常プレイ)/ "detail"(詳細モード)。
        self.play_mode = "normal"
        self._finish_timer = QTimer(self)
        self._finish_timer.setInterval(self.FINISH_POLL_MS)
        self._finish_timer.timeout.connect(self._check_finished)

        # 音量(全体・曲・効果音)。選曲の小窓・難易度設定・途中メニューの3か所
        # から同じ値を変える。保存は動かし終わってからまとめて1回。
        self.volume_popover = VolumePopover(self)
        self.volume_popover.rows.changed.connect(self.set_audio_level)
        self.difficulty.settings.volume_changed.connect(self.set_audio_level)
        self.play_page.menu.volume.changed.connect(self.set_audio_level)
        self._volume_save = QTimer(self)
        self._volume_save.setSingleShot(True)
        self._volume_save.setInterval(400)
        self._volume_save.timeout.connect(self._flush_volume_save)
        self._sync_volume_widgets()

        self.refresh_theme()

    # ------------------------------------------------------------------
    def on_shown(self):
        """ランチャーから入ったとき。選曲の最初の画面へ。"""
        # Editor 側で変えた音量を、こちらの欄にも出す。
        self._sync_volume_widgets()
        # 音声のコールバックが GUI の描画に待たされないように(core の説明)。
        from neotja.player.core import audio_friendly_gil
        audio_friendly_gil(True)
        self._set_page(self.library)
        self.library.show_default()
        # 押した「Player を起動」が消えて、フォーカスが「戻る」へ流れ着く
        # のを避ける(Enter 一つでランチャーへ戻ってしまう)。
        self.library.pages.currentWidget().setFocus()
        # 試聴の係(音声エンジン一式)は、選曲が出てから裏で用意しておく
        # (起動時には作らない。最初の1曲を押したときに待たせないため)。
        QTimer.singleShot(0, self._warm_core)

    def _warm_core(self):
        if self._shutting_down or not self.isVisible() or self._core is not None:
            return
        try:
            core = self._ensure_core()
            # 再生ウィンドウをいまのうちに再生画面の子にしておく(初回の付け替えは
            # 約 0.7 秒 GUI と GIL をふさぐ。曲名表示の後では試聴・本編の音に
            # 重なるので、まだ何も鳴っていない選曲の入口で済ませる)。
            adopt = getattr(self.play_page, "adopt", None)
            gpw = getattr(getattr(core, "dock", None), "game_preview_window", None)
            if adopt is not None and gpw is not None:
                adopt(gpw)
                # GL の部品が窓に入ったあとの最初の撮影(フェード)は 90ms ほど
                # 重い。その1回目もここで済ませる。
                self.library.grab()
                # ゲーム画面の最初の描画(GL の初期化・絵の読み込み 約 0.4 秒)も、
                # 見せずに1枚描いて済ませる(再生へ入る黒の中で止まらない)。
                gs = getattr(core.dock, "game_screen", None)
                warm = getattr(gs, "grabFramebuffer", None)
                if warm is not None:
                    warm()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 音量(Editor と同じ master_volume / preview_volume / sfx_volume)
    def set_audio_level(self, key, value):
        """音量を1つ変える。設定へ書き、Player と Editor の音へすぐ当て、
        ほかの欄もそろえる。保存は少し待ってまとめて。"""
        from neotja.player.volume import DEFAULTS
        if key not in DEFAULTS:
            return
        try:
            value = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return
        self.cfg[key] = value
        self._sync_volume_widgets()
        self.apply_audio_settings()
        self._volume_save.start()

    def apply_audio_settings(self):
        """設定の音を Player の音声エンジン(あれば)と Editor の両方へ当てる。"""
        core = self._core
        fn = getattr(core, "apply_audio_settings", None) if core is not None else None
        if fn is not None:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        cb = self._editor_audio_cb
        if cb is not None:
            try:
                cb()
            except Exception:  # noqa: BLE001
                pass

    def _sync_volume_widgets(self):
        from neotja.player.volume import level_of
        self.volume_popover.rows.set_values(self.cfg)
        self.play_page.menu.volume.set_values(self.cfg)
        self.difficulty.settings.set_volume_values(self.cfg)
        muted = level_of(self.cfg, "master_volume") <= 0.0
        if self.btn_volume.muted != muted:
            self.btn_volume.muted = muted
            self.btn_volume.update()

    def _flush_volume_save(self):
        self._volume_save.stop()
        try:
            self._save_cb()
        except Exception:  # noqa: BLE001
            pass

    def flush_volume_save(self):
        """Player を離れる・閉じるとき。待たせている保存を今すぐ。"""
        if self._volume_save.isActive():
            self._flush_volume_save()

    def _toggle_volume_popover(self, on):
        self.sounds.pi()
        if on:
            self._sync_volume_widgets()
            self.volume_popover.open_under(self.btn_volume)
        else:
            self.volume_popover.close_popover()

    def _on_song_selected(self, path):
        """選曲で曲を1回押した。少し待ってから、その曲の DEMOSTART から試聴。"""
        self._select_path = path
        self._select_timer.start()

    def _start_selected_preview(self):
        path, self._select_path = self._select_path, str()
        if (not path or self._shutting_down or not self.isVisible()
                or self.body.currentWidget() is not self.library
                or self.library.selected_path() != path
                or not self.library.preview_playing()):
            return
        # 同じ曲がもう流れていればそのまま(違う曲なら DEMOSTART から)。
        self.start_preview(path, restart=False)

    def _on_list_switched(self):
        """選曲で右の一覧を替えた(ジャンル・カテゴリ・最近/保存済み・検索の
        結果など)。流している試聴は止める(難易度選択から戻ったときは
        一覧を替えないので続いたまま)。"""
        if self.body.currentWidget() is not self.library:
            return
        self._select_timer.stop()
        self._select_path = str()
        self._stop_demo_video()
        if self._core is not None:
            self._core.stop_audio()
        self.library.set_preview_playing(False)

    def _on_preview_toggled(self, path, play):
        """選んでいる行の右の印: 止める / DEMOSTART から流し直す。"""
        if self.body.currentWidget() is not self.library:
            return
        self._select_timer.stop()
        self._select_path = str()
        if play:
            self.start_preview(path, restart=True)
        else:
            self._stop_demo_video()
            if self._core is not None:
                self._core.stop_audio()

    def _sync_library_mark(self):
        """選曲へ戻るとき。選んでいる曲の試聴が流れているかを印へ。"""
        path = self.library.selected_path()
        core = self._core
        on = False
        if path and core is not None:
            fn = getattr(core, "demo_active_for", None)
            try:
                on = bool(fn(path)) if fn is not None else False
            except Exception:  # noqa: BLE001
                on = False
        self.library.set_preview_playing(on)

    def _on_song_chosen(self, path):
        # 選んでいた曲をもう一度押した: 試聴は止めずに難易度選択へ。
        self._select_timer.stop()
        self._select_path = str()
        self.current_song = path
        self.song_chosen.emit(path)
        # プレイリストの曲なら、保存したコースを選んだ状態で。
        self.show_difficulty(path, course=self.library.chosen_course(), restart=False)

    def _set_page(self, page, fade=None, done=None):
        """画面を替える。fade は None(すぐ)/ "cross" / "black"。"""
        full = page in (self.difficulty, self.intro, self.play_page, self.results,
                        self.opened, getattr(self, "playlist_overview", None))
        if full:
            self.volume_popover.close_popover()
        self.top_bar.setVisible(not full)
        self.top_rule.setVisible(not full)
        if fade:
            self.body.fade_to(page, mode=fade, done=done)
        else:
            self.body.cancel()
            self.body.setCurrentWidget(page)

    # ------------------------------------------------------------------
    # 「TJAを開く」のあとの画面
    def show_opened(self, path):
        """開いた(選んだ・落とした)TJA の画面へフェードで。"""
        if not path or not os.path.isfile(path):
            return
        path = os.path.normpath(os.path.abspath(path))
        if self.body.currentWidget() not in (self.library, self.opened):
            # 演奏・難易度選択などの途中で落とされた: 鳴っているものを止める
            # (選曲の試聴はそのまま)。
            self.stop_preview()
        self._select_timer.stop()
        self._select_path = str()
        self._abort_sequence()
        self.opened.set_song(path)
        self._set_page(self.opened, fade="cross",
                       done=lambda: self.opened.setFocus(Qt.OtherFocusReason))

    def _opened_back(self):
        self.library.refresh_recent()
        self._sync_library_mark()
        self._set_page(self.library, fade="cross")

    def _opened_add(self, path):
        from neotja.player import song_lists
        if song_lists.add_saved(self.cfg, path) or song_lists.is_saved(self.cfg, path):
            try:
                self._save_cb()
            except Exception:  # noqa: BLE001
                pass
            self.opened.set_added(True)
            # 保存済みの一覧は、次に開いたときに作り直される(_fill_saved)。
            if self.library.current_section() == "saved":
                self.library._fill_saved()

    def _opened_play(self, path):
        """すぐに再生: 難易度選択へ(試聴つき)。"""
        self.library.mark_chosen(path)
        self.current_song = path
        self.song_chosen.emit(path)
        self.show_difficulty(path, fade="cross")

    def show_difficulty(self, path, course=None, fade=None, restart=True):
        """難易度選択へ。course を渡すとそのコースを選んだ状態にする
        (演奏から戻ったとき)。fade を渡すとフェードしてから試聴を始める。
        restart=False は選曲から: 同じ曲の試聴が流れていれば続ける。"""
        self._abort_sequence()
        self.difficulty.close_settings()
        self.difficulty.set_song(path)
        if course:
            self._select_course(course)

        def after():
            self.start_preview(path, restart=restart)
            self.difficulty.setFocus()
        if fade:
            self._set_page(self.difficulty, fade=fade, done=after)
        else:
            self._set_page(self.difficulty)
            after()

    def _select_course(self, course):
        """難易度選択で course を選んだ状態にする(裏ならおにの枠を裏へ)。"""
        d = self.difficulty
        if course in ("Oni", "Edit") and d.has_ura_toggle():
            d.oni_side = course
            d.select_slot("Oni")
        elif course != "Edit":
            d.select_slot(course)
        d._sync()
        d.update()

    def show_library(self):
        """難易度選択の「戻る」。選曲は作り直さないので、開いていた
        フォルダ・検索・並び・選んだ行はそのまま。**試聴は止めない**(選曲で
        流していた続き。同じ曲をもう一度押すと、そのまま難易度選択へ)。
        背景動画は選曲に出す所が無いので止める。"""
        self._stop_demo_video()
        self._abort_sequence()
        self._end_play()
        self.difficulty.close_settings()
        self.library.refresh_recent()
        self._sync_library_mark()
        self._set_page(self.library)

    def shutdown(self):
        """窓を閉じるとき。試聴を止めて、Player の音声デバイスを閉じる。"""
        self._shutting_down = True
        from neotja.player.core import audio_friendly_gil
        audio_friendly_gil(False)
        self.volume_popover.close_popover()
        self.flush_volume_save()
        self._stop_demo_video()
        self._art_pending = None
        if self._art_dialog is not None:
            self._art_dialog.reject()
        self.library.shutdown()
        self._abort_sequence()
        self._end_play()
        if self._pl_session is not None:
            self._pl_session = None
            self.play_page.menu.set_playlist_mode(False)
            self.intro.clear_playlist()
        core, self._core = self._core, None
        if core is not None:
            try:
                core.stop_audio()
                core.shutdown()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # 難易度選択の試聴(DEMOSTART から流して、音源の終わりでまた DEMOSTART へ)
    #: PlayerCore を作る呼び出し。試験で差し替える。
    core_factory = None

    def _ensure_core(self):
        if self._core is None:
            factory = self.core_factory
            if factory is None:
                from neotja.player.core import PlayerCore
                factory = PlayerCore
            self._core = factory(self.cfg)
        return self._core

    def start_preview(self, path, restart=True):
        """選んだ曲を試聴する。

        restart=True(既定): **必ず DEMOSTART から**始め直す。開き直し・
        録画や設定から戻ったときはこちら。前の曲は必ず先に止める(音源が
        無い曲へ移ったときに前の曲が鳴り続けないように)。
        restart=False: 同じ曲の試聴が続いていれば位置を変えずにそのまま
        (選曲画面から試聴を引き継ぐ場合のための口)。違う曲なら restart と同じ。"""
        from neotja.player import core as core_mod
        try:
            content = core_mod.read_text(path)
        except OSError:
            self.stop_preview()
            return
        core = self._ensure_core()
        # 音の設定(音量・打音・ワイヤレス調整・出力デバイス)は Editor と同じ
        # config_data。Editor で変えられていてもよいように、そのたびに当て直す。
        self.apply_audio_settings()
        demo = core_mod.demo_start_seconds(content)
        if not restart and getattr(core, "demo_active_for", None) and core.demo_active_for(path):
            if self._demo_video is None:
                self._start_demo_video(path)
            return
        self._stop_demo_video()
        core.stop_audio()
        core.play_demo(path, demo)
        self._start_demo_video(path)

    def _stop_demo_video(self):
        video, self._demo_video = self._demo_video, None
        if video is not None:
            video.stop()
            video.deleteLater()

    def _start_demo_video(self, path):
        self._stop_demo_video()
        if not self.cfg.get("player_show_bg", True) or self._core is None:
            return
        from neotja.player.song_lists import existing_art
        from neotja.player.demo_video import DemoVideo, is_video_path
        video_path = existing_art(self.cfg, path, "background")
        if not is_video_path(video_path):
            return
        self._demo_video = self._make_video(
            self.difficulty.set_video_frame, self.difficulty.clear_video_frame)
        self._demo_video.start(video_path)

    def _on_demo_setting(self, key, value):
        if key == "player_show_bg" and self.body.currentWidget() is self.difficulty:
            if value:
                self._start_demo_video(self.difficulty.path)
            else:
                self._stop_demo_video()

    def release_audio_cache(self):
        """Player を出るとき。持っている曲(読み終わった PCM)を手放す。"""
        from neotja.player.core import audio_friendly_gil
        audio_friendly_gil(False)
        fn = getattr(self._core, "release_cache", None) if self._core is not None else None
        if fn is not None:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def stop_preview(self):
        """鳴っているものを全部止める(試聴・曲名表示の段取り・本編)。"""
        self._select_timer.stop()
        self._select_path = str()
        self._stop_demo_video()
        self._abort_sequence()
        self._end_play()
        if self._core is not None:
            self._core.stop_audio()

    def _on_play(self, path, course):
        # 最近開いた楽曲へ入れるのは、演奏・録画を始めたときだけ。
        self.library.note_played(path)
        self.play_requested.emit(path, course)
        self.start_intro(path, course)

    # ------------------------------------------------------------------
    # 曲名表示 → 再生
    def start_intro(self, path, course):
        """Play のあと。曲名表示へフェードし、試聴は 2 秒そのまま→3 秒で
        0 へ。そのあいだに本編の音源を先読みし、5 秒(間に合わなければ準備が
        できるまで)で再生画面へ。"""
        self._abort_sequence()
        core = self._ensure_core()
        self.intro.clear_playlist()
        self.intro.set_song(path, course, self.difficulty.levels.get(course))
        # 背景動画は止めずに曲名表示へ引き継ぐ(難易度選択・結果画面で流して
        # いたもの)。流していなければ、ここから無音で流す。
        prev_clear = self._video_handoff(path, self.intro.set_video_frame,
                                         self.intro.clear_video_frame)
        try:
            core.predecode(path)
        except Exception:  # noqa: BLE001
            pass
        base = float(self.cfg.get("preview_volume", 0.8) or 0.0)
        seq = IntroSequence(
            set_volume=self._set_demo_volume, base_volume=base,
            stop_demo=lambda: self._stop_demo_audio(core),
            begin_load=lambda: self._begin_load(path, course),
            is_ready=core.chart_ready, parent=self)
        seq.finished.connect(lambda: self._enter_play(path, course))
        seq.failed.connect(lambda: self._intro_failed(path))
        self._seq = seq
        self._set_page(self.intro, fade="cross")
        if prev_clear is not None:
            # 前の画面の絵は、クロスフェード用に撮ったあとで外す。
            prev_clear()
        self.intro.setFocus()
        seq.start()

    def _stop_demo_audio(self, core):
        """曲名表示の5秒目。試聴を止め、背景動画は自分の時計で流し続ける。"""
        video = self._demo_video
        if video is not None:
            video.free_run()
        core.stop_audio()

    def _bg_video_path(self, path):
        """曲の背景が動画で、背景を出す設定なら、その道。でなければ ""。"""
        if not self.cfg.get("player_show_bg", True):
            return ""
        from neotja.player.song_lists import existing_art
        from neotja.player.demo_video import is_video_path
        p = existing_art(self.cfg, path, "background")
        return p if is_video_path(p) else ""

    #: 背景動画を作る呼び出し(試験で差し替える)。None なら DemoVideo。
    video_factory = None

    def _make_video(self, frame_cb, clear_cb):
        factory = self.video_factory
        if factory is None:
            from neotja.player.demo_video import DemoVideo as factory
        return factory(self._core, frame_cb, clear_cb, self)

    def _video_handoff(self, path, frame_cb, clear_cb):
        """流している背景動画の渡し先を替える。同じ動画でなければ止めて、
        無音・自分の時計で流し直す。前の渡し先の「外す」を返す(無ければ None)。"""
        want = self._bg_video_path(path)
        video = self._demo_video
        if video is not None and want and getattr(video, "path", "") == want:
            prev = getattr(video, "_clear_callback", None)
            video.set_targets(frame_cb, clear_cb)
            return prev
        self._stop_demo_video()
        if want:
            self._demo_video = self._make_video(frame_cb, clear_cb)
            self._demo_video.start(want, free=True)
        return None

    def _set_demo_volume(self, v):
        core = self._core
        if core is None:
            return
        try:
            core.dock.set_volume(max(0.0, min(1.0, float(v))))
        except Exception:  # noqa: BLE001
            pass

    def manual_play(self):
        """自分で叩くか。難易度設定の「オート演奏」(player_auto、既定 ON)が
        OFF のとき。プレイリストはいつもオート(利用者の指定)。"""
        return (self._pl_session is None
                and not bool(self.cfg.get("player_auto", True)))

    def _begin_load(self, path, course):
        from neotja.constants import PLAY_MODE_PLAY, PLAY_MODE_WATCH
        core = self._core
        if core is None:
            return False
        mode = PLAY_MODE_PLAY if self.manual_play() else PLAY_MODE_WATCH
        try:
            return bool(core.load(path, course_key=course, play_mode=mode))
        except Exception:  # noqa: BLE001
            return False

    def _enter_play(self, path, course):
        """準備ができた。黒を挟んで再生画面へ移り、ふつうの頭から流す
        (助走・OFFSET は Editor のプレビューと同じ決まり。DEMOSTART ではない)。
        黒は再生画面の上で薄くしていき(GL の窓でも、画面自身が黒を描く)、
        **明け切ってから**曲を流す(利用者の指定)。"""
        self._seq = None
        self._stop_demo_video()
        core = self._core
        if core is None:
            return
        dock = core.dock
        try:
            core.use_normal_play_mode()
        except Exception:  # noqa: BLE001
            pass
        try:
            dock.chart_preview.reset_to_start()
        except Exception:  # noqa: BLE001
            pass
        native = bool(getattr(getattr(dock, "game_screen", None), "native_screen_window", False))
        # 通常プレイ: 止める・巻き戻す・飛ばすを締める(キーは再生画面へ回る)。
        self.play_mode = "normal"
        self.play_page.menu.set_detail_mode(False)
        self.play_page.hide_menu()
        self.play_page.attach(dock.game_preview_window, native,
                              screen_fn=lambda: getattr(dock, "game_screen", None))
        # 自分で叩くときは D F J K を打面へ(縁・面・面・縁)。
        cp = getattr(dock, "chart_preview", None)
        if cp is not None:
            from neotja.player.core import input_lag_sec
            cp.input_lag_sec = input_lag_sec(self.cfg)
        self.play_page.pad_cb = (getattr(cp, "_queue_press", None)
                                 if getattr(cp, "play_state", lambda: None)() is not None
                                 else None)
        self._play_ctx = (path, course)
        self._apply_fps_overlay()
        # 曲の背景・ジャケットを録画と同じ見え方で画面の下へ敷く(フェードの
        # 黒が明けたときにはもう出ているよう、切り替える前に)。
        self._start_backdrop(path)
        # 通常の窓なら、ゲーム画面の部分が 16:9 になるよう窓を1回合わせる
        # (左右の黒い帯を出さない)。
        self.play_entering.emit()

        def go():
            # キーは再生画面(PlayPage)が受ける。ゲーム画面にフォーカスを
            # 渡さない(締めてあるので渡っても再生画面へ回るが、GL の窓だと
            # 窓ごと前に出てしまうため)。
            self.play_page.setFocus(Qt.OtherFocusReason)
            # 曲名表示で下げた音量を戻し、音の設定を当て直してから流す。
            self.apply_audio_settings()
            core.play_chart(0.0)
            self._finish_timer.start()
        self._set_page(self.play_page, fade="black", done=go)

    # ------------------------------------------------------------------
    # 通常プレイの途中メニュー(Esc)と終わり
    def in_play(self):
        return self._play_ctx is not None

    def wants_aspect_lock(self):
        """窓の比を 16:9 に保ちたいか。Player を出しているあいだはずっと
        (選曲・難易度選択・曲名表示・再生・結果・詳細モード。利用者の指定
        2026-11-19)。最大化・全画面かどうかは MainWindow が見る。"""
        return self.isVisible()

    def aspect_widget(self):
        """16:9 に保つ部分。Player 全体(上の段を出す画面と出さない画面で
        窓の大きさが変わらないように)。上の段を隠す再生画面では、これが
        そのままゲーム画面の部分になる。"""
        return self

    def open_pause_menu(self):
        """Esc。音源・譜面の時計をまとめて止めてメニューを出す。"""
        if (self._play_ctx is None or self.play_page.menu_open()
                or self.body.currentWidget() is not self.play_page
                or self.body.is_fading()):
            return
        self._finish_timer.stop()
        core = self._core
        if core is not None:
            core.pause_chart()
        self.sounds.pi()
        self._sync_volume_widgets()
        self.play_page.show_menu(self._game_snapshot())

    def _game_snapshot(self):
        """GL の窓のときだけ、止めた画面を1枚もらう(メニューの後ろに敷く)。"""
        if not self.play_page.native_gl or self._core is None:
            return None
        try:
            img = self._core.dock.game_screen.grabFramebuffer()
            return None if img.isNull() else img
        except Exception:  # noqa: BLE001
            return None

    def _on_menu(self, key):
        ctx = self._play_ctx
        if ctx is None:
            self.play_page.hide_menu()
            return
        path, course = ctx
        if self._pl_session is not None and key in ("next", "restart", "library"):
            # 連続再生: 次の曲へ / この曲の曲名表示から / プレイリストごとやめる。
            self._end_play()
            if self._core is not None:
                self._core.stop_audio()
            if key == "next":
                self._playlist_advance()
            elif key == "restart":
                self._playlist_intro(self._pl_session["index"], fade="black")
            else:
                self.end_playlist()
            return
        if key == "resume":
            self.play_page.hide_menu()
            if self.play_mode == "detail" and self._core is not None:
                try:
                    self._core.dock.chart_edit.setFocus(Qt.OtherFocusReason)
                except Exception:  # noqa: BLE001
                    self.play_page.setFocus(Qt.OtherFocusReason)
            else:
                self.play_page.setFocus(Qt.OtherFocusReason)
            if self._core is not None:
                self._core.resume_chart()
            if self.play_mode == "normal":
                self._finish_timer.start()
        elif key == "restart":
            # 暫定方針: 同じコース・表示設定で 5 秒の曲名表示からやり直す。
            # 本編は曲名表示の 5 秒目に読み直す(core.load)ので、譜面の状態・
            # 集計は全部そこで初めからになる。
            self._end_play()
            if self._core is not None:
                self._core.stop_audio()
            self.start_intro(path, course)
        elif key == "detail":
            if self.play_mode == "detail":
                self.switch_to_normal()
            else:
                self.switch_to_detail()
        elif key == "library":
            self._end_play()
            if self._core is not None:
                self._core.stop_audio()
            self.difficulty.close_settings()

            def focus_library():
                try:
                    self.library.pages.currentWidget().setFocus()
                except Exception:  # noqa: BLE001
                    pass
            # 選曲は作り直さないので、開いていたフォルダ・検索・選んだ行は
            # そのまま残っている(最近開いた楽曲だけは演奏した曲を足す)。
            self.library.refresh_recent()
            self._sync_library_mark()
            self._set_page(self.library, fade="black", done=focus_library)

    def switch_to_detail(self):
        """詳細モードへ(メニューの3番目)。

        暫定方針: 位置はそのまま、切り替えた直後は止まったまま(続けるか
        Space で流す)。下に作譜ペインを読むだけで出し、止める・シーク・
        速度・表示の切り替えを許す。譜面を変える道は PlayerCore の
        PreviewDock が最初から set_read_only(True) でふさいでいる。
        終わりまで流れても結果へは進まない(音源が止まる = そこで一時停止)。"""
        if self._play_ctx is None:
            return
        core = self._core
        self._finish_timer.stop()
        self.play_page.hide_menu()
        if core is not None:
            core.pause_chart()
            core.drop_hold()
            try:
                core.use_detail_mode()
            except Exception:  # noqa: BLE001
                pass
        self.play_mode = "detail"
        self.play_page.set_detail(True)
        self._apply_fps_overlay()
        self._set_value_readout(True)
        if core is not None:
            try:
                core.dock.set_speed_row_visible(True)
            except Exception:  # noqa: BLE001
                pass
            self.play_page._fit()
            try:
                core.dock.chart_edit.setFocus(Qt.OtherFocusReason)
            except Exception:  # noqa: BLE001
                self.play_page.setFocus(Qt.OtherFocusReason)
        else:
            self.play_page.setFocus(Qt.OtherFocusReason)

    def switch_to_normal(self):
        """通常プレイへ戻る(詳細モードのメニューの3番目「通常演奏に移行する」)。
        位置はそのまま、そこから流し直す(通常プレイは止める・流す操作を
        締めているので、止めたままだと続けられなかった。2026-11-20)。

        詳細でシークしたあと通常で終わりまで流したときの結果も、同じ計算の
        「譜面の終わり」の値(on_play_finished)。"""
        if self._play_ctx is None:
            return
        core = self._core
        self.play_page.hide_menu()
        if core is not None:
            core.pause_chart()
            core.drop_hold()
            try:
                core.dock.set_speed_row_visible(False)
            except Exception:  # noqa: BLE001
                pass
            try:
                core.use_normal_play_mode()
            except Exception:  # noqa: BLE001
                pass
        self.play_mode = "normal"
        self.play_page.set_detail(False)
        self._apply_fps_overlay()
        self._set_value_readout(False)
        self.play_page.setFocus(Qt.OtherFocusReason)
        if core is not None:
            core.resume_chart()
            self._finish_timer.start()

    def _set_value_readout(self, on):
        """詳細モードの右上の数字(BPM・HS・見た目BPM)。"""
        fn = getattr(getattr(self._core, "dock", None), "set_value_readout", None)
        if fn is None:
            return
        try:
            fn(on)
        except Exception:  # noqa: BLE001
            pass
        # 見出し(BPM・拍子…)は小さく #93a3bf にして、値を目立たせる。
        # 枠の形は Editor と同じまま(この見た目は Player だけ)。
        rd = getattr(getattr(self._core, "dock", None), "value_readout", lambda: None)()
        if on and rd is not None and not getattr(rd, "_player_look", False):
            from PySide6.QtGui import QColor
            rd.TITLE_COLOR = QColor("#93a3bf")
            rd.TITLE_PX = 9
            from neotja.player.difficulty import ui_css_family
            rd.FAMILY = ui_css_family()
            rd._relayout()
            rd._player_look = True
            rd.update()

    def _apply_fps_overlay(self):
        """実測 fps の表示。詳細モードでは常に、通常プレイでは設定
        「FPS表示」(player_show_fps、既定 OFF)が ON のときだけ出す。"""
        core = self._core
        fn = getattr(getattr(core, "dock", None), "set_view_fps", None)
        if fn is None:
            return
        on = self.play_mode == "detail" or bool(self.cfg.get("player_show_fps", False))
        try:
            from neotja.player.difficulty import ui_css_family
            core.dock.VIEW_FPS_FAMILY = ui_css_family()
        except Exception:  # noqa: BLE001
            pass
        try:
            fn(on)
        except Exception:  # noqa: BLE001
            pass

    def _check_finished(self):
        core = self._core
        if core is None or self._play_ctx is None:
            self._finish_timer.stop()
            return
        if (self.play_page.menu_open() or self.body.is_fading()
                or self.play_mode != "normal"):
            return
        try:
            done = bool(core.chart_finished())
        except Exception:  # noqa: BLE001
            done = False
        if done:
            self.on_play_finished()

    def on_play_finished(self):
        """通常プレイで譜面が終わった。結果画面へ(黒を挟んで)。

        値は再生画面の HUD と同じ計算を譜面の終わりの時刻で引いたもの
        (game_screen.result_values)。詳細モードでシークしてから通常に戻して
        終わった場合も、同じ計算の「譜面の終わり」の値になる(オートは時刻
        から決まるので、途中を飛ばしても終わりの値は変わらない)。"""
        ctx = self._play_ctx
        if ctx is None:
            return
        path, course = ctx
        if self._pl_session is not None:
            # 連続再生では結果画面を出さず、次の曲の曲名表示へ(最後ならプレイリストへ)。
            self._end_play()
            if self._core is not None:
                self._core.stop_audio()
            self.play_finished.emit(path, course)
            self._playlist_advance()
            return
        values, gauge, judges = self._collect_results()
        self._end_play()
        if self._core is not None:
            self._core.stop_audio()
        self.play_finished.emit(path, course)
        self._result_ctx = (path, course)
        self.results.auto = self.play_page.pad_cb is None
        self.results.set_result(path, course, self.difficulty.levels.get(course),
                                values, gauge, judges)
        # 背景が動画なら、結果画面でも無音・ループで流す(離れるときに止める)。
        self._video_handoff(path, self.results.set_video_frame,
                            self.results.clear_video_frame)
        self._set_page(self.results, fade="black",
                       done=lambda: self.results.btn_retry.setFocus(Qt.OtherFocusReason))

    def _collect_results(self):
        """結果の値・魂ゲージの絵・良/可/不可の絵を再生画面からもらう。"""
        values, gauge, judges = {}, None, {}
        core = self._core
        dock = getattr(core, "dock", None)
        gs = getattr(dock, "game_screen", None)
        try:
            values = dict(gs.result_values())
        except Exception:  # noqa: BLE001
            values = {}
        try:
            gauge = gs.gauge_strip(float(values.get("gauge_ratio") or 0.0))
        except Exception:  # noqa: BLE001
            gauge = None
        cp = getattr(dock, "chart_preview", None)
        for k in ("good", "ok", "bad"):
            try:
                judges[k] = cp.judge_sprite(k)
            except Exception:  # noqa: BLE001
                pass
        return values, gauge, judges

    def _on_results_retry(self):
        """もう一度プレイする: 同じコースで 5 秒の曲名表示から(全部初めから)。"""
        ctx = self._result_ctx
        if ctx is None:
            return
        self._result_ctx = None
        self.start_intro(*ctx)

    def _on_results_library(self):
        """選曲画面に戻る(Esc も)。選曲の状態はそのまま。"""
        self._result_ctx = None
        self._stop_demo_video()
        if self._core is not None:
            self._core.stop_audio()

        def focus_library():
            try:
                self.library.pages.currentWidget().setFocus()
            except Exception:  # noqa: BLE001
                pass
        self.library.refresh_recent()
        self._sync_library_mark()
        self._set_page(self.library, fade="black", done=focus_library)

    #: 背景を作る呼び出し(試験で差し替える)。None なら ChartBackdrop。
    backdrop_factory = None

    def _start_backdrop(self, path):
        """再生画面の背景・ジャケット。背景/ジャケットを出す設定と、曲別の
        「表示しない」を守る。どちらも無ければ何も敷かない。"""
        self._stop_backdrop()
        core = self._core
        dock = getattr(core, "dock", None)
        screen = getattr(dock, "game_screen", None)
        if dock is None or screen is None or not hasattr(screen, "set_underlay"):
            return
        from neotja.player.backdrop import ChartBackdrop, player_background
        try:
            bg = player_background(self.cfg, path)
        except Exception:  # noqa: BLE001
            return

        def clock():
            cp = dock.chart_preview
            lead = float(getattr(dock, "_lead_sec", 0.0) or 0.0)
            return (cp._current_audio_time() - lead,
                    bool(getattr(cp, "_playing", False)))
        factory = self.backdrop_factory or ChartBackdrop
        backdrop = factory(clock, self)
        backdrop.configure(bg, show_bg=bool(self.cfg.get("player_show_bg", True)),
                           show_jacket=bool(self.cfg.get("player_show_jacket", True)))
        if not backdrop.active():
            backdrop.stop()
            backdrop.deleteLater()
            return
        backdrop.attach(screen)
        self._backdrop = backdrop

    def _stop_backdrop(self):
        backdrop, self._backdrop = self._backdrop, None
        if backdrop is not None:
            backdrop.stop()
            backdrop.deleteLater()

    def backdrop(self):
        return self._backdrop

    def _end_play(self):
        """再生画面の後片付け(音は止めない — 呼んだ側が止める)。"""
        self._finish_timer.stop()
        self._stop_backdrop()
        self._play_ctx = None
        self.play_page.hide_menu()
        self.play_page.unlock()
        if self.play_mode == "detail":
            # 次に再生画面へ入るときは通常プレイから(_enter_play が
            # use_normal_play_mode と set_view_only を当て直す)。
            self.play_mode = "normal"
            self._set_value_readout(False)
            core = self._core
            if core is not None:
                try:
                    core.dock.set_speed_row_visible(False)
                except Exception:  # noqa: BLE001
                    pass

    def _intro_failed(self, path):
        """本編を読めなかった。難易度選択へ戻す(連続再生なら次の曲へ)。"""
        self._seq = None
        if self._pl_session is not None:
            self._playlist_advance()
            return
        self.show_difficulty(path)

    # ------------------------------------------------------------------
    # プレイリストの連続再生(今は手動だけ: 毎曲、曲名表示で「再生する」を待つ)
    #: 「再生する」を押してから試聴を 0 まで下げる長さ(秒)。
    PLAYLIST_FADE_SEC = 1.0

    def playlist_session(self):
        return self._pl_session

    #: 自動のときの待ち(秒)の既定と幅。
    PLAYLIST_WAIT_DEFAULT = 10
    PLAYLIST_WAIT_RANGE = (3, 60)

    def start_playlist(self, pid, start=0, end=None, auto=False, wait=None):
        """プレイリストの start 番目から end 番目まで(0 から数える、end を含む。
        None なら最後まで)を順に。見つからない曲は飛ばす。auto は完全オート
        (曲名表示で wait 秒待って自分で進む)。始めたら True。"""
        store = self.library.playlist_store()
        pl = store.get(pid)
        if pl is None:
            return False
        stop = len(pl["items"]) if end is None else max(0, int(end)) + 1
        items = [dict(i) for i in pl["items"][max(0, int(start)):stop]
                 if os.path.isfile(i.get("path") or "")]
        if not items:
            return False
        if wait is None:
            wait = self.cfg.get("player_playlist_wait", self.PLAYLIST_WAIT_DEFAULT)
        lo, hi = self.PLAYLIST_WAIT_RANGE
        try:
            wait = max(lo, min(hi, int(wait)))
        except (TypeError, ValueError):
            wait = self.PLAYLIST_WAIT_DEFAULT
        self._select_timer.stop()
        self._select_path = str()
        self._abort_sequence()
        self._end_play()
        self._pl_session = {"pid": pid, "items": items, "index": 0,
                            "auto": bool(auto), "wait": wait}
        self.play_page.menu.set_playlist_mode(True)
        # 最初にプレイリストの照会画面(自動でも自分では進まない。利用者の指定)。
        self._stop_demo_video()
        if self._core is not None:
            self._core.stop_audio()
        self.library.set_preview_playing(False)
        self._show_playlist_overview(pl, max(0, int(start)), stop)
        return True

    def _show_playlist_overview(self, pl, start, stop):
        from neotja.player import playlists, song_lists
        from neotja.player.playlist_view import playlist_jacket_pixmap
        entries = []
        for n, item in enumerate(pl["items"][start:stop], start=start + 1):
            path = item.get("path") or ""
            if os.path.isfile(path):
                info = song_lists.song_info(path)
                levels = dict(info.get("courses") or [])
                course = item.get("course")
                if course not in levels:
                    course = playlists.default_course(info.get("courses") or [])
                entries.append((n, info.get("title") or os.path.basename(path), course,
                                levels.get(course), not levels))
            else:
                entries.append((n, os.path.splitext(os.path.basename(path))[0], None, None, True))
        self.playlist_overview.set_playlist(pl, entries,
                                            playlist_jacket_pixmap(pl, self.cfg, px=360))
        self._set_page(self.playlist_overview, fade="cross",
                       done=lambda: self.playlist_overview.setFocus(Qt.OtherFocusReason))
        self.playlist_overview.setFocus()

    def _playlist_overview_play(self):
        """照会画面の「再生する」: 1曲目の曲名表示へ(そこからは手動/自動のとおり)。"""
        if self._pl_session is None or self.body.currentWidget() is not self.playlist_overview:
            return
        self._playlist_intro(0, fade="cross")

    def _playlist_intro(self, index, fade="black"):
        """index 番目の曲名表示(右に板)。押されるまで待つ。試聴は DEMOSTART から。"""
        from neotja.player import core as core_mod
        from neotja.player import playlists, song_lists
        sess = self._pl_session
        if sess is None:
            return
        items = sess["items"]
        while index < len(items):
            path = items[index]["path"]
            info = song_lists.song_info(path) if os.path.isfile(path) else None
            courses = (info or {}).get("courses") or []
            if courses:
                break
            index += 1               # 消えた・読めない曲は飛ばす
        if index >= len(items):
            self.end_playlist()
            return
        self._abort_sequence()
        levels = dict(courses)
        course = items[index].get("course")
        if course not in levels:
            course = playlists.default_course(courses)
        sess["index"] = index
        sess["course"] = course
        self.intro.set_song(path, course, levels.get(course))
        self.intro.set_course(course, levels.get(course))
        self.intro.set_playlist(index + 1, len(items), courses, auto=sess.get("auto", False))
        core = self._ensure_core()
        self.apply_audio_settings()
        try:
            demo = core_mod.demo_start_seconds(core_mod.read_text(path))
        except OSError:
            demo = 0.0
        active = getattr(core, "demo_active_for", None)
        if not (active is not None and active(path)):
            self._stop_demo_video()
            core.stop_audio()
            core.play_demo(path, demo)
        self._set_demo_volume(float(self.cfg.get("preview_volume", 0.8) or 0.0))
        video = self._bg_video_path(path)
        if self._demo_video is None or getattr(self._demo_video, "path", "") != video:
            self._stop_demo_video()
            if video:
                self._demo_video = self._make_video(self.intro.set_video_frame,
                                                    self.intro.clear_video_frame)
                self._demo_video.start(video)
        else:
            self._demo_video.set_targets(self.intro.set_video_frame,
                                         self.intro.clear_video_frame)
        try:
            core.predecode(path)
        except Exception:  # noqa: BLE001
            pass
        def shown():
            self.intro.setFocus(Qt.OtherFocusReason)
            # 自動: 出し切ってから数え始める(待ち時間は見えている長さ)。
            cur = self._pl_session
            if cur is sess and cur.get("auto") and cur["index"] == index:
                self.intro.start_countdown(cur["wait"])
        self._set_page(self.intro, fade=fade, done=shown)
        self.intro.setFocus()

    def _playlist_course_changed(self, course):
        if self._pl_session is not None and self._seq is None:
            self._pl_session["course"] = course

    def _playlist_play(self):
        """曲名表示の「再生する」(Enter も)。試聴を短く下げてから本編へ。"""
        sess = self._pl_session
        if sess is None or self._seq is not None or self.body.currentWidget() is not self.intro:
            return
        path = sess["items"][sess["index"]]["path"]
        course = sess.get("course")
        self.library.note_played(path)
        self.play_requested.emit(path, course)
        core = self._ensure_core()
        base = float(self.cfg.get("preview_volume", 0.8) or 0.0)
        seq = IntroSequence(
            set_volume=self._set_demo_volume, base_volume=base,
            stop_demo=lambda: self._stop_demo_audio(core),
            begin_load=lambda: self._begin_load(path, course),
            is_ready=core.chart_ready, parent=self,
            hold=0.0, total=self.PLAYLIST_FADE_SEC)
        seq.finished.connect(lambda: self._enter_play(path, course))
        seq.failed.connect(lambda: self._intro_failed(path))
        self._seq = seq
        seq.start()

    def _playlist_advance(self):
        sess = self._pl_session
        if sess is None:
            return
        nxt = sess["index"] + 1
        if nxt >= len(sess["items"]):
            self.end_playlist()
        else:
            self._playlist_intro(nxt, fade="black")

    def end_playlist(self):
        """連続再生を終えて、そのプレイリストの画面へ戻る。"""
        sess, self._pl_session = self._pl_session, None
        self.play_page.menu.set_playlist_mode(False)
        self._abort_sequence()
        self._end_play()
        self._stop_demo_video()
        if self._core is not None:
            self._core.stop_audio()
        self.intro.clear_playlist()
        if sess is None:
            return
        self.library.refresh_recent()
        if self.library.playlist_store().get(sess["pid"]) is not None:
            self.library.open_playlist(sess["pid"])
        self.library.set_preview_playing(False)

        def focus_library():
            try:
                self.library.pages.currentWidget().setFocus()
            except Exception:  # noqa: BLE001
                pass
        self._set_page(self.library, fade="black", done=focus_library)

    def _abort_sequence(self):
        seq, self._seq = self._seq, None
        if seq is not None:
            seq.abort()
        if self._core is not None:
            try:
                fn = getattr(self._core, "cancel_predecode", None)
                if fn is not None:
                    fn()
                else:
                    self._core.dock.cancel_predecode()
            except Exception:  # noqa: BLE001
                pass
        if self.body.is_fading():
            self.body.cancel()

    def in_intro(self):
        return self._seq is not None

    # ------------------------------------------------------------------
    # 録画(難易度選択の「録画」→ Editor と同じ録画の窓)
    #: 録画の窓を作る呼び出し(試験で差し替える)。None なら RecordDialog。
    record_dialog_cls = None
    #: 譜面の読み込みを待つ上限(秒)。
    RECORD_LOAD_TIMEOUT = 15.0

    def _record_owner(self):
        """録画の窓の置き場。Editor と同じ MainWindow._record_dialog を使う
        (同じ窓を2つ開かないため・窓を閉じるときの「書き出し中」の確認も
        そのまま効くため)。単独で動かす試験では自分。"""
        top = self.window()
        if top is not self and hasattr(top, "_record_dialog"):
            return top
        return self

    def record_dialog(self):
        owner = self._record_owner()
        dlg = getattr(owner, "_record_dialog", None)
        if dlg is None:
            return None
        try:
            dlg.isVisible()
        except RuntimeError:          # C++ 側が消えていた
            owner._record_dialog = None
            return None
        return dlg

    def _on_record(self, path, course):
        """録画の窓を開く。すでに開いていれば(Editor のものでも)前へ出すだけ。

        試聴を止め、PlayerCore に譜面とコースを読み込んで(見るだけ)から
        RecordDialog を作る。閉じたら難易度選択へ戻って試聴をやり直す。"""
        existing = self.record_dialog()
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        self.stop_preview()
        self.record_requested.emit(path, course)
        core = self._ensure_core()
        from neotja.constants import PLAY_MODE_WATCH
        try:
            ok = bool(core.load(path, course_key=course, play_mode=PLAY_MODE_WATCH))
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            self.start_preview(path)
            return
        self._record_pending = (path, course, time.monotonic())
        self._record_wait()

    _record_pending = None
    #: 作れなかった理由(最後の1回)。
    record_failed = ""
    #: 作れなかったときに知らせの窓を出すか(試験では切る)。
    show_record_errors = True

    def _record_wait(self):
        """音源が読めるのを待ってから窓を作る(長さが要るため)。"""
        pend = self._record_pending
        if pend is None:
            return
        path, course, t0 = pend
        core = self._core
        if core is None:
            self._record_pending = None
            return
        if not core.chart_ready():
            if time.monotonic() - t0 < self.RECORD_LOAD_TIMEOUT:
                QTimer.singleShot(50, self._record_wait)
                return
            self._record_pending = None
            self.start_preview(path)
            return
        self._record_pending = None
        from neotja.player.record import build_dialog
        owner = self._record_owner()
        parent = owner if owner is not self else self.window()
        dlg, why = build_dialog(core, path, self.cfg, parent, self.record_dialog_cls)
        if dlg is None:
            self.record_failed = why
            self.start_preview(path)
            if self.show_record_errors:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "動画を書き出す", why)
            return
        owner._record_dialog = dlg
        started = getattr(dlg, "recording_started", None)
        if started is not None:
            # 録画を始めた曲も「最近開いた楽曲」へ(窓を開いただけでは入れない)。
            started.connect(lambda p=path: self.library.note_played(p))
        dlg.finished.connect(lambda *_a, d=dlg, p=path: self._on_record_closed(d, p))
        dlg.show()

    def _on_record_closed(self, dlg, path):
        """録画の窓が閉じた。置き場を空け、難易度選択なら試聴をやり直す。"""
        owner = self._record_owner()
        if getattr(owner, "_record_dialog", None) is dlg:
            owner._record_dialog = None
        try:
            dlg.deleteLater()
        except RuntimeError:
            pass
        if (self.body.currentWidget() is self.difficulty and self.isVisible()
                and self.difficulty.path == path):
            self.start_preview(path)
            self.difficulty.setFocus()

    # ------------------------------------------------------------------
    # 難易度設定から開く、曲別の背景・ジャケット。
    art_dialog_builder = None  # 試験で差し替える。

    def _on_art(self, path, course):
        existing = self._art_dialog or self.record_dialog()
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        if self._art_pending is not None:
            return
        self.stop_preview()
        self.difficulty.settings.btn_art.setEnabled(False)
        core = self._ensure_core()
        from neotja.constants import PLAY_MODE_WATCH
        try:
            ok = bool(core.load(path, course_key=course, play_mode=PLAY_MODE_WATCH))
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            self.difficulty.settings.btn_art.setEnabled(True)
            self.start_preview(path)
            return
        self._art_pending = (path, course, time.monotonic())
        self._art_wait()

    def _art_wait(self):
        pending = self._art_pending
        if pending is None:
            return
        path, course, started = pending
        if (self._shutting_down or self._core is None
                or self.body.currentWidget() is not self.difficulty
                or self.difficulty.path != path):
            self._art_pending = None
            self.difficulty.settings.btn_art.setEnabled(True)
            return
        if not self._core.chart_ready():
            if time.monotonic() - started < self.RECORD_LOAD_TIMEOUT:
                QTimer.singleShot(50, self._art_wait)
                return
            self._art_pending = None
            self.difficulty.settings.btn_art.setEnabled(True)
            self.start_preview(path)
            return
        self._art_pending = None
        try:
            builder = self.art_dialog_builder
            if builder is None:
                from neotja.player.record import build_art_dialog as builder
            dlg = builder(self._core, path, self.cfg, self.window())
        except Exception as exc:  # noqa: BLE001
            self.difficulty.settings.btn_art.setEnabled(True)
            self.start_preview(path)
            if self.show_record_errors:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "背景・ジャケット", str(exc))
            return
        self._art_dialog = dlg
        dlg.setWindowModality(Qt.WindowModal)
        dlg.finished.connect(lambda result, d=dlg, p=path: self._on_art_closed(d, p, result))
        dlg.show()

    def _on_art_closed(self, dlg, path, result):
        if self._art_dialog is dlg:
            self._art_dialog = None
        self.difficulty.settings.btn_art.setEnabled(True)
        try:
            if result == QDialog.Accepted:
                from neotja.chart_art import set_record_background
                from neotja.player.record import matching_editor
                bg = dlg.background()
                # sidecarのbaseだけ更新し、歌詞ごとの絵や未保存編集を保持。
                set_record_background(self.cfg, path, bg)
                hidden = getattr(dlg, "jacket_hidden", None)
                if hidden is not None:
                    from neotja.chart_art import set_jacket_hidden
                    set_jacket_hidden(self.cfg, path, hidden())
                editor = matching_editor(self.window(), path)
                if editor is not None:
                    editor.set_jacket_base(bg.art)
                self._save_cb()
                if self.difficulty.path == path:
                    self.difficulty._load_art()
                    self.difficulty.update()
                self.library.refresh_thumb(path)
        except Exception as exc:  # noqa: BLE001
            if self.show_record_errors:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "背景・ジャケット", "保存できませんでした。\n%s" % exc)
        finally:
            dlg.deleteLater()
            if (not self._shutting_down and self.isVisible()
                    and self.body.currentWidget() is self.difficulty
                    and self.difficulty.path == path):
                self.start_preview(path)
                self.difficulty.settings.btn_art.setFocus()

    # ------------------------------------------------------------------
    # TJA を落とされたら、Editor で開かずに Player で選んだことにする
    # (窓の dropEvent まで上がると Editor が開いてしまう)。
    def dragEnterEvent(self, ev):
        if dropped_tja_path(ev):
            ev.acceptProposedAction()
            # 落とせる場所だと分かるように「TJAを開く」を光らせる(選曲の
            # 画面にいるときだけ。ほかの画面は切り替えない)。
            if self.body.currentWidget() is self.library and not self.body.is_fading():
                self.library.begin_drop_hint()
        else:
            super().dragEnterEvent(ev)

    def dragLeaveEvent(self, ev):
        self.library.end_drop_hint()
        super().dragLeaveEvent(ev)

    def dragMoveEvent(self, ev):
        if dropped_tja_path(ev):
            ev.acceptProposedAction()
        else:
            super().dragMoveEvent(ev)

    def dropEvent(self, ev):
        self.library.end_drop_hint()
        path = dropped_tja_path(ev)
        if not path or not os.path.isfile(path):
            super().dropEvent(ev)
            return
        ev.acceptProposedAction()
        self.show_opened(path)

    # ------------------------------------------------------------------
    def paintEvent(self, ev):
        """背景。ランチャーと同じ地色に、やわらかい光を2つ。"""
        g = glass_colors()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(COLORS["bg"]))
        w, h = self.width(), self.height()
        if w > 0 and h > 0:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            for (cx, cy, rad, col, a) in (
                    (w * 0.62, h * 0.30, max(300.0, w * 0.42), COLORS["accent"], g["glow1_alpha"]),
                    (w * 0.15, h * 0.92, max(200.0, w * 0.25), COLORS["ka"], g["glow2_alpha"])):
                grad = QRadialGradient(cx, cy, rad)
                grad.setColorAt(0, _c(col, a))
                grad.setColorAt(0.55, _c(col, a * 0.35))
                grad.setColorAt(1, _c(col, 0))
                p.setBrush(QBrush(grad))
                p.drawEllipse(QPointF(cx, cy), rad, rad)
        p.end()

    def refresh_theme(self):
        fam_qss = StartScreen._qss()
        self.setStyleSheet(fam_qss + library_qss() + """
        QLabel#playerName { color: %s; %s font-size: 22px; font-weight: 300; letter-spacing: 1px; }
        QPushButton#back { background-color: transparent; border: 1px solid transparent; border-radius: 8px;
                           padding: 6px 14px; font-size: 14px; font-weight: 600; color: %s; }
        QPushButton#back:hover { background-color: %s; color: %s; }
        QPushButton#back:focus { border: 1px solid %s; }
        """ % (COLORS["fg_bright"],
               ('font-family: "%s";' % wordmark_family()) if wordmark_family() else "",
               COLORS["fg"], _rgba(glass_colors()["btn_fill"]), COLORS["fg_bright"],
               COLORS["accent"]))
        self.lbl_name.setText(
            '<span style="font-weight:300">NeoTJA</span>'
            '<span style="font-weight:600; color:%s">Player</span>' % COLORS["accent"])
        self.library.refresh_theme()
        self.update()
