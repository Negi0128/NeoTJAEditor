# -*- coding: utf-8 -*-
"""録画の背景を決める専用の窓(利用者の指定 2026-10-24「背景は専用の画面で
調整したい」)。

録画ダイアログには「背景」の1行だけを残し、中身はここで決める。
**見本は本番と同じ大きさで重ねる** — 背景の上に、背景を描かずに起こした
譜面画面を重ねたもの。出来上がりと同じ道(ffmpeg の overlay)ではなく
QPainter で重ねているが、縮め方・暗さ・位置の出し方は本番と同じ式。
押してすぐ出るほうが大事なので、確認の1枚に ffmpeg を起こさない。
"""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSlider, QSpinBox, QVBoxLayout,
)

from neotja import recorder
from neotja import settings as settings_mod
from neotja.dialogs.record_bg_check import _bg_frame, compose_check_frame

#: 見本を焼き直すまでの間合い(ms)。つまみを動かしているあいだは焼かない。
PREVIEW_MS = 150

FILE_FILTER = ("動画と画像 (*.mp4 *.webm *.mov *.mkv *.avi *.m4v"
               " *.png *.jpg *.jpeg *.bmp *.webp);;すべて (*.*)")


def _tight(widget):
    """欄を横いっぱいに伸ばさない(フォームに直に置くと伸びてしまう)。"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(widget)
    row.addStretch(1)
    return row


class RecordBackgroundDialog(QDialog):
    """背景の調整。OK を押したら background() が新しい設定を返す。

    make_widget は「画面外の描画用ウィジェットを返す呼び出し」。録画
    ダイアログが見積もりに使っているものをそのまま借りる(同じ大きさ・
    同じ中身でなければ、見本が本番と食い違う)。
    """

    MAX_VIEW_W = 900

    def __init__(self, parent, make_widget, duration, bg, out_dir="",
                 song_path="", canvas_wh=None, art_track=None, art_note="",
                 chart_path="", file_title=""):
        super().__init__(parent)
        #: 基本ジャケット → 歌詞に合わせた切り替えの区切り、を作る呼び出し
        #: (録画の窓が渡す)。見本の1コマを「見ている時刻の絵」にするため。
        #: まとめ録画からは渡らない(切り替えない)。
        self._art_track = art_track
        self.setWindowTitle("録画の背景")
        self._make_widget = make_widget
        self._widget = None
        #: YouTube から落としたものを置く場所(録画の書き出し先と同じ)。
        self._out_dir = str(out_dir or "")
        #: 曲の音源。「音から推定」で背景の音と見比べる相手。
        self._song = str(song_path or "")
        #: 譜面ファイル。PNG に書き出すときの名前と、置き場所の候補に使う。
        #: まとめ録画からは渡らない(曲が1つに決まらない)。
        self._chart = str(chart_path or "")
        #: 録画の窓で入れた動画タイトル(ファイル名に使える形にしたもの)。
        #: 渡されたら PNG も動画と同じ名前「<タイトル>.png」にする(利用者の
        #: 指定 2026-11-13)。まとめ録画からは渡らず、従来の名前のまま。
        self._file_title = str(file_title or "")
        self._yt = None
        #: 出来上がりの大きさ (w, h)。見本を本番と同じ画面比で作るため。
        self._canvas_wh = tuple(canvas_wh) if canvas_wh else None
        self._duration = max(0.1, float(duration or 0.1))

        lay = QVBoxLayout(self)

        # ---- ファイル ----
        row = QHBoxLayout()
        self.ed_path = QLineEdit(bg.path if bg is not None else "")
        self.ed_path.setPlaceholderText("なし（動画 mp4/webm/mov・画像 png/jpg）")
        self.ed_path.textChanged.connect(self._schedule)
        btn_pick = QPushButton("選ぶ...")
        btn_pick.clicked.connect(self._browse)
        self.btn_yt = QPushButton("YouTube...")
        self.btn_yt.setToolTip("URL を貼って背景の動画を落とします。")
        self.btn_yt.clicked.connect(self._from_youtube)
        btn_none = QPushButton("なし")
        btn_none.clicked.connect(lambda: self.ed_path.setText(""))
        row.addWidget(QLabel("ファイル"))
        row.addWidget(self.ed_path, 1)
        for b in (btn_pick, self.btn_yt, btn_none):
            row.addWidget(b)
        lay.addLayout(row)

        # ---- 見本 ----
        self.view = QLabel("背景を選ぶと、ここに重なった1コマが出ます。")
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumHeight(240)
        self.view.setFrameShape(QFrame.StyledPanel)
        lay.addWidget(self.view, 1)

        at_row = QHBoxLayout()
        self.sl_at = QSlider(Qt.Horizontal)
        self.sl_at.setRange(0, int(self._duration * 100))
        self.sl_at.setValue(min(int(self._duration * 100), 200))
        self.sl_at.valueChanged.connect(self._schedule)
        self.lb_at = QLabel()
        at_row.addWidget(QLabel("見る位置"))
        at_row.addWidget(self.sl_at, 1)
        at_row.addWidget(self.lb_at)
        # 見ているコマを PNG に書き出す(利用者の指定 2026-11-13「この画面を
        # png で保存ボタンでいいや その場で生成した方がいいね」)。
        # 窓に出ている縮めた絵を取るのではなく、押したときに**出来上がりの
        # 大きさ**で焼き直す。サムネや告知に使うので、縮んだ絵では足りない。
        self.btn_png = QPushButton("この画面をPNGで保存…")
        self.btn_png.setToolTip(
            "いまの設定と見る位置で、出来上がりと同じ大きさの1コマを"
            + chr(10) + "PNG で保存します。")
        self.btn_png.clicked.connect(self._save_png)
        at_row.addWidget(self.btn_png)
        lay.addLayout(at_row)

        # ---- つまみ ----
        form = QFormLayout()
        off_row = QHBoxLayout()
        self.sp_offset = QDoubleSpinBox()
        self.sp_offset.setDecimals(3)
        self.sp_offset.setRange(-3600.0, 3600.0)
        self.sp_offset.setSingleStep(0.1)
        self.sp_offset.setSuffix(" 秒")
        self.sp_offset.setValue(bg.offset if bg is not None else 0.0)
        self.sp_offset.setToolTip(
            "曲の 0 秒のときに、背景動画の何秒目を出すか。\n"
            "録画は曲の %.0f 秒前から始まるので、そのあいだは黒くなります。"
            % recorder.RECORD_LEAD_SEC)
        self.sp_offset.valueChanged.connect(self._schedule)
        self.btn_guess = QPushButton("音から推定")
        self.btn_guess.setToolTip("背景動画の音と曲の音源を見比べて、ずれを当てます。")
        self.btn_guess.clicked.connect(self._guess_offset)
        self.lb_sure = QLabel()
        off_row.addWidget(self.sp_offset)
        off_row.addWidget(self.btn_guess)
        off_row.addWidget(self.lb_sure)
        off_row.addStretch(1)
        form.addRow("開始位置", off_row)

        self.sp_dim = QSpinBox()
        self.sp_dim.setRange(0, 80)
        self.sp_dim.setSuffix(" %")
        self.sp_dim.setValue(int(round((bg.dim if bg is not None else 0.0) * 100)))
        self.sp_dim.setToolTip("背景を暗くして譜面を見やすくします。")
        self.sp_dim.valueChanged.connect(self._schedule)
        form.addRow("暗さ", _tight(self.sp_dim))

        self.sp_lane = QSpinBox()
        self.sp_lane.setRange(0, 100)
        self.sp_lane.setSuffix(" %")
        self.sp_lane.setValue(int(round((bg.lane if bg is not None else 1.0) * 100)))
        self.sp_lane.setToolTip(
            "譜面レーンの地の濃さ。下げると背景が透けます。\n"
            "音符・文字・枠はそのままです。")
        self.sp_lane.valueChanged.connect(self._schedule)
        form.addRow("レーンの濃さ", _tight(self.sp_lane))

        # フェード。入りと終わりで別々に秒数を決める(利用者の指定 2026-10-25)。
        fade = QHBoxLayout()
        self.chk_in = QCheckBox("入り")
        self.chk_in.setChecked(bool(getattr(bg, "fade_in", 0.0)))
        self.sp_in = QDoubleSpinBox()
        self.sp_in.setDecimals(1)
        self.sp_in.setRange(0.1, 30.0)
        self.sp_in.setSingleStep(0.5)
        self.sp_in.setSuffix(" 秒")
        self.sp_in.setValue(float(getattr(bg, "fade_in", 0.0)) or 1.0)
        self.chk_out = QCheckBox("終わり")
        self.chk_out.setChecked(bool(getattr(bg, "fade_out", 0.0)))
        self.sp_out = QDoubleSpinBox()
        self.sp_out.setDecimals(1)
        self.sp_out.setRange(0.1, 30.0)
        self.sp_out.setSingleStep(0.5)
        self.sp_out.setSuffix(" 秒")
        self.sp_out.setValue(float(getattr(bg, "fade_out", 0.0)) or 1.0)
        self.cb_target = QComboBox()
        self.cb_target.addItem("画面全体", "all")
        self.cb_target.addItem("背景だけ", "bg")
        i = self.cb_target.findData(getattr(bg, "fade_target", "all"))
        self.cb_target.setCurrentIndex(max(0, i))
        for wdg in (self.chk_in, self.sp_in, self.chk_out, self.sp_out,
                    self.cb_target):
            fade.addWidget(wdg)
        fade.addStretch(1)
        self.chk_in.toggled.connect(self.sp_in.setEnabled)
        self.chk_out.toggled.connect(self.sp_out.setEnabled)
        self.sp_in.setEnabled(self.chk_in.isChecked())
        self.sp_out.setEnabled(self.chk_out.isChecked())
        form.addRow("フェード", fade)
        lay.addLayout(form)

        # ---- 飾り(ジャケット写真) ----
        # 以下の欄は **bg is not None** で見る。背景の道が空の bg は偽に
        # なるが、ジャケットは背景なしでも付けられるようになった
        # (2026-11-13)ので、偽だからと既定へ戻すと、窓を開いて閉じる
        # だけで大きさ・位置・枠が初期値へ戻ってしまう。
        art_row = QHBoxLayout()
        self.ed_art = QLineEdit(getattr(bg, "art", "") if bg is not None else "")
        self.ed_art.setPlaceholderText("なし（png / jpg）")
        if art_note:
            self.ed_art.setToolTip(art_note)
        self.ed_art.textChanged.connect(self._schedule)
        btn_art = QPushButton("選ぶ...")
        btn_art.clicked.connect(self._browse_art)
        btn_art_none = QPushButton("なし")
        btn_art_none.clicked.connect(lambda: self.ed_art.setText(""))
        art_row.addWidget(self.ed_art, 1)
        art_row.addWidget(btn_art)
        art_row.addWidget(btn_art_none)
        form.addRow("ジャケット", art_row)

        art_opt = QHBoxLayout()
        self.sp_art_pct = QSpinBox()
        self.sp_art_pct.setRange(2, 60)
        self.sp_art_pct.setSuffix(" %")
        self.sp_art_pct.setValue(int(round(getattr(bg, "art_pct", 20.0)
                                           if bg is not None else 20.0)))
        self.sp_art_pct.setToolTip("画面の高さに対する大きさ。")
        self.sp_art_pct.valueChanged.connect(self._schedule)
        self.sp_art_pad = QSpinBox()
        self.sp_art_pad.setRange(0, 400)
        self.sp_art_pad.setSuffix(" px")
        self.sp_art_pad.setValue(int(getattr(bg, "art_pad", 24) if bg is not None else 24))
        self.sp_art_pad.setToolTip("左と下の端からの余白。")
        self.sp_art_pad.valueChanged.connect(self._schedule)
        # 置き場所は**座標(画素)**(利用者の指定 2026-10-31)。左上が 0,0。
        # 負の数は反対の端からの距離(-24 なら右端/下端から 24px)。
        self.sp_art_x = QSpinBox()
        self.sp_art_x.setRange(-9999, 9999)
        self.sp_art_x.setSuffix(" px")
        self.sp_art_x.setValue(int(getattr(bg, "art_x", 24) if bg is not None else 24))
        self.sp_art_x.setToolTip("左からの位置。負の数は右端からの距離。")
        self.sp_art_x.valueChanged.connect(self._schedule)
        self.sp_art_y = QSpinBox()
        self.sp_art_y.setRange(-9999, 9999)
        self.sp_art_y.setSuffix(" px")
        self.sp_art_y.setValue(int(getattr(bg, "art_y", -24) if bg is not None else -24))
        self.sp_art_y.setToolTip("上からの位置。負の数は下端からの距離。")
        self.sp_art_y.valueChanged.connect(self._schedule)
        art_opt.addWidget(QLabel("大きさ"))
        art_opt.addWidget(self.sp_art_pct)
        art_opt.addWidget(QLabel("位置 X"))
        art_opt.addWidget(self.sp_art_x)
        art_opt.addWidget(QLabel("Y"))
        art_opt.addWidget(self.sp_art_y)
        self.sp_art_edge = QSpinBox()
        self.sp_art_edge.setRange(0, 40)
        self.sp_art_edge.setSuffix(" px")
        self.sp_art_edge.setValue(int(getattr(bg, "art_edge", 6) if bg is not None else 6))
        self.sp_art_edge.setToolTip("写真のまわりの枠の太さ。0 で枠なし。")
        self.sp_art_edge.valueChanged.connect(self._schedule)
        self._art_color = str(getattr(bg, "art_edge_color", "#ffffff")
                              if bg is not None else "#ffffff")
        self.btn_art_color = QPushButton("枠の色")
        self.btn_art_color.clicked.connect(self._pick_art_color)
        art_opt.addWidget(QLabel("枠"))
        art_opt.addWidget(self.sp_art_edge)
        art_opt.addWidget(self.btn_art_color)
        art_opt.addStretch(1)
        self._show_art_color()
        form.addRow("", art_opt)

        # ---- 波形(音に合わせて動く棒) ----
        # **出さない。** 使わないことにした(利用者の指定 2026-10-31
        # 「ジャケットの下の波形は消して」)。中の仕掛けは残してあるが、
        # 窓から入れる口が無いので、どの道これ以降は描かれない。
        self._wave_color = "#ffb300"

        lay.addWidget(QLabel(
            "※ 縦横が合わないときは上下を合わせて、左右は黒で埋めます。"))

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        # 「既定にする」= いまの**見た目**を覚えて、次に開いたときの
        # 始まりにする(利用者の指定 2026-11-06)。絵の道は覚えない —
        # 曲ごとに選び直すもの。
        self.btn_def = QPushButton("既定にする")
        self.btn_def.setToolTip(
            "いまの暗さ・レーンの濃さ・フェード・ジャケットの大きさと"
            + chr(10) + "位置を、次に開いたときの始まりにします。"
            + chr(10) + "背景の動画とジャケットの絵は覚えません。")
        self.btn_def.clicked.connect(self._save_default)
        self.btn_def_clear = QPushButton("既定を消す")
        self.btn_def_clear.setToolTip("覚えた既定を忘れて、元の初期値に戻します。")
        self.btn_def_clear.clicked.connect(self._clear_default)
        self.lbl_def = QLabel("")
        box.addButton(self.btn_def, QDialogButtonBox.ResetRole)
        box.addButton(self.btn_def_clear, QDialogButtonBox.ResetRole)
        row_def = QHBoxLayout()
        row_def.addWidget(self.lbl_def)
        row_def.addStretch()
        lay.addLayout(row_def)
        lay.addWidget(box)
        self._sync_default_buttons()

        # 窓の大きさが変わったら見本も焼き直す(収まる大きさが変わるため)。
        self.view.installEventFilter(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render)
        self._sync_enabled()
        self._schedule()

    # ------------------------------------------------------------------
    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.view and ev.type() == QEvent.Resize:
            self._timer.start(PREVIEW_MS)
        return super().eventFilter(obj, ev)

    # ------------------------------------------------------------------
    # 既定(利用者の指定 2026-11-06「デフォルト設定を自分で作れるように」)
    # ------------------------------------------------------------------
    def _sync_default_buttons(self):
        cfg = settings_mod.load_settings()
        has = isinstance(cfg.get("rec_bg_default"), dict) and             bool(cfg.get("rec_bg_default"))
        self.btn_def_clear.setEnabled(has)
        self.lbl_def.setText("既定を覚えています。" if has else "")

    def _save_default(self):
        cfg = settings_mod.load_settings()
        cfg["rec_bg_default"] = recorder.bg_defaults(self.background())
        settings_mod.save_settings(cfg)
        self._sync_default_buttons()
        self.lbl_def.setText("いまの設定を既定にしました。")

    def _clear_default(self):
        cfg = settings_mod.load_settings()
        cfg.pop("rec_bg_default", None)
        settings_mod.save_settings(cfg)
        self._sync_default_buttons()
        self.lbl_def.setText("既定を消しました。")

    def background(self):
        """いま欄に入っている背景。path が空なら「背景なし」。"""
        return recorder.RecordBackground(
            self.ed_path.text().strip(),
            offset=self.sp_offset.value(),
            dim=self.sp_dim.value() / 100.0,
            lane=self.sp_lane.value() / 100.0,
            fade_in=self.sp_in.value() if self.chk_in.isChecked() else 0.0,
            fade_out=self.sp_out.value() if self.chk_out.isChecked() else 0.0,
            fade_target=self.cb_target.currentData(),
            art=self.ed_art.text().strip(),
            art_pct=self.sp_art_pct.value(),
            art_pad=self.sp_art_pad.value(),
            art_x=self.sp_art_x.value(),
            art_y=self.sp_art_y.value(),
            art_edge=self.sp_art_edge.value(),
            art_edge_color=self._art_color,
            wave=False,
            wave_pct=15.0,
            wave_color=self._wave_color,
            wave_alpha=70,
            art_track=(self._art_track(self.ed_art.text().strip())
                       if self._art_track else None))

    def _sync_enabled(self):
        on = bool(self.ed_path.text().strip())
        for wdg in (self.sp_offset, self.sp_dim, self.sp_lane, self.chk_in,
                    self.chk_out, self.cb_target,
                    ):
            wdg.setEnabled(on)
        # ジャケットは**背景が無くても**付けられる(利用者の指定 2026-11-13
        # 「背景なしでデフォルト背景でもジャケットつけられるようにしてほしい」)。
        # いつもの画面の上へ重ねる(recorder._art_only_filter_complex)。
        # 見る時刻のつまみも、ジャケットの切り替えを確かめるのに要る。
        for wdg in (self.ed_art, self.sp_art_pct, self.sp_art_pad,
                    self.sp_art_x, self.sp_art_y,
                    self.sp_art_edge, self.btn_art_color):
            wdg.setEnabled(True)
        self.sl_at.setEnabled(on or bool(self.ed_art.text().strip()))
        # 見本が出せるときだけ押せる(出ないときに押しても書くものが無い)。
        self.btn_png.setEnabled(self.sl_at.isEnabled())
        self.sp_in.setEnabled(on and self.chk_in.isChecked())
        self.sp_out.setEnabled(on and self.chk_out.isChecked())
        # 「音から推定」は動画(＝音が入りうるもの)で、曲の音源があるときだけ。
        bg = self.background()
        self.btn_guess.setEnabled(on and not bg.is_image and bool(self._song))

    def _show_art_color(self):
        """ボタンの地をいまの色にする(見れば分かるように)。"""
        c = QColor(self._art_color)
        fg = "#000" if c.lightness() > 140 else "#fff"
        self.btn_art_color.setStyleSheet(
            "QPushButton{background:%s;color:%s}" % (self._art_color, fg))

    def _pick_art_color(self):
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(self._art_color), self, "枠の色")
        if c.isValid():
            self._art_color = c.name()
            self._show_art_color()
            self._schedule()

    def _browse_art(self):
        got, _ = QFileDialog.getOpenFileName(
            self, "左下へ置くジャケット写真",
            self.ed_art.text().strip() or "",
            "画像 (*.png *.jpg *.jpeg *.bmp *.webp);;すべて (*.*)")
        if got:
            self.ed_art.setText(got)

    def _browse(self):
        got, _ = QFileDialog.getOpenFileName(
            self, "背景に使う動画・画像",
            self.ed_path.text().strip() or "", FILE_FILTER)
        if got:
            self.ed_path.setText(got)

    def _from_youtube(self):
        """URL を貼って、背景に敷く動画を落とす(利用者の指定 2026-10-25
        「新規作成の時と同じように YouTube からダウンロードしたい」)。

        落とし屋は新規作成と同じ YtDlpDownloadWorker。あちらは音だけを
        取るので、こちらは want="video" で頼む。"""
        from PySide6.QtWidgets import (QInputDialog, QMessageBox,
                                       QProgressDialog)
        from neotja.ytdlp_worker import YtDlpDownloadWorker
        url, okd = QInputDialog.getText(self, "YouTube から",
                                        "動画の URL を貼ってください")
        url = (url or "").strip()
        if not okd or not url:
            return
        out_dir = self._out_dir or os.path.dirname(self.ed_path.text()) or "."
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError:
            pass
        prog = QProgressDialog("用意しています...", "やめる", 0, 100, self)
        prog.setWindowTitle("YouTube から")
        prog.setWindowModality(Qt.WindowModal)
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        worker = YtDlpDownloadWorker(url, out_dir, parent=self, want="video")
        self._yt = worker                      # 途中で捨てられないよう持つ

        def on_pct(v):
            if v is None or v < 0:
                prog.setRange(0, 0)            # 何割か分からないときは流す
            else:
                prog.setRange(0, 100)
                prog.setValue(int(v))

        def done(path, title, _uploader, _thumb):
            prog.close()
            self.ed_path.setText(path)
            self.setWindowTitle("録画の背景 — %s" % (title or ""))

        def failed(msg):
            prog.close()
            QMessageBox.warning(self, "YouTube から",
                                "落とせませんでした。\n%s" % msg)

        worker.progress.connect(prog.setLabelText)
        worker.progress_pct.connect(on_pct)
        worker.finished_ok.connect(done)
        worker.failed.connect(failed)
        prog.canceled.connect(worker.cancel)
        worker.start()

    def _guess_offset(self):
        """背景の音と曲の音源を見比べて、ずれを当てる。

        当てた値は**欄に入れるだけ**(仕様 2026-10-24「候補として欄に
        入れるだけにする」)。そのまま見本で確かめてもらう。"""
        from PySide6.QtWidgets import QApplication, QMessageBox
        from neotja import bg_align
        bg = self.background()
        if not bg or not self._song:
            QMessageBox.information(self, "音から推定",
                                    "曲の音源が分からないので当てられません。")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            got, sure = bg_align.estimate_offset(self._song, bg.path)
        finally:
            QApplication.restoreOverrideCursor()
        if got is None:
            QMessageBox.information(
                self, "音から推定",
                "当てられませんでした。\n"
                "背景の動画に音が入っていないか、曲と違う音のようです。")
            return
        self.sp_offset.setValue(got)
        self.lb_sure.setText("推定 %.3f 秒（確からしさ %d%%）"
                             % (got, int(round(sure * 100))))

    # ------------------------------------------------------------------
    def _schedule(self):
        self._sync_enabled()
        at = self.sl_at.value() / 100.0
        self.lb_at.setText("%6.2f 秒" % at)
        self._timer.start(PREVIEW_MS)

    def _frame(self):
        """いまの設定と見る位置の1コマ。(絵, 出せないときの文言) を返す。

        見本も PNG の書き出しも**ここ1か所**で作る。別々に組むと、
        見本と保存した絵が食い違っても気づけない。"""
        bg = self.background()
        plain = not bg and recorder.wants_art_only(bg)
        if not plain and (not bg or not os.path.exists(bg.path)):
            return None, ("背景かジャケットを選ぶと、ここに重なった"
                          "1コマが出ます。")
        if self._widget is None:
            self._widget = self._make_widget()
        if plain and self._widget is None:
            # まとめ録画から開いたとき(重ねる譜面が決まらない)は、背景が
            # 無ければ出す絵が無い。
            return None, "背景を選ぶと、ここに重なった1コマが出ます。"
        if self._widget is None:
            # まとめ録画から開いたときは、重ねる譜面が1つに決まらない。
            # 背景だけを出す(暗さと位置はここで見られる)。
            cw, ch = self._canvas_wh or (960, 540)
            img = _bg_frame(bg, bg.offset + self.sl_at.value() / 100.0,
                            cw, ch)
        else:
            img = compose_check_frame(self._widget, bg,
                                      self.sl_at.value() / 100.0,
                                      canvas=self._canvas_wh,
                                      song=self._song)
        if img is None:
            return None, "背景のコマを取り出せませんでした。"
        return img, ""

    def _render(self):
        img, msg = self._frame()
        if img is None:
            self.view.setText(msg)
            return
        # **入る大きさに収める。** 幅だけで縮めていたので、窓が低いと
        # 下が切れて「レーンの所だけ」に見えていた(利用者の報告
        # 2026-10-26「全部プレビューしてほしい」)。
        box = self.view.contentsRect()
        bw = max(160, min(self.MAX_VIEW_W, box.width()))
        bh = max(120, box.height())
        pm = QPixmap.fromImage(img).scaled(bw, bh, Qt.KeepAspectRatio,
                                           Qt.SmoothTransformation)
        self.view.setPixmap(pm)

    # ------------------------------------------------------------------
    # PNG の書き出し
    # ------------------------------------------------------------------
    def _png_dir(self):
        """保存の窓を最初に開く場所。録画の書き出し先 → 譜面のフォルダ →
        前回 PNG を置いた場所 → ホーム、の順で、実在するものを使う
        (外付けを外したなどで無くなったフォルダは飛ばす)。"""
        cfg = settings_mod.load_settings()
        for cand in (self._out_dir,
                     os.path.dirname(self._chart) if self._chart else "",
                     str(cfg.get("rec_bg_png_dir", "") or "")):
            if cand and os.path.isdir(cand):
                return cand
        return os.path.expanduser("~")

    def _png_name(self):
        """既定のファイル名「譜面名_分-秒.png」。同じ曲で何コマか書き出しても
        見る位置で見分けられるように時刻を付ける(":" は名前に使えないので "-")。"""
        if self._file_title:
            return self._file_title + ".png"
        at = int(self.sl_at.value() / 100.0)
        base = (os.path.splitext(os.path.basename(self._chart))[0]
                if self._chart else "背景")
        return "%s_%02d-%02d.png" % (base, at // 60, at % 60)

    def _save_png(self):
        """見ているコマを出来上がりの大きさで焼いて PNG に保存する。

        欄も見本も触らない(焼くだけ)。保存の窓でやめたら何もしない。"""
        img, msg = self._frame()
        if img is None:
            self.lbl_def.setText(msg)
            return
        got, _ = QFileDialog.getSaveFileName(
            self, "この画面をPNGで保存",
            os.path.join(self._png_dir(), self._png_name()),
            "PNG 画像 (*.png)")
        if not got:
            return
        if not got.lower().endswith(".png"):
            got += ".png"
        if not img.save(got, "PNG"):
            self.lbl_def.setText("保存できませんでした: %s" % got)
            return
        # 置いた場所を覚える。譜面も書き出し先も分からない(まとめ録画)
        # ときに、次はここから開けるように。
        cfg = settings_mod.load_settings()
        cfg["rec_bg_png_dir"] = os.path.dirname(got)
        settings_mod.save_settings(cfg)
        self.lbl_def.setText("保存しました: %s（%d×%d）"
                             % (os.path.basename(got), img.width(), img.height()))
