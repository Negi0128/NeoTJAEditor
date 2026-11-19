import copy
import json
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from neotja import nameplate_profiles as np_mod
from neotja import settings as settings_mod
from neotja import theme as theme_mod

#: メッセージ内の改行。
LF = chr(10)


class _NoWheel(QObject):
    """ホイールで値が変わるのを止める見張り。

    数値入力や選択肢の上でホイールを回すと、この画面ではスクロールのつもりが
    値のほうが変わってしまう。画面が縦に長くスクロールしながら見るので、
    通りすがりに設定を書き換えてしまう事故が起きやすい。ホイールは常に
    スクロールへ回し、値は矢印キーか直接入力で変える。"""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel:
            event.ignore()
            return True
        return False


class _Hint(QLabel):
    """項目の補足説明。画面には出さず、直前の行の ⓘ(と行そのもの)の
    ツールチップへ移す(_Form.addRow / _Page.addWidget が受け取ったとき)。
    live=True のもの(ネームプレートの「素材が無い」の知らせ)はその場に出す。"""
    live = False


class _InfoDot(QWidget):
    """説明のある行に付ける小さな i の丸。乗せると説明が出る。"""

    def __init__(self, tip, parent=None):
        super().__init__(parent)
        self.setToolTip(tip)
        self.setFixedSize(16, 16)
        self.setCursor(Qt.WhatsThisCursor)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(_small_text())
        if not self.isEnabled():
            col.setAlpha(110)
        p.setPen(QPen(col, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(1.5, 1.5, 13, 13))
        f = QFont(self.font())
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 0, 16, 16), Qt.AlignCenter, "i")
        p.end()


def _small_text():
    return "#93a3bf" if theme_mod.is_dark_theme() else "#4a5568"


def _attach_tip(widgets, tip):
    for w in widgets:
        if w is None:
            continue
        old = w.toolTip()
        w.setToolTip(old + LF + LF + tip if old else tip)


class _Form(QFormLayout):
    """枠の中のフォーム。_Hint を受け取ったら行を足さず、直前の行に ⓘ を
    付けて説明をツールチップへ移す(1画面に収めるため)。"""

    def addRow(self, *args):
        if (len(args) == 1 and isinstance(args[0], _Hint) and not args[0].live
                and self.rowCount() > 0):
            self._fold(args[0])
            return
        return super().addRow(*args)

    def _fold(self, hint):
        tip = hint.text()
        host = self.parentWidget()
        # 消さずに隠して持っておく(setEnabled をつないでいる説明がある)。
        hint.setParent(host)
        hint.hide()
        hint.setProperty("folded", True)
        row = self.rowCount() - 1
        # 同じ行に説明が2つ続くときは、ⓘ を増やさず1つにまとめる。
        for role in (QFormLayout.SpanningRole, QFormLayout.LabelRole):
            it = self.itemAt(row, role)
            box = it.widget() if it is not None else None
            dots = box.findChildren(_InfoDot) if box is not None else []
            if dots:
                _attach_tip([dots[0]], tip)
                for w in box.findChildren(QWidget):
                    if not isinstance(w, _InfoDot) and w.parentWidget() is box:
                        _attach_tip([w], tip)
                return
        span = self.itemAt(row, QFormLayout.SpanningRole)
        lab = self.itemAt(row, QFormLayout.LabelRole)
        fld = self.itemAt(row, QFormLayout.FieldRole)
        dot = _InfoDot(tip)
        if span is not None and span.widget() is not None:
            w = span.widget()
            _attach_tip([w], tip)
            self.takeRow(row)
            box = QWidget()
            box.setObjectName("tipRow")
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            h.addWidget(w)
            h.addWidget(dot, 0, Qt.AlignVCenter)
            if not isinstance(w, (QPushButton, QLineEdit)):
                h.addStretch(1)
            w.setVisible(True)
            super().addRow(box)
        elif lab is not None and lab.widget() is not None:
            lw = lab.widget()
            fw = fld.widget() if fld is not None else None
            _attach_tip([lw, fw], tip)
            self.takeRow(row)
            box = QWidget()
            box.setObjectName("tipRow")
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            h.addWidget(lw)
            h.addWidget(dot, 0, Qt.AlignVCenter)
            h.addStretch(1)
            lw.setVisible(True)
            # 親のチェックで灰色になる行は ⓘ も一緒に。
            dot.setEnabled(lw.isEnabled())
            if fw is not None:
                super().addRow(box, fw)
                fw.setVisible(True)
            else:
                super().addRow(box)
        elif fld is not None and fld.widget() is not None:
            _attach_tip([fld.widget()], tip)


class _Page(QVBoxLayout):
    """ページの中身。ページ全体の説明(_Hint)はページの見出しの ⓘ へ。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.page_tips = []

    def addWidget(self, w, *args):
        if isinstance(w, _Hint) and not w.live:
            self.page_tips.append(w.text())
            w.setParent(self.parentWidget())
            w.hide()
            return
        return super().addWidget(w, *args)


class SettingsDialog(QDialog):
    # 「初期化」で既定へ戻すキー。この画面に入力欄がある項目だけを並べる
    # (_save が書き込むキーと同じ顔ぶれ)。ここに無いもの — recent_files,
    # window_geometry, splitter_state, 各種音量, theme, roll_speed など — は
    # 別の場所で決まる状態なので、環境設定の初期化では触らない。
    # system_dir(素材の在りか)もあえて外してある。空へ戻すと次の起動で
    # 素材が見つからず、内蔵スキンで起動することになる — 初期化ボタンを
    # 押しただけで絵と音が変わるのは、利用者の意図とかけ離れているため。
    _RESET_KEYS = (
        "run_config", "custom_shortcuts",
        "font_family", "font_size",
        "resize_ext", "resize_wrap_16", "resize_wrap_12",
        "check_updates_on_startup", "auto_save_enabled",
        "se_text_enabled", "note_input_sound",
        "record_output_dir",
        "hit_sound_don_path", "hit_sound_ka_path", "hit_sound_use_custom",
        "audio_output_device", "wireless_offset_enabled", "wireless_offset_ms",
        "player_select_bgm", "player_select_bgm_volume", "player_play_mode",
        "preview_modes_legacy", "gpu_render", "gpu_vsync",
        "preview_max_fps",
        "nameplate_name", "nameplate_title", "nameplate_title_type",
        "nameplate_title_image", "nameplate_dan", "nameplate_dan_type",
        "nameplate_dan_text_color", "show_tuner", "arrange_ref",
        "nameplate_title_dx", "nameplate_title_dy", "nameplate_title_size",
        "nameplate_name_dx", "nameplate_name_dy", "nameplate_name_size",
        "nameplate_dan_dx", "nameplate_dan_dy", "nameplate_dan_size",
        "nameplate_title_image_dx", "nameplate_title_image_dy",
        "nameplate_title_image_dw", "nameplate_title_image_dh",
        # 起動時の案内。これは初期化で戻してよい(戻ると「知らせる」= 既定)。
        "warn_missing_system",
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
        self.resize(min(860, max(520, avail.width() - 80)),
                    min(700, max(420, int(avail.height() * 0.88))))
        self.setObjectName("settingsDialog")

        # 左に検索と項目の一覧、右にその項目のページ(利用者の選択 2026-11-19:
        # 以前のタブは7枚で、探すのに順に開く必要があった)。ページは1画面に
        # 収まる大きさに分け、収まらないときだけページの中でスクロールする。
        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        body.setSpacing(0)
        layout.addLayout(body, 1)
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 12, 0)
        side.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("settingsSearch")
        self.search.setPlaceholderText("検索")
        self.search.setClearButtonEnabled(True)
        side.addWidget(self.search)
        self.nav = QListWidget()
        self.nav.setObjectName("settingsNav")
        self.nav.setFixedWidth(160)
        side.addWidget(self.nav, 1)
        body.addLayout(side)
        rule = QWidget()
        rule.setObjectName("settingsRule")
        rule.setFixedWidth(1)
        body.addWidget(rule)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        self.pages = []          # (名前, スクロール, 中身)
        for name, build in (
                ("シミュレータ起動", self._build_run_tab),
                ("ショートカット", self._build_shortcuts_tab),
                ("エディタ", self._build_editor_tab),
                ("音声", self._build_audio_tab),
                ("表示", self._build_display_tab),
                ("ネームプレート", self._build_nameplate_tab),
                ("Player", self._build_player_page),
                # 「開発者用」と「実験的機能」は1枚にまとめてある(中身が近い)。
                ("開発・実験的機能", self._build_dev_experimental_tab)):
            self._add_page(name, build())
        # エディタは枠が多く1画面に収まらないので、後半を「書き出し・素材」へ。
        self._split_page("エディタ", "書き出し・素材",
                         ("動画書き出し", "素材(System フォルダ)", "Discord", "その他"))
        # ネームプレートも同じく2枚に。「見本」は開いている方のページへ移す
        # (どちらで数値を変えても出来上がりが見える)。
        self._split_page("ネームプレート", "プレートの名前・段位", ("名前", "段位", "素材"))
        self._np_sample = next(b for b in self.findChildren(QGroupBox) if b.title() == "見本")
        self.nav.currentRowChanged.connect(self._carry_np_sample)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._jump_first)
        self.setStyleSheet(self._qss())
        # どのページも横にはみ出さない幅を下限にする(長いチェックの文など)。
        self.stack.ensurePolished()
        need = max(inner.minimumSizeHint().width() for _n, _a, inner in self.pages)
        need += self.nav.width() + 12 + 1 + 24 + layout.contentsMargins().left() * 2
        self.setMinimumWidth(min(need, max(620, avail.width() - 40)))
        if self.width() < self.minimumWidth():
            self.resize(self.minimumWidth(), self.height())

        # 数値入力と選択肢は、ホイールで値が変わらないようにする。
        # 全タブを作り終えてからまとめて掛ける(作る順に依存しない)。
        self._no_wheel = _NoWheel(self)
        for kind in (QSpinBox, QDoubleSpinBox, QComboBox):
            for w in self.findChildren(kind):
                w.installEventFilter(self._no_wheel)
                w.setFocusPolicy(Qt.StrongFocus)

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

    def _add_page(self, name, inner, at=None):
        lay = inner.layout()
        tips = list(getattr(lay, "page_tips", []) or [])
        # ページの見出し(名前)。ページ全体の説明はその ⓘ へ。
        head = QWidget()
        head.setObjectName("pageHead")
        h = QHBoxLayout(head)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        title = QLabel(name)
        title.setObjectName("pageTitle")
        h.addWidget(title)
        if tips:
            h.addWidget(_InfoDot((LF + LF).join(tips)), 0, Qt.AlignVCenter)
        h.addStretch(1)
        lay.insertWidget(0, head)
        inner.setObjectName("settingsPage")
        area = self._scrollable(inner)
        area.setObjectName("settingsPageArea")
        area.viewport().setObjectName("settingsPageView")
        item = QListWidgetItem(name)
        item.setSizeHint(QSize(150, 34))
        if tips:
            item.setToolTip((LF + LF).join(tips))
        if at is None:
            self.stack.addWidget(area)
            self.nav.addItem(item)
            self.pages.append((name, area, inner))
        else:
            self.stack.insertWidget(at, area)
            self.nav.insertItem(at, item)
            self.pages.insert(at, (name, area, inner))

    def _split_page(self, src_name, new_name, titles):
        """src のページの枠(見出しが titles のもの)を新しいページへ移す。
        枠の中の部品はそのまま(_save が読む self.* は変わらない)。"""
        idx = [n for n, _a, _w in self.pages].index(src_name)
        src = self.pages[idx][2]
        w, outer = self._tab_body()
        boxes = {b.title(): b for b in src.findChildren(QGroupBox) if b.parentWidget() is src}
        for t in titles:
            box = boxes.get(t)
            if box is not None:
                src.layout().removeWidget(box)
                outer.addWidget(box)
        outer.addStretch()
        self._add_page(new_name, w, at=idx + 1)

    def _carry_np_sample(self, row):
        if not (0 <= row < len(self.pages)):
            return
        name, _area, inner = self.pages[row]
        if name not in ("ネームプレート", "プレートの名前・段位"):
            return
        box = self._np_sample
        if box.parentWidget() is inner:
            return
        old = box.parentWidget()
        if old is not None and old.layout() is not None:
            old.layout().removeWidget(box)
        # 見出しの次(ネームプレートの選択の下 / 名前・段位なら一番上)。
        at = 2 if name == "ネームプレート" else 1
        inner.layout().insertWidget(at, box)
        box.show()

    # ------------------------------------------------------------------
    # 検索: 項目の名前・枠の見出し・説明(ツールチップ)で一覧をしぼり、
    # Enter で最初に当たった欄へ移る。
    # ------------------------------------------------------------------
    @staticmethod
    def _texts(w):
        out = [w.toolTip()]
        if isinstance(w, QGroupBox):
            out.append(w.title())
        elif isinstance(w, (QLabel, QAbstractButton)):
            out.append(w.text())
        return " ".join(t for t in out if t)

    def _matches(self, inner, q):
        """ページの中で q を含む部品(見出し・ラベル・チェック・ボタン)を上から。"""
        q = q.casefold()
        hits = []
        for w in inner.findChildren(QWidget):
            if isinstance(w, _Hint) and not w.live:
                continue
            if not w.isVisibleTo(inner) and not isinstance(w, _InfoDot):
                continue
            if q in self._texts(w).casefold():
                hits.append(w)
        hits.sort(key=lambda w: w.mapTo(inner, w.rect().topLeft()).y())
        return hits

    def _filter(self, text):
        q = text.strip()
        first = None
        for i, (name, _area, inner) in enumerate(self.pages):
            ok = (not q) or (q.casefold() in name.casefold()) or bool(self._matches(inner, q))
            self.nav.item(i).setHidden(not ok)
            if ok and first is None:
                first = i
        cur = self.nav.currentRow()
        if first is not None and (cur < 0 or self.nav.item(cur).isHidden()):
            self.nav.setCurrentRow(first)

    def _focus_target(self, w, inner):
        """当たった部品から、移る先(入力欄)を決める。ラベルならその行の欄。"""
        if isinstance(w, _InfoDot):
            w = w.parentWidget() or w
        for form in inner.findChildren(QFormLayout):
            for r in range(form.rowCount()):
                lab = form.itemAt(r, QFormLayout.LabelRole)
                fld = form.itemAt(r, QFormLayout.FieldRole)
                lw = lab.widget() if lab is not None else None
                if lw is not None and (lw is w or lw.isAncestorOf(w)) and fld is not None:
                    return fld.widget() or w
        return w

    def _jump_first(self):
        q = self.search.text().strip()
        if not q:
            return
        for i, (name, area, inner) in enumerate(self.pages):
            hits = self._matches(inner, q)
            if not hits:
                continue
            self.nav.setCurrentRow(i)
            target = self._focus_target(hits[0], inner)
            area.ensureWidgetVisible(target, 0, 40)
            if target.focusPolicy() != Qt.NoFocus:
                target.setFocus(Qt.OtherFocusReason)
            return

    # ------------------------------------------------------------------
    # 見た目(選曲の表・Player の板と同じ: 小さな字 #93a3bf、細い区切り線、
    # 平らなボタン、角はほぼ四角)。明るいテーマでは同じ形で明るい色。
    # ------------------------------------------------------------------
    @staticmethod
    def _qss():
        dark = theme_mod.is_dark_theme()
        c = theme_mod.COLORS
        small = _small_text()
        accent = c.get("accent", "#4f9cf9")
        if dark:
            bg, fg, line = "#0f1a2c", "#e5edf7", "rgba(255,255,255,26)"
            field, edge = "rgba(16,28,44,200)", "rgba(139,217,238,110)"
            btn, btn_edge, btn_hover = "rgba(16,28,44,170)", "rgba(139,217,238,150)", "rgba(35,61,84,200)"
            sel = "rgba(127,227,255,40)"
        else:
            bg, fg, line = "#f6f8fb", "#1f2733", "rgba(0,0,0,26)"
            field, edge = "#ffffff", "rgba(40,90,120,110)"
            btn, btn_edge, btn_hover = "#ffffff", "rgba(40,90,120,140)", "#eef4f8"
            sel = "rgba(13,110,253,30)"
        return f"""
        QDialog#settingsDialog {{ background-color: {bg}; }}
        QDialog#settingsDialog QWidget {{ font-family: "Yu Gothic UI"; background: transparent; }}
        QDialog#settingsDialog QComboBox QAbstractItemView {{ background-color: {bg}; color: {fg};
                     border: 1px solid {edge}; selection-background-color: {sel}; }}
        QScrollArea#settingsPageArea, QWidget#settingsPageView, QWidget#settingsPage
            {{ background: transparent; border: none; }}
        QStackedWidget, QWidget#pageHead, QWidget#tipRow {{ background: transparent; }}
        QWidget#settingsRule {{ background-color: {line}; }}
        QLabel#pageTitle {{ color: {fg}; font-size: 17px; font-weight: bold; }}
        QListWidget#settingsNav {{ background: transparent; border: none; outline: 0;
                                   font-size: 13px; font-weight: bold; color: {fg}; }}
        QListWidget#settingsNav::item {{ padding: 0 10px; border-radius: 3px; }}
        QListWidget#settingsNav::item:hover {{ background-color: {btn_hover}; }}
        QListWidget#settingsNav::item:selected {{ background-color: {sel}; color: {fg};
                                                  border: 1px solid {edge}; }}
        QDialog#settingsDialog QGroupBox {{ background: transparent; border: none; border-top: 1px solid {line};
                     margin-top: 18px; padding-top: 6px; font-weight: bold; color: {fg}; }}
        QDialog#settingsDialog QGroupBox::title {{ subcontrol-origin: margin; left: 0;
                     top: 2px; padding: 0; color: {small}; font-size: 12px; }}
        QDialog#settingsDialog QLabel {{ color: {fg}; background: transparent; }}
        QDialog#settingsDialog QLineEdit, QDialog#settingsDialog QSpinBox,
        QDialog#settingsDialog QDoubleSpinBox, QDialog#settingsDialog QComboBox
            {{ background-color: {field}; border: 1px solid {edge}; border-radius: 3px;
               padding: 3px 6px; color: {fg}; }}
        QDialog#settingsDialog QLineEdit:focus, QDialog#settingsDialog QSpinBox:focus,
        QDialog#settingsDialog QDoubleSpinBox:focus, QDialog#settingsDialog QComboBox:focus
            {{ border: 2px solid {accent}; }}
        QDialog#settingsDialog QLineEdit:disabled, QDialog#settingsDialog QSpinBox:disabled,
        QDialog#settingsDialog QDoubleSpinBox:disabled {{ color: {small}; border-color: {line}; }}
        QDialog#settingsDialog QCheckBox {{ color: {fg}; spacing: 8px; background: transparent; }}
        QDialog#settingsDialog QCheckBox:disabled {{ color: {small}; }}
        QDialog#settingsDialog QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 3px;
                     border: 1px solid {small}; background: transparent; }}
        QDialog#settingsDialog QCheckBox::indicator:checked {{ background-color: {accent};
                     border: 1px solid {accent}; image: none; }}
        QDialog#settingsDialog QCheckBox::indicator:focus {{ border: 2px solid {accent}; }}
        QDialog#settingsDialog QPushButton {{ background-color: {btn}; border: 1px solid {btn_edge};
                     border-radius: 3px; padding: 6px 14px; font-weight: bold; color: {fg}; }}
        QDialog#settingsDialog QPushButton:hover {{ background-color: {btn_hover}; }}
        QDialog#settingsDialog QPushButton:focus {{ border: 2px solid {accent}; }}
        QDialog#settingsDialog QPushButton:disabled {{ color: {small}; border-color: {line}; }}
        QDialog#settingsDialog QPushButton#accentButton {{ background-color: {sel};
                     border: 1px solid {accent}; }}
        QDialog#settingsDialog QPushButton#dangerButton {{ border: 1px solid rgba(236,80,80,170);
                     color: #ec7a7a; }}
        QLineEdit#settingsSearch {{ padding: 5px 8px; }}
        """

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
        outer = _Page(w)
        outer.setContentsMargins(16, 4, 12, 12)
        outer.setSpacing(6)
        return w, outer

    @staticmethod
    def _group(outer: QVBoxLayout, title: str) -> QFormLayout:
        """見出し付きの枠を1つ足し、その中身のフォームを返す。関連する設定を
        枠でくくることで、以前のように何十行も同じ調子で縦に並ぶのを避ける。
        フォームにしてあるのでラベルの左端と入力欄の左端が枠ごとに揃う。"""
        box = QGroupBox(title)
        form = _Form(box)
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
        lbl = _Hint(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: %s; font-size: 11px;" % _small_text())
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
        # **押された状態(bool)は捨てる。** clicked は「押されているか」を1つ
        # 渡してくるので、引数を受け取れる形の on_browse にはそれが入って
        # しまう。F2・F3 のパスは `def browse(edit=path_edit)` と書いて欄を
        # 持たせていたため、edit に False が入って AttributeError になり、
        # ファイルを選んでも欄が空のままだった(利用者の報告 2026-11-04
        # 「環境設定でTNDEを選択してもパスが登録されない」)。例外は Qt が
        # 握りつぶすので、exe では何も出ずに「押しても反応しない」に見える。
        # 呼ぶ側ではなくここで断つ — 参照ボタンは6か所あり、同じ書き方を
        # した誰かがまた踏む。
        browse_btn.clicked.connect(lambda *_: on_browse())
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
            "F1キーは打音の ON/OFF に使っています。"))

        cfg = self.main_window.config_data["run_config"]

        # F1 は main_window 側で打音の ON/OFF に固定してあり、ここで exe を
        # 指定しても起動しない。以前は F2・F3 と同じ入力欄を並べていたので、
        # 指定して保存しても何も起きず、原因も分からなかった。欄は出さず、
        # 上の説明文で F1 の役目だけ伝える。v15 で「F1 キーで起動」の枠
        # (内蔵プレビュー固定の説明)は外した — F1 はもう何も起動しない。

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
        self.play_flush_check = QCheckBox(
            "再生の前に、直前の編集をレーンへ反映する"
            "(重い譜面では再生開始が少し遅れます)")
        self.play_flush_check.setChecked(bool(cfg.get("play_flush_before_play", False)))
        form.addRow(self.play_flush_check)
        form.addRow(self._hint("編集してすぐ再生したとき、まだレーンに入っていない編集を"
                               "先に反映してから流します。オフにすると、反映を待たずに"
                               "すぐ再生します(直前の編集は少し遅れてレーンに入ります)。"))

        # 「連打の計算」(短い連打の補正)の欄は 2026-11-13 に外した。
        # 短い連打だけ秒速50〜60で数えるため、BPM の速い2マスの連打で打数が
        # 増えすぎ、長い連打より短い連打のほうが多くなることもあった
        # (利用者の報告)。今は常に 長さ×連打秒速 で数える。

        # --- 動画書き出し ---
        # 動画書き出し(NeoTJAPlayer の録画ボタン)の保存先の既定。
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
        # 反映されるのは次の起動から。未指定なら exe の隣だけを見る
        # (デスクトップ配下の自動探索はやめた)。どこにも無ければ内蔵スキンで
        # 起動するので、ここは「別の場所の System を使いたい人」の入口。
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
            "未指定のときは exe と同じ場所だけを見ます。どこにも無いときは"
            "内蔵スキン(アプリが自前で描く絵と合成打音)で起動します。"
            "※変更の反映にはアプリの再起動が必要です。"))

        # 起動時の案内を黙らせたあと、戻す手段がここにしか無い。案内側の
        # 「次回から表示しない」は押せば消えるが、押した本人が気が変わった
        # ときに settings.json を手で開かせるのは筋が悪い。
        self.warn_missing_system_check = QCheckBox(
            "素材が見つからないときに起動時に知らせる")
        self.warn_missing_system_check.setChecked(
            cfg.get("warn_missing_system", True))
        form.addRow(self.warn_missing_system_check)

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

        # --- Discord ---
        form = self._group(outer, "Discord")
        self.discord_check = QCheckBox("Discord に「NeoTJAEditor をプレイ中」と表示する")
        self.discord_check.setChecked(cfg.get("discord_presence", True))
        form.addRow(self.discord_check)
        self.discord_editing_check = QCheckBox("その下に「作譜中」と表示する")
        self.discord_editing_check.setChecked(cfg.get("discord_show_editing", True))
        self.discord_editing_check.setEnabled(self.discord_check.isChecked())
        self.discord_check.toggled.connect(self.discord_editing_check.setEnabled)
        form.addRow(self.discord_editing_check)
        form.addRow(self._hint(
            "PC で Discord が起動しているときだけ表示されます。"
            "曲名や難易度は送りません。"))

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
        from neotja.mixer_engine import canonical_output_name, list_output_devices
        form = self._group(outer, "出力デバイス")
        self.audio_device_combo = QComboBox()
        self.audio_device_combo.addItem("既定のデバイス", "")
        # 12.1.6 までは MME の切り詰め名(31文字)が保存されていた。一覧は
        # WASAPI のフル名を並べるようになったので、そのままだと
        # 「(見つかりません)」になってしまう。フル名へ読み替えて選ばせる
        # (保存すればフル名に置き換わり、移行が済む)。
        current = canonical_output_name(cfg.get("audio_output_device", "") or "")
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
        # オフ(既定)なら System の TNDE-R/Sounds/Taiko の dong.ogg / ka.ogg が
        # 鳴る。下のファイルを選ぶと自動でオンになる。
        self.hit_custom_check = QCheckBox("自分で選んだ WAV を使う")
        self.hit_custom_check.setChecked(bool(cfg.get("hit_sound_use_custom", False)))
        form.addRow(self.hit_custom_check)
        self.hit_don_edit = QLineEdit(cfg.get("hit_sound_don_path", ""))
        self.hit_don_edit.setReadOnly(True)

        def browse_don():
            p, _ = QFileDialog.getOpenFileName(self, "ドン音源を選択", "", "音声ファイル (*.wav);;すべて (*)")
            if p:
                self.hit_don_edit.setText(p)
                self.hit_custom_check.setChecked(True)

        form.addRow("ドン(WAV)", self._path_row(self.hit_don_edit, browse_don))

        self.hit_ka_edit = QLineEdit(cfg.get("hit_sound_ka_path", ""))
        self.hit_ka_edit.setReadOnly(True)

        def browse_ka():
            p, _ = QFileDialog.getOpenFileName(self, "カツ音源を選択", "", "音声ファイル (*.wav);;すべて (*)")
            if p:
                self.hit_ka_edit.setText(p)
                self.hit_custom_check.setChecked(True)

        form.addRow("カツ(WAV)", self._path_row(self.hit_ka_edit, browse_ka))
        form.addRow(self._hint(
            "オフのときは System（TNDE-R/Sounds/Taiko）の dong.ogg / ka.ogg が鳴ります。"
            "System が見つからないときは内蔵の合成音です。"))

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

    def _rect_rows(self, form, prefix, val, pos_label, size_label):
        """絵の「置き場のずらし」と「大きさに足す量」の行を足す。

        絵の縦横比が帯の枠と違うときに、手で詰められるようにするもの。
        4つとも 0 なら帯の枠にぴったり合わせる(これまでどおり)。"""
        spins = []
        for label, keys, names in ((pos_label, ("_dx", "_dy"), ("右", "下")),
                                   (size_label, ("_dw", "_dh"), ("幅", "高さ"))):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            for key, name in zip(keys, names):
                sp = QSpinBox()
                sp.setRange(-400, 400)
                sp.setValue(int(val(prefix + key)))
                row_layout.addWidget(QLabel(name))
                row_layout.addWidget(sp, 1)
                spins.append(sp)
            form.addRow(label, row)
        return tuple(spins)

    def _text_rows(self, form, prefix, val):
        """文字の「位置」と「大きさ」の行を足す。戻り値は作った入力欄3つ。

        位置は既定からのずらし量。段位のあるなしで名前の枠が変わるので、
        絶対値ではなくずらしで持つ(絶対値だと片方が必ずずれる)。"""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        dx = QSpinBox()
        dx.setRange(-200, 200)
        dx.setValue(int(val(prefix + "_dx")))
        dy = QSpinBox()
        dy.setRange(-200, 200)
        dy.setValue(int(val(prefix + "_dy")))
        row_layout.addWidget(QLabel("右"))
        row_layout.addWidget(dx, 1)
        row_layout.addWidget(QLabel("下"))
        row_layout.addWidget(dy, 1)
        form.addRow("文字の位置", row)
        size = QSpinBox()
        size.setRange(6, 60)
        size.setSuffix(" px")
        size.setValue(int(val(prefix + "_size")))
        form.addRow("文字の大きさ", size)
        return dx, dy, size

    def _build_nameplate_tab(self):
        """ゲーム画面の左下に出るネームプレートの中身。

        並びは実際の絵と同じで、上から 称号 → 名前 → 段位。"""
        w, outer = self._tab_body()
        cfg = self.main_window.config_data
        d = settings_mod.default_settings()

        def val(key):
            return cfg.get(key, d[key])

        # 保存したネームプレート。いじるのはこの画面の中の控え(_np_profiles)で、
        # 設定へ書くのは「保存して適用」のとき。キャンセルで閉じたら、
        # 保存・削除・読み込みも含めてすべて無かったことになる。
        np_mod.ensure_profiles(cfg)
        self._np_profiles = copy.deepcopy(cfg["nameplate_profiles"])
        self._np_loading = False
        form = self._group(outer, "保存したネームプレート")
        self.np_profile_combo = QComboBox()
        form.addRow("ネームプレート", self.np_profile_combo)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        for text, slot in (("保存", self._np_save_profile),
                           ("別名で保存", self._np_save_profile_as),
                           ("削除", self._np_delete_profile),
                           ("書き出す", self._np_export_profile),
                           ("読み込む", self._np_import_profiles)):
            btn = QPushButton(text)
            # clicked の bool を捨てる(_path_row と同じ理由)。
            btn.clicked.connect(lambda _=False, f=slot: f())
            row_layout.addWidget(btn)
        row_layout.addStretch()
        form.addRow(row)
        form.addRow(self._hint(
            "選ぶと、そのネームプレートの中身が下の欄に入ります。下の欄を変えて"
            "「保存」か「保存して適用」を押すと、選んでいるネームプレートに上書きされ、"
            "ゲーム画面にもそれが出ます。書き出すのも、いま下の欄に入っている中身です。"))
        self._np_refresh_profile_combo(int(cfg.get("nameplate_profile_index", 0)))
        self.np_profile_combo.currentIndexChanged.connect(
            self._np_on_profile_selected)

        # 出来上がりの見本。入力のたびに描き直す。数値をいじって窓を開き直す
        # までどう出るか分からない、という往復を無くすため。
        form = self._group(outer, "見本")
        self.np_preview = QLabel()
        self.np_preview.setAlignment(Qt.AlignCenter)
        self.np_preview.setMinimumHeight(100)
        self.np_preview.setStyleSheet(
            "background: #3a3a3a; border: 1px solid rgba(255,255,255,30);"
            " border-radius: 3px;")
        form.addRow(self.np_preview)
        self.np_nosheet_hint = self._hint("")
        self.np_nosheet_hint.live = True       # 状態の知らせなので、その場に出す
        self.np_nosheet_hint.setStyleSheet("color: #e0a33a; font-size: 11px;")
        self.np_nosheet_hint.hide()
        form.addRow(self.np_nosheet_hint)
        self._np_preview_renderer = None

        form = self._group(outer, "素材")
        btn_dir = QPushButton("NamePlate フォルダを開く")
        btn_dir.clicked.connect(self._open_nameplate_dir)
        form.addRow(btn_dir)
        form.addRow(self._hint(
            "ネームプレート・称号バー・段位バッジの絵は NamePlate.png から切り出して"
            "います。太鼓さん次郎のスキンによっては入っていないので、"
            "このフォルダに自分で置くこともできます"
            "(形式は 220x1189 の縦長の部品シート)。"))
        form.addRow(self._hint(
            "フォルダにも System にも無いときは、名前だけの簡単な板を"
            "自前で描きます。"))

        form = self._group(outer, "称号(上の帯)")
        self.np_title_edit = QLineEdit(str(val("nameplate_title")))
        self.np_title_edit.setMaxLength(64)
        form.addRow("称号", self.np_title_edit)
        self.np_title_type_combo = QComboBox()
        for label, value in settings_mod.NAMEPLATE_TITLE_TYPES:
            self.np_title_type_combo.addItem(label, value)
        i = self.np_title_type_combo.findData(int(val("nameplate_title_type")))
        self.np_title_type_combo.setCurrentIndex(max(0, i))
        form.addRow("帯の色", self.np_title_type_combo)
        (self.np_title_dx, self.np_title_dy,
         self.np_title_size) = self._text_rows(form, "nameplate_title", val)
        self.np_title_image_edit = QLineEdit(str(val("nameplate_title_image")))

        def browse_title_image():
            path, _ = QFileDialog.getOpenFileName(
                self, "称号の画像を選ぶ", self.np_title_image_edit.text(),
                "画像 (*.png *.webp *.jpg *.jpeg);;すべてのファイル (*)")
            if path:
                self.np_title_image_edit.setText(path)

        form.addRow("画像で置き換え",
                    self._path_row(self.np_title_image_edit, browse_title_image))
        (self.np_title_img_dx, self.np_title_img_dy,
         self.np_title_img_dw, self.np_title_img_dh) = self._rect_rows(
            form, "nameplate_title_image", val, "画像の位置", "画像の大きさ")
        form.addRow(self._hint(
            "画像を指定すると、帯の色の代わりにその画像を帯として貼ります。"
            "画像は背景の扱いで、称号の文字は上の入力どおりに書かれるので、"
            "帯1枚をいろいろな称号に使い回せます。文字まで描き込んだ画像を"
            "使うときは、称号を空欄にしてください。"))

        form = self._group(outer, "名前")
        self.np_name_edit = QLineEdit(str(val("nameplate_name")))
        self.np_name_edit.setMaxLength(32)
        form.addRow("名前", self.np_name_edit)
        (self.np_name_dx, self.np_name_dy,
         self.np_name_size) = self._text_rows(form, "nameplate_name", val)

        form = self._group(outer, "段位")
        self.np_dan_combo = QComboBox()
        for name in settings_mod.NAMEPLATE_DAN_NAMES:
            self.np_dan_combo.addItem(name or "(出さない)", name)
        i = self.np_dan_combo.findData(str(val("nameplate_dan")))
        self.np_dan_combo.setCurrentIndex(max(0, i))
        form.addRow("段位", self.np_dan_combo)
        self.np_dan_type_combo = QComboBox()
        for label, value in settings_mod.NAMEPLATE_DAN_TYPES:
            self.np_dan_type_combo.addItem(label, value)
        i = self.np_dan_type_combo.findData(int(val("nameplate_dan_type")))
        self.np_dan_type_combo.setCurrentIndex(max(0, i))
        form.addRow("背景", self.np_dan_type_combo)
        self.np_dan_text_combo = QComboBox()
        for label, value in settings_mod.NAMEPLATE_DAN_TEXT_COLORS:
            self.np_dan_text_combo.addItem(label, value)
        i = self.np_dan_text_combo.findData(str(val("nameplate_dan_text_color")))
        self.np_dan_text_combo.setCurrentIndex(max(0, i))
        form.addRow("文字の色", self.np_dan_text_combo)
        (self.np_dan_dx, self.np_dan_dy,
         self.np_dan_size) = self._text_rows(form, "nameplate_dan", val)
        form.addRow(self._hint("「(出さない)」を選ぶと、段位そのものを描きません。"))

        for widget in (self.np_title_edit, self.np_name_edit,
                       self.np_title_image_edit):
            widget.textChanged.connect(self._update_nameplate_preview)
        for combo in (self.np_title_type_combo, self.np_dan_combo,
                      self.np_dan_type_combo, self.np_dan_text_combo):
            combo.currentIndexChanged.connect(self._update_nameplate_preview)
        for spin in (self.np_title_img_dx, self.np_title_img_dy,
                     self.np_title_img_dw, self.np_title_img_dh,
                     self.np_title_dx, self.np_title_dy, self.np_title_size,
                     self.np_name_dx, self.np_name_dy, self.np_name_size,
                     self.np_dan_dx, self.np_dan_dy, self.np_dan_size):
            spin.valueChanged.connect(self._update_nameplate_preview)
        self._update_nameplate_preview()

        outer.addStretch()
        return w

    def _open_nameplate_dir(self):
        """NamePlate フォルダをエクスプローラで開く(無ければ作ってから)。"""
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        path = settings_mod.nameplate_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ---- 保存したネームプレート ------------------------------------------------

    def _np_refresh_profile_combo(self, index: int):
        """選ぶ欄を控えの一覧から作り直す。入力欄へは読み込まない
        (作り直しのたびに、いじりかけの欄が上書きされないように)。"""
        combo = self.np_profile_combo
        combo.blockSignals(True)
        combo.clear()
        for prof in self._np_profiles:
            combo.addItem(prof["label"])
        combo.setCurrentIndex(max(0, min(len(self._np_profiles) - 1, index)))
        combo.blockSignals(False)

    def _np_on_profile_selected(self, index: int):
        if 0 <= index < len(self._np_profiles):
            self._np_apply_values(np_mod.values_of(self._np_profiles[index]))

    def _np_apply_values(self, values: dict):
        """ネームプレートの値を下の入力欄へ入れる。見本の描き直しは最後に1回だけ
        (欄ごとに描き直すと20回描くことになるため)。"""
        def set_combo(combo, data):
            i = combo.findData(data)
            combo.setCurrentIndex(max(0, i))

        self._np_loading = True
        try:
            self.np_title_edit.setText(values["nameplate_title"])
            set_combo(self.np_title_type_combo, values["nameplate_title_type"])
            self.np_title_image_edit.setText(values["nameplate_title_image"])
            self.np_name_edit.setText(values["nameplate_name"])
            set_combo(self.np_dan_combo, values["nameplate_dan"])
            set_combo(self.np_dan_type_combo, values["nameplate_dan_type"])
            set_combo(self.np_dan_text_combo, values["nameplate_dan_text_color"])
            for spin, key in (
                    (self.np_title_img_dx, "nameplate_title_image_dx"),
                    (self.np_title_img_dy, "nameplate_title_image_dy"),
                    (self.np_title_img_dw, "nameplate_title_image_dw"),
                    (self.np_title_img_dh, "nameplate_title_image_dh"),
                    (self.np_title_dx, "nameplate_title_dx"),
                    (self.np_title_dy, "nameplate_title_dy"),
                    (self.np_title_size, "nameplate_title_size"),
                    (self.np_name_dx, "nameplate_name_dx"),
                    (self.np_name_dy, "nameplate_name_dy"),
                    (self.np_name_size, "nameplate_name_size"),
                    (self.np_dan_dx, "nameplate_dan_dx"),
                    (self.np_dan_dy, "nameplate_dan_dy"),
                    (self.np_dan_size, "nameplate_dan_size")):
                spin.setValue(int(values[key]))
        finally:
            self._np_loading = False
        self._update_nameplate_preview()

    def _np_save_profile(self):
        """選んでいるネームプレートを、いまの入力欄の中身で上書きする。"""
        i = self.np_profile_combo.currentIndex()
        if not 0 <= i < len(self._np_profiles):
            return
        label = self._np_profiles[i]["label"]
        self._np_profiles[i] = np_mod.profile_from_values(
            label, self._nameplate_values())

    def _np_save_profile_as(self):
        """いまの入力欄の中身を、名前を付けて新しい1枚として足す。"""
        label, ok = QInputDialog.getText(
            self, "別名で保存", "ネームプレートの名前:", QLineEdit.Normal,
            self.np_name_edit.text())
        if not ok:
            return
        label = np_mod.clean_label(label, "")
        if not label:
            return
        label = np_mod.unique_label(
            label, [p["label"] for p in self._np_profiles])
        self._np_profiles.append(
            np_mod.profile_from_values(label, self._nameplate_values()))
        # 入力欄はすでに同じ中身なので、読み込み直さずに選ぶだけにする。
        self._np_refresh_profile_combo(len(self._np_profiles) - 1)

    def _np_delete_profile(self):
        i = self.np_profile_combo.currentIndex()
        if not 0 <= i < len(self._np_profiles):
            return
        if len(self._np_profiles) <= 1:
            # 一覧が空になると「選んでいるもの」が無くなり、保存や書き出しの
            # 行き先が決まらないので、最後の1枚は残す。
            QMessageBox.information(
                self, "削除できません",
                "最後の1枚は削除できません。"
                "別のネームプレートを作ってから削除してください。")
            return
        label = self._np_profiles[i]["label"]
        ans = QMessageBox.question(
            self, "確認",
            "ネームプレート「%s」を一覧から削除しますか？%s%s"
            "(「保存して適用」を押すまでは、キャンセルで元に戻せます)"
            % (label, LF, LF))
        if ans != QMessageBox.Yes:
            return
        del self._np_profiles[i]
        new_i = max(0, i - 1)
        self._np_refresh_profile_combo(new_i)
        self._np_on_profile_selected(new_i)

    def _np_export_profile(self):
        """選んでいるネームプレートを .json に書き出す(別の PC へ持っていく用)。"""
        i = self.np_profile_combo.currentIndex()
        if not 0 <= i < len(self._np_profiles):
            return
        # 下の欄の手直しも含めて渡す(「保存」を押し忘れて古い中身を
        # 渡してしまわないように)。
        self._np_save_profile()
        prof = self._np_profiles[i]
        start = str(Path.home() / (np_mod.safe_file_stem(prof["label"]) + ".json"))
        path, _ = QFileDialog.getSaveFileName(
            self, "ネームプレートを書き出す", start, "ネームプレート (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(np_mod.export_json(prof))
        except OSError as exc:
            QMessageBox.warning(
                self, "書き出せませんでした",
                "ファイルを書けませんでした。%s%s" % (LF, exc))

    def _np_import_profiles(self):
        """書き出した .json を読み、新しいネームプレートとして一覧へ足す。
        手元のネームプレートは上書きしない。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "ネームプレートを読み込む", "", "ネームプレート (*.json);;すべてのファイル (*)")
        if not path:
            return
        try:
            size = Path(path).stat().st_size
            if size > np_mod.FILE_MAX_BYTES:
                raise np_mod.NameplateImportError("ファイルが大きすぎます。")
            with open(path, "rb") as f:
                data = f.read()
            added = np_mod.import_plates(
                data, [p["label"] for p in self._np_profiles],
                settings_mod.nameplate_dir())
        except np_mod.NameplateImportError as exc:
            QMessageBox.warning(self, "読み込めませんでした", str(exc))
            return
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(
                self, "読み込めませんでした",
                "ファイルを読めませんでした。%s%s" % (LF, exc))
            return
        first = len(self._np_profiles)
        self._np_profiles.extend(added)
        self._np_refresh_profile_combo(first)
        self._np_on_profile_selected(first)

    def _nameplate_values(self):
        """この画面の入力欄から、設定と同じ形の辞書を作る。"""
        return {
            "nameplate_name": self.np_name_edit.text(),
            "nameplate_title": self.np_title_edit.text(),
            "nameplate_title_type": int(self.np_title_type_combo.currentData()),
            "nameplate_title_image": self.np_title_image_edit.text(),
            "nameplate_title_image_dx": self.np_title_img_dx.value(),
            "nameplate_title_image_dy": self.np_title_img_dy.value(),
            "nameplate_title_image_dw": self.np_title_img_dw.value(),
            "nameplate_title_image_dh": self.np_title_img_dh.value(),
            "nameplate_dan": str(self.np_dan_combo.currentData() or ""),
            "nameplate_dan_type": int(self.np_dan_type_combo.currentData()),
            "nameplate_dan_text_color": str(self.np_dan_text_combo.currentData()),
            "nameplate_title_dx": self.np_title_dx.value(),
            "nameplate_title_dy": self.np_title_dy.value(),
            "nameplate_title_size": self.np_title_size.value(),
            "nameplate_name_dx": self.np_name_dx.value(),
            "nameplate_name_dy": self.np_name_dy.value(),
            "nameplate_name_size": self.np_name_size.value(),
            "nameplate_dan_dx": self.np_dan_dx.value(),
            "nameplate_dan_dy": self.np_dan_dy.value(),
            "nameplate_dan_size": self.np_dan_size.value(),
        }

    def _update_nameplate_preview(self):
        """見本を描き直す。

        部品シート(NamePlate.png)が見つからないときは、下の設定が効かない
        ことを伝えて入力欄を灰色にする。効かない値をいじれてしまうと
        「設定が悪いのか、そもそも無効なのか」が分からなくなるため。"""
        if getattr(self, "_np_loading", False):
            return   # ネームプレートをまとめて入れている途中。最後に1回だけ描く
        have_sheet = True
        try:
            from neotja import game_screen as gs_mod
            if self._np_preview_renderer is None:
                self._np_preview_renderer = gs_mod.NameplateRenderer()
            have_sheet = gs_mod.nameplate_sheet() is not None
            pm = gs_mod.nameplate_preview(self._nameplate_values(), scale=2.0,
                                          renderer=self._np_preview_renderer)
        except Exception:  # noqa: BLE001
            pm = None
        if pm is None:
            self.np_preview.setText("見本を出せませんでした。")
        else:
            self.np_preview.setPixmap(pm)
        for widget in self._nameplate_inputs():
            widget.setEnabled(have_sheet)
        self.np_nosheet_hint.setVisible(not have_sheet)
        if not have_sheet:
            self.np_nosheet_hint.setText(
                "NamePlate.png が見つからないので、下の設定は効きません。"
                "上の見本のとおり「1P どんちゃん」の姿で出ます。"
                "「NamePlate フォルダを開く」から NamePlate.png を置くか、"
                "「エディタ・ツール」タブで System フォルダを指定してください。")

    def _nameplate_inputs(self):
        """部品シートが無いときに灰色にする入力欄。"""
        return (self.np_title_edit, self.np_title_type_combo,
                self.np_title_image_edit, self.np_name_edit,
                self.np_title_img_dx, self.np_title_img_dy,
                self.np_title_img_dw, self.np_title_img_dh,
                self.np_dan_combo, self.np_dan_type_combo,
                self.np_dan_text_combo,
                self.np_title_dx, self.np_title_dy, self.np_title_size,
                self.np_name_dx, self.np_name_dy, self.np_name_size,
                self.np_dan_dx, self.np_dan_dy, self.np_dan_size)

    def _build_player_page(self):
        """Player(選曲〜結果の画面)だけに効く項目を1か所に。"""
        w, outer = self._tab_body()
        cfg = self.main_window.config_data
        form = self._group(outer, "演奏")
        self.player_play_mode_check = QCheckBox("演奏モード（実験的）")
        self.player_play_mode_check.setChecked(bool(cfg.get("player_play_mode", False)))
        form.addRow(self.player_play_mode_check)
        form.addRow(self._hint(
            "コースを選んだあとに「再生／演奏」を選ぶ画面を出します。演奏を選ぶと、"
            "D F J K（左縁・左面・右面・右縁）で自分で叩いて判定・スコア・魂ゲージが"
            "動きます。曲の終わりの演出やリザルトはまだありません。"))
        form = self._group(outer, "選曲画面のBGM")
        self.select_bgm_check = QCheckBox("譜面を開いていないあいだ BGM を流す")
        self.select_bgm_check.setChecked(bool(cfg.get("player_select_bgm", True)))
        form.addRow(self.select_bgm_check)
        self.select_bgm_vol = QSpinBox()
        self.select_bgm_vol.setRange(0, 100)
        self.select_bgm_vol.setSuffix(" %")
        self.select_bgm_vol.setValue(
            int(round(float(cfg.get("player_select_bgm_volume", 0.6) or 0.0) * 100)))
        vol_label = QLabel("音量")
        form.addRow(vol_label, self.select_bgm_vol)
        self._bind_enabled(self.select_bgm_check, vol_label, self.select_bgm_vol)
        form.addRow(self._hint(
            "NeoTJAPlayer で譜面を開く前の画面に流れる曲の音量です。"
            "曲の音量に対する比率で、100% なら譜面と同じ音量になります。"))
        # 旧 NeoTJAPlayer 専用の設定で、v15.4.0 で Player を外してからは何にも
        # 効かない。値は新しい Player で使うかもしれないので消さずに残し、枠ごと
        # 隠す(利用者の承認 2026-11-16)。
        form.parentWidget().hide()

        outer.addStretch()
        return w

    def _build_dev_experimental_tab(self):
        """実験的機能と開発者用を1枚に積む。

        中身はこれまでの2枚そのまま。先に実験的機能(使う人が触るのはこちら)、
        あとに開発者用を置く。"""
        w, outer = self._tab_body()
        for build in (self._build_experimental_tab, self._build_developer_tab):
            inner = build()
            lay = inner.layout()
            if lay is not None:
                lay.setContentsMargins(0, 0, 0, 0)
            outer.addWidget(inner)
        outer.addStretch()
        return w

    def _build_developer_tab(self):
        """作る側だけが使う道具。遊ぶ人には要らないものを分けてある。"""
        w, outer = self._tab_body()
        cfg = self.main_window.config_data
        d = settings_mod.default_settings()

        form = self._group(outer, "位置合わせ")
        self.np_tuner_check = QCheckBox("位置合わせのキーを有効にする")
        self.np_tuner_check.setChecked(
            bool(cfg.get("show_tuner", d["show_tuner"])))
        form.addRow(self.np_tuner_check)
        form.addRow(self._hint(
            "再生ウィンドウで Ctrl+Shift+矢印 などを使い、ネームプレートや"
            "虹の部品を 1px ずつ動かせるようになります。"))
        form.addRow(self._hint(
            "Ctrl+Shift+N / B で対象を選ぶ、Ctrl+Shift+M で「位置 ↔ 大きさ」を"
            "切り替える、Alt を足すと 5px、Ctrl+Shift+0 で全部を起動時の値へ"
            "戻す、Ctrl+Shift+S で今の値を tune.txt へ書き出す。"))
        form.addRow(self._hint(
            "動かした値は設定には保存されません。書き出した tune.txt の中身を"
            "ソースの定数へ書き写して固定する使い方です。"
            "※反映にはアプリの再起動が必要です。"))

        outer.addStretch()
        return w

    def _build_display_tab(self):
        """譜面プレビューの描き方。どれも反映にアプリの再起動が要る
        (描画の面はアプリを作るときに1回だけ決まるため)。"""
        w, outer = self._tab_body()
        cfg = self.main_window.config_data

        form = self._group(outer, "描画")
        self.gpu_render_check = QCheckBox("GPUで描画する")
        self.gpu_render_check.setChecked(cfg.get("gpu_render", True))
        form.addRow(self.gpu_render_check)
        form.addRow(self._hint(
            "ゲーム画面の描画をグラフィックボードに任せます。実測で1コマ"
            "3.4ms → 1.4ms。絵は変わりません。※反映にはアプリの再起動が"
            "必要です。グラフィック周りがおかしいときは切ってください"
            "（切っても今までどおり動きます）。"))

        self.gpu_vsync_check = QCheckBox("モニタの表示に同期する")
        self.gpu_vsync_check.setChecked(cfg.get("gpu_vsync", False))
        # GPU で描かないなら同期する面が無いので、触れないようにする。
        self.gpu_vsync_check.setEnabled(self.gpu_render_check.isChecked())
        self.gpu_render_check.toggled.connect(self.gpu_vsync_check.setEnabled)
        form.addRow(self.gpu_vsync_check)
        _vsync_hint = self._hint(
            "モニタの書き換えに合わせて1コマずつ出します。絵が横に裂ける現象"
            "（ティアリング）が出なくなり、fps はモニタの数値ちょうどに揃います。"
            "代わりに、次の書き換えを待つあいだアプリ全体が止まります"
            "（120Hzなら8.3ms刻み）。入力や音のもたつきが気になるときは"
            "切ってください。「GPUで描画する」が切ってあるときは関係ありません。")
        # 説明文はチェックとは別のウィジェットなので、放っておくと
        # チェックだけ薄くなって説明だけ濃いままになる。道連れにする。
        _vsync_hint.setEnabled(self.gpu_render_check.isChecked())
        self.gpu_render_check.toggled.connect(_vsync_hint.setEnabled)
        form.addRow(_vsync_hint)

        form2 = self._group(outer, "最大fps")
        self.max_fps_spin = QSpinBox()
        self.max_fps_spin.setRange(20, 1000)
        self.max_fps_spin.setSingleStep(10)
        self.max_fps_spin.setSuffix(" fps")
        # 0(=無制限)は 12.1.4 の既定。数値で持たせる形に変えたので、
        # 古い設定ファイルを開いたときは上限いっぱいとして見せる。
        _cap = int(cfg.get("preview_max_fps", 1000) or 0)
        self.max_fps_spin.setValue(1000 if _cap <= 0 else max(20, min(1000, _cap)))
        form2.addRow("譜面プレビューの上限", self.max_fps_spin)
        form2.addRow(self._hint(
            "譜面プレビューを1秒に何コマまで描き直すか。20〜1000、既定は1000。"
            "GPU描画で実際に出るのは360前後なので、既定のままなら頭打ちに"
            "なりません。非力な機械で負荷を抑えたいときは 60 などに下げて"
            "ください。上のモニタ同期を入れている場合は、この値より先に"
            "モニタの数値で頭打ちになります。"))

        outer.addStretch()
        return w

    def _build_experimental_tab(self):
        """まだ様子見の機能をまとめて置くタブ。既定は全部オフ、有効化しても
        すぐには反映されずアプリの再起動が要るものが多い(この点は各項目の
        説明文で個別に断る)。"""
        w, outer = self._tab_body()
        cfg = self.main_window.config_data

        form = self._group(outer, "譜面プレビュー")
        self.legacy_modes_check = QCheckBox("以前のモードに戻す")
        self.legacy_modes_check.setChecked(
            bool(cfg.get("preview_modes_legacy", False)))
        form.addRow(self.legacy_modes_check)
        form.addRow(self._hint(
            "再生ウィンドウのモードを、12.x までの「通常再生 / 軽量 / 音声波形 / "
            "情報」に戻します。オフ（既定）は 13.0.0 からの"
            "「通常再生 / 軽量 / 同時再生 / 作譜」です。\n"
            "※戻すと同時再生と作譜は使えません。反映にはアプリの再起動が必要です。"))

        self.tail_measure_check = QCheckBox(
            "作譜: 譜面の末尾に空の小節を用意しておく")
        self.tail_measure_check.setChecked(
            bool(cfg.get("chart_edit_tail_measure", True)))
        form.addRow(self.tail_measure_check)
        form.addRow(self._hint(
            "譜面を書いたあと、いちばん後ろに空の小節を1つ足しておきます。"
            "続きを打つ場所が見えるようになります。"))

        self.command_panel_follow_check = QCheckBox(
            "命令パネルの数値を、カーソルの位置に合わせる")
        self.command_panel_follow_check.setChecked(
            bool(cfg.get("command_panel_follow", True)))
        form.addRow(self.command_panel_follow_check)
        form.addRow(self._hint(
            "作譜モードの命令パネル（BPM・拍子記号・スクロール）の数値を、"
            "カーソルを動かしたときと再生中に、その位置で効いている値へ"
            "合わせ続けます。自分で打ち込んでいる欄（カーソルが入っている欄）は"
            "書き換えません。オフにすると、入れた数字がそのまま残ります。"))

        # 枠の名前は以前「NeoTJAPlayer」だった。内蔵の再生画面のことなのに
        # 旧 Player(別 exe、v15 で廃止)と紛らわしいので「プレビュー」にした。
        form2 = self._group(outer, "プレビュー")
        self.arrange_ref_check = QCheckBox("アレンジ譜面")
        self.arrange_ref_check.setChecked(cfg.get("arrange_ref", False))
        form2.addRow(self.arrange_ref_check)
        form2.addRow(self._hint(
            "表譜面（おに）を本家、裏譜面（うら）をアレンジとみなして重ねた"
            "「アレンジ」をコース選択に追加します。裏にだけある音符—"
            "アレンジで足した音符—が薄く出るので、どこを埋めたのかが"
            "見えます。おにとうらの両方がある譜面でだけ出ます。"))


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
        ときだけ、起動時と同じ探し方(exe の隣)に任せる。"""
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

        cfg["check_updates_on_startup"] = self.check_updates_check.isChecked()
        cfg["auto_save_enabled"] = self.auto_save_check.isChecked()
        cfg["se_text_enabled"] = self.se_text_check.isChecked()
        cfg["play_flush_before_play"] = self.play_flush_check.isChecked()
        cfg["note_input_sound"] = self.note_input_sound_check.isChecked()

        cfg["record_output_dir"] = self.rec_dir_edit.text()
        cfg["system_dir"] = self.system_dir_edit.text()
        cfg["warn_missing_system"] = self.warn_missing_system_check.isChecked()
        cfg["discord_presence"] = self.discord_check.isChecked()
        cfg["discord_show_editing"] = self.discord_editing_check.isChecked()
        cfg["hit_sound_don_path"] = self.hit_don_edit.text()
        cfg["hit_sound_ka_path"] = self.hit_ka_edit.text()
        cfg["hit_sound_use_custom"] = self.hit_custom_check.isChecked()
        cfg["preview_modes_legacy"] = self.legacy_modes_check.isChecked()
        cfg["command_panel_follow"] = self.command_panel_follow_check.isChecked()
        cfg["chart_edit_tail_measure"] = self.tail_measure_check.isChecked()
        cfg["player_play_mode"] = self.player_play_mode_check.isChecked()
        cfg["gpu_render"] = self.gpu_render_check.isChecked()
        cfg["gpu_vsync"] = self.gpu_vsync_check.isChecked()
        cfg["preview_max_fps"] = int(self.max_fps_spin.value())

        cfg["audio_output_device"] = self.audio_device_combo.currentData() or ""
        cfg["wireless_offset_enabled"] = self.wireless_check.isChecked()
        cfg["wireless_offset_ms"] = float(self.wireless_spin.value())
        cfg["player_select_bgm"] = self.select_bgm_check.isChecked()
        cfg["player_select_bgm_volume"] = self.select_bgm_vol.value() / 100.0

        # 見本を描くのに使っている辞書をそのまま入れる。二か所で並べると
        # 片方に足し忘れて「見本では変わるのに保存されない」ことになる。
        cfg.update(self._nameplate_values())
        # 「保存して適用」= 選んでいるネームプレートにも上書きする(利用者に
        # 任された 2026-11-15。押したのに一覧の中身が古いまま、を無くす)。
        self._np_save_profile()
        cfg["nameplate_profiles"] = copy.deepcopy(self._np_profiles)
        cfg["nameplate_profile_index"] = max(0, self.np_profile_combo.currentIndex())
        cfg["show_tuner"] = self.np_tuner_check.isChecked()
        cfg["arrange_ref"] = self.arrange_ref_check.isChecked()
        # 画面へ映すのは呼び出し元(PreviewDock.refresh_nameplate)がやる。
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
            "・音声 (出力デバイス、打音のWAVパス、ワイヤレス調整、選曲画面のBGM)\n"
            "・表示 (GPU描画・モニタ同期・最大fps)\n"
            "・ネームプレート (名前・称号・段位)\n"
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
