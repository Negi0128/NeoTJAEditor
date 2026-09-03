"""まとめて録画。譜面を並べて、順番に動画へ書き出す。

**なぜ Player 側に要るのか**
Editor の録画ダイアログは「いま開いている譜面を1本」しか想定していない。
30譜面を撮るのに30回ダイアログを開くのは道具として成立しない。

**なぜ画面を1つずつ作り直すのか**
録画は画面外に専用のウィジェットを作り、1コマずつ時刻を与えて描く方式
(recorder.make_offline_widget)。譜面ごとに中身が違うので使い回せない。
1本終わるたびに捨てて作り直す — 持ち回すと前の譜面の状態が残る。

**なぜ1本の失敗で止めないのか**
30本流して28本目で音源が見つからなかったとき、そこで全部止まると
やり直しの手間が大きい。失敗したものは印を付けて次へ進み、最後にまとめて
見せる。
"""

import os
import subprocess
import sys
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from neotja import recorder
from neotja import settings as settings_mod
from neotja.dialogs.record_dialog import QUALITY_PRESETS
from neotja.player.core import read_text
from neotja.preview_dock import parse_preview_headers
from neotja.tja_analyzer import TJACourseAnalyzer

#: 表の列は2つだけ。「譜面」と「コース」。
#:
#: 以前は状態と備考の列もあったが、情報が多すぎて何を見ればいいのか
#: 分からない、という指摘を受けて落とした。いま何をしているかは下の1行に
#: 出ていれば足りるし、終わったかどうかは行の色で分かる。
COL_NAME, COL_COURSE = 0, 1

#: 譜面名とコースの幅の比。
COL_RATIO = (3, 1)

#: 行の色。終わった/失敗した/いま書いている、を色だけで見せる。
COLOR_DONE = "#5b8f5b"
COLOR_FAILED = "#c05c5c"
COLOR_RUNNING = "#d8b45a"

#: 待ち行列1件の状態。
WAITING, RUNNING, DONE, FAILED, SKIPPED = "待機", "書き出し中", "完了", "失敗", "とばした"


class _Job:
    """待ち行列の1件。"""

    def __init__(self, path):
        self.path = path
        self.state = WAITING
        self.note = ""
        self.out_path = ""
        self.seconds = 0.0          # 書き出す長さ(見積もり用)
        # その譜面に入っているコース [{"key","label","level"}, ...] と、
        # 選ばれているキー。積んだ時点で読んでおき、行のプルダウンに出す。
        self.courses = []
        self.course = ""


class BatchPage(QWidget):
    """まとめて録画の画面。

    進み方は「1本ぶんの下ごしらえ(別スレッド) → 1コマずつ描く(GUIスレッド)
    → 次の1本」。描くのを QTimer で少しずつ回すので、書き出し中も画面が
    固まらず、中止も効く。
    """

    #: 全部終わった(中止も含む)。
    finished = Signal()

    def __init__(self, config_data, save_cb, parent=None):
        super().__init__(parent)
        self.cfg = config_data
        self.save_cb = save_cb
        self.analyzer = TJACourseAnalyzer(self.cfg)
        self.jobs = []
        self._running = False
        self._cancel = False
        self._index = -1
        self._rec = None            # 進行中の VideoRecording
        self._widget = None         # 画面外の描画用
        self._prep_task = None
        self._token = None
        self._t0 = 0.0
        self._batch = 1             # step() 1回で描くコマ数

        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._tick)

        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self):
        v = QVBoxLayout(self)
        # ゆったりめの余白と行間。詰まっていると設定画面のように見えて、
        # 「放り込む場所」だと分かりにくい。
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(14)

        # 上は「何をすればいいか」の1行と、追加のボタンだけ。
        head = QHBoxLayout()
        self.lbl_drop = QLabel("TJAを追加してください。（D＆Dでも読み込めます。）")
        head.addWidget(self.lbl_drop, 1)
        self.b_add = QPushButton("TJAを追加")
        self.b_add.clicked.connect(self.add_files)
        head.addWidget(self.b_add)
        v.addLayout(head)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("画質:"))
        self.cb_quality = QComboBox()
        for label, q in QUALITY_PRESETS:
            self.cb_quality.addItem(label, q)
        self.cb_quality.setCurrentIndex(0)
        opts.addWidget(self.cb_quality)

        opts.addWidget(QLabel("見た目:"))
        self.cb_layout = QComboBox()
        self.cb_layout.addItem("通常再生", "game")
        self.cb_layout.addItem("音声波形", "wave")
        opts.addWidget(self.cb_layout)
        opts.addStretch()
        v.addLayout(opts)

        out = QHBoxLayout()
        out.setSpacing(8)
        self.lbl_out = QLabel(self._short_dir(self._out_dir()))
        # 保存先は「変えたいときだけ見ればいい」ものなので、色を落として
        # 1行に収める(折り返すと画面の主役が保存先になってしまう)。
        self.lbl_out.setStyleSheet("color: #8a94a6;")
        self.lbl_out.setToolTip(self._out_dir())
        self.lbl_out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        out.addWidget(QLabel("保存先"), 0)
        out.addWidget(self.lbl_out, 1)
        b_out = QPushButton("変更")
        b_out.setFlat(True)
        b_out.clicked.connect(self.pick_out_dir)
        out.addWidget(b_out, 0)
        v.addLayout(out)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["譜面", "コース"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        # 譜面名とコースを 3:1 に。
        #
        # Stretch を2つ並べても**等分にしかならない**(実際にそうなった)。
        # 名前側だけ伸ばし、コース側は幅を固定して、窓の大きさが変わるたびに
        # 割合から計算し直す(resizeEvent)。
        h.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        h.setSectionResizeMode(COL_COURSE, QHeaderView.Fixed)
        h.setStretchLastSection(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        v.addWidget(self.table, 1)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        # 数字は下の1行に出るので、バーには出さない(同じことを2か所に
        # 書くと視線が散る)。細くして線のように見せる。
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        v.addWidget(self.bar)

        self.lbl_status = QLabel("譜面を追加してください。")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

        run = QHBoxLayout()
        run.addStretch()
        self.b_start = QPushButton("まとめて書き出す")
        self.b_start.setObjectName("accentButton")
        self.b_start.clicked.connect(self.start)
        run.addWidget(self.b_start)
        self.b_stop = QPushButton("中止")
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self.stop)
        run.addWidget(self.b_stop)
        v.addLayout(run)

        self.setAcceptDrops(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_column_ratio()

    def _apply_column_ratio(self):
        """コースの列幅を、表の幅の比(COL_RATIO)から決め直す。"""
        total = max(120, self.table.viewport().width())
        w = int(total * COL_RATIO[1] / float(sum(COL_RATIO)))
        self.table.setColumnWidth(COL_COURSE, w)

    # ------------------------------------------------------------------
    # 待ち行列
    # ------------------------------------------------------------------
    def add_paths(self, paths):
        """ファイルでもフォルダでも受ける。フォルダは中の .tja を全部。"""
        added = 0
        have = {j.path for j in self.jobs}
        for p in paths:
            if os.path.isdir(p):
                for root, _dirs, files in os.walk(p):
                    for fn in sorted(files):
                        if fn.lower().endswith(".tja"):
                            full = os.path.join(root, fn)
                            if full not in have:
                                self.jobs.append(self._make_job(full))
                                have.add(full)
                                added += 1
            elif p.lower().endswith(".tja") and p not in have:
                self.jobs.append(self._make_job(p))
                have.add(p)
                added += 1
        if added:
            self._reload_table()
            self.lbl_status.setText("%d 件を追加しました（全 %d 件）。"
                                    % (added, len(self.jobs)))
        return added

    def _make_job(self, path):
        """1件ぶんを作る。コースの顔ぶれはここで読んでおく。

        行のプルダウンに出すため。積んだあとに読むと、表を出してから
        しばらく選べない時間ができて手が止まる。"""
        job = _Job(path)
        try:
            from neotja.player.core import read_courses, read_text
            job.courses = read_courses(read_text(path), self.analyzer)
        except Exception:  # noqa: BLE001
            job.courses = []
        if job.courses:
            # 既定はいちばん難しいもの。撮りたいのはたいていそれ。
            order = ("Easy", "Normal", "Hard", "Oni", "Edit")
            job.courses.sort(key=lambda c: order.index(c["key"])
                             if c.get("key") in order else -1)
            job.course = job.courses[-1]["key"]
        return job

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "譜面を追加", self.cfg.get("player_last_file", ""),
            "TJA Files (*.tja)")
        if paths:
            self.add_paths(paths)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "フォルダを追加")
        if d:
            self.add_paths([d])

    def keyPressEvent(self, event):
        # 消すのは Delete。ボタンを置かない代わりの道。
        from PySide6.QtCore import Qt as _Qt
        if event.key() in (_Qt.Key_Delete, _Qt.Key_Backspace):
            self.remove_selected()
            return
        super().keyPressEvent(event)

    def remove_selected(self):
        if self._running:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            if 0 <= r < len(self.jobs):
                del self.jobs[r]
        self._reload_table()

    def clear_all(self):
        if self._running:
            return
        self.jobs = []
        self._reload_table()

    def _reload_table(self):
        # 行数を変えるとセルに載せたプルダウンは捨てられるので、いったん
        # 全部消してから作り直す(行がずれたまま古いプルダウンが残るのを防ぐ)。
        self.table.clearContents()
        self.table.setRowCount(len(self.jobs))
        for i, job in enumerate(self.jobs):
            self._set_row(i, job)

    def _set_row(self, i, job):
        def put(col, text):
            item = self.table.item(i, col)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(i, col, item)
            item.setText(text)

        put(COL_NAME, os.path.basename(job.path))
        # 状態は色で見せる。列を1つ増やすより、行を見て分かるほうが速い。
        # 失敗の理由はその行の吹き出し(ツールチップ)と、下の1行に出す。
        item = self.table.item(i, COL_NAME)
        color = {DONE: COLOR_DONE, FAILED: COLOR_FAILED,
                 RUNNING: COLOR_RUNNING}.get(job.state)
        item.setForeground(QColor(color) if color else QBrush())
        item.setToolTip(job.note or "")

        # コースは行ごとのプルダウン。譜面によって入っているコースが違うので、
        # 全体でひとつ選ばせると撮れないものが出る。
        box = self.table.cellWidget(i, COL_COURSE)
        if box is None:
            box = QComboBox()
            box.setEnabled(not self._running)
            self.table.setCellWidget(i, COL_COURSE, box)
            box.currentIndexChanged.connect(
                lambda _i, row=i: self._on_course_picked(row))
        if box.count() != len(job.courses):
            box.blockSignals(True)
            box.clear()
            for c in job.courses:
                lv = c.get("level")
                box.addItem("%s%s" % (c.get("label") or c["key"],
                                      "" if lv is None else " ★%d" % lv),
                            c["key"])
            idx = box.findData(job.course)
            if idx >= 0:
                box.setCurrentIndex(idx)
            box.blockSignals(False)

    def _on_course_picked(self, row):
        box = self.table.cellWidget(row, COL_COURSE)
        if box is not None and 0 <= row < len(self.jobs):
            self.jobs[row].course = box.currentData() or ""

    # ---- ドラッグ&ドロップ ----
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_paths([u.toLocalFile() for u in event.mimeData().urls()])

    # ------------------------------------------------------------------
    # 保存先
    # ------------------------------------------------------------------
    def _out_dir(self):
        for key in ("record_output_dir", "record_last_dir"):
            d = self.cfg.get(key, "")
            if d and os.path.isdir(d):
                return d
        return os.path.expanduser("~")

    def _short_dir(self, d):
        """保存先を1行に収める。長い絶対パスをそのまま出すと折り返して、
        画面の主役が保存先になってしまう。"""
        d = d or ""
        if len(d) <= 60:
            return d
        return d[:26] + " … " + d[-30:]

    def pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "保存先", self._out_dir())
        if d:
            self.cfg["record_output_dir"] = d
            self.lbl_out.setText(self._short_dir(d))
            self.lbl_out.setToolTip(d)
            if self.save_cb is not None:
                self.save_cb()

    # ------------------------------------------------------------------
    # 実行
    # ------------------------------------------------------------------
    def start(self):
        if self._running or not self.jobs:
            return
        for job in self.jobs:
            job.state = WAITING
            job.note = ""
        self._reload_table()
        self._running = True
        self._cancel = False
        self._index = -1
        self._t0 = time.perf_counter()
        self._set_busy(True)
        self._next_job()

    def stop(self):
        """中止。いま描いている1本は捨てる(書きかけの動画は残さない)。"""
        if not self._running:
            return
        self._cancel = True
        self.lbl_status.setText("中止しています...")
        if self._token is not None:
            self._token.cancel()
        self._timer.stop()
        if self._rec is not None:
            try:
                self._rec.abort()
            except Exception:  # noqa: BLE001
                pass
            self._rec = None
        self._finish_all()

    def _set_busy(self, busy):
        for w in (self.b_start, self.cb_quality, self.cb_layout):
            w.setEnabled(not busy)
        # 行のプルダウンも、走っている間は触れないようにする。
        for i in range(self.table.rowCount()):
            box = self.table.cellWidget(i, COL_COURSE)
            if box is not None:
                box.setEnabled(not busy)
        self.b_stop.setEnabled(busy)

    def _next_job(self):
        """次の1本の下ごしらえを始める。無ければ終わり。"""
        self._drop_widget()
        self._index += 1
        if self._cancel or self._index >= len(self.jobs):
            self._finish_all()
            return
        job = self.jobs[self._index]
        job.state = RUNNING
        self._set_row(self._index, job)
        self._update_progress()

        try:
            prep = self._prepare_one(job)
        except Exception as exc:  # noqa: BLE001
            self._fail(job, "%s: %s" % (type(exc).__name__, exc))
            QTimer.singleShot(0, self._next_job)
            return
        if prep is None:
            QTimer.singleShot(0, self._next_job)
            return
        preview, wave, out_path, end_sec = prep
        job.out_path = out_path
        job.seconds = end_sec

        q = self.cb_quality.currentData()
        layout = self.cb_layout.currentData()
        se = bool(self.cfg.get("se_text_enabled", True))
        if layout == "wave":
            self._widget = recorder.make_offline_wave_widget(
                preview, self._offset, mips=None, se_text_enabled=se)
        else:
            self._widget = recorder.make_offline_widget(preview, self._offset, se)

        don, ka = settings_mod.effective_hit_sound_paths(self.cfg)
        self._token = recorder.CancelToken()
        from neotja.dialogs.record_dialog import _Task
        task = _Task(
            lambda: recorder.prepare_recording(
                preview_data=preview, offset=self._offset, song_path=wave,
                start_sec=0.0, end_sec=end_sec, fps=int(q["fps"]),
                don_path=don, ka_path=ka,
                song_volume=float(self.cfg.get("preview_volume", 0.8)),
                sfx_volume=float(self.cfg.get("sfx_volume", 0.9)),
                hit_sounds=True, cancel=self._token,
                want_mips=(layout == "wave")),
            self, cancel=self._token.cancel,
            discard=lambda plan: plan.discard())
        task.ok.connect(self._on_prepared)
        task.ng.connect(self._on_prep_failed)
        self._prep_task = task
        task.start()

    def _prepare_one(self, job):
        """1本ぶんの解析と、出力先・長さの決定。

        音源が見つからない・譜面が読めない等はここで弾いて、失敗として
        次へ進む(30本の途中で止めない)。"""
        try:
            content = read_text(job.path)
        except OSError as exc:
            self._fail(job, "読めません: %s" % exc)
            return None

        course = job.course or None
        preview = self.analyzer.build_preview_timeline(
            content, None, course, branch_level="M")
        if not preview.get("notes") and not preview.get("rolls"):
            self._fail(job, "音符がありません")
            return None

        # OFFSET は preview_data には入っていない(あちらは譜面時間で持つ)。
        # ヘッダから取る — エディタでは OFFSET スピンボックスの値を使うが、
        # Player は譜面を書き換えないのでファイルの値がそのまま正。
        self._offset = float(parse_preview_headers(content).get("offset", 0.0) or 0.0)
        wave = self._find_wave(job.path, content)
        if not wave:
            self._fail(job, "音源(WAVE:)が見つかりません")
            return None

        song_sec = recorder.probe_song_seconds(wave)
        end_sec = recorder.chart_end_seconds(preview, self._offset,
                                             fallback=song_sec)
        if end_sec <= 0.1:
            self._fail(job, "書き出す長さがありません")
            return None

        label = preview.get("course_label") or preview.get("course_key") or ""
        job.course = str(label)
        self._set_row(self._index, job)

        base = os.path.splitext(os.path.basename(job.path))[0]
        if label:
            base += "_" + str(label)
        out_path = os.path.join(self._out_dir(), base + ".mp4")
        out_path = _unique_path(out_path)
        return preview, wave, out_path, end_sec

    @staticmethod
    def _find_wave(tja_path, content):
        """WAVE: の音源を探す。TJA と同じフォルダにある前提。"""
        folder = os.path.dirname(tja_path)
        for line in content.splitlines():
            s = line.split("//")[0].strip()
            if s.upper().startswith("WAVE:"):
                name = s[5:].strip()
                if not name:
                    return ""
                p = name if os.path.isabs(name) else os.path.join(folder, name)
                if os.path.exists(p):
                    return p
                # 拡張子違い(ogg で書いてあるが wav しかない等)も見る。
                stem = os.path.splitext(p)[0]
                for ext in (".ogg", ".wav", ".mp3", ".m4a"):
                    if os.path.exists(stem + ext):
                        return stem + ext
                return ""
        return ""

    def _on_prep_failed(self, msg):
        self._prep_task = None
        job = self.jobs[self._index]
        self._fail(job, str(msg))
        QTimer.singleShot(0, self._next_job)

    def _on_prepared(self, plan):
        self._prep_task = None
        if self._cancel or self._widget is None:
            plan.discard()
            self._finish_all()
            return
        job = self.jobs[self._index]
        if self.cb_layout.currentData() == "wave" and getattr(plan, "mips", None):
            try:
                self._widget.wave.set_mips(plan.mips)
            except Exception:  # noqa: BLE001
                pass
        q = self.cb_quality.currentData()
        try:
            self._rec = recorder.VideoRecording(
                self._widget, job.out_path, plan=plan, canvas=q["canvas"],
                supersample=q["supersample"], preset=q["preset"])
        except Exception as exc:  # noqa: BLE001
            plan.discard()
            self._fail(job, "%s: %s" % (type(exc).__name__, exc))
            QTimer.singleShot(0, self._next_job)
            return
        self._batch = 1
        self._timer.start()

    def _tick(self):
        """1コマずつ描く。1回で描く枚数は実測から調整する。"""
        if self._rec is None or self._cancel:
            self._timer.stop()
            return
        t0 = time.perf_counter()
        try:
            # step() は「**まだ続きがあれば True**」。終わったかどうかでは
            # ない。ここを取り違えていて、最初の数コマで finish() を呼んで
            # しまい、書き出した動画が1秒しか無かった。
            more = self._rec.step(self._batch)
        except Exception as exc:  # noqa: BLE001
            self._timer.stop()
            job = self.jobs[self._index]
            self._fail(job, "%s: %s" % (type(exc).__name__, exc))
            self._rec = None
            QTimer.singleShot(0, self._next_job)
            return
        dt = time.perf_counter() - t0
        # 1回の呼び出しが 30ms 程度に収まるように枚数を調整する。長すぎると
        # 中止ボタンが効かなくなり、短すぎるとタイマーの往復ばかりで遅くなる。
        if dt < 0.02:
            self._batch = min(self._batch + 1, 16)
        elif dt > 0.05:
            self._batch = max(1, self._batch - 1)
        self._update_progress()
        if not more:
            self._timer.stop()
            job = self.jobs[self._index]
            try:
                self._rec.finish()
                job.state = DONE
                job.note = os.path.basename(job.out_path)
            except Exception as exc:  # noqa: BLE001
                self._fail(job, "%s: %s" % (type(exc).__name__, exc))
            self._rec = None
            self._set_row(self._index, job)
            QTimer.singleShot(0, self._next_job)

    def _fail(self, job, note):
        job.state = FAILED
        job.note = note
        self._set_row(self._index, job)

    def _drop_widget(self):
        """画面外の描画用ウィジェットを捨てる。譜面ごとに作り直すので、
        持ち回すと前の譜面のスキンや状態が残る。"""
        if self._widget is not None:
            self._widget.deleteLater()
            self._widget = None

    def _update_progress(self):
        total = len(self.jobs)
        if total == 0:
            return
        frac_in_job = 0.0
        if self._rec is not None and self._rec.total_frames:
            frac_in_job = self._rec.frame / float(self._rec.total_frames)
        done = max(0, self._index)
        self.bar.setValue(int(round((done + frac_in_job) / total * 100)))
        elapsed = time.perf_counter() - self._t0
        eta = ""
        if done + frac_in_job > 0.2:
            rate = elapsed / (done + frac_in_job)
            left = rate * (total - done - frac_in_job)
            eta = "　残り約 %s" % _fmt_duration(left)
        name = os.path.basename(self.jobs[self._index].path) if 0 <= self._index < total else ""
        self.lbl_status.setText("%d / %d　%s%s" % (done + 1, total, name, eta))

    def _finish_all(self):
        self._timer.stop()
        self._drop_widget()
        self._running = False
        self._rec = None
        self._token = None
        self._set_busy(False)
        for job in self.jobs:
            if job.state in (WAITING, RUNNING):
                job.state = SKIPPED if self._cancel else job.state
        self._reload_table()
        ok = sum(1 for j in self.jobs if j.state == DONE)
        ng = sum(1 for j in self.jobs if j.state == FAILED)
        took = time.perf_counter() - self._t0
        self.bar.setValue(100 if not self._cancel else self.bar.value())
        self.lbl_status.setText(
            "%s　完了 %d 件 / 失敗 %d 件　（%s）"
            % ("中止しました。" if self._cancel else "終わりました。",
               ok, ng, _fmt_duration(took)))
        # 終わったら保存先を開く。書き出した動画をすぐ確認したいはずで、
        # 開くまでにフォルダを辿らせる理由が無い。中止したときと、1本も
        # 出来ていないときは開かない(見るものが無い)。
        if ok and not self._cancel:
            self._open_out_dir()
        if ng and not self._cancel:
            # 理由は行の吹き出しにも出るが、それだけだと気づけないので
            # ここでまとめて見せる。
            lines = ["%s: %s" % (os.path.basename(j.path), j.note)
                     for j in self.jobs if j.state == FAILED]
            QMessageBox.information(
                self, "まとめて録画",
                "%d 件が失敗しました。\n\n%s" % (ng, "\n".join(lines[:12])))
        self.finished.emit()


    def _open_out_dir(self):
        """保存先をエクスプローラーで開く。開けなくても録画は成功なので、
        ここで転んでも黙って流す(「開けませんでした」とだけ言われても
        利用者にできることが無い)。"""
        d = self._out_dir()
        if not os.path.isdir(d):
            return
        try:
            if sys.platform == "win32":
                os.startfile(d)          # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception:  # noqa: BLE001
            pass


def _fmt_duration(seconds):
    """所要時間の表示。1分未満を「1分」と出さない(3譜面 5秒でも「1分」と
    出ていて、見積もりとして役に立たなかった)。"""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return "%d 秒" % int(round(seconds))
    if seconds < 3600:
        return "%d 分" % int(round(seconds / 60.0))
    return "%d 時間 %d 分" % (int(seconds // 3600),
                             int(round((seconds % 3600) / 60.0)))


def _unique_path(path):
    """同じ名前があったら _2, _3 … を付ける。上書きしない。"""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists("%s_%d%s" % (stem, i, ext)):
        i += 1
    return "%s_%d%s" % (stem, i, ext)
