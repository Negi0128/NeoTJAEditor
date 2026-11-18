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

    #: 譜面の終わりを見張る間合い(ms)。
    FINISH_POLL_MS = 100

    def __init__(self, config_data, save_cb, parent=None, audio_provider=None):
        super().__init__(parent)
        self.setObjectName("playerHost")
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
        self.library.sound_requested.connect(self.sounds.play)
        self.body.addWidget(self.library)
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

        self.refresh_theme()

    # ------------------------------------------------------------------
    def on_shown(self):
        """ランチャーから入ったとき。選曲の最初の画面へ。"""
        self._set_page(self.library)
        self.library.show_default()
        # 押した「Player を起動」が消えて、フォーカスが「戻る」へ流れ着く
        # のを避ける(Enter 一つでランチャーへ戻ってしまう)。
        self.library.pages.currentWidget().setFocus()

    def _on_song_chosen(self, path):
        self.current_song = path
        self.song_chosen.emit(path)
        self.show_difficulty(path)

    def _set_page(self, page, fade=None, done=None):
        """画面を替える。fade は None(すぐ)/ "cross" / "black"。"""
        full = page in (self.difficulty, self.intro, self.play_page, self.results)
        self.top_bar.setVisible(not full)
        self.top_rule.setVisible(not full)
        if fade:
            self.body.fade_to(page, mode=fade, done=done)
        else:
            self.body.cancel()
            self.body.setCurrentWidget(page)

    def show_difficulty(self, path, course=None, fade=None):
        """難易度選択へ。course を渡すとそのコースを選んだ状態にする
        (演奏から戻ったとき)。fade を渡すとフェードしてから試聴を始める。"""
        self._abort_sequence()
        self.difficulty.close_settings()
        self.difficulty.set_song(path)
        if course:
            self._select_course(course)

        def after():
            self.start_preview(path)
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
        フォルダ・検索・並び・選んだ行はそのまま。"""
        self.stop_preview()
        self.difficulty.close_settings()
        self.library.refresh_recent()
        self._set_page(self.library)

    def shutdown(self):
        """窓を閉じるとき。試聴を止めて、Player の音声デバイスを閉じる。"""
        self._shutting_down = True
        self._stop_demo_video()
        self._art_pending = None
        if self._art_dialog is not None:
            self._art_dialog.reject()
        self.library.shutdown()
        self._abort_sequence()
        self._end_play()
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
        # 音量は Editor と同じ設定(config_data)をそのたびに当て直す。
        try:
            core.dock.set_master_volume(float(self.cfg.get("master_volume", 1.0)))
        except Exception:  # noqa: BLE001
            pass
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
        self._demo_video = DemoVideo(
            self._core, self.difficulty.set_video_frame,
            self.difficulty.clear_video_frame, self)
        self._demo_video.start(video_path)

    def _on_demo_setting(self, key, value):
        if key == "player_show_bg" and self.body.currentWidget() is self.difficulty:
            if value:
                self._start_demo_video(self.difficulty.path)
            else:
                self._stop_demo_video()

    def stop_preview(self):
        """鳴っているものを全部止める(試聴・曲名表示の段取り・本編)。"""
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
        self._stop_demo_video()
        self._abort_sequence()
        core = self._ensure_core()
        self.intro.set_song(path, course, self.difficulty.levels.get(course))
        try:
            core.predecode(path)
        except Exception:  # noqa: BLE001
            pass
        base = float(self.cfg.get("preview_volume", 0.8) or 0.0)
        seq = IntroSequence(
            set_volume=self._set_demo_volume, base_volume=base,
            stop_demo=core.stop_audio,
            begin_load=lambda: self._begin_load(path, course),
            is_ready=core.chart_ready, parent=self)
        seq.finished.connect(lambda: self._enter_play(path, course))
        seq.failed.connect(lambda: self._intro_failed(path))
        self._seq = seq
        self._set_page(self.intro, fade="cross")
        self.intro.setFocus()
        seq.start()

    def _set_demo_volume(self, v):
        core = self._core
        if core is None:
            return
        try:
            core.dock.set_volume(max(0.0, min(1.0, float(v))))
        except Exception:  # noqa: BLE001
            pass

    def _begin_load(self, path, course):
        from neotja.constants import PLAY_MODE_WATCH
        core = self._core
        if core is None:
            return False
        try:
            return bool(core.load(path, course_key=course, play_mode=PLAY_MODE_WATCH))
        except Exception:  # noqa: BLE001
            return False

    def _enter_play(self, path, course):
        """準備ができた。黒を挟んで再生画面へ移り、ふつうの頭から流す
        (助走・OFFSET は Editor のプレビューと同じ決まり。DEMOSTART ではない)。"""
        self._seq = None
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
        self.play_page.attach(dock.game_preview_window, native)
        self._play_ctx = (path, course)

        def go():
            # キーは再生画面(PlayPage)が受ける。ゲーム画面にフォーカスを
            # 渡さない(締めてあるので渡っても再生画面へ回るが、GL の窓だと
            # 窓ごと前に出てしまうため)。
            self.play_page.setFocus(Qt.OtherFocusReason)
            core.play_chart(0.0)
            self._finish_timer.start()
        self._set_page(self.play_page, fade="black", done=go)

    # ------------------------------------------------------------------
    # 通常プレイの途中メニュー(Esc)と終わり
    def in_play(self):
        return self._play_ctx is not None

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
        位置はそのまま、止まったまま。メニューの「続ける」で流れ、操作は
        また締める。

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
        self.play_page.setFocus(Qt.OtherFocusReason)

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
        values, gauge, judges = self._collect_results()
        self._end_play()
        if self._core is not None:
            self._core.stop_audio()
        self.play_finished.emit(path, course)
        self._result_ctx = (path, course)
        self.results.set_result(path, course, self.difficulty.levels.get(course),
                                values, gauge, judges)
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
        if self._core is not None:
            self._core.stop_audio()

        def focus_library():
            try:
                self.library.pages.currentWidget().setFocus()
            except Exception:  # noqa: BLE001
                pass
        self.library.refresh_recent()
        self._set_page(self.library, fade="black", done=focus_library)

    def _end_play(self):
        """再生画面の後片付け(音は止めない — 呼んだ側が止める)。"""
        self._finish_timer.stop()
        self._play_ctx = None
        self.play_page.hide_menu()
        self.play_page.unlock()
        if self.play_mode == "detail":
            # 次に再生画面へ入るときは通常プレイから(_enter_play が
            # use_normal_play_mode と set_view_only を当て直す)。
            self.play_mode = "normal"
            core = self._core
            if core is not None:
                try:
                    core.dock.set_speed_row_visible(False)
                except Exception:  # noqa: BLE001
                    pass

    def _intro_failed(self, path):
        """本編を読めなかった。難易度選択へ戻す。"""
        self._seq = None
        self.show_difficulty(path)

    def _abort_sequence(self):
        seq, self._seq = self._seq, None
        if seq is not None:
            seq.abort()
        if self._core is not None:
            try:
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
                editor = matching_editor(self.window(), path)
                if editor is not None:
                    editor.set_jacket_base(bg.art)
                self._save_cb()
                if self.difficulty.path == path:
                    self.difficulty._load_art()
                    self.difficulty.update()
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
        else:
            super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev):
        if dropped_tja_path(ev):
            ev.acceptProposedAction()
        else:
            super().dragMoveEvent(ev)

    def dropEvent(self, ev):
        path = dropped_tja_path(ev)
        if not path or not os.path.isfile(path):
            super().dropEvent(ev)
            return
        ev.acceptProposedAction()
        self.library.choose(path)

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
