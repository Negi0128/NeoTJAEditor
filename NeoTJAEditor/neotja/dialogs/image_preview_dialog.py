from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QGraphicsScene, QGraphicsView, QHBoxLayout,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from neotja import settings as settings_mod
from neotja.tja_image_export import generate_chart_image, load_sprites


class ChartGraphicsView(QGraphicsView):
    """Alt+ホイールで拡大縮小できる表示(素のホイールはスクロール)。倍率には
    上限/下限を設ける。

    以前は無制限に scale() していたため、縮小しすぎて画像が点のように消えたり、
    拡大しすぎて位置を見失ったりした。等倍(1.0)を基準に MIN_ZOOM〜MAX_ZOOM の
    範囲でしか変化しないようにする。"""

    MIN_ZOOM = 0.25     # これ以上は縮小しない
    MAX_ZOOM = 4.0      # これ以上は拡大しない

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._zoom = 1.0

    def reset_zoom(self):
        """新しい画像を表示したときに倍率を等倍へ戻す。"""
        self.resetTransform()
        self._zoom = 1.0

    # 拡大縮小の修飾キー。Alt は Windows 側(メニュー起動・IME・マウスユーティ
    # リティ)に取られて届かないことがあるので、Ctrl でも同じことができる。
    ZOOM_MODIFIERS = Qt.AltModifier | Qt.ControlModifier

    def wheelEvent(self, event):
        # ホイールは素直にスクロール、拡大縮小は Alt(または Ctrl)+ホイール。
        # 譜面画像は縦に長いので、見たい所まで送る操作のほうが拡大縮小より多い。
        if not (event.modifiers() & self.ZOOM_MODIFIERS):
            super().wheelEvent(event)
            return
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        new_zoom = self._zoom * factor
        # 範囲外なら、範囲の端までに切り詰めて適用する(端で止まる)。
        if new_zoom > self.MAX_ZOOM:
            factor = self.MAX_ZOOM / self._zoom
            new_zoom = self.MAX_ZOOM
        elif new_zoom < self.MIN_ZOOM:
            factor = self.MIN_ZOOM / self._zoom
            new_zoom = self.MIN_ZOOM
        if abs(factor - 1.0) < 1e-9:
            event.accept()
            return
        self._zoom = new_zoom
        self.scale(factor, factor)
        event.accept()


class _ImagePreviewBase(QDialog):
    """譜面画像プレビューの共通土台(スタイル選択・プレビュー表示・保存)。

    「現在の譜面」用と「自由入力」用は別ウィンドウに分けてあり、どちらも
    この土台を使う。派生側は _build_options_row() で上部の行を組み立て、
    _render_image() で PIL 画像を返す。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.img = None
        self._save_basename = "譜面画像"

        layout = QVBoxLayout(self)

        # --- 上部の行: 左にスタイル、右に派生側のオプション ---
        top = QHBoxLayout()
        top.addWidget(QLabel("スタイル:"))
        self.style_combo = QComboBox()
        self.style_combo.addItem("デフォルト", "default")
        self.style_combo.addItem("本家風", "honke")
        self.style_combo.currentIndexChanged.connect(self._request_regen)
        top.addWidget(self.style_combo)
        top.addStretch()                      # ← これで派生側の行は右寄せになる
        self._build_options_row(top)
        layout.addLayout(top)

        self._build_extra(layout)

        self.lbl_status = QLabel("画像生成中...")
        layout.addWidget(self.lbl_status)

        self.view = ChartGraphicsView()
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.scene = QGraphicsScene()
        self.view.setScene(self.scene)
        self.view.hide()
        layout.addWidget(self.view, 1)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("閉じる")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        self._build_footer(btn_row)
        btn_row.addStretch()
        self.btn_save = QPushButton("この画像を保存")
        self.btn_save.setObjectName("accentButton")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._save_image)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

        QTimer.singleShot(100, self._generate_and_show)

    # --- 派生側で差し替えるフック ---
    def _build_options_row(self, row):
        pass

    def _build_extra(self, layout):
        pass

    def _build_footer(self, row):
        pass

    def _render_image(self, style, sprites):
        raise NotImplementedError

    # --- 共通処理 ---
    def _request_regen(self, *_a):
        self.lbl_status.setText("画像生成中...")
        self.lbl_status.show()
        self.view.hide()
        self.btn_save.setEnabled(False)
        QTimer.singleShot(50, self._generate_and_show)

    def _generate_and_show(self):
        try:
            style = self.style_combo.currentData()
            sprites = load_sprites(settings_mod.notes_png_path())
            self.img = self._render_image(style, sprites)
            pixmap = QPixmap.fromImage(ImageQt(self.img))
            self.scene.clear()
            self.scene.addPixmap(pixmap)
            self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
            self.view.reset_zoom()   # 再生成のたびに等倍へ戻す(倍率が残らない)
            self.lbl_status.hide()
            self.view.show()
            self.btn_save.setEnabled(True)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self.view.hide()
            self.lbl_status.setText(f"エラーが発生しました: {str(e)}")
            self.lbl_status.show()

    def _save_image(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存", f"{self._save_basename}.png", "PNG Image (*.png)")
        if path:
            self.img.save(path)
            QMessageBox.information(self, "成功", "譜面画像を保存しました。")
            self.accept()


class TJAImagePreviewDialog(_ImagePreviewBase):
    """開いている譜面を画像化するウィンドウ。小節範囲は上部の右端に置く。"""

    def __init__(self, main_window, content, selected_label, parent=None):
        self.main_window = main_window
        self.content = content
        self.selected_label = selected_label
        super().__init__(parent or main_window)
        self.setWindowTitle("譜面画像プレビュー")
        self.resize(1000, 700)

    def _build_options_row(self, row):
        # 一番上の行の右端に小節範囲を置く。「先頭/末尾」ではなく実際の小節番号を
        # 最初から入れておき(1 〜 最終小節)、値を変えたらボタンを押さなくても
        # そのまま再生成する(要望)。
        n = self._measure_count()
        row.addWidget(QLabel("小節範囲:"))
        self.spin_from = QSpinBox()
        self.spin_from.setRange(1, max(1, n))
        self.spin_from.setValue(1)
        self.spin_from.setToolTip("開始小節")
        self.spin_from.setKeyboardTracking(False)   # 入力途中で走らせない
        row.addWidget(self.spin_from)
        row.addWidget(QLabel("〜"))
        self.spin_to = QSpinBox()
        self.spin_to.setRange(1, max(1, n))
        self.spin_to.setValue(max(1, n))
        self.spin_to.setToolTip("終了小節")
        self.spin_to.setKeyboardTracking(False)
        row.addWidget(self.spin_to)
        row.addWidget(QLabel(f"/ 全{max(1, n)}小節"))
        # 値が変わったら即再生成(ボタン不要)。
        self.spin_from.valueChanged.connect(self._on_range_changed)
        self.spin_to.valueChanged.connect(self._on_range_changed)

    def _measure_count(self) -> int:
        """選択中コースの小節数(カンマ区切りの数)。取得できなければ 1。"""
        try:
            courses = self.main_window.analyzer.parse_courses(self.content)
            target = next((c for c in courses if c["label"] == self.selected_label), None)
            if target is None:
                return 1
            return max(1, sum(line.count(",") for line in target["data"]))
        except Exception:
            return 1

    def _on_range_changed(self, *_a):
        # 開始 > 終了 にならないよう相互に押さえてから再生成する。
        if self.spin_from.value() > self.spin_to.value():
            if self.sender() is self.spin_from:
                self.spin_to.blockSignals(True)
                self.spin_to.setValue(self.spin_from.value())
                self.spin_to.blockSignals(False)
            else:
                self.spin_from.blockSignals(True)
                self.spin_from.setValue(self.spin_to.value())
                self.spin_from.blockSignals(False)
        self._request_regen()

    def _build_footer(self, row):
        # 自由入力は別ウィンドウ。ここから開けるようにしておく。
        btn = QPushButton("自由入力で作成...")
        btn.clicked.connect(self._open_free_window)
        row.addWidget(btn)

    def _open_free_window(self):
        dlg = FreeChartImageDialog(self.main_window, parent=self)
        dlg.show()          # モーダルにせず別ウィンドウとして開く

    def _render_image(self, style, sprites):
        courses = self.main_window.analyzer.parse_courses(self.content)
        m_from = self.spin_from.value()
        m_to = self.spin_to.value()
        n = self.spin_to.maximum()
        full = (m_from <= 1 and m_to >= n)      # 全体ならフィルタを掛けない
        img = generate_chart_image(
            self.content, self.selected_label, courses, sprites, style=style,
            measure_from=None if full else m_from,
            measure_to=None if full else m_to,
        )
        title = next((l[6:].strip() for l in self.content.split('\n')
                      if l.startswith("TITLE:")), "No Title")
        rng = "" if full else f"_{m_from}-{m_to}"
        self._save_basename = f"{title}_{self.selected_label}{rng}"
        return img


class FreeChartImageDialog(_ImagePreviewBase):
    """譜面本文を直接書いて画像化する別ウィンドウ(ミニエディタ)。"""

    def __init__(self, main_window, parent=None):
        self.main_window = main_window
        super().__init__(parent or main_window)
        self.setWindowTitle("譜面画像 - 自由入力")
        self.resize(1000, 720)
        self._save_basename = "譜面画像"

    def _build_options_row(self, row):
        row.addWidget(QLabel("BPM:"))
        self.spin_bpm = QSpinBox()
        self.spin_bpm.setRange(1, 1000)
        self.spin_bpm.setValue(150)
        row.addWidget(self.spin_bpm)
        row.addWidget(QLabel("拍子:"))
        self.spin_num = QSpinBox()
        self.spin_num.setRange(1, 32)
        self.spin_num.setValue(4)
        row.addWidget(self.spin_num)
        row.addWidget(QLabel("/"))
        self.spin_den = QSpinBox()
        self.spin_den.setRange(1, 32)
        self.spin_den.setValue(4)
        row.addWidget(self.spin_den)
        btn = QPushButton("生成")
        btn.setObjectName("accentButton")
        btn.clicked.connect(self._request_regen)
        row.addWidget(btn)

    def _build_extra(self, layout):
        self.free_edit = QPlainTextEdit()
        self.free_edit.setPlainText("1011,\n2022,\n5008,\n7008,")
        self.free_edit.setPlaceholderText(
            "譜面本文をそのまま書きます(1行=1小節、末尾に , )。\n"
            "例) 1122,  1010,  5008,(連打)  7008,(風船)  #BPMCHANGE 200 も使えます。")
        self.free_edit.setFixedHeight(140)
        layout.addWidget(self.free_edit)
        layout.addWidget(QLabel("※ 風船の打数は BALLOON: 行を書くか、既定 8 が入ります。"))

    def _free_content(self) -> str:
        """入力本文から、エクスポータに渡せる最小限の TJA を組む。"""
        lines = self.free_edit.toPlainText().strip("\n").split("\n")
        has_balloon = any(l.strip().upper().startswith("BALLOON:") for l in lines)
        n_balloon = sum(l.count("7") + l.count("9")
                        for l in lines if not l.strip().startswith("#"))
        head = ["TITLE:譜面画像", f"BPM:{self.spin_bpm.value()}", "WAVE:dummy.wav"]
        if not has_balloon and n_balloon:
            head.append("BALLOON:" + ",".join(["8"] * n_balloon))
        head += ["COURSE:Oni", "LEVEL:9", "#START"]
        meas = f"#MEASURE {self.spin_num.value()}/{self.spin_den.value()}"
        return "\n".join(head + [meas] + lines + ["#END"]) + "\n"

    def _render_image(self, style, sprites):
        content = self._free_content()
        courses = self.main_window.analyzer.parse_courses(content)
        if not courses:
            raise ValueError("譜面を読み取れませんでした。本文を確認してください。")
        self._save_basename = "譜面画像"
        return generate_chart_image(content, courses[0]["label"], courses, sprites, style=style)
