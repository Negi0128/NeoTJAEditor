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

from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QBrush, QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from neotja.player.difficulty import DifficultyPage
from neotja.player.library_view import LibraryView, library_qss
from neotja.start_screen import StartScreen, dropped_tja_path, wordmark_family
from neotja.theme import COLORS, glass_colors
from neotja.theme import with_alpha as _c, rgba as _rgba


class PlayerHost(QWidget):
    back_requested = Signal()
    song_chosen = Signal(str)
    play_requested = Signal(str, str)     # (tja, コースキー)
    record_requested = Signal(str, str)

    def __init__(self, config_data, save_cb, parent=None):
        super().__init__(parent)
        self.setObjectName("playerHost")
        self.setAcceptDrops(True)
        self.cfg = config_data
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

        self.body = QStackedWidget()
        v.addWidget(self.body, 1)
        self.library = LibraryView(config_data, save_cb)
        self.library.song_chosen.connect(self._on_song_chosen)
        self.body.addWidget(self.library)
        self.difficulty = DifficultyPage(config_data, save_cb)
        self.difficulty.back_requested.connect(self.show_library)
        self.difficulty.play_requested.connect(self.play_requested)
        self.difficulty.record_requested.connect(self.record_requested)
        self.body.addWidget(self.difficulty)

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

    def _set_page(self, page):
        full = page is self.difficulty
        self.top_bar.setVisible(not full)
        self.top_rule.setVisible(not full)
        self.body.setCurrentWidget(page)

    def show_difficulty(self, path):
        self.difficulty.close_settings()
        self.difficulty.set_song(path)
        self._set_page(self.difficulty)
        self.difficulty.setFocus()

    def show_library(self):
        """難易度選択の「戻る」。選曲は作り直さないので、開いていた
        フォルダ・検索・並び・選んだ行はそのまま。"""
        self.difficulty.close_settings()
        self._set_page(self.library)

    def shutdown(self):
        self.library.shutdown()

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
                           padding: 6px 14px; font-size: 14px; color: %s; }
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
