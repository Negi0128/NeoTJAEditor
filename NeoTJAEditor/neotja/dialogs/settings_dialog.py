from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from neotja import settings as settings_mod


class SettingsDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.setWindowTitle("環境設定")
        self.resize(640, 760)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        tabs.addTab(self._build_run_tab(), "シミュレータ起動")
        tabs.addTab(self._build_shortcuts_tab(), "ショートカット")
        tabs.addTab(self._build_editor_tab(), "エディタ・ツール")

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

        cfg["hit_sound_don_path"] = self.hit_don_edit.text()
        cfg["hit_sound_ka_path"] = self.hit_ka_edit.text()
        self.accept()

    def _reset(self):
        ans = QMessageBox.question(self, "確認", "すべての環境設定を初期化しますか？")
        if ans != QMessageBox.Yes:
            return
        self.main_window.config_data.clear()
        self.main_window.config_data.update(settings_mod.default_settings())
        self.accept()
