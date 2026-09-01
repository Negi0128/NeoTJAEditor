"""えぬいーさん次郎(ゲームプレビュー)の動画書き出しダイアログ。

書き出し本体は neotja/recorder.py。ここは範囲・fps・画面サイズを決めて、
進捗と中止を面倒みるだけ。

**書き出し中もエディタを使える**ようにしてある。そのための作りは3つ:

  1) このダイアログはモーダルにしない(main_window は exec() ではなく
     show() で出す)。以前は exec() で出していたので、書き出しが終わるまで
     メインウィンドウのクリックもキー入力も一切通らなかった。
  2) 絵を描くところ(QWidget.render)は Qt の決まりで GUI スレッドからしか
     呼べない。ここは動かしようがないので、従来どおり QTimer で数コマずつ
     描いてはイベントループへ返す(_SLICE_SEC)。だから書き出し中も
     入力・再生・保存は普通に通る(描画のぶんだけ全体は少し重くなる)。
  3) Qt に触らない重い部分 — 曲のデコードと音声のオフライン合成、それに
     最後に ffmpeg がファイルを閉じ切るのを待つところ — はワーカースレッドへ
     追い出した(_Task)。ここは以前 GUI スレッドで同期に走らせていて、
     3分の曲だと開始時に2秒ほど完全に固まっていた。

画面を録画するのではなく1コマずつ描き直すので、書き出し中に譜面を編集しても、
他のウィンドウを重ねても、出来上がりには一切影響しない。
"""

import os
import threading
import traceback

import time

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QVBoxLayout,
)

from neotja import recorder
from neotja import settings as settings_mod
from neotja.theme import COLORS

# step() 1回あたりに使う時間の目安。絵を描くのは GUI スレッドでしかできないので
# (recorder.VideoRecording の説明を参照)、この長さだけ描いてはイベントループへ
# 戻る。16ms = 60fps 1コマぶんで、これなら操作の引っかかりは体感できない。
_SLICE_SEC = 0.016

# 画質は3択。
#   supersample: レーンは 908px 幅しかないので、そのまま引き伸ばすとぼやける。
#     この倍率で細かく描いてから縮小するため、輪郭が立つ。
# supersample は「出力解像度と同じ画素数で描く」値にしてある(720p は等倍、
# 1080p は 1.5 倍 = 1920x1080)。以前は両方 2 倍で描いてから ffmpeg 側で
# lanczos 縮小しており、出力の 4 倍(720p)/1.8 倍(1080p)の画素を毎コマ描いて
# 捨てていた。等倍にすると縮小フィルタ自体が不要になり、パイプに流す量も減る。
#
# 所要時間の目安に固定の係数は持たない。同じ画質でも PC の速さ・譜面の密度・
# スキンの有無で数倍変わるので、ダイアログを開いたときに実際に十数コマ捨て
# 描きして測る(_measure_costs / recorder.FrameCostProbe)。
QUALITY_PRESETS = [
    ("60 fps / 720p",  {"fps": 60,  "canvas": "720p",  "supersample": 1.0,
                        "preset": "veryfast"}),
    ("60 fps / 1080p", {"fps": 60, "canvas": "1080p", "supersample": 1.5,
                        "preset": "veryfast"}),
    ("120 fps / 1080p", {"fps": 120, "canvas": "1080p", "supersample": 1.5,
                         "preset": "veryfast"}),
]
DEFAULT_QUALITY = 1        # 既定は 60fps/1080p(速度と画質のつり合いが良い)

# 見積もりの捨て描き。1コマ 5〜16ms なので、これだけ描いても 1画質あたり 0.6 秒
# ほど。warmup を必ず捨てるのは、最初の1コマがフォント/スキンの初回読み込みで
# 100ms〜1秒かかり、裏で回している x264 も走り出すまで数コマかかるため。
_PROBE_WARMUP = 8
_PROBE_SAMPLES = 24
# 見積もり中に1回の QTimer で測るコマ数。これを跨いでイベントループへ戻るので
# 「見積もり中...」の表示や閉じるボタンが効いたままになる。
_PROBE_CHUNK = 3


class _Task(QThread):
    """裏で1つだけ仕事をして、結果かエラーを返すだけのスレッド。

    録画のうち **Qt に一切触らない部分** をここへ載せる。具体的には
    recorder.prepare_recording(曲のデコードと音声合成)と、
    VideoRecording.finish()/abort()(ffmpeg の閉じ待ち)。どちらも
    subprocess と numpy しか使わないので、GUI スレッドの外で安全に走る。

    逆に絵を描く step() は絶対にここへ載せない。QWidget.render() は GUI
    スレッド専用で、別スレッドから呼ぶとその場で落ちる。

    結果は Signal で返す。別スレッドから GUI スレッドの QObject へ繋いだ
    シグナルは Qt が自動でキュー接続にしてくれるので、受け側(スロット)は
    GUI スレッドで動く = そこでウィジェットを触ってよい。

    **中止できる**: cancel に「仕事を中断させる呼び出し」を渡しておくと
    cancel() でそれを呼ぶ(下ごしらえなら recorder.CancelToken.cancel で
    ffmpeg を殺す)。中断させたぶんのエラーは ng では返さない — 頼んだのは
    こちらなので、失敗として見せるとおかしなことになる。代わりに
    cancelled を出す。

    **やりかけの後始末**: 中止が間に合わず仕事が仕上がってしまったときは、
    もう受け取り手が居ない。放っておくと一時ファイルが %TEMP% に残るので、
    discard(結果を捨てる呼び出し)を渡してもらってここで片付ける。"""

    ok = Signal(object)
    ng = Signal(str)
    cancelled = Signal()

    def __init__(self, fn, parent=None, cancel=None, discard=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel_fn = cancel
        self._discard_fn = discard
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._result = None          # 渡しそびれた結果(discard_result が捨てる)

    def cancel(self):
        """中止を頼む(GUI スレッドから呼ぶ)。すぐ返る。

        worker_util.detach_worker も cancel があれば呼んでくれるので、
        待ち切れずに待機所へ預けたワーカーもここで止まる。"""
        self._cancelled.set()
        if self._cancel_fn is None:
            return
        try:
            self._cancel_fn()
        except BaseException:           # noqa: BLE001  止められなくても進む
            traceback.print_exc()

    def discard_result(self):
        """渡しそびれた結果を捨てる。無ければ何もしない(何度呼んでもよい)。"""
        with self._lock:
            result, self._result = self._result, None
        if result is None or self._discard_fn is None:
            return
        try:
            self._discard_fn(result)
        except BaseException:           # noqa: BLE001
            traceback.print_exc()

    def run(self):
        try:
            result = self._fn()
        except BaseException as e:      # noqa: BLE001  何が出ても UI へ返す
            if self._cancelled.is_set():
                self.cancelled.emit()   # こちらが止めたのだから失敗ではない
                return
            traceback.print_exc()
            self.ng.emit(str(e) or e.__class__.__name__)
            return
        if self._cancelled.is_set():
            # 止める前に仕上がってしまった。受け側は畳んだあとなので後始末する。
            with self._lock:
                self._result = result
            self.discard_result()
            self.cancelled.emit()
            return
        # 出来上がりをここにも控えておく。emit した直後に窓が閉じられると
        # キューに積んだシグナルは配られずに捨てられ、受け側が discard()
        # する機会が無くなるため(abort_now が discard_result で拾う)。
        with self._lock:
            self._result = result
        self.ok.emit(result)


def _join_task(task, timeout_ms=5000):
    """走っているワーカーを畳む。終わらなければ待機所へ預ける。

    アプリ終了時にここで無条件に待ち続けると「閉じたのにプロセスが残る」に
    なるし、待たずに捨てると QThread が
    "Destroyed while thread is still running" でアプリごと落とす。そこで
    しばらく待ち、それでも終わらなければ worker_util の待機所へ移して
    自力で終わってもらう(参照が残るので GC されない)。"""
    if task is None:
        return
    try:
        if not task.isRunning():
            return
    except RuntimeError:
        return
    if not task.wait(timeout_ms):
        from neotja.worker_util import detach_worker
        detach_worker(task)


def _estimate_text(seconds):
    """所要時間の目安の文言。速い/きれいといった言い回しではなく、
    実際にどれだけ待つのかを分で出す。"""
    if seconds < 60.0:
        return "1分未満"
    return "%d分" % round(seconds / 60.0)


class RecordDialog(QDialog):
    """書き出す範囲と画質を決めて動画を作る。

    描画に使うウィジェットは、いま見えているプレビューとは別に画面外へ用意する
    (recorder.make_offline_widget)。そのため書き出し中も再生位置やコースは
    動かないし、途中で編集しても出来上がりは始めた時点の譜面のまま。

    **譜面のスナップショットについて**: ここへ渡ってくる preview_data は
    main_window.open_video_recorder が analyzer.build_preview_timeline で
    その場から作り直した新しい dict で、数値と文字列の入れ子しか入っていない
    (エディタの状態を指す参照は持たない)。つまり渡された時点で既に
    「開いたときの譜面の写し」になっている。だから deepcopy はしない —
    数万音符ぶんを丸ごと複製する費用に見合わないうえ、書き換える者が
    いないものを守っても意味がないため。画面外ウィジェットにもこの写しを
    そのまま持たせるので、書き出し中にエディタで譜面をいくら編集しても
    出来上がりは変わらない。"""

    def __init__(self, main_window, preview_data, offset, song_path,
                 song_seconds, default_dir, parent=None, layout="game"):
        super().__init__(parent or main_window)
        # モーダルにしない。書き出し中にメインウィンドウを触れるようにするのが
        # 目的なので、ここで入力を横取りしてはいけない(呼び出し側も exec() では
        # なく show() で出すこと)。
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self._mw = main_window
        self._preview = preview_data or {}
        self._offset = offset
        self._song = song_path
        # 何を録るか。"game" は本家レイアウトの 1280x720、"wave" は音声波形
        # モードの見た目(上=ゲーム画面 / 下=波形・譜面・命令)。録画を始めた
        # ときのモードで決まる。
        self._layout = layout if layout in ("game", "wave") else "game"
        self._rec = None            # 進行中の VideoRecording
        self._widget = None         # 画面外の描画用ウィジェット
        self._cancel = False
        self._t0 = 0.0
        # 裏で回している下ごしらえ / 後始末。どちらか動いていれば「書き出し中」。
        self._prep_task = None
        self._fin_task = None
        self._pending = None        # 下ごしらえ待ちのあいだ覚えておく (出力先, 画質)
        # 所要時間の見積もり用。supersample ごとに「1コマ何ms か」の実測を貯める
        # (720p と 1080p は supersample が違うので別々に測るが、1080p の 60fps と
        # 120fps は同じ大きさを描くので測り直さない)。
        self._cost_ms = {}
        self._probe = None
        self._probe_ss = None
        self._probe_queue = []
        self._probe_timer = QTimer(self)
        self._probe_timer.setInterval(0)
        self._probe_timer.timeout.connect(self._probe_tick)
        # step() 1回で描くコマ数。1コマの実測から毎回調整するので、最初は
        # 必ず 1 から始める(いきなり多めに描くと初回だけ引っかかる)。
        self._batch = 1
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._tick)
        self.setWindowTitle("動画を書き出す")
        self.resize(560, 340)

        # プレビュー側の長さは音源の読み込みが済むまで 0 のことがある。
        # そのままだと範囲が「0秒〜0秒」になって何も書き出せないので、
        # 音源そのものに聞き直す。
        if song_seconds <= 0.0:
            song_seconds = recorder.probe_song_seconds(song_path)
        self._song_seconds = song_seconds

        # 小節線の時刻(音源時刻)。bar_times は小節ごとに1件・譜面時刻なので、
        # OFFSET を引いて音源時刻に直す(VideoRecording の start_sec/end_sec は
        # 音源時刻)。0秒より前・曲の終わりより後ろに出る小節もあるので、
        # ここで曲の長さの中へ押し込んでおく。
        # 上を曲の長さで頭打ちにはしない。譜面のほうが音源より長いことは
        # ふつうにあり(音源が途中で切れている等)、丸めてしまうと終わりの
        # 数小節がすべて同じ時刻に潰れて選べなくなる。
        self._bar_starts = [
            max(float(t) - float(offset), 0.0)
            for t, *_rest in (self._preview.get("bar_times") or [])
        ]
        self._bar_count = len(self._bar_starts)
        # 譜面そのものの終わり(=最終小節が終わる時刻、音源時刻)。
        #
        # bar_times は小節の**開始**時刻しか持っていないので、最終小節の
        # 終わりは自分で足す必要がある。1小節の長さは 240/BPM × 拍子 なので、
        # 最後の小節線の BPM と、その時点の #MEASURE から出す。
        #
        # これを「曲全体」の終わりに使う。音源は譜面が終わったあとも
        # 鳴り続けていることが多く(フェードアウトや後奏)、そこまで録ると
        # 何も起きない映像が延々と続くため。
        self._chart_end_sec = self._compute_chart_end(offset, song_seconds)

        layout = QVBoxLayout(self)
        course = self._preview.get("course_label") or self._preview.get("course_key") or "-"
        head = QLabel(
            f"対象コース: {course}    曲の長さ: {song_seconds:.1f} 秒"
            f"    譜面の長さ: {self._chart_end_sec:.1f} 秒")
        head.setStyleSheet("font-weight: bold;")
        layout.addWidget(head)

        form = QFormLayout()

        # 範囲は小節番号で決める。秒で選ばせないのは、譜面を作っている側が
        # 見ているのは常に小節番号だから(「サビの 65 小節から」と考える)。
        # bar_times が空(音源だけで譜面が無い等)のときは選びようがないので、
        # 従来どおり「曲全体」のラベルだけ出す。
        self.sp_start = None
        self.sp_end = None
        self.lbl_range = QLabel()
        self.lbl_range.setStyleSheet(f"color: {COLORS['fg_dim']};")
        if self._bar_count >= 1:
            box = QVBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            self.sp_start = QSpinBox()
            self.sp_end = QSpinBox()
            for sp in (self.sp_start, self.sp_end):
                sp.setRange(1, self._bar_count)
                sp.setAccelerated(True)          # 押しっぱなしで速く送れる
            self.sp_start.setValue(1)
            self.sp_end.setValue(self._bar_count)
            # 開始 > 終了 を作れないよう、互いの上限/下限で押さえる。値を
            # 弾くのではなく範囲そのものを狭めるので、上下ボタンでも入力でも
            # ひっくり返らない。
            self.sp_start.valueChanged.connect(self._on_range_changed)
            self.sp_end.valueChanged.connect(self._on_range_changed)
            row.addWidget(self.sp_start)
            row.addWidget(QLabel("〜"))
            row.addWidget(self.sp_end)
            row.addWidget(QLabel(f"小節  / 全{self._bar_count}小節"))
            row.addStretch(1)
            box.addLayout(row)
            box.addWidget(self.lbl_range)
            form.addRow("範囲", box)
        else:
            self.lbl_range.setText(
                f"譜面全体（0 〜 {self._chart_end_sec:.1f} 秒）")
            self.lbl_range.setStyleSheet("font-weight: bold;")
            form.addRow("範囲", self.lbl_range)
        self._update_range_label()

        self.cb_quality = QComboBox()
        for label, cfgq in QUALITY_PRESETS:
            # 実測が出るまでは「見積もり中...」。_refresh_estimates が書き換える。
            self.cb_quality.addItem(f"{label}（見積もり中...）", cfgq)
        self.cb_quality.setCurrentIndex(DEFAULT_QUALITY)
        self.cb_quality.currentIndexChanged.connect(self._update_estimate)
        form.addRow("画質", self.cb_quality)

        self.chk_hit = QCheckBox("打音(ドン/カッ)を入れる")
        self.chk_hit.setChecked(True)
        form.addRow("", self.chk_hit)

        path_row = QHBoxLayout()
        self._auto_path = os.path.join(
            self._resolve_out_dir(main_window.config_data, default_dir),
            self._default_name())
        self.ed_path = QLineEdit(self._auto_path)
        btn_browse = QPushButton("参照...")
        btn_browse.clicked.connect(self._browse)
        path_row.addWidget(self.ed_path)
        path_row.addWidget(btn_browse)
        form.addRow("保存先", path_row)

        layout.addLayout(form)

        self.lbl_status = QLabel()
        self._update_estimate()
        self.lbl_status.setStyleSheet(f"color: {COLORS['fg_dim']};")
        layout.addWidget(self.lbl_status)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)

        self.buttons = QDialogButtonBox()
        self.btn_start = self.buttons.addButton("書き出す", QDialogButtonBox.AcceptRole)
        self.btn_close = self.buttons.addButton("閉じる", QDialogButtonBox.RejectRole)
        self.btn_start.clicked.connect(self._start)
        self.btn_close.clicked.connect(self.reject)
        layout.addWidget(self.buttons)

        # 見積もりはダイアログが出てから始める。描画用ウィジェットの用意
        # (スキンの読み込み)だけで数百ms かかることがあり、__init__ の中で
        # やると「メニューを押したのに窓が出ない」時間になってしまう。
        QTimer.singleShot(0, self._begin_measure)

    @staticmethod
    def _resolve_out_dir(cfg, default_dir):
        """保存先の既定を決める。順番は
        「環境設定の保存先 → 前回使った場所 → 呼び出し側が渡した場所
        (TJA と同じフォルダ、無ければホーム)」。

        ユーザーが明示した指定を履歴より先に見るのが肝で、こうしておけば
        よそへ一度書き出しても、次に開いたときは環境設定の場所へ戻る。
        実在しないフォルダ(外付けを外した等)は飛ばす。"""
        for cand in (cfg.get("record_output_dir", ""),
                     cfg.get("record_last_dir", "")):
            if cand and os.path.isdir(cand):
                return cand
        return default_dir

    def _default_name(self):
        base = os.path.splitext(os.path.basename(self._mw.current_file or "chart"))[0]
        course = self._preview.get("course_key") or ""
        # 区間を切ったときだけ小節番号を足す。曲全体なら従来どおりの名前のまま
        # にしておかないと、いつもの書き出しでファイル名が変わってしまう。
        tail = ""
        if not self._is_full_range():
            tail = f"_m{self.sp_start.value()}-{self.sp_end.value()}"
        return f"{base}{('_' + course) if course else ''}{tail}.mp4"

    def _compute_chart_end(self, offset, song_seconds):
        """最終小節が終わる時刻(音源時刻)。求められなければ曲の長さを返す。"""
        bars = self._preview.get("bar_times") or []
        if not bars:
            return song_seconds
        last_t = float(bars[-1][0])
        bpm = float(bars[-1][1]) if len(bars[-1]) > 1 and bars[-1][1] else 0.0
        if bpm <= 0:
            return song_seconds
        # その時点の拍子。#MEASURE が無ければ 4/4。
        num, den = 4.0, 4.0
        for entry in (self._preview.get("measure_changes") or []):
            try:
                t, n, d = float(entry[0]), float(entry[1]), float(entry[2])
            except (TypeError, ValueError, IndexError):
                continue
            if t <= last_t + 1e-6 and d:
                num, den = n, d
        bar_len = (240.0 / bpm) * (num / den) if den else (240.0 / bpm)
        end = last_t + bar_len - float(offset)
        # 譜面が音源より長いことも短いこともある。0 より前へは行かせない。
        return max(0.0, end)

    def _make_widget(self):
        """画面外の描画用ウィジェットを作る。録画を始めたときのモードで
        中身が変わる。

        音声波形モードのときは波形(WaveformMips)が要るが、それは曲を
        デコードして初めて手に入る。ここではまだ無いので波形の線は出ない
        まま作り、下ごしらえが済んだ時点(_on_prepared)で流し込む。見積もりの
        捨て描きはこの時点で始まるので、待たせないためにこうしてある。"""
        cfg = self._mw.config_data
        se = cfg.get("se_text_enabled", True)
        if self._layout == "wave":
            return recorder.make_offline_wave_widget(
                self._preview, self._offset, mips=None, se_text_enabled=se)
        return recorder.make_offline_widget(self._preview, self._offset, se)

    # ------------------------------------------------------------------
    # 書き出す区間
    # ------------------------------------------------------------------
    def _is_full_range(self):
        """1 〜 最終小節(＝曲全体)か。"""
        if self.sp_start is None or self.sp_end is None:
            return True
        return self.sp_start.value() == 1 and self.sp_end.value() == self._bar_count

    def _range_secs(self):
        """書き出す区間を音源時刻の (start_sec, end_sec) で返す。

        全体のときは (0.0, 譜面の終わり)。以前はここで (0.0, None) を返して
        「音源そのものの長さ」に任せていたが、譜面が終わったあとも鳴り続ける
        音源だと、何も起きない映像が後奏のぶんだけ続いてしまう。"""
        if self._is_full_range():
            # 譜面の終わりで切る。音源が続いていても、最終小節が終わったら
            # そこで終わり(後奏の無音を延々と録らないため)。
            return 0.0, self._chart_end_sec
        s = self.sp_start.value()
        e = self.sp_end.value()
        start = self._bar_starts[s - 1]
        # 終了小節の「終わり」は次の小節線。最終小節を選んだときは曲の終わりまで。
        end = (self._bar_starts[e] if e < self._bar_count
               else self._chart_end_sec)
        return start, max(start, end)

    def _range_span(self):
        """区間の長さ(秒)。見積もりはこの長さで計算する。"""
        start, end = self._range_secs()
        return (self._song_seconds if end is None else end - start)

    def _on_range_changed(self):
        """開始/終了が動いたときの後始末。開始 > 終了 を作れないよう互いの
        範囲を狭め、秒数表示・見積もり・既定のファイル名を追従させる。"""
        self.sp_end.blockSignals(True)
        self.sp_start.blockSignals(True)
        self.sp_end.setMinimum(self.sp_start.value())
        self.sp_start.setMaximum(self.sp_end.value())
        self.sp_end.blockSignals(False)
        self.sp_start.blockSignals(False)
        self._update_range_label()
        self._refresh_estimates()
        # 保存先を自分で書き換えた人の入力は壊さない。こちらが入れたままの
        # ときだけ、区間に合わせて名前を付け直す。
        if self.ed_path.text() == self._auto_path:
            self._auto_path = os.path.join(
                os.path.dirname(self._auto_path), self._default_name())
            self.ed_path.setText(self._auto_path)

    def _update_range_label(self):
        """「12 〜 40 小節（23.4 〜 78.1 秒 / 54.7 秒間）」を出す。"""
        if self.sp_start is None:
            return
        start, end = self._range_secs()
        if end is None:
            end = self._song_seconds
        self.lbl_range.setText(
            f"{self.sp_start.value()} 〜 {self.sp_end.value()} 小節"
            f"（{start:.1f} 〜 {end:.1f} 秒 / {end - start:.1f} 秒間）")

    # ------------------------------------------------------------------
    # 所要時間の見積もり
    #
    # 固定の係数表(以前の time_factor)はやめた。同じ「1080p 60fps」でも、PC の
    # 速さ・譜面の密度・スキンの有無で1コマの重さが数倍変わるので、書いた本人の
    # PC で測った数字は他所ではまるで当たらない。代わりに、ダイアログを開いた
    # ところで本番とまったく同じ形(同じ大きさ・同じ譜面・裏で同じ x264)で
    # 30コマほど捨て描きして「1コマ何ms か」をその場で測る。詳しくは
    # recorder.FrameCostProbe。1画質あたり 1秒足らずで、実測との誤差は
    # SUPERNOVA(2分31秒)で 720p -1%, 1080p +16% だった。
    # ------------------------------------------------------------------
    def _begin_measure(self):
        """描画用ウィジェットを用意し、画質ごとの1コマの重さを測り始める。"""
        # 書き出しが始まっていたら測らない。捨て描きと本番は同じウィジェットを
        # 使うので、割り込むと offline モードの入り切りがぶつかり、どんちゃんの
        # コマ送り(GameScreenWidget が render() の呼ばれ方で進める)まで
        # ずれて、出来上がりの絵が変わってしまう。ダイアログを開いた直後に
        # 書き出しを始めたときだけ起こりうる(この singleShot が届く前に
        # ボタンが押された場合)。
        if self.is_busy():
            return
        # 既に測っている最中なら二重に始めない(_finish から測り直しを仕掛ける
        # ようになったので、走っているところへ重ねて来ることがある)。
        if self._probe is not None or self._probe_queue:
            return
        if self._widget is None:
            self._widget = self._make_widget()
        # 測るのは supersample 違いだけ。1080p の 60fps と 120fps は同じ大きさを
        # 描くので(違うのはコマ数だけ)、測り直しても同じ数字にしかならない。
        seen = []
        for _label, q in QUALITY_PRESETS:
            ss = float(q["supersample"])
            if ss not in seen:
                seen.append(ss)
        self._probe_queue = seen
        self._next_probe()

    def _next_probe(self):
        """まだ測っていない supersample があれば次を測り始める。"""
        while self._probe_queue:
            ss = self._probe_queue.pop(0)
            if ss in self._cost_ms or self._widget is None:
                continue
            self._probe = recorder.FrameCostProbe(
                self._widget, supersample=ss, song_seconds=self._song_seconds,
                warmup=_PROBE_WARMUP, samples=_PROBE_SAMPLES)
            self._probe_ss = ss
            self._probe_timer.start()
            return
        self._probe = None
        self._probe_timer.stop()
        self._refresh_estimates()

    def _probe_tick(self):
        """数コマだけ測ってイベントループへ返す。測っているあいだも窓は動く。"""
        if self._probe is None:
            self._probe_timer.stop()
            return
        if self._probe.step(_PROBE_CHUNK):
            return
        self._cost_ms[self._probe_ss] = self._probe.ms_per_frame
        self._probe = None
        self._probe_timer.stop()
        # 1つ測れるたびに表示を差し替える(全部揃うのを待たない)。
        self._refresh_estimates()
        self._next_probe()

    def _cancel_measure(self):
        """見積もりを打ち切る。書き出しを始めるときは必ず呼ぶ:
        捨て描きと本番の描画は同じウィジェットを取り合ってしまう。"""
        self._probe_timer.stop()
        self._probe_queue = []
        if self._probe is not None:
            self._probe.cancel()
            self._probe = None

    def _estimate_for(self, index):
        """その画質での所要秒数。まだ測れていなければ None。"""
        q = self.cb_quality.itemData(index)
        if q is None:
            return None
        ms = self._cost_ms.get(float(q["supersample"]))
        if not ms:
            return None
        # 曲全体ではなく「実際に書き出す区間の長さ」で見積もる。描くコマ数は
        # 区間の長さに比例するので、20小節だけ書き出すのに曲全体の目安を出すと
        # 何倍も長く見えてしまう。
        return recorder.estimate_seconds(ms, self._range_span(), q["fps"])

    def _refresh_estimates(self):
        """コンボの各項目と下の説明文を、いま分かっている実測で書き直す。"""
        for i, (label, _q) in enumerate(QUALITY_PRESETS):
            est = self._estimate_for(i)
            tail = "見積もり中..." if est is None else f"出力 約{_estimate_text(est)}"
            self.cb_quality.setItemText(i, f"{label}（{tail}）")
        self._update_estimate()

    def _update_estimate(self):
        """選んだ画質での所要目安を出す。書き出しが始まれば進捗側に実測の
        残り時間が出るので、ここはあくまで始める前の目安。"""
        est = self._estimate_for(self.cb_quality.currentIndex())
        tail = ("この画質にかかる時間を見積もり中です..." if est is None
                else f"この画質だと目安で 約{_estimate_text(est)} ほどかかります。")
        self.lbl_status.setText(
            "書き出しは裏で進みます。そのあいだもエディタで編集・再生できます\n"
            "（出来上がるのは書き出しを始めた時点の譜面です）。\n"
            + tail)

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "動画の保存先", self.ed_path.text(), "MP4 動画 (*.mp4)")
        if path:
            self.ed_path.setText(path)

    # ------------------------------------------------------------------
    def is_busy(self):
        """書き出し(下ごしらえ・描画・仕上げのどれか)が進行中か。

        二重に始めさせないため、そしてアプリを閉じてよいかの判断のために、
        外(main_window)からも見る。"""
        return (self._prep_task is not None or self._rec is not None
                or self._fin_task is not None)

    def _start(self):
        if self.is_busy():
            # 二重起動よけの最後の砦。ふつうはボタンが「中止」に化けているので
            # ここへは来ないが、状態の取りこぼしで2本走ると同じウィジェットを
            # 取り合って絵が混ざる。
            return
        out = self.ed_path.text().strip()
        if not out:
            QMessageBox.warning(self, "動画を書き出す", "保存先を指定してください。")
            return
        if not out.lower().endswith(".mp4"):
            out += ".mp4"
            self.ed_path.setText(out)
        if self._song_seconds < 1.0:
            QMessageBox.warning(self, "動画を書き出す", "音源の長さが取得できませんでした。")
            return
        # 1コマも作れない区間は VideoRecording が RecordingError にするが、
        # 「録画する範囲が短すぎます」だけでは何を直せばいいか分からないので、
        # ここで小節の話として先に止める。
        q_fps = float(self.cb_quality.currentData()["fps"])
        if self._range_span() < 1.0 / q_fps:
            QMessageBox.warning(
                self, "動画を書き出す",
                "指定した区間が短すぎます（1コマにもなりません）。\n"
                "終了小節を後ろへずらしてください。")
            return
        if os.path.exists(out) and QMessageBox.question(
                self, "動画を書き出す",
                f"{os.path.basename(out)} は既にあります。上書きしますか？") != QMessageBox.Yes:
            return

        # 見積もりの捨て描きが残っていたら止める。同じウィジェットを本番の描画と
        # 取り合ってしまい、offline モードの入り切りもぶつかる。
        self._cancel_measure()

        cfg = self._mw.config_data
        q = self.cb_quality.currentData()
        # 打音は再生と同じものを使う。設定の欄だけを見るとスキン同梱の打音が
        # 拾えず、動画だけ内蔵音になってしまう。
        rec_don, rec_ka = settings_mod.effective_hit_sound_paths(cfg)
        # 次に開いたときも同じ場所が出るよう、実際に使ったフォルダを覚える。
        # 環境設定の record_output_dir(ユーザーが決めた既定)ではなく専用の
        # 履歴キーへ書く — 同じキーだと、一度よそへ書き出しただけで環境設定の
        # 指定が黙って上書きされ、戻す手立てが無くなるため。
        cfg["record_last_dir"] = os.path.dirname(out)
        settings_mod.save_settings(cfg)
        # 描画用ウィジェットは見積もりのときに作ってある(_begin_measure)。
        # 作り直さないのは、スキンの読み込みぶんだけ待ちが増えるのと、
        # 「開いた時点の譜面で書き出す」という約束がそのまま守られるため。
        if self._widget is None:
            self._widget = self._make_widget()

        self._cancel = False
        self._set_running(True)
        self.lbl_status.setText("音声を用意しています...")
        # 最初の1コマだけはフォントの読み込みや各種キャッシュの用意で 100ms 前後
        # かかる。録画が始まってからだとそこだけ引っかかるので、下ごしらえに
        # 入る前に1枚捨て描きして済ませておく。ふつうは見積もりの捨て描きで
        # 済んでいるが、見積もりが終わる前に押されることもある。
        from PySide6.QtGui import QImage as _QImage
        self._widget.render(_QImage(self._widget.width(), self._widget.height(),
                                    _QImage.Format_RGB32))
        # 選んだ小節を音源時刻に直して渡す。全体を選んでいるときは
        # (0.0, 最終小節の終わり) が返る。
        start_sec, end_sec = self._range_secs()
        # 曲のデコードと音声合成(3分の曲で2秒ほど)は裏へ回す。ここは Qt に
        # 触らないので別スレッドで走らせて構わない。
        # **渡す値はここで全部ただの数値・文字列にしておく**。向こうのスレッドで
        # チェックボックスやスピンボックスを読みに行ってはいけない(ウィジェットは
        # GUI スレッド専用)し、待っているあいだに触られた値が混ざるのも困る。
        #
        # 音量は**再生と同じ設定**を渡す。既定値も settings.py / main_window の
        # 復元と同じ数字にしておくこと(曲 preview_volume=0.8、打音
        # sfx_volume=0.9)。ここだけ違う数字を書いていると、スライダーを一度も
        # 触っていない人の動画が、聞いているのと違う音量で出来上がる。
        # master_volume は掛けない — あれは手元で聞くときの音量つまみで、
        # 出来上がるファイルの中身とは関係がないため。
        prep_kwargs = dict(
            preview_data=self._preview, offset=self._offset,
            song_path=self._song, start_sec=start_sec, end_sec=end_sec,
            fps=q["fps"], don_path=rec_don, ka_path=rec_ka,
            song_volume=float(cfg.get("preview_volume", 0.8)),
            sfx_volume=float(cfg.get("sfx_volume", 0.9)),
            hit_sounds=self.chk_hit.isChecked(),
            want_mips=(self._layout == "wave"))
        self._pending = (out, q)
        self._t0 = time.perf_counter()
        # 下ごしらえは途中で止められるようにしておく。止めないと、書き出しを
        # 始めた直後に閉じた人がデコードの終わりまで待たされる(_Task.cancel /
        # recorder.CancelToken)。
        token = recorder.CancelToken()
        self._start_task("_prep_task",
                         lambda: recorder.prepare_recording(cancel=token,
                                                            **prep_kwargs),
                         self._on_prepared, self._on_prep_failed,
                         cancel=token.cancel,
                         discard=lambda plan: plan.discard(),
                         on_cancelled=self._on_prep_cancelled)

    def _start_task(self, attr, fn, on_ok, on_ng, cancel=None, discard=None,
                    on_cancelled=None):
        """裏の仕事を1つ始めて、self.<attr> に持たせる(_Task の説明を参照)。"""
        task = _Task(fn, self, cancel=cancel, discard=discard)
        task.ok.connect(on_ok)
        task.ng.connect(on_ng)
        if on_cancelled is not None:
            task.cancelled.connect(on_cancelled)
        setattr(self, attr, task)
        task.start()
        return task

    def _on_prep_cancelled(self):
        """下ごしらえの最中に中止された。一時ファイルは _Task が捨てている。"""
        self._prep_task = None
        self._pending = None
        self._finish()
        self.lbl_status.setText("中止しました。")

    def _on_prep_failed(self, msg):
        self._prep_task = None
        self._pending = None
        self._finish()
        self.lbl_status.setText("失敗しました。")
        QMessageBox.warning(self, "動画を書き出す", f"書き出しを開始できませんでした:\n{msg}")

    def _on_prepared(self, plan):
        """音声が出来たので、ここから先(描画)を GUI スレッドで始める。"""
        self._prep_task = None
        pending, self._pending = self._pending, None
        if self._cancel or self._widget is None or pending is None:
            # 用意しているあいだに中止された。作った一時音声だけ捨てる。
            plan.discard()
            self._finish()
            self.lbl_status.setText("中止しました。")
            return
        out, q = pending
        # 音声波形モードの録画は、ここで初めて曲の波形が手に入る。
        # 流し込まないと下画面の波形の線だけが出ないまま録れてしまう。
        if self._layout == "wave" and getattr(plan, "mips", None) is not None:
            try:
                self._widget.wave.set_mips(plan.mips)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._rec = recorder.VideoRecording(
                self._widget, out, plan=plan, canvas=q["canvas"],
                supersample=q["supersample"], preset=q["preset"])
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            plan.discard()
            self._finish()
            QMessageBox.warning(self, "動画を書き出す", f"書き出しを開始できませんでした:\n{e}")
            return
        # 見積もりの目安ではなく実測で残り時間を出したいので、時計はここで
        # 引き直す(下ごしらえのぶんを1コマ目の所要に混ぜない)。
        self._t0 = time.perf_counter()
        self._timer.start()

    def _tick(self):
        """1コマ〜数コマ描いてはイベントループへ返す。詳しくは _SLICE_SEC。"""
        if self._rec is None:
            self._timer.stop()
            return
        if self._cancel:
            self._timer.stop()
            rec, self._rec = self._rec, None
            self.lbl_status.setText("中止しています...")
            # ウィジェットを戻すのは GUI スレッドで。ffmpeg の後始末(待ちが
            # ある)だけ裏へ回す。終わるまでボタンは戻さない — 2本目が
            # 前の ffmpeg の店じまい中に走り出すのを防ぐため。
            rec.detach_widget()
            self._start_task("_fin_task", rec.abort, self._on_aborted,
                             self._on_aborted)
            return

        t0 = time.perf_counter()
        more = self._rec.step(self._batch)
        spent = time.perf_counter() - t0
        # 1コマの実測から、次回のコマ数を _SLICE_SEC に収まるよう調整する。
        per = spent / max(1, self._batch)
        if per > 0:
            self._batch = max(1, min(60, int(_SLICE_SEC / per)))
        self._on_progress(self._rec.frame, self._rec.total_frames)

        if more:
            return
        self._timer.stop()
        rec, self._rec = self._rec, None
        # 最後に ffmpeg が動画を閉じ切るのを待つ数百 ms は、こちらからは
        # 縮められない。裏へ回してしまえば待っている間も窓は動く。
        self.bar.setValue(100)
        self.lbl_status.setText("仕上げています...")
        # 仕上げのあいだは「中止」を押させない。押しても止める相手(_rec も
        # _timer も)が既に居らず、_cancel が立つだけで ffmpeg はそのまま
        # 書き終える。「中止しています...」と出したあとに成功のダイアログが
        # 出る、という食い違いになる。
        self.btn_start.setEnabled(False)
        rec.detach_widget()               # ウィジェットに触るのはここだけ(GUI)
        self._start_task("_fin_task", rec.finish, self._on_finished,
                         self._on_finish_failed)

    def _on_aborted(self, _result=None):
        """中止の後始末が済んだ。ここで初めてボタンを戻す。"""
        self._fin_task = None
        self._finish()
        self.lbl_status.setText("中止しました。")

    def _on_finish_failed(self, msg):
        self._fin_task = None
        self._finish()
        self.lbl_status.setText("失敗しました。")
        QMessageBox.warning(self, "動画を書き出す", f"書き出しに失敗しました:\n{msg}")

    def _on_finished(self, path):
        self._fin_task = None
        el = time.perf_counter() - self._t0
        self._finish()
        self.lbl_status.setText(f"書き出しました({el:.0f} 秒): {path}")
        if QMessageBox.question(
                self, "動画を書き出す",
                f"書き出しました。({el:.0f} 秒)\n{path}\n\n"
                "保存先のフォルダを開きますか？") == QMessageBox.Yes:
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    def _set_running(self, running):
        # 画質も止めておく。始めた時点の値で走っているので、途中で変えられると
        # 表示と中身が食い違って見える。
        for wdg in (self.chk_hit, self.ed_path, self.sp_start, self.sp_end,
                    self.cb_quality):
            if wdg is not None:            # 小節が無いときは範囲の入力欄も無い
                wdg.setEnabled(not running)
        self.bar.setVisible(running)
        self.bar.setValue(0)
        self.btn_start.setText("中止" if running else "書き出す")
        try:
            self.btn_start.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_start.clicked.connect(self._request_cancel if running else self._start)
        self.btn_close.setEnabled(not running)
        if running:
            self.lbl_status.setText("書き出し中...")

    def _request_cancel(self):
        self._cancel = True
        self.btn_start.setEnabled(False)
        self.lbl_status.setText("中止しています...")
        # 下ごしらえ(曲のデコード・音声合成)の最中なら、その場で降ろす。
        # 描画中なら次の _tick が _cancel に気づいて畳む。
        if self._prep_task is not None:
            self._prep_task.cancel()

    def _on_progress(self, done, total):
        if total > 0:
            self.bar.setValue(int(done * 100 / total))
            el = time.perf_counter() - self._t0
            rest = (el / done * (total - done)) if done > 0 else 0.0
            self.lbl_status.setText(
                f"書き出し中... {done * 100 // total}%  ({done}/{total} コマ)"
                f"  残り約 {rest:.0f} 秒")

    def _finish(self):
        self._timer.stop()
        self._rec = None
        self._prep_task = None
        self._fin_task = None
        self._pending = None
        # 画面外ウィジェットはダイアログを閉じるまで持ったままにする。続けて
        # 別の画質でもう一度書き出すことがあり、そのたびにスキンを読み直すのは
        # 無駄(閉じるときに closeEvent で手放す)。
        self.btn_start.setEnabled(True)
        self._set_running(False)
        # 見積もりを取りこぼしていたら測り直す(_schedule_measure)。
        self._schedule_measure()

    def _schedule_measure(self):
        """まだ数字の出ていない画質があれば、見積もりを測り直す。

        _begin_measure は「書き出し中なら測らない」で降りる(捨て描きと本番が
        同じウィジェットを取り合うため)。ダイアログを開いた直後に「書き出す」を
        押されるとそこへ入るので、そのままだと画質の欄が「見積もり中...」の
        まま二度と変わらない。書き出しが終わったこの時点で仕掛け直す。
        閉じたあとに測り始めても意味がない(手放したウィジェットを作り直して
        しまう)ので、見えているときだけ。"""
        if self.is_busy() or not self.isVisible():
            return
        if all(self._estimate_for(i) is not None
               for i in range(len(QUALITY_PRESETS))):
            return
        QTimer.singleShot(0, self._begin_measure)

    def abort_now(self):
        """書き出しを中止し、裏のスレッドまで畳んでから返る(同期)。

        アプリを閉じるときに main_window から呼ぶ。閉じると決まったあとなら
        数百 ms 待たされても構わないので、ここは裏へ回さず待ち切る —
        走ったままのスレッドを残すと、プロセスが終わらなかったり
        "QThread: Destroyed while thread is still running" で落ちたりする。"""
        self._cancel = True
        self._cancel_measure()
        self._timer.stop()
        rec, self._rec = self._rec, None
        if rec is not None:
            rec.detach_widget()        # まだ GUI スレッドにいるうちに戻す
            rec.abort()                # 書きかけの mp4 は消える
        # 下ごしらえは先に**止めてから**待つ。ただ待つだけだと、曲のデコードが
        # 終わるまで(長い曲なら数秒)GUI スレッドごと止まってしまう。
        # cancel() が中の ffmpeg を殺すので、待ちはすぐ明ける。
        prep, self._prep_task = self._prep_task, None
        if prep is not None:
            prep.cancel()
            _join_task(prep)
            # 止める寸前に仕上がっていたら、その一時ファイルを捨てる。窓を
            # 閉じたあとでは ok シグナルが配られず、受け側が捨てられない。
            prep.discard_result()
        _join_task(self._fin_task)
        self._fin_task = None
        self._pending = None

    def _confirm_abort(self):
        """書き出し中に閉じられようとしたとき、本当に中止してよいか聞く。

        黙って畳んでいた頃と違い、いまは裏で走っていて他の作業ができるので、
        窓を閉じるつもりでうっかり ESC を押した、が起こりやすい。長い曲だと
        十数分の書き出しが一瞬で消えるので、ひと言確認する。"""
        return QMessageBox.question(
            self, "動画を書き出す",
            "書き出しの途中です。中止して閉じますか？\n"
            "（書きかけの動画は残りません）") == QMessageBox.Yes

    def reject(self):
        # QDialog.reject() は closeEvent を通らない。ESC で窓だけ消えて裏の
        # 書き出しが走り続ける、ということが無いようここでも畳んでおく。
        if self.is_busy() and not self._confirm_abort():
            return
        self.abort_now()
        self._widget = None
        super().reject()

    def closeEvent(self, event):
        # 書き出し中に閉じられたら、その場で畳んで書きかけを消す。裏のスレッドも
        # ここで待ち切るので、閉じたあとに何かが走り続けることはない。
        if self.is_busy() and not self._confirm_abort():
            event.ignore()
            return
        self.abort_now()
        self._widget = None            # 画面外ウィジェットを解放
        super().closeEvent(event)
