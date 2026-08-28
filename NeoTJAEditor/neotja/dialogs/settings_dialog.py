from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from neotja import settings as settings_mod
from neotja import theme as theme_mod


class SettingsDialog(QDialog):
    # 「初期化」で既定へ戻すキー。この画面に入力欄がある項目だけを並べる
    # (_save が書き込むキーと同じ顔ぶれ)。ここに無いもの — recent_files,
    # window_geometry, splitter_state, 各種音量, theme, roll_speed など — は
    # 別の場所で決まる状態なので、環境設定の初期化では触らない。
    # system_dir(素材の在りか)もあえて外してある。空へ戻すと次の起動で
    # 素材が見つからず「フォルダを選んでください」からやり直しになり、
    # 初期化ボタンが起動できない状態を作ってしまうため。
    _RESET_KEYS = (
        "run_config", "custom_shortcuts",
        "font_family", "font_size",
        "resize_ext", "resize_wrap_16", "resize_wrap_12",
        "short_roll_comp", "check_updates_on_startup", "auto_save_enabled",
        "se_text_enabled", "note_input_sound",
        "record_output_dir",
        "hit_sound_don_path", "hit_sound_ka_path",
        "audio_output_device", "wireless_offset_enabled", "wireless_offset_ms",
        "peepo_chart_edit",
    )

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
        # 他のダイアログと同じく「キャンセル」を右下の左隣に置く。これが無い
        # せいで、値をいじってから止めたいときに閉じるボタンしか手が無く、
        # 「閉じたら保存されるのか」が分からなかった。押しても _save を通らない
        # ので reject() = config_data には一切触れずに閉じる。
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("保存して適用")
        btn_save.setObjectName("accentButton")
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # レイアウト用の小道具
    # ------------------------------------------------------------------

    @staticmethod
    def _scrollable(inner: QWidget) -> QScrollArea:
        """タブの中身をスクロール可能にする。項目が増えてもダイアログが縦に
        伸び続けず、下端のボタンが画面外へ押し出されない。"""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(inner)
        return area

    @staticmethod
    def _tab_body():
        """タブ1枚ぶんの器を作る。中身は QGroupBox を縦に積んでいく形にして
        いるので、下端に伸縮を足して枠が間延びしないようにする(返す側で
        addStretch すること)。"""
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)
        return w, outer

    @staticmethod
    def _group(outer: QVBoxLayout, title: str) -> QFormLayout:
        """見出し付きの枠を1つ足し、その中身のフォームを返す。関連する設定を
        枠でくくることで、以前のように何十行も同じ調子で縦に並ぶのを避ける。
        フォームにしてあるのでラベルの左端と入力欄の左端が枠ごとに揃う。"""
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        # ラベルが長くても勝手に上下2段へ折り返させない(段差ができて逆に読み
        # づらいため)。横に入りきらないぶんは入力欄側が縮む。
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        outer.addWidget(box)
        return form

    @staticmethod
    def _hint(text: str) -> QLabel:
        """項目に添える補足説明。設定そのものと見分けが付くよう一段小さい
        グレー文字にし、折り返しを有効にする。以前は説明も普通のラベルだった
        ので、どれが設定でどれが説明なのか一目で分からず、長い説明はそのまま
        横に伸びてタブが横スクロールしていた。"""
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            "color: %s; font-size: 11px;" % theme_mod.COLORS.get("fg_dim", "#6c757d"))
        return lbl

    def _path_row(self, edit: QLineEdit, on_browse, with_clear: bool = True) -> QWidget:
        """[入力欄][参照...][クリア] を横一列にまとめた行。ファイル/フォルダを
        選ぶ設定が4か所あり、それぞれ手書きしていたので幅も並びも揃って
        いなかった。"""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(edit, 1)
        browse_btn = QPushButton("参照...")
        browse_btn.clicked.connect(on_browse)
        row_layout.addWidget(browse_btn)
        if with_clear:
            clear_btn = QPushButton("クリア")
            clear_btn.clicked.connect(lambda: edit.setText(""))
            row_layout.addWidget(clear_btn)
        return row

    @staticmethod
    def _bind_enabled(parent_check: QCheckBox, *children):
        """親のチェックが外れている間、ぶら下がる設定をグレーアウトする。
        効かない値をいじれてしまうと「設定が悪いのか、そもそも無効なのか」が
        分からなくなるため。"""
        def sync(on):
            for c in children:
                c.setEnabled(on)
        parent_check.toggled.connect(sync)
        sync(parent_check.isChecked())

    # ------------------------------------------------------------------
    # 各タブ
    # ------------------------------------------------------------------

    def _build_run_tab(self):
        w, outer = self._tab_body()
        outer.addWidget(self._hint(
            "F2・F3キーで起動する外部シミュレータの名前とexeパスを設定します。"
            "F1は内蔵プレビュー(えぬいーさん次郎)に固定されています。"))

        cfg = self.main_window.config_data["run_config"]

        # F1 は main_window 側で内蔵プレビュー固定になっており、ここで exe を
        # 指定しても起動しない(メニューのラベル更新でも F1 だけ飛ばしている)。
        # 以前は F2・F3 と同じ入力欄を並べていたので、指定して保存しても何も
        # 起きず、原因も分からなかった。欄自体を出さず、そう決まっていることを
        # 書いておく。
        form = self._group(outer, "F1 キーで起動")
        form.addRow(self._hint(
            "内蔵プレビュー(えぬいーさん次郎)で固定です。変更できません。\n"
            "外部シミュレータを使いたい場合は F2・F3 に設定してください。"))

        self.run_entries = {}
        for key in ("F2", "F3"):
            # キーごとに枠を分ける。以前は名前とパスが同じフォームに続けて
            # 並ぶだけで、どの行がどのキーのものか目で追う必要があった。
            form = self._group(outer, "%s キーで起動" % key)
            name_edit = QLineEdit(cfg[key]["name"])
            name_edit.setToolTip("メニューに表示される名前です。")
            path_edit = QLineEdit(cfg[key]["path"])
            path_edit.setToolTip("起動する実行ファイル(.exe)のパスです。")

            def browse(edit=path_edit):
                p, _ = QFileDialog.getOpenFileName(self, "実行ファイルを選択")
                if p:
                    edit.setText(p)

            form.addRow("名前", name_edit)
            form.addRow("パス", self._path_row(path_edit, browse, with_clear=False))
            self.run_entries[key] = (name_edit, path_edit)
        outer.addStretch()
        return w

    def _build_shortcuts_tab(self):
        w, outer = self._tab_body()
        outer.addWidget(self._hint(
            "Alt + 数字キーを押した際に即座に入力されるカスタムコマンドや文字列を設定します。"))

        # 10行を素直に縦へ並べるとスクロールが必要になるので、5行ずつの2列に
        # する。1画面に収まれば「どこが空いているか」が一目で分かる。
        box = QGroupBox("Alt + 数字キー")
        cols = QHBoxLayout(box)
        left = QFormLayout()
        right = QFormLayout()
        for f in (left, right):
            f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        outer.addWidget(box)

        self.sc_entries = {}
        shortcuts = self.main_window.config_data["custom_shortcuts"]
        for i in range(10):
            ent = QLineEdit(shortcuts.get(str(i), ""))
            (left if i < 5 else right).addRow("Alt + %d" % i, ent)
            self.sc_entries[str(i)] = ent
        outer.addStretch()
        return w

    def _build_editor_tab(self):
        w, outer = self._tab_body()
        cfg = self.main_window.config_data

        # --- 表示 ---
        form = self._group(outer, "表示")
        self.font_family_combo = QComboBox()
        self.font_family_combo.addItems(QFontDatabase.families())
        self.font_family_combo.setCurrentText(cfg.get("font_family", "Consolas"))
        self.font_family_combo.setToolTip("エディタの表示に使用するフォントです。")
        form.addRow("フォント", self.font_family_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 72)
        self.font_size_spin.setValue(cfg.get("font_size", 12))
        self.font_size_spin.setToolTip("起動時の文字サイズです。")
        form.addRow("基本フォントサイズ", self.font_size_spin)
        form.addRow(self._hint("起動時の文字サイズです。エディタ上では Ctrl+ホイール でも一時的に変えられます。"))

        # --- リサイズ ---
        # 分解能・折り返しは「リサイズ機能まわり」で一続きの話なのでまとめる。
        form = self._group(outer, "リサイズ")
        self.resize_ext_check = QCheckBox("256分以上の分解能をデフォルトで表示")
        self.resize_ext_check.setChecked(cfg.get("resize_ext", False))
        self.resize_ext_check.setToolTip(
            "リサイズのダイアログを開いた際、256分以上の細かい分解能を最初からリストに出します。")
        form.addRow(self.resize_ext_check)

        self.wrap16_combo = QComboBox()
        self.wrap16_combo.addItems(["16", "32", "改行なし"])
        self.wrap16_combo.setCurrentText(str(cfg.get("resize_wrap_16", 16)))
        self.wrap16_combo.setToolTip("16分や32分音符などにリサイズした際の自動改行の文字数です。")
        form.addRow("折り返し(16の倍数)", self.wrap16_combo)

        self.wrap12_combo = QComboBox()
        self.wrap12_combo.addItems(["12", "24", "48", "改行なし"])
        self.wrap12_combo.setCurrentText(str(cfg.get("resize_wrap_12", 24)))
        self.wrap12_combo.setToolTip("12分や24分音符などにリサイズした際の自動改行の文字数です。")
        form.addRow("折り返し(12の倍数)", self.wrap12_combo)
        form.addRow(self._hint("リサイズ後に指定文字数で自動改行し、1行が長くなりすぎないようにします。"))

        # --- 編集 ---
        form = self._group(outer, "編集")
        self.note_input_sound_check = QCheckBox("ノーツ文字を入力した際にドン/カツ音を鳴らす")
        self.note_input_sound_check.setChecked(cfg.get("note_input_sound", True))
        form.addRow(self.note_input_sound_check)
        form.addRow(self._hint("譜面本体(#START〜#END)内で1〜9のノーツ文字を打鍵した瞬間に対応する打音を"
                               "即座に鳴らします。ヘッダ/コメントや貼り付け操作では鳴りません。"))

        self.auto_save_check = QCheckBox("自動保存を有効にする")
        self.auto_save_check.setChecked(cfg.get("auto_save_enabled", False))
        form.addRow(self.auto_save_check)
        form.addRow(self._hint("保存先(ファイル)が決まっている場合、編集の手が止まってから約0.6秒後に"
                               "自動保存します。"))

        # --- 譜面プレビュー ---
        form = self._group(outer, "譜面プレビュー")
        self.se_text_check = QCheckBox("打音表記(ド/カ)を表示する")
        self.se_text_check.setChecked(cfg.get("se_text_enabled", True))
        form.addRow(self.se_text_check)
        form.addRow(self._hint("ゲーム風プレビューのレーン下段に、各音符の打音(ド/ドン/コ/カ/カッ)を"
                               "自動判定して表示します。判定は PeepoDrumKit と同じアルゴリズムです。"))

        # --- 連打の計算 ---
        form = self._group(outer, "連打の計算")
        self.comp_combo = QComboBox()
        self.comp_combo.addItems(["通常計算", "段階的補正 (60fps理論値)", "段階的補正 (理論値-1)"])
        self.comp_combo.setCurrentText(cfg.get("short_roll_comp", "段階的補正 (60fps理論値)"))
        self.comp_combo.setToolTip("極端に短い連打に対するシミュレータの仕様を再現する補正モードです。")
        form.addRow("0.1秒未満の連打処理", self.comp_combo)
        form.addRow(self._hint(
            "極端に短い連打に対するシミュレータの仕様を再現する補正モードです。\n"
            "・通常計算 : 常に (秒数 × 左パネルの連打秒速) で計算します。\n"
            "・60fps理論値 : 0.1秒以下=秒速60、0.15秒以下=秒速55 で計算します。\n"
            "・理論値-1 : 0.1秒以下=秒速55、0.15秒以下=秒速50 で計算します。\n"
            "※設定した「連打秒速」が上記の補正値を上回る場合は、設定値（高い方）が優先されます。"))

        # --- 動画書き出し ---
        # 動画書き出し(えぬいーさん次郎の録画ボタン)の保存先の既定。
        # 未指定なら「前回使った場所 → TJA と同じフォルダ」が使われる。
        # 「前回使った場所」は record_last_dir に分けてあるので、ここで指定した
        # 場所が書き出しのたびに勝手に書き換わることはない。
        form = self._group(outer, "動画書き出し")
        self.rec_dir_edit = QLineEdit(cfg.get("record_output_dir", ""))
        self.rec_dir_edit.setReadOnly(True)

        def browse_rec_dir():
            p = QFileDialog.getExistingDirectory(
                self, "動画の保存先フォルダを選択", self.rec_dir_edit.text())
            if p:
                self.rec_dir_edit.setText(p)

        form.addRow("保存先", self._path_row(self.rec_dir_edit, browse_rec_dir))
        form.addRow(self._hint("ここで指定した場所が常に既定になります。未指定のときだけ"
                               "「前回書き出した場所 → TJAと同じフォルダ」が使われます。"))

        # --- 素材(System フォルダ) ---
        # 音符・背景・打音の出どころ。起動時にここから素材を取り出して
        # キャッシュへ展開する(neotja/skin_cache.py)ので、変えたぶんが
        # 反映されるのは次の起動から。未指定なら exe の隣などを自動で探す。
        form = self._group(outer, "素材(System フォルダ)")
        self.system_dir_edit = QLineEdit(cfg.get("system_dir", ""))
        self.system_dir_edit.setReadOnly(True)

        def browse_system_dir():
            p = QFileDialog.getExistingDirectory(
                self, "TNDE の System フォルダを選択", self.system_dir_edit.text())
            if not p:
                return
            # 妥当性はここで見る。保存してから次の起動で怒られるより、
            # 選んだその場で言われたほうが直しやすい。
            from neotja import skin_cache
            if not skin_cache.is_valid_system_dir(p):
                QMessageBox.warning(
                    self, "System フォルダではありません",
                    "選ばれたフォルダの中に TNDE-R\\Graphics と TNDE-R\\Sounds が"
                    "見つかりません。\n「System」という階層を選んでください。")
                return
            self.system_dir_edit.setText(p)

        form.addRow("System フォルダ", self._path_row(self.system_dir_edit,
                                                     browse_system_dir))
        form.addRow(self._hint(
            "音符・背景・打音などの素材は、TNDE に付属する System フォルダから"
            "読み込みます(素材は再配布できないためアプリには同梱していません)。"
            "未指定のときは exe と同じ場所やデスクトップ配下を自動で探します。"
            "※変更の反映にはアプリの再起動が必要です。"))

        # 展開のやり直しは自動でも起きる(System の中身が変わったときや、
        # キャッシュのファイルが消えているとき)が、それでも直らないときの
        # 逃げ道が UI に1つも無かった。キャッシュフォルダを自分で探して消す
        # しか手が無い、という状態は利用者に強いるものではない。
        rebuild_btn = QPushButton("素材を再展開する")
        rebuild_btn.clicked.connect(self._rebuild_skin_cache)
        form.addRow("", rebuild_btn)
        form.addRow(self._hint(
            "上の System フォルダから素材を取り出し直します(数秒かかります)。"
            "絵や音がおかしいとき、素材を差し替えても反映されないときに"
            "お試しください。"))

        # --- その他 ---
        form = self._group(outer, "その他")
        self.check_updates_check = QCheckBox("起動時に自動で更新を確認する")
        self.check_updates_check.setChecked(cfg.get("check_updates_on_startup", True))
        form.addRow(self.check_updates_check)

        outer.addStretch()
        return w

    def _build_audio_tab(self):
        """出力デバイスの選択、打音の音源、ワイヤレス調整(出力遅延の補正)。

        音量そのもの(マスター/曲/SE)はプレビュー窓のスライダーが持ち場なので、
        ここには置かない。打音の音源(WAV)は以前「エディタ・ツール」タブにあり、
        音の設定を探して2つのタブを行き来する必要があったのでこちらへ移した
        (設定キーは同じ hit_sound_don_path / hit_sound_ka_path)。"""
        w, outer = self._tab_body()
        cfg = self.main_window.config_data

        # --- 出力デバイス ---
        from neotja.mixer_engine import list_output_devices
        form = self._group(outer, "出力デバイス")
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
            self.audio_device_combo.addItem("%s (見つかりません)" % current, current)
        idx = self.audio_device_combo.findData(current)
        self.audio_device_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.audio_device_combo.setToolTip(
            "音声の出力先です。この設定はミキサー再生方式のときのみ有効です。")
        form.addRow("出力先", self.audio_device_combo)
        form.addRow(self._hint("「既定のデバイス」ならWindowsの既定に従います。変更すると保存時に"
                               "音声出力を開き直します。(ミキサー再生方式のときのみ有効です)"))

        reopen_btn = QPushButton("いますぐ音声出力を開き直す")
        reopen_btn.clicked.connect(
            lambda: self.main_window.preview_dock.reopen_audio_output())
        form.addRow(reopen_btn)
        form.addRow(self._hint("ほかのアプリがWASAPI排他モードでデバイスを掴んだ等の理由で音が出なく"
                               "なったときの復帰用です。再生位置・音量・打音の予定はそのまま戻ります。"))

        # --- 打音の音源 ---
        form = self._group(outer, "打音の音源")
        self.hit_don_edit = QLineEdit(cfg.get("hit_sound_don_path", ""))
        self.hit_don_edit.setReadOnly(True)

        def browse_don():
            p, _ = QFileDialog.getOpenFileName(self, "ドン音源を選択", "", "音声ファイル (*.wav);;すべて (*)")
            if p:
                self.hit_don_edit.setText(p)

        form.addRow("ドン(WAV)", self._path_row(self.hit_don_edit, browse_don))

        self.hit_ka_edit = QLineEdit(cfg.get("hit_sound_ka_path", ""))
        self.hit_ka_edit.setReadOnly(True)

        def browse_ka():
            p, _ = QFileDialog.getOpenFileName(self, "カツ音源を選択", "", "音声ファイル (*.wav);;すべて (*)")
            if p:
                self.hit_ka_edit.setText(p)

        form.addRow("カツ(WAV)", self._path_row(self.hit_ka_edit, browse_ka))
        form.addRow(self._hint("未指定なら内蔵の合成音が鳴ります。"))

        # --- ワイヤレス調整 ---
        form = self._group(outer, "ワイヤレス調整（出力遅延の補正）")
        self.wireless_check = QCheckBox("ワイヤレス調整を有効にする")
        self.wireless_check.setChecked(bool(cfg.get("wireless_offset_enabled", False)))
        form.addRow(self.wireless_check)

        self.wireless_spin = QDoubleSpinBox()
        self.wireless_spin.setRange(-500.0, 500.0)
        self.wireless_spin.setDecimals(1)
        self.wireless_spin.setSingleStep(5.0)
        self.wireless_spin.setSuffix(" ms")
        self.wireless_spin.setValue(float(cfg.get("wireless_offset_ms", 0.0) or 0.0))
        self.wireless_spin.setToolTip("正の値 = 音がそのミリ秒だけ遅れて耳に届くとみなします。")
        wireless_label = QLabel("補正値")
        form.addRow(wireless_label, self.wireless_spin)
        # 補正値は上のチェックが入っているときだけ効くので、OFFの間は
        # ラベルごとグレーアウトして「いま効いていない」ことを見せる。
        self._bind_enabled(self.wireless_check, wireless_label, self.wireless_spin)
        form.addRow(self._hint(
            "Bluetoothイヤホンなどで音が遅れて聞こえるぶんを打ち消します。曲・打音・"
            "メトロノームすべてに一律で効き、既存の打音レイテンシ補正とは別に足されます。\n"
            "正の値 = 音がそのミリ秒だけ遅れて耳に届くとみなす、という意味です。譜面が"
            "見た目より遅れて聞こえるなら値を増やしてください。"))

        outer.addStretch()
        return w

    def _build_experimental_tab(self):
        """まだ様子見の機能をまとめて置くタブ。既定は全部オフ、有効化しても
        すぐには反映されずアプリの再起動が要るものが多い(この点は各項目の
        説明文で個別に断る)。"""
        w, outer = self._tab_body()
        cfg = self.main_window.config_data

        form = self._group(outer, "譜面プレビュー")
        self.peepo_chart_edit_check = QCheckBox("Peepo式作譜（実験的）")
        self.peepo_chart_edit_check.setChecked(cfg.get("peepo_chart_edit", False))
        form.addRow(self.peepo_chart_edit_check)
        form.addRow(self._hint("譜面プレビューの下部パネルに、音符を直接置ける「作譜」モードを"
                               "追加します。※反映にはアプリの再起動が必要です。"))

        outer.addStretch()
        return w

    # ------------------------------------------------------------------
    # 保存・初期化
    # ------------------------------------------------------------------

    def _rebuild_skin_cache(self):
        """素材キャッシュを作り直す。ダイアログの「保存して適用」とは独立で、
        押したその場で走る(保存を挟むと、まだ確定していない他のタブの入力まで
        一緒に書かれてしまう)。

        使う System は、この画面でいま指しているフォルダ。まだ選んでいない
        ときだけ、起動時と同じ自動探索に任せる。"""
        from neotja import skin_cache

        picked = self.system_dir_edit.text().strip()
        if picked:
            if not skin_cache.is_valid_system_dir(picked):
                QMessageBox.warning(
                    self, "System フォルダが使えません",
                    "指定されている System フォルダが見つからないか、中身が"
                    "TNDE の System ではありません。\n\n　%s" % picked)
                return
            system_dir = picked
        else:
            system_dir, searched, _unusable = skin_cache.find_system_dir(
                self.main_window.config_data)
            if system_dir is None:
                QMessageBox.warning(
                    self, "System フォルダが見つかりません",
                    "素材の取り出し元が見つかりませんでした。上の「参照...」から"
                    "TNDE の System フォルダを指定してください。\n\n"
                    "探した場所:\n" + "\n".join("　・%s" % p for p in searched))
                return

        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = skin_cache.ensure_cache(system_dir, force=True)
        finally:
            QGuiApplication.restoreOverrideCursor()

        if res.get("error"):
            QMessageBox.critical(
                self, "再展開できませんでした",
                "素材を展開するフォルダに書き込めませんでした。\n\n　%s"
                % res.get("error", ""))
            return
        if res.get("failed"):
            QMessageBox.warning(
                self, "一部の素材を取り出せませんでした",
                "%d 件を取り出しました（%d 件は失敗）。\n\n"
                "失敗したぶんは本来と違う見た目・音になります。対応していない"
                "版の TNDE か、System フォルダが壊れている可能性があります。"
                % (res.get("ok", 0), res["failed"]))
            return
        QMessageBox.information(
            self, "再展開しました",
            "素材 %d 件を取り出し直しました（%.1f 秒）。\n\n"
            "見た目に反映されるのはアプリの再起動後です。"
            % (res.get("ok", 0), res.get("elapsed", 0.0)))

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
        cfg["system_dir"] = self.system_dir_edit.text()
        cfg["hit_sound_don_path"] = self.hit_don_edit.text()
        cfg["hit_sound_ka_path"] = self.hit_ka_edit.text()
        cfg["peepo_chart_edit"] = self.peepo_chart_edit_check.isChecked()

        cfg["audio_output_device"] = self.audio_device_combo.currentData() or ""
        cfg["wireless_offset_enabled"] = self.wireless_check.isChecked()
        cfg["wireless_offset_ms"] = float(self.wireless_spin.value())
        self.accept()

    def _reset(self):
        # 何が消えるのかを具体的に出す。以前は「すべての環境設定を初期化します
        # か？」の一言だけで、押した瞬間にカスタムショートカット10個も打音の
        # WAVパスも消えることが分からなかった。
        ans = QMessageBox.question(
            self, "確認",
            "このダイアログで設定した項目を、すべて既定値に戻します。\n\n"
            "・シミュレータ起動 (F2・F3 の名前とexeパス)\n"
            "・カスタムショートカット (Alt+0〜9 の10個)\n"
            "・フォント / リサイズ / 編集 / 譜面プレビュー / 連打の計算\n"
            "・動画の保存先\n"
            "・音声 (出力デバイス、打音のWAVパス、ワイヤレス調整)\n"
            "・実験的機能\n\n"
            "最近使ったファイルやウィンドウの位置など、この画面に無い項目は"
            "そのまま残ります。\n\n"
            "戻してよろしいですか？")
        if ans != QMessageBox.Yes:
            return
        # 環境設定ダイアログが扱う項目だけを既定へ戻す。以前は config_data ごと
        # default_settings() に差し替えていたので、この画面に出ていない最近
        # 使ったファイル・ウィンドウ位置・分割比・音量まで巻き添えで消えていた。
        # 「見えているものが戻る」ほうが、押した結果を予想できる。
        defaults = settings_mod.default_settings()
        cfg = self.main_window.config_data
        for key in self._RESET_KEYS:
            cfg[key] = defaults[key]
        self.accept()
