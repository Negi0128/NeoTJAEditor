from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from neotja import settings as settings_mod


class SettingsDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.setWindowTitle("環境設定")
        # 画面に収まる高さにする。以前は 760px 固定だったので、画面の小さい環境や
        # 表示スケールが大きい環境ではダイアログが画面からはみ出し、下端の
        # 「保存して適用」ボタンが押せなかった。利用可能な画面高の 88% を上限にし、
        # 中身は下の QScrollArea でスクロールさせる(ボタンは常に見える)。
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(640, max(480, avail.width() - 80)),
                    min(760, max(420, int(avail.height() * 0.88))))

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        tabs.addTab(self._scrollable(self._build_run_tab()), "シミュレータ起動")
        tabs.addTab(self._scrollable(self._build_shortcuts_tab()), "ショートカット")
        tabs.addTab(self._scrollable(self._build_editor_tab()), "エディタ・ツール")
        tabs.addTab(self._scrollable(self._build_audio_tab()), "音声")
        tabs.addTab(self._scrollable(self._build_experimental_tab()), "実験的機能")

        btn_row = QHBoxLayout()
        btn_reset = QPushButton("初期化")
        btn_reset.setObjectName("dangerButton")
        btn_reset.clicked.connect(self._reset)
        btn_save = QPushButton("保存して適用")
        btn_save.setObjectName("accentButton")
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

    @staticmethod
    def _scrollable(inner: QWidget) -> QScrollArea:
        """タブの中身をスクロール可能にする。項目が増えてもダイアログが縦に
        伸び続けず、下端のボタンが画面外へ押し出されない。"""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(inner)
        return area

    def _build_run_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        form.addRow(QLabel("F1〜F3キーで起動するシミュレータの名前とexeパスを設定します。"))

        cfg = self.main_window.config_data["run_config"]
        self.run_entries = {}
        for key in ("F1", "F2", "F3"):
            name_edit = QLineEdit(cfg[key]["name"])
            path_edit = QLineEdit(cfg[key]["path"])
            browse_btn = QPushButton("参照")

            def browse(edit=path_edit):
                p, _ = QFileDialog.getOpenFileName(self, "実行ファイルを選択")
                if p:
                    edit.setText(p)
            browse_btn.clicked.connect(browse)

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(QLabel("パス"))
            row_layout.addWidget(path_edit, 1)
            row_layout.addWidget(browse_btn)

            form.addRow(f"{key} 名前", name_edit)
            form.addRow(row)
            self.run_entries[key] = (name_edit, path_edit)
        return w

    def _build_shortcuts_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        form.addRow(QLabel("Alt + 数字キーを押した際に即座に入力されるカスタムコマンドや文字列を設定します。"))

        self.sc_entries = {}
        shortcuts = self.main_window.config_data["custom_shortcuts"]
        for i in range(10):
            ent = QLineEdit(shortcuts.get(str(i), ""))
            form.addRow(f"Alt + {i}", ent)
            self.sc_entries[str(i)] = ent
        return w

    def _build_editor_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        cfg = self.main_window.config_data

        self.font_family_combo = QComboBox()
        self.font_family_combo.addItems(QFontDatabase.families())
        self.font_family_combo.setCurrentText(cfg.get("font_family", "Consolas"))
        form.addRow("フォント", self.font_family_combo)
        form.addRow(QLabel("エディタの表示に使用するフォントです。"))

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 72)
        self.font_size_spin.setValue(cfg.get("font_size", 12))
        form.addRow("基本フォントサイズ", self.font_size_spin)
        form.addRow(QLabel("起動時の文字サイズです。（エディタ上でCtrl+ホイールでも一時変更可能）"))

        self.resize_ext_check = QCheckBox("リサイズ時に256分以上の分解能をデフォルトで表示")
        self.resize_ext_check.setChecked(cfg.get("resize_ext", False))
        form.addRow(self.resize_ext_check)
        form.addRow(QLabel("リサイズ機能のダイアログを開いた際、256分以上の細かい分解能を最初からリストに表示します。"))

        self.wrap16_combo = QComboBox()
        self.wrap16_combo.addItems(["16", "32", "改行なし"])
        self.wrap16_combo.setCurrentText(str(cfg.get("resize_wrap_16", 16)))
        form.addRow("リサイズ折り返し(16の倍数)", self.wrap16_combo)
        form.addRow(QLabel("16分や32分音符などにリサイズした際、指定文字数で自動改行して視認性を保ちます。"))

        self.wrap12_combo = QComboBox()
        self.wrap12_combo.addItems(["12", "24", "48", "改行なし"])
        self.wrap12_combo.setCurrentText(str(cfg.get("resize_wrap_12", 24)))
        form.addRow("リサイズ折り返し(12の倍数)", self.wrap12_combo)
        form.addRow(QLabel("12分や24分音符などにリサイズした際、指定文字数で自動改行して視認性を保ちます。"))

        self.check_updates_check = QCheckBox("起動時に自動で更新を確認する")
        self.check_updates_check.setChecked(cfg.get("check_updates_on_startup", True))
        form.addRow(self.check_updates_check)

        self.se_text_check = QCheckBox("ゲームプレビューに打音表記(ド/カ)を表示する")
        self.se_text_check.setChecked(cfg.get("se_text_enabled", True))
        form.addRow(self.se_text_check)
        form.addRow(QLabel("ゲーム風プレビューのレーン下段に、各音符の打音(ド/ドン/コ/カ/カッ)を"
                           "自動判定して表示します。判定は PeepoDrumKit と同じアルゴリズムです。"))

        self.note_input_sound_check = QCheckBox("エディタでノーツ文字を入力した際にドン/カツ音を鳴らす")
        self.note_input_sound_check.setChecked(cfg.get("note_input_sound", True))
        form.addRow(self.note_input_sound_check)
        form.addRow(QLabel("譜面本体(#START〜#END)内で1〜9のノーツ文字を打鍵した瞬間に対応する打音を"
                           "即座に鳴らします。ヘッダ/コメントや貼り付け操作では鳴りません。"))

        self.auto_save_check = QCheckBox("自動保存を有効にする")
        self.auto_save_check.setChecked(cfg.get("auto_save_enabled", False))
        form.addRow(self.auto_save_check)
        form.addRow(QLabel("保存先(ファイル)が決まっている場合、変更を一定間隔で自動保存します。"))

        self.comp_combo = QComboBox()
        self.comp_combo.addItems(["通常計算", "段階的補正 (60fps理論値)", "段階的補正 (理論値-1)"])
        self.comp_combo.setCurrentText(cfg.get("short_roll_comp", "段階的補正 (60fps理論値)"))
        form.addRow("0.1秒未満の連打処理", self.comp_combo)

        desc = (
            "極端に短い連打に対するシミュレータの仕様を再現する補正モードです。\n"
            "・通常計算 : 常に (秒数 × 左パネルの連打秒速) で計算します。\n"
            "・60fps理論値 : 0.1秒以下=秒速60、0.15秒以下=秒速55 で計算します。\n"
            "・理論値-1 : 0.1秒以下=秒速55、0.15秒以下=秒速50 で計算します。\n"
            "※設定した「連打秒速」が上記の補正値を上回る場合は、設定値（高い方）が優先されます。"
        )
        lbl = QLabel(desc)
        form.addRow(lbl)

        self.hit_don_edit = QLineEdit(cfg.get("hit_sound_don_path", ""))
        self.hit_don_edit.setReadOnly(True)
        don_browse_btn = QPushButton("参照...")
        don_clear_btn = QPushButton("クリア")

        def browse_don():
            p, _ = QFileDialog.getOpenFileName(self, "ドン音源を選択", "", "音声ファイル (*.wav);;すべて (*)")
            if p:
                self.hit_don_edit.setText(p)
        don_browse_btn.clicked.connect(browse_don)
        don_clear_btn.clicked.connect(lambda: self.hit_don_edit.setText(""))

        don_row = QWidget()
        don_row_layout = QHBoxLayout(don_row)
        don_row_layout.setContentsMargins(0, 0, 0, 0)
        don_row_layout.addWidget(self.hit_don_edit, 1)
        don_row_layout.addWidget(don_browse_btn)
        don_row_layout.addWidget(don_clear_btn)
        form.addRow("ドン音源(WAV)", don_row)

        self.hit_ka_edit = QLineEdit(cfg.get("hit_sound_ka_path", ""))
        self.hit_ka_edit.setReadOnly(True)
        ka_browse_btn = QPushButton("参照...")
        ka_clear_btn = QPushButton("クリア")

        def browse_ka():
            p, _ = QFileDialog.getOpenFileName(self, "カツ音源を選択", "", "音声ファイル (*.wav);;すべて (*)")
            if p:
                self.hit_ka_edit.setText(p)
        ka_browse_btn.clicked.connect(browse_ka)
        ka_clear_btn.clicked.connect(lambda: self.hit_ka_edit.setText(""))

        ka_row = QWidget()
        ka_row_layout = QHBoxLayout(ka_row)
        ka_row_layout.setContentsMargins(0, 0, 0, 0)
        ka_row_layout.addWidget(self.hit_ka_edit, 1)
        ka_row_layout.addWidget(ka_browse_btn)
        ka_row_layout.addWidget(ka_clear_btn)
        form.addRow("カツ音源(WAV)", ka_row)

        form.addRow(QLabel("未指定なら内蔵の合成音が鳴ります。"))

        # 動画書き出し(えぬいーさん次郎の録画ボタン)の保存先の既定。
        # 未指定なら「前回使った場所 → TJA と同じフォルダ」が使われる。
        self.rec_dir_edit = QLineEdit(cfg.get("record_output_dir", ""))
        self.rec_dir_edit.setReadOnly(True)
        rec_browse_btn = QPushButton("参照...")
        rec_clear_btn = QPushButton("クリア")

        def browse_rec_dir():
            p = QFileDialog.getExistingDirectory(
                self, "動画の保存先フォルダを選択", self.rec_dir_edit.text())
            if p:
                self.rec_dir_edit.setText(p)
        rec_browse_btn.clicked.connect(browse_rec_dir)
        rec_clear_btn.clicked.connect(lambda: self.rec_dir_edit.setText(""))

        rec_row = QWidget()
        rec_row_layout = QHBoxLayout(rec_row)
        rec_row_layout.setContentsMargins(0, 0, 0, 0)
        rec_row_layout.addWidget(self.rec_dir_edit, 1)
        rec_row_layout.addWidget(rec_browse_btn)
        rec_row_layout.addWidget(rec_clear_btn)
        form.addRow("動画の保存先", rec_row)
        form.addRow(QLabel("未指定ならTJAと同じフォルダが既定になります。"))
        return w

    def _build_audio_tab(self):
        """出力デバイスの選択と、ワイヤレス調整(出力遅延の補正)。

        音量そのもの(マスター/曲/SE)はプレビュー窓のスライダーが持ち場なので、
        ここには置かない。"""
        w = QWidget()
        form = QFormLayout(w)
        cfg = self.main_window.config_data

        # --- 出力デバイス ---
        from neotja.mixer_engine import list_output_devices
        self.audio_device_combo = QComboBox()
        self.audio_device_combo.addItem("既定のデバイス", "")
        current = cfg.get("audio_output_device", "") or ""
        found = False
        for name, label in list_output_devices():
            self.audio_device_combo.addItem(label, name)
            if name == current:
                found = True
        if current and not found:
            # いま繋がっていない機器が設定に残っている場合。黙って「既定」に
            # 見せると、保存した瞬間に設定が消えてしまうので項目として残す。
            self.audio_device_combo.addItem(f"{current} (見つかりません)", current)
        idx = self.audio_device_combo.findData(current)
        self.audio_device_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("出力デバイス", self.audio_device_combo)
        form.addRow(QLabel("音声の出力先です。「既定のデバイス」ならWindowsの既定に従います。\n"
                           "変更すると保存時に音声出力を開き直します。"
                           "(この設定はミキサー再生方式のときのみ有効です)"))

        reopen_btn = QPushButton("いますぐ音声出力を開き直す")
        reopen_btn.clicked.connect(
            lambda: self.main_window.preview_dock.reopen_audio_output())
        form.addRow(reopen_btn)
        form.addRow(QLabel("ほかのアプリがWASAPI排他モードでデバイスを掴んだ等の理由で音が出なく"
                           "なったときの復帰用です。再生位置・音量・打音の予定はそのまま戻ります。"))

        # --- ワイヤレス調整 ---
        self.wireless_check = QCheckBox("ワイヤレス調整（出力遅延の補正）を有効にする")
        self.wireless_check.setChecked(bool(cfg.get("wireless_offset_enabled", False)))
        form.addRow(self.wireless_check)

        self.wireless_spin = QDoubleSpinBox()
        self.wireless_spin.setRange(-500.0, 500.0)
        self.wireless_spin.setDecimals(1)
        self.wireless_spin.setSingleStep(5.0)
        self.wireless_spin.setSuffix(" ms")
        self.wireless_spin.setValue(float(cfg.get("wireless_offset_ms", 0.0) or 0.0))
        form.addRow("補正値", self.wireless_spin)
        form.addRow(QLabel(
            "Bluetoothイヤホンなどで音が遅れて聞こえるぶんを打ち消します。曲・打音・"
            "メトロノームすべてに一律で効き、既存の打音レイテンシ補正とは別に足されます。\n"
            "正の値 = 音がそのミリ秒だけ遅れて耳に届くとみなす、という意味です。譜面が"
            "見た目より遅れて聞こえるなら値を増やしてください。"))
        return w

    def _build_experimental_tab(self):
        """まだ様子見の機能をまとめて置くタブ。既定は全部オフ、有効化しても
        すぐには反映されずアプリの再起動が要るものが多い(この点は各項目の
        説明文で個別に断る)。"""
        w = QWidget()
        form = QFormLayout(w)
        cfg = self.main_window.config_data

        self.peepo_chart_edit_check = QCheckBox("Peepo式作譜（実験的）")
        self.peepo_chart_edit_check.setChecked(cfg.get("peepo_chart_edit", False))
        form.addRow(self.peepo_chart_edit_check)
        form.addRow(QLabel("譜面プレビューの下部パネルに、音符を直接置ける「作譜」モードを"
                           "追加します。※反映にはアプリの再起動が必要です。"))
        return w

    def _save(self):
        cfg = self.main_window.config_data
        for k, (name_edit, path_edit) in self.run_entries.items():
            cfg["run_config"][k]["name"] = name_edit.text()
            cfg["run_config"][k]["path"] = path_edit.text()
        for k, ent in self.sc_entries.items():
            cfg["custom_shortcuts"][k] = ent.text()

        cfg["font_family"] = self.font_family_combo.currentText()
        cfg["font_size"] = self.font_size_spin.value()
        cfg["resize_ext"] = self.resize_ext_check.isChecked()

        w16 = self.wrap16_combo.currentText()
        cfg["resize_wrap_16"] = int(w16) if w16.isdigit() else "改行なし"
        w12 = self.wrap12_combo.currentText()
        cfg["resize_wrap_12"] = int(w12) if w12.isdigit() else "改行なし"

        cfg["short_roll_comp"] = self.comp_combo.currentText()
        cfg["check_updates_on_startup"] = self.check_updates_check.isChecked()
        cfg["auto_save_enabled"] = self.auto_save_check.isChecked()
        cfg["se_text_enabled"] = self.se_text_check.isChecked()
        cfg["note_input_sound"] = self.note_input_sound_check.isChecked()

        cfg["record_output_dir"] = self.rec_dir_edit.text()
        cfg["hit_sound_don_path"] = self.hit_don_edit.text()
        cfg["hit_sound_ka_path"] = self.hit_ka_edit.text()
        cfg["peepo_chart_edit"] = self.peepo_chart_edit_check.isChecked()

        cfg["audio_output_device"] = self.audio_device_combo.currentData() or ""
        cfg["wireless_offset_enabled"] = self.wireless_check.isChecked()
        cfg["wireless_offset_ms"] = float(self.wireless_spin.value())
        self.accept()

    def _reset(self):
        ans = QMessageBox.question(self, "確認", "すべての環境設定を初期化しますか？")
        if ans != QMessageBox.Yes:
            return
        self.main_window.config_data.clear()
        self.main_window.config_data.update(settings_mod.default_settings())
        self.accept()
