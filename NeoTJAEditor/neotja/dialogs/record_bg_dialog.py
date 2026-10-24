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
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSlider, QSpinBox, QVBoxLayout,
)

from neotja import recorder
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
                 song_path=""):
        super().__init__(parent)
        self.setWindowTitle("録画の背景")
        self._make_widget = make_widget
        self._widget = None
        #: YouTube から落としたものを置く場所(録画の書き出し先と同じ)。
        self._out_dir = str(out_dir or "")
        #: 曲の音源。「音から推定」で背景の音と見比べる相手。
        self._song = str(song_path or "")
        self._yt = None
        self._duration = max(0.1, float(duration or 0.1))

        lay = QVBoxLayout(self)

        # ---- ファイル ----
        row = QHBoxLayout()
        self.ed_path = QLineEdit(bg.path if bg else "")
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
        self.view.setMinimumHeight(180)
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
        lay.addLayout(at_row)

        # ---- つまみ ----
        form = QFormLayout()
        off_row = QHBoxLayout()
        self.sp_offset = QDoubleSpinBox()
        self.sp_offset.setDecimals(3)
        self.sp_offset.setRange(-3600.0, 3600.0)
        self.sp_offset.setSingleStep(0.1)
        self.sp_offset.setSuffix(" 秒")
        self.sp_offset.setValue(bg.offset if bg else 0.0)
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
        self.sp_dim.setValue(int(round((bg.dim if bg else 0.0) * 100)))
        self.sp_dim.setToolTip("背景を暗くして譜面を見やすくします。")
        self.sp_dim.valueChanged.connect(self._schedule)
        form.addRow("暗さ", _tight(self.sp_dim))

        self.sp_lane = QSpinBox()
        self.sp_lane.setRange(0, 100)
        self.sp_lane.setSuffix(" %")
        self.sp_lane.setValue(int(round((bg.lane if bg else 1.0) * 100)))
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

        lay.addWidget(QLabel(
            "※ 縦横が合わないときは上下を合わせて、左右は黒で埋めます。"))

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render)
        self._sync_enabled()
        self._schedule()

    # ------------------------------------------------------------------
    def background(self):
        """いま欄に入っている背景。path が空なら「背景なし」。"""
        return recorder.RecordBackground(
            self.ed_path.text().strip(),
            offset=self.sp_offset.value(),
            dim=self.sp_dim.value() / 100.0,
            lane=self.sp_lane.value() / 100.0,
            fade_in=self.sp_in.value() if self.chk_in.isChecked() else 0.0,
            fade_out=self.sp_out.value() if self.chk_out.isChecked() else 0.0,
            fade_target=self.cb_target.currentData())

    def _sync_enabled(self):
        on = bool(self.ed_path.text().strip())
        for wdg in (self.sp_offset, self.sp_dim, self.sp_lane, self.chk_in,
                    self.chk_out, self.cb_target, self.sl_at):
            wdg.setEnabled(on)
        self.sp_in.setEnabled(on and self.chk_in.isChecked())
        self.sp_out.setEnabled(on and self.chk_out.isChecked())
        # 「音から推定」は動画(＝音が入りうるもの)で、曲の音源があるときだけ。
        bg = self.background()
        self.btn_guess.setEnabled(on and not bg.is_image and bool(self._song))

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

    def _render(self):
        bg = self.background()
        if not bg or not os.path.exists(bg.path):
            self.view.setText("背景を選ぶと、ここに重なった1コマが出ます。")
            return
        if self._widget is None:
            self._widget = self._make_widget()
        if self._widget is None:
            # まとめ録画から開いたときは、重ねる譜面が1つに決まらない。
            # 背景だけを出す(暗さと位置はここで見られる)。
            img = _bg_frame(bg, bg.offset + self.sl_at.value() / 100.0,
                            960, 270)
        else:
            img = compose_check_frame(self._widget, bg,
                                      self.sl_at.value() / 100.0)
        if img is None:
            self.view.setText("背景のコマを取り出せませんでした。")
            return
        pm = QPixmap.fromImage(img)
        if pm.width() > self.MAX_VIEW_W:
            pm = pm.scaledToWidth(self.MAX_VIEW_W, Qt.SmoothTransformation)
        self.view.setPixmap(pm)
