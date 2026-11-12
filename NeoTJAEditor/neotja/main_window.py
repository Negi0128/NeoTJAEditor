import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

from PySide6.QtCore import Qt, QEvent, QTimer, QByteArray, QThread, Signal, QPointF
from PySide6.QtGui import (
    QFont, QFontInfo, QKeySequence, QShortcut, QTextCursor,
    QPainter, QPixmap, QColor, QBrush, QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QLineEdit, QProgressDialog, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QStackedWidget, QStatusBar, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)

from neotja import measure_edit
from neotja import note_edit
from neotja import settings as settings_mod
from neotja.analysis_worker import AnalysisWorker
from neotja.child_env import child_env
from neotja.constants import APP_NAME, VERSION, new_file_text
from neotja.find_replace import FindReplaceBar
from neotja.editor_widget import TJAEditor
from neotja.theme import COLORS, glass_colors, is_dark_theme
from neotja.theme import with_alpha as _c, rgba as _rgba
from neotja.highlighter import TJAHighlighter, compute_highlight_data
from neotja.ai_chart_gen import build_ai_variant_content
from neotja.audio_engine import BpmOffsetDetectWorker, ChartGenWorker
from neotja.preview_dock import PreviewDock, parse_preview_headers
from neotja.ruler_widget import RulerWidget
from neotja.start_screen import StartScreen, GlassCard
from neotja.theme import apply_theme
from neotja.tja_analyzer import TJACourseAnalyzer


def find_non_cp932_chars(content: str):
    """TJA は ANSI (cp932) で保存するのが前提だが、絵文字や拡張漢字など
    cp932 に無い文字はそのままでは書けない。以前は errors="replace" で
    無言のうちに置換していて原文が壊れていたため、書く前にここで洗い出す。

    戻り値は [(行番号(1始まり), 文字), ...]。すべて cp932 で表せるなら空リスト。
    まず全体を1回 encode してみて、成功すればそこで打ち切る(圧倒的多数を
    占める通常のファイルでは C 実装の encode 1回分のコストしかかからない)。"""
    try:
        content.encode("cp932")
        return []
    except UnicodeEncodeError:
        pass

    bad = []
    for line_no, line in enumerate(content.split("\n"), start=1):
        try:
            line.encode("cp932")
        except UnicodeEncodeError:
            for ch in line:
                try:
                    ch.encode("cp932")
                except UnicodeEncodeError:
                    bad.append((line_no, ch))
    return bad


class ResizeNotifier(QWidget):
    """大きさが変わったら知らせる入れ物(resize_cb)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.resize_cb = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cb = self.resize_cb
        if cb is not None:
            cb()


class PlayerPane(ResizeNotifier):
    """右ペイン(v15 統合画面)。幅が変わったら知らせる(再生画面の倍率を
    取り直すため)。"""


class BottomRow(ResizeNotifier):
    """右下の段の入れ物。起動画面と同じ「ガラスと光」(利用者の承認
    2026-11-12)の地。地色の上に、やわらかい光を 2 つ置く(アクセント色を
    右上に、空色を左下にもっと薄く)。半透明のカードはこの光の上に乗るので、
    カードごとに少しずつ明るさが違って見える — 平らな面が並ぶより奥行きが
    出る、という見本の要。

    光は QPixmap に焼いて持ち、大きさかテーマが変わったときだけ描き直す。
    カードや札が描き直るたびに下の光も(その範囲だけ)描くことになるが、
    焼いた絵を写すだけなので放射グラデーションの計算は走らない。上の
    再生画面(GL)は別のウィジェットで、ここを描いてもあちらは描き直らない。
    タイマーで描き直すことはしない(fps を落とさないため)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._glow = None

    def paintEvent(self, ev):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        key = (w, h, COLORS["bg"], COLORS["accent"])
        if self._glow is None or self._glow[0] != key:
            g = glass_colors()
            pm = QPixmap(w, h)
            pm.fill(QColor(COLORS["bg"]))
            q = QPainter(pm)
            q.setRenderHint(QPainter.Antialiasing)
            q.setPen(Qt.NoPen)
            # 半径は段の幅に比例。段は横長なので、縦に対しては大きく
            # はみ出すが、はみ出したぶんは切れるだけ。
            for (cx, cy, rad, col, a) in (
                    (w * 0.62, h * 0.10, max(260.0, w * 0.36), COLORS["accent"], g["glow1_alpha"] * 0.8),
                    (w * 0.08, h * 0.95, max(160.0, w * 0.18), COLORS["ka"], g["glow2_alpha"])):
                grad = QRadialGradient(cx, cy, rad)
                grad.setColorAt(0, _c(col, a))
                grad.setColorAt(0.55, _c(col, a * 0.35))
                grad.setColorAt(1, _c(col, 0))
                q.setBrush(QBrush(grad))
                q.drawEllipse(QPointF(cx, cy), rad, rad)
            q.end()
            self._glow = (key, pm)
        p = QPainter(self)
        p.drawPixmap(0, 0, self._glow[1])
        p.end()


class CourseCard(GlassCard):
    """Sidebar card showing one course's stats (notes/measures/time/roll/balloon),
    with a collapsible roll-by-roll detail list.

    見た目は起動画面と同じガラスのカード(GlassCard が塗る)。コース色は
    見出しの札に出す — 以前はカードの枠と見出しの帯をコース色で塗って
    いたが、半透明のカードにべた塗りの帯が乗ると札だけ別の部品に見える。"""

    #: 見出しを押した。本文のそのコースの所へ跳ぶ(コースの key)。
    jumpRequested = Signal(str)

    def __init__(self, course, parent=None):
        super().__init__(radius=12, parent=parent)
        self.details_visible = False
        self._label = course["label"]
        self._key = course.get("key", "")
        self._color = course["color"]

        outer = QVBoxLayout(self)
        # ガラスの縁(1px)の内側に見出しの札を置く。
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        # 見出しは押すと中身を畳む。コースが何枚も並ぶと縦に長くなるので、
        # 見ないコースは畳んでおけるようにする(利用者の指定 2026-10-01)。
        # 既定は開いたまま — 畳んだ状態を既定にすると、何も出ていない帯だけが
        # 並んでいるように見える。
        header = QPushButton(f"  {course['label']}")
        header.setFlat(True)
        header.setCursor(Qt.PointingHandCursor)
        header.setFocusPolicy(Qt.NoFocus)
        self.header = header
        self.refresh_theme()
        # **押したら本文のそのコースへ跳ぶ**(利用者の指定 2026-10-17)。
        # 畳むほうは右クリックへ移した — 跳ぶほうが使う回数が多い。
        header.clicked.connect(
            lambda: self.jumpRequested.emit(self._key))
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda _p: self.toggle_body())
        header.setToolTip("押すと本文のこのコースへ跳びます"
                          "（右クリックで畳む）")
        outer.addWidget(header)
        self.body_visible = True

        # 右下の段(設定欄 5 段ぶん、約 160px)に**丸ごと**入れる(利用者の
        # 指定 2026-11-12「下が切れている」)。行間は 0、余白は最小、
        # 時間+小節 / ノーツ+BPM は 1 行にまとめて 6 行 + ボタン 1 行。
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(9, 3, 9, 5)
        body_layout.setSpacing(0)
        outer.addWidget(body)

        self.lbl_time = QLabel()       # 時間 ／ 小節
        self.lbl_notes = QLabel()      # ノーツ ／ BPM
        # 音符の内訳と BPM の幅(利用者の希望 2026-11-04。tja-tools に
        # あるものを入れてほしい、という要望から)。
        self.lbl_breakdown = QLabel()
        self.lbl_breakdown.setToolTip(
            "ドン/カッは大音符もそれぞれに数えています"
            "(tja-tools の Don+DON / Ka+KA と同じ数え方)。" + chr(10) +
            "括弧はドン:カッの割合です。連打と風船は数に入れません。")
        self.lbl_density = QLabel()
        self.lbl_density.setToolTip(
            "最初の音符から最後の音符までで割った、音符の詰まり具合。\n"
            "連打と風船は数に入れません(上の「ノーツ」と同じ数え方)。")
        self.lbl_roll = QLabel()
        self.lbl_balloon = QLabel()
        for lbl in (self.lbl_time, self.lbl_notes, self.lbl_breakdown,
                    self.lbl_density, self.lbl_roll, self.lbl_balloon):
            body_layout.addWidget(lbl)

        # 連打詳細のボタンは**カードの下端**に置く。カードは段の高さいっぱいに
        # 伸ばすので(_build_sidebar)、字の行とボタンのあいだを伸び縮みさせて
        # 下に隙間を残さない(利用者の指定 2026-11-13「常に下までちゃんと
        # 埋めて欲しい」)。開くと下へ伸び、そのときは段の中でスクロールする。
        body_layout.addStretch(1)
        self.btn_toggle = QPushButton("連打詳細 [開く]")
        self.btn_toggle.setFlat(True)
        self.btn_toggle.setFixedHeight(18)
        self.btn_toggle.setStyleSheet("QPushButton { padding: 0px 4px; }")
        self.btn_toggle.clicked.connect(self._toggle_details)
        body_layout.addWidget(self.btn_toggle)

        self.details_frame = QWidget()
        self.details_layout = QVBoxLayout(self.details_frame)
        self.details_layout.setContentsMargins(8, 0, 0, 0)
        self.details_layout.setSpacing(0)
        body_layout.addWidget(self.details_frame)
        self.details_frame.setVisible(False)

        self.body = body
        #: 右下の段に余りがあるときの拡大率(set_scale)。字の元の大きさは
        #: テーマの既定(pointSize が無い環境もあるので QFontInfo で読む)。
        self._scale = 1.0
        self._base_pt = QFontInfo(self.font()).pointSizeF()
        self._refresh_header()
        self.update_course(course)

    def refresh_theme(self):
        """見出しの札の見た目(テーマを替えたら当て直す)。

        コース色を薄く敷いた札に、コース色の太字 — 起動画面の最近使った
        項目に並ぶ難易度チップと同じ作り。上の角だけ丸めてカードの角丸
        (12px)に沿わせる。明るいテーマではコース色が白地で薄く見えるので
        字だけ少し濃くする。"""
        g = glass_colors()
        col = QColor(self._color)
        fg = col if is_dark_theme() else col.darker(135)
        self.header.setStyleSheet(
            "QPushButton { text-align: left; border: none;"
            " border-bottom: 1px solid %s;"
            " border-top-left-radius: 11px; border-top-right-radius: 11px;"
            " background-color: %s; color: %s; font-weight: bold;"
            " padding: 2px 4px; }"
            "QPushButton:hover { background-color: %s; }"
            % (_rgba(_c(col, g["chip_border_alpha"] * 0.5)),
               _rgba(_c(col, g["chip_fill_alpha"])), fg.name(),
               _rgba(_c(col, g["chip_fill_alpha"] * 1.6))))

    def set_scale(self, f):
        """右下の段が高いとき(作譜モードなど)、字と行間を少し大きくして
        余りを使う。上限 1.25 — それ以上は 1 行が札の幅(270px 基準)を
        越えて切れる。幅のほうは MainWindow が同じ比で広げる。"""
        f = max(1.0, min(1.25, float(f)))
        if abs(f - self._scale) < 0.01:
            return
        self._scale = f
        font = QFont(self.font())
        font.setPointSizeF(self._base_pt * f)
        # 子にも1つずつ当てる。アプリにスタイルシートが入っていると親の
        # setFont は子へ伝わらない(実測 2026-11-12: 親 10.5pt、札 9pt のまま)。
        for wd in [self] + self.findChildren(QWidget):
            wd.setFont(font)
        # 行間は 0 → 3px まで。字だけ大きくすると詰まって見えるので。
        self.body.layout().setSpacing(int(round(12 * (f - 1.0))))
        self.btn_toggle.setFixedHeight(int(round(18 * f)))

    def toggle_body(self, on=None):
        """カードの中身を開閉する。on を渡せばその状態にする。"""
        self.body_visible = (not self.body_visible) if on is None else bool(on)
        self.body.setVisible(self.body_visible)
        self._refresh_header()

    def _refresh_header(self):
        # 畳んでいるときだけ三角を向きで示す(開いているときは何も足さない —
        # 今までの見た目をできるだけ変えないため)。
        mark = "" if self.body_visible else "  ▸"
        self.header.setText("  %s%s" % (self._label, mark))

    def _toggle_details(self):
        self.details_visible = not self.details_visible
        self.details_frame.setVisible(self.details_visible)
        self.btn_toggle.setText("連打詳細 [閉じる]" if self.details_visible else "連打詳細 [開く]")

    def update_course(self, course):
        self.lbl_time.setText(
            f"時間: {course['time']}　／　小節: {course['measures']}")

        # 内訳と、ドン:カッの割合(利用者の指定 2026-11-04)。音符が1つも
        # 無いコースでは出さない(「0 / 0」は読む意味が無い)。大音符の数は
        # 出さない — ドン/カッそれぞれに数え込んである。
        don = course.get("don_count") or 0
        ka = course.get("ka_count") or 0
        if don or ka:
            tot = float(don + ka)
            self.lbl_breakdown.setText(
                f"　ドン {don} / カッ {ka}"
                f"　({don / tot * 100:.2f}% : {ka / tot * 100:.2f}%)")
            self.lbl_breakdown.setVisible(True)
        else:
            self.lbl_breakdown.setVisible(False)

        # BPM の幅。変わらない曲は1つだけ出す。
        b0 = course.get("bpm_min") or 0.0
        b1 = course.get("bpm_max") or 0.0

        def _bpm(v):
            # 150.0 は 150 と出す。小数が要るものだけ小数で。
            return f"{v:.3f}".rstrip("0").rstrip(".")

        # ノーツと同じ行に出す(BPM が無い譜面はノーツだけ)。
        notes = f"ノーツ: {course['notes']}"
        if b1 > 0:
            notes += "　／　BPM: " + (_bpm(b0) if abs(b1 - b0) < 1e-9
                                    else f"{_bpm(b0)}〜{_bpm(b1)}")
        self.lbl_notes.setText(notes)

        dens = course.get("density") or 0.0
        self.lbl_density.setText(
            f"平均密度: {dens:.2f} 打/秒" if dens > 0 else "平均密度: ―")

        rolls = course.get("rolls_info", [])
        total_roll_dur = sum(r["duration"] for r in rolls)
        total_roll_hits = sum(r["hits"] for r in rolls)
        self.lbl_roll.setText(f"連打総計: {total_roll_dur:.2f}秒 ({total_roll_hits}打)")

        while self.details_layout.count():
            item = self.details_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if rolls:
            for i, r in enumerate(rolls, 1):
                self.details_layout.addWidget(QLabel(f"  {i}本目: {r['duration']:.2f}秒 ({r['hits']}打)"))
            self.btn_toggle.setEnabled(True)
            self.btn_toggle.setText("連打詳細 [閉じる]" if self.details_visible
                                    else "連打詳細 [開く]")
        else:
            # 連打の無い譜面でもボタンは消さない。消すとカードの下が空いて
            # 見え、譜面によって並びが変わる。押せない形で残す。
            self.btn_toggle.setEnabled(False)
            self.btn_toggle.setText("連打詳細 (連打なし)")
            self.details_frame.setVisible(False)
            self.details_visible = False

        balloons = course.get("balloons_info", [])
        total_balloon_hits = sum(b["hits"] for b in balloons)
        self.lbl_balloon.setText(f"風船総計: {len(balloons)}個 ({total_balloon_hits}打)")


class _ArrowLineEdit(QLineEdit):
    """↑↓キーとホイールで stepped(±1) を出す字の欄(右下の BPM 用)。

    QDoubleSpinBox だと打った字を自分の形(3桁など)に直してしまう。
    ヘッダの BPM: は打ったとおり残したいので、字の欄のまま矢印の操作
    だけ足す。"""
    stepped = Signal(int)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Up:
            self.stepped.emit(1)
            return
        if event.key() == Qt.Key_Down:
            self.stepped.emit(-1)
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        # 命令パネルの箱と同じく、欄の上でホイールを回しても ±1。
        # 欄に入っている(フォーカスがある)ときだけ。素通りすると
        # 設定欄全体が転がる心配はないが、狙わず回して値が動くのを防ぐ。
        if not self.hasFocus():
            event.ignore()
            return
        d = event.angleDelta().y()
        if d:
            self.stepped.emit(1 if d > 0 else -1)
        event.accept()


class _ToastStatusBar(QStatusBar):
    """隠したままの QStatusBar。showMessage の文はレーンのトーストへ回す。

    v15 で下の帯は TJA 側の細い帯(文字コード/テーマ/倍率)だけになり、
    QStatusBar は出さない(右ペインがそのぶん高くなる)。ただし
    statusBar().showMessage(...) は合作・作譜・保存など何十か所から
    呼ばれているので、呼び手は触らずここ 1 か所で受け止める。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        #: 文を見せる先。(text, seconds) を受ける。無ければ黙って捨てる。
        self.toast_cb = None

    def showMessage(self, text, timeout=0):
        super().showMessage(text, timeout)
        cb = self.toast_cb
        if cb is None or not str(text).strip():
            return
        # timeout 0 は「消すまで出しっぱなし」。トーストは消えるものなので
        # 4 秒にそろえる。
        secs = (timeout / 1000.0) if timeout and timeout > 0 else 4.0
        try:
            cb(str(text), secs)
        except Exception:  # noqa: BLE001
            pass


class MainWindow(QMainWindow):
    # プレビューの組み立てがこの秒数を超えたら、解析パスと同時にやらず
    # 少し後ろへ回す(重い譜面で打鍵停止時の固まりを 1 回あたり半分にする)。
    # 普通の譜面は数 ms なので、これまでどおり解析と同時に更新される。
    PREVIEW_DEFER_SEC = 0.08

    # ワーカースレッドへ「保留中のジョブを処理しろ」と伝えるための合図。
    # キュー接続なので、emit した時点では何も起きず、向こうのイベント
    # ループが回ったときに AnalysisWorker.process() が呼ばれる。
    analysis_requested = Signal()

    def __init__(self, app):
        super().__init__()
        self.app = app

        self.config_data = settings_mod.load_settings()
        apply_theme(app, self.config_data.get("theme", "dark"))

        self.analyzer = TJACourseAnalyzer(self.config_data)
        self.current_file = None
        self.courses_info = []
        self._preview_course_override = None
        # 譜面分岐。既定は「自動」= 本家と同じく #BRANCHSTART の条件で区間
        # ごとに決まる。普通/玄人/達人を選ぶと、そこだけを通して流す
        # (作譜モードで系統を選んで編集するとき用)。
        self._preview_branch_level = note_edit.BRANCH_AUTO
        # 直近の解析が返した「区間ごとの系統」。編集の書き込み先に使う。
        self._preview_branch_path = []
        # プレビュー(NeoTJAPlayer/情報/打音/メトロノーム)が映す譜面テキスト。
        # 入力中のバッファではなく、最後に保存/読込した内容を保持する。保存/開く/
        # 新規のときだけ更新し、打鍵では変えない(=プレビューは保存済みの譜面を
        # 映す)。_preview_courses はその内容のコース解析結果のキャッシュ。
        self._preview_content = ""
        self._preview_courses = []
        # 行番号 -> (小節番号, 譜面時刻[秒], コース番号) の索引。解析パスで
        # 作り直し、ステータスバーとプレビュー再構築の要否判定が O(1) で引く。
        # 数万行の譜面ではこれが無いとカーソル移動1回で 70ms 以上かかっていた。
        self._cursor_index = {}
        # ファイルを開くとき、setPlainText の前に済ませておいた解析結果
        # (content, courses_info, HighlightData)。直後の _heavy_analysis が
        # 同じ内容なら作り直さずにこれを使う。使ったら即座に捨てる。
        # 詳しくは _precompute_for_load。
        self._preload_analysis = None
        # 直近にプレビュー/メトロノームを組み立てたときの入力条件。同じなら
        # 組み直しても結果が1ビットも変わらないので丸ごと省く(下記 _preview_key)。
        self._preview_key = None
        # 直近のプレビュー組み立てにかかった秒数。これが PREVIEW_DEFER_SEC を
        # 超える重い譜面のときだけ、解析パスとプレビューを別々のタイミングに
        # 分けて、打鍵を止めたときの停止時間を一度あたり半分にする。
        self._preview_build_sec = 0.0
        # 構文ハイライト側の反映にかかった秒数(内訳を見るためだけの控え)。
        self._highlight_apply_sec = 0.0
        # 重い譜面のとき、ワーカーが用意したプレビュー結果を一旦ここへ置いて
        # 120ms 後に貼る。置いてある間に内容が変わったら捨てる。
        self._pending_preview = None
        self._heavy_timer = QTimer(self)
        self._heavy_timer.setSingleShot(True)
        self._heavy_timer.timeout.connect(self._heavy_tasks)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._preview_tick)
        # 直近に報告した解析エラーの種類。デバウンスタイマで何度も同じ例外が
        # 出るため、同じものはステータスバーに出し直さない(トレースも1回だけ)。
        self._last_heavy_error = None
        # cp932 で保存できない文字を自動保存が見つけたときの直近の報告内容。
        # 自動保存は打鍵のたびに走るので、同じ理由でスキップし続ける間は
        # 警告を出し直さない(モーダルは絶対に出さない)。
        self._last_autosave_encode_warning = None
        # ゲーム風プレビューの素材の先読みを予約したか(paintEvent 参照)。
        self._skin_warm_armed = False
        #: 起動したとき譜面プレビューを1回だけ開くための印。
        self._preview_opened_on_start = False

        self._metronome_timer = QTimer(self)
        self._metronome_timer.setSingleShot(True)
        self._metronome_timer.timeout.connect(self._update_metronome_schedule)

        # 解析をGUIスレッドの外へ。数万行の譜面では解析+プレビュー組み立てで
        # 650ms かかり、そのあいだウィンドウが完全に無反応になっていた。
        # 計算だけを向こうへ渡し、画面への反映はこちらで行う(ウィジェットを
        # 別スレッドから触ると即クラッシュするため、境界は analysis_worker.py
        # 側のコメントどおり厳守)。
        # 依頼には通し番号を振り、返ってきたときに最新でなければ捨てる。
        # 打鍵中は内容がどんどん変わるので、古い結果を貼ると画面が巻き戻る。
        self._analysis_job_id = 0
        self._analysis_inflight = 0     # 0 = 走っていない
        self._analysis_thread = QThread(self)
        self._analysis_worker = AnalysisWorker(self.analyzer)
        self._analysis_worker.moveToThread(self._analysis_thread)
        self.analysis_requested.connect(self._analysis_worker.process, Qt.QueuedConnection)
        self._analysis_worker.finished.connect(self._on_analysis_done, Qt.QueuedConnection)
        self._analysis_thread.start()

        self.resize(1280, 820)
        # .tja/.txt をウィンドウにドラッグして開けるようにする(dropEvent)。
        self.setAcceptDrops(True)
        self._refresh_title()

        # Set once the update batch has been armed and the exit is already
        # agreed to, so closeEvent doesn't re-ask and veto the shutdown the
        # updater is waiting on (see _run_update_download).
        self._updating = False

        # 開いている動画書き出しダイアログ(モーダルではないので、こちらで
        # 参照を持っていないと GC で消える)。二重起動よけの目印も兼ねる。
        self._record_dialog = None

        self._build_editor()
        self._build_toolbars()
        self._build_sidebar()
        self._build_central_layout()
        self._build_preview_dock()
        self._build_statusbar()
        self._build_menu()
        self._bind_shortcuts()

        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.cursorPositionChanged.connect(self._update_status)
        self.editor.cursorPositionChanged.connect(lambda: self._metronome_timer.start(150))
        self.editor.checkpointsChanged.connect(self._update_status)
        # エディタが正。Alt+P で付け外ししたぶんを譜面プレビューへ流す。
        self.editor.checkpointsChanged.connect(self._push_checkpoints_to_preview)
        self.editor.set_note_typed_cb(self._on_note_typed)

        self._rebuild_recent_menu()
        self._restore_window_state()
        self.new_file(confirm=False)
        # エディタにはひな型が入っているが、ファイルとしては何も開いていない
        # ので起動画面を出す。開く/作るが決まった時点で分割画面へ切り替わる。
        self._show_start_screen()

        # Alt単独の押下/離しでメニューバーへフォーカスが移る既定動作を止める
        # (eventFilter 参照)。QApplication へ登録するアプリ全体フィルタは、
        # QMenuBar が MainWindow へ独自に張っているオブジェクト個別フィルタ
        # (Alt単独リリースでメニューをハイライトする内蔵動作)より先に呼ばれる
        # ので、そちらへ生の Alt イベントが渡る前にここで握りつぶせる。
        #
        # 登録を組み立ての**あと**にしているのは速さのため。ここまでの組み立てで
        # アプリへ流れるイベントは(子の追加・整形など)3400 件あり、以前は
        # その全部が Python の eventFilter を通っていた(実測 20ms)。この
        # フィルタが握りつぶすのは Alt キーの生イベントだけで、それは利用者が
        # 触れるようになってからしか来ないので、遅らせても挙動は変わらない。
        self.app.installEventFilter(self)

        # A failed update can only be reported now: the batch that applies it
        # runs after the previous process is gone.
        QTimer.singleShot(300, self._report_failed_update)
        # 前回 Player だけ入れ替えられなかったときの拾い直し。
        QTimer.singleShot(900, self.check_player_pending)

        if self.config_data.get("check_updates_on_startup", True):
            QTimer.singleShot(1500, lambda: self.check_for_updates(manual=False))

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_editor(self):
        self.editor = TJAEditor()
        self.editor.set_mono_font(self.config_data.get("font_family", "Consolas"), self.config_data.get("font_size", 12))
        self.highlighter = TJAHighlighter(self.editor.document())
        self.ruler = RulerWidget(self.editor)

        self.find_bar = FindReplaceBar(self.editor)

        editor_container = QWidget()
        v = QVBoxLayout(editor_container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.find_bar)
        v.addWidget(self.ruler)
        v.addWidget(self.editor)
        self.editor_container = editor_container

    def _toolbar_button(self, toolbar, text, slot, accent=False):
        btn = QPushButton(text)
        if accent:
            btn.setObjectName("accentButton")
        btn.clicked.connect(slot)
        toolbar.addWidget(btn)
        return btn

    def _build_toolbars(self):
        self.toolbar_top = QToolBar("main")
        self.toolbar_top.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, self.toolbar_top)

        self._toolbar_button(self.toolbar_top, "新規", self.new_file_dialog)
        self._toolbar_button(self.toolbar_top, "開く", self.open_file)
        self._toolbar_button(self.toolbar_top, "フォルダを開く", self.open_folder)
        self._toolbar_button(self.toolbar_top, "保存", self.save_file, accent=True)
        self.btn_auto_save = QPushButton()
        self.btn_auto_save.setCheckable(True)
        self.btn_auto_save.setToolTip(
            "保存先(ファイル)が決まっている場合、編集の手が止まってから約0.6秒後に自動保存します。")
        self.btn_auto_save.toggled.connect(self._on_auto_save_toggled)
        self.btn_auto_save.setChecked(self.config_data.get("auto_save_enabled", False))
        self._sync_auto_save_button(self.btn_auto_save.isChecked())
        self.toolbar_top.addWidget(self.btn_auto_save)
        self._toolbar_button(self.toolbar_top, "元に戻す", self.editor.undo)
        self._toolbar_button(self.toolbar_top, "やり直す", self.editor.redo)
        self._toolbar_button(self.toolbar_top, "譜面画像生成", self.open_image_exporter)
        # 密度グラフ(利用者の希望 2026-11-04)。譜面画像生成の隣に置く
        # — どちらも「いま見ているコースを別窓で眺める」道具なので。
        self._toolbar_button(self.toolbar_top, "密度グラフ",
                             self.open_density_graph)
        self._toolbar_button(self.toolbar_top, "ヘルプ", self.open_help)

        # ハイスピ変換〜TJA校正は TJA 側だけの道具なので、窓の幅いっぱい
        # ではなくエディタの上に置く(v15 統合画面)。
        self.toolbar_bottom = QToolBar("tools")
        self.toolbar_bottom.setMovable(False)
        self.editor_container.layout().insertWidget(0, self.toolbar_bottom)
        # いちばん左は「難易度を追加」(利用者の指定 2026-11-12)。コースの
        # ひな型を書き足す道具で、ほかの変換より先に使うものなので先頭に。
        self._toolbar_button(self.toolbar_bottom, "難易度を追加", self.open_add_course)
        self._toolbar_button(self.toolbar_bottom, "ハイスピ変換", self.open_scroll_splitter)
        self._toolbar_button(self.toolbar_bottom, "リサイズ", self.open_measure_converter)
        self._toolbar_button(self.toolbar_bottom, "反転", self.reverse_don_ka)
        self._toolbar_button(self.toolbar_bottom, "ストロボ生成", self.open_strobe_tool)
        self._toolbar_button(self.toolbar_bottom, "TJA校正", self.open_tja_tidy)

    def _build_sidebar(self):
        self.sb_outer = QWidget()
        sb_layout = QVBoxLayout(self.sb_outer)
        sb_layout.setContentsMargins(0, 0, 0, 0)

        # 曲名・サブタイトルの札は置かない(利用者の指定 2026-11-12)。右下の
        # 設定欄の TITLE / SUBTITLE が同じものを出すので二重になる。

        # 連打秒速の欄は右下の設定欄(連打速度)へ移した(_build_bottom_controls)。
        self.sb_scroll = QScrollArea()
        self.sb_scroll.setWidgetResizable(True)
        # 枠も余白も付けない。カードには色の枠があるので二重になるし、
        # 右下の段の高さ(約 160px)をカードに全部使いたい。
        self.sb_scroll.setFrameShape(QFrame.NoFrame)
        self.sb_cards_container = QWidget()
        self.sb_cards_layout = QVBoxLayout(self.sb_cards_container)
        self.sb_cards_layout.setContentsMargins(0, 0, 0, 0)
        # カードはそれぞれがガラスの札なので、重ねず少し離す。
        self.sb_cards_layout.setSpacing(8)
        # 末尾の伸び縮みは置かない。見せるカードは1枚だけなので、それに
        # 段の高さを全部渡して下まで埋める(insertWidget の stretch=1)。
        self.sb_scroll.setWidget(self.sb_cards_container)
        sb_layout.addWidget(self.sb_scroll, 1)

        self._course_cards = {}

    def jump_to_course(self, key):
        """本文のそのコースの `COURSE:` の行へカーソルを移す。

        難易度パネルの見出しを押したときの行き先(利用者の指定 2026-10-17)。
        `#START` の手前にある `COURSE:` を探す — 書き方(Oni / おに / 4)が
        譜面ごとに違うので、行の字面ではなく解析結果の範囲から辿る。"""
        if not key:
            return
        text = self.editor.toPlainText()
        try:
            rng = self.analyzer.course_line_range(text, key)
        except Exception:  # noqa: BLE001
            rng = None
        if not rng:
            self.statusBar().showMessage("そのコースが本文に見つかりません", 4000)
            return
        lines = text.split("\n")
        line_no = rng[0]                      # #START の行(1 から数える)
        for i in range(rng[0] - 2, max(-1, rng[0] - 40), -1):
            if 0 <= i < len(lines) and lines[i].upper().lstrip().startswith("COURSE:"):
                line_no = i + 1
                break
        block = self.editor.document().findBlockByNumber(max(0, line_no - 1))
        cur = QTextCursor(block)
        self.editor.setTextCursor(cur)
        self.editor.centerCursor()
        self.editor.setFocus(Qt.OtherFocusReason)

    def _present_course_keys(self):
        """本文にあるコースの鍵(Easy/Normal/Hard/Oni/Edit)の集まり。

        courses_info は非同期の解析の結果なので、開いた直後や打った直後は
        古いことがある。ここは本文をその場で見る — COURSE:4 / Oni / おに
        の違いは course_line_range が course_key で吸収する。"""
        text = self.editor.toPlainText()
        keys = set()
        for key in ("Easy", "Normal", "Hard", "Oni", "Edit"):
            try:
                if self.analyzer.course_line_range(text, key):
                    keys.add(key)
            except Exception:  # noqa: BLE001
                pass
        return keys

    def open_add_course(self):
        """「難易度を追加」の窓を出し、OK なら本文の末尾にコースのひな型を書く。"""
        from neotja.dialogs.add_course_dialog import AddCourseDialog
        dlg = AddCourseDialog(self._present_course_keys(), self)
        if dlg.exec() != QDialog.Accepted:
            return
        self.add_course(dlg.course_value(), dlg.level())

    def add_course(self, course_value, level):
        """本文の末尾に COURSE:<course_value> のひな型を1つ書き足す。

        書く場所は末尾(最後の #END の後)。途中に挟むと、どのコースの
        BALLOON: かが変わってしまう(balloon_defs_for は #START から上へ
        たどる)ので、必ず最後に足す。手前に空行を1つ置いて前のコースと
        切り離す。1回の undo で消えるよう、1つの QTextCursor で書く。"""
        key = self.analyzer.course_key(course_value, fallback=None)
        if key is None:
            self.statusBar().showMessage("その難易度は書けません: %s" % course_value, 4000)
            return False
        if key in self._present_course_keys():
            self.statusBar().showMessage(
                "%s はもうこの譜面にあります" % self.analyzer.DIFF_LABEL.get(key, key), 4000)
            return False
        doc = self.editor.document()
        text = self.editor.toPlainText()
        # 末尾の空行を数えて「ちょうど1行の空行」になるように前置きを決める。
        tail = text.rstrip(chr(10))
        lead = "" if not tail else chr(10) * 2
        block = chr(10).join([
            "COURSE:%s" % key, "LEVEL:%d" % int(level), "BALLOON:",
            "SCOREINIT:", "SCOREDIFF:", "", "#START", "", "#END", ""])
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        # 末尾に改行がいくつあっても、本文の最後の字の直後から書き直す。
        if len(tail) < len(text):
            cursor.setPosition(len(tail), QTextCursor.MoveMode.KeepAnchor)
        start = cursor.selectionStart()
        cursor.insertText(lead + block)
        cursor.endEditBlock()
        # プログラム編集なので dirty と自動保存の対象を明示的に立てる
        # (open_measure_converter と同じ作法)。
        self.editor.mark_edited(start, cursor.position())
        self.setWindowModified(True)
        self._force_update()
        # カーソルは #START と #END の間の空行へ — すぐ音符を打てるように。
        empty_line = doc.blockCount() - 1 - 2     # 末尾: "" / "#END" / "" の並び
        cur = QTextCursor(doc.findBlockByNumber(max(0, empty_line)))
        self.editor.setTextCursor(cur)
        self.editor.centerCursor()
        self.editor.setFocus(Qt.OtherFocusReason)
        return True

    #: 再生画面の原寸の幅(px)。右ペインがこれより狭いときは縮めて入れる。
    PLAYER_W = 1280

    def _build_central_layout(self):
        """v15 統合画面: 左に TJA、右上に再生画面、右下に情報・操作。

        右ペインは PlayerPane。幅が変わるたびに再生画面の倍率を取り直す
        (_fit_player_to_pane)。再生画面そのものは固定サイズなので、
        入れ物(player_host)には並び(レイアウト)を付けず、固定サイズが
        外へ伝わらないようにしてある — 伝わると分割バーが 1280px より
        左へ動かせなくなる。"""
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.editor_container)

        self.right_pane = PlayerPane()
        rl = QVBoxLayout(self.right_pane)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.player_host = QWidget()
        rl.addWidget(self.player_host)
        # 下の段は再生画面の残りを全部もらう(rl の stretch)。再生画面の
        # 高さはモードで違う(作譜は低い)ので、段の高さもモードで変わる。
        # 余った高さは部品を大きくして使う(_scale_bottom_block)。
        self.bottom_row = BottomRow()
        self.bottom_row.setObjectName("bottomRow")
        hb = QHBoxLayout(self.bottom_row)
        hb.setContentsMargins(6, 6, 6, 6)
        # 箱と箱(難易度カード / BPM測定 / 設定欄)のあいだは余白だけ。箱が
        # ガラスの札(縁つき)になったので、仕切り線は要らない。
        hb.setSpacing(self.BLOCK_GAP)
        self.sb_outer.setFixedWidth(self.CARD_W)
        hb.addWidget(self.sb_outer)
        rl.addWidget(self.bottom_row, 1)
        self.bottom_row.resize_cb = self._scale_bottom_block
        splitter.addWidget(self.right_pane)

        splitter.setSizes([640, 1280])
        # 余った幅は左(TJA)へ。再生画面は原寸(1280)が上限なので、右を
        # それより広げても余白が増えるだけ。1:2 の比で分けると 1920 幅で
        # 右が 1277 になり、再生画面が 99.8% の縮小表示に落ちていた。
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        # どちらも畳ませない。0 幅まで畳むと取っ手が窓の端に貼り付いて、
        # つかめず開けなくなる(利用者の報告 2026-10-17)。
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        # 右下の設定欄(5 段。OFFSET と DEMOSTART が同じ段)の最小幅が
        # 1196 あるので、1400 幅の窓にも入るように左は 200 まで譲る。
        self.editor_container.setMinimumWidth(200)
        self.right_pane.setMinimumWidth(320)
        self.right_pane.resize_cb = self._fit_player_to_pane
        self.splitter = splitter   # 分割比の保存/復元に使う
        # 起動直後の画面(利用者の指定 2026-11-12)。何も開いていない間だけ
        # 分割画面の代わりに見せ、開いたら分割画面へ切り替える。メニューと
        # ツールバーはそのまま。分割画面は裏で組み上がっているので、再生
        # 画面やスキンの先読みは起動画面の後ろで従来どおり進む。
        self.start_screen = StartScreen()
        self.start_screen.new_from_url.connect(self.new_file_dialog)
        self.start_screen.new_from_file.connect(
            lambda: self.new_file_dialog(browse_audio=True))
        self.start_screen.open_requested.connect(self.open_file)
        self.start_screen.recent_activated.connect(self._open_recent)
        self.start_screen.help_requested.connect(self.open_help)
        self.start_screen.settings_requested.connect(self.open_settings)
        self.central_stack = QStackedWidget()
        self.central_stack.addWidget(self.start_screen)   # 0
        self.central_stack.addWidget(splitter)            # 1
        self.central_stack.setCurrentIndex(1)
        self.setCentralWidget(self.central_stack)

    def _show_start_screen(self):
        """起動画面を前に出す。起動時に1回だけ呼ぶ。

        上のツールバー(新規/開く/保存…)も隠す(利用者の指定 2026-11-12)。
        起動画面に同じ入口(新規作成/開く)があるので二重になるし、保存や
        元に戻すは開くものが無い間は押しても意味が無い。メニューバーは
        残す。キー(Ctrl+S など)は窓に張った QShortcut なので、ツール
        バーを隠しても効き方は変わらない(_bind_shortcuts)。"""
        self.start_screen.set_recent(self.config_data.get("recent_files", []))
        self.toolbar_top.hide()
        self.central_stack.setCurrentIndex(0)

    def _leave_start_screen(self):
        """起動画面を片付けて分割画面へ。開く/新規作成が決まったときに呼ぶ。
        何度呼んでも害は無い(すでに分割画面なら何もしない)。"""
        stack = getattr(self, "central_stack", None)
        if stack is not None and stack.currentIndex() != 1:
            stack.setCurrentIndex(1)
        tb = getattr(self, "toolbar_top", None)
        if tb is not None and tb.isHidden():
            tb.show()

    def is_start_screen_visible(self):
        stack = getattr(self, "central_stack", None)
        return stack is not None and stack.currentIndex() == 0

    def _fit_player_to_pane(self):
        """右ペインの幅と高さに合わせて再生画面の倍率を決める(原寸が上限)。

        幅だけで決めると、窓が低いとき下の設定欄が窓の下へはみ出す
        (通常再生は画面が 720 ぶん高い)。下の段の最小の高さを引いた残りにも
        収まる倍率にする。5 段にしてからは 1920x1040 なら原寸で収まるが、
        もっと低い窓(1400x900 など)のための保険として残す。

        引くのは**等倍のときの**最小の高さ(_bottom_base_h)。下の段は余りが
        あると部品を大きくする(_scale_bottom_block)ので、そのときの
        minimumSizeHint を引くと「大きくした → 画面を縮めた → もっと余った
        → もっと大きく…」と回る。"""
        dock = getattr(self, "preview_dock", None)
        if dock is None or getattr(self, "_fitting_player", False):
            return
        gpw = dock.game_preview_window
        if gpw.is_fullscreen():
            return
        w = self.player_host.width()
        if w <= 0:
            return
        s = min(1.0, w / float(self.PLAYER_W))
        s0 = gpw.scaled_host.scale()
        nat_h = gpw.height() / s0 if s0 > 0 else 0.0
        avail_h = self.right_pane.height() - self._bottom_min_h()
        if nat_h > 0 and avail_h > 0:
            s = min(s, avail_h / nat_h)
        s = max(0.25, s)
        if abs(s - s0) < 0.005:
            # 倍率は同じでも幅は変わったかもしれない。置き場所だけ直す。
            gpw.move(max(0, (w - gpw.width()) // 2), 0)
            return
        self._fitting_player = True
        try:
            dock.apply_preview_scale(s)
            gpw.refit()
        finally:
            self._fitting_player = False

    def _on_player_refit(self):
        """再生画面の大きさが変わった(モード切替・倍率)。入れ物を合わせる。"""
        gpw = self.preview_dock.game_preview_window
        self.player_host.setFixedHeight(gpw.height())
        # 高さの都合で縮んだときは、右に空く隙間を左右に振り分ける。
        gpw.move(max(0, (self.player_host.width() - gpw.width()) // 2), 0)
        s = gpw.scaled_host.scale()
        lbl = getattr(self, "lbl_scale", None)
        if lbl is not None:
            lbl.setText("ウィンドウ倍率 %d%%" % int(round(s * 100)))
        # モード切替で画面の高さが変わった(360 ↔ 720)ので、高さの都合の
        # 倍率も取り直す。倍率が同じなら中で何もしない。
        self._fit_player_to_pane()

    def _build_preview_dock(self):
        self.preview_dock = PreviewDock(
            lambda v: self.replace_header_line("OFFSET", v),
            self,
            demostart_cb=lambda v: self.set_header_line("DEMOSTART", v),
            header_edit_cb=self.open_header_dialog,
            embedded=True,
            seek_cursor_cb=self._cursor_preview_seconds,
            volume_cb=self._save_preview_volume,
            duration_ready_cb=self._update_metronome_schedule,
            expanded_changed_cb=self._on_preview_expanded_changed,
            refresh_preview_cb=self._update_metronome_schedule,
            course_select_cb=self._on_preview_course_selected,
            game_preview_changed_cb=self._on_game_preview_visibility_changed,
            branch_select_cb=self._on_preview_branch_selected,
            audio_backend=self.config_data.get("audio_backend", "mixer"),
            sfx_volume_cb=self._save_sfx_volume,
            master_volume_cb=self._save_master_volume,
            audio_output_device=self.config_data.get("audio_output_device", ""),
            waveform_stereo=self.config_data.get("waveform_stereo", True),
            waveform_stereo_cb=self._save_waveform_stereo,
            se_text_enabled=self.config_data.get("se_text_enabled", True),
            record_cb=self.open_video_recorder,
            note_edit_cb=self._apply_note_edit,
            chart_op_cb=self._apply_chart_op,
            config_data=self.config_data,
            save_settings_cb=self._save_config,
            undo_cb=self.editor.undo,
            redo_cb=self.editor.redo,
            checkpoint_lines_cb=self._set_checkpoint_lines,
        )
        # ドックとしては出さない。中身(再生画面と操作)を右ペインへ移す。
        self.preview_dock.hide()
        # ドック時代の中身(曲名・波形・再生ボタンの段・音量の段…)は出さない。
        # 使う部品(音量スライダー・OFFSET・メトロノーム・タップ)は
        # _build_bottom_controls が右下の設定欄へ付け替える。
        self.preview_dock.widget().hide()
        self._build_bottom_controls()
        self.preview_dock.course_label_cb = self._on_lane_course_label
        gpw = self.preview_dock.game_preview_window
        gpw.setParent(self.player_host)
        gpw.move(0, 0)
        gpw.show()
        gpw.set_embed_resized_cb(self._on_player_refit)
        # 音源を読み直したいとき(合作で後から降ってきた / 音声を再接続)に、
        # 譜面の中身から全部を組み直してもらう道。
        self.preview_dock.reload_all_cb = self._force_update
        # 合作の操作(画面の上の帯)は置かない(利用者の指定 2026-11-12
        # 「合作を一回削除」)。仕組み(neotja/collab.py と下の collab_*)は
        # 残してあり、入口だけ無い。
        # 作譜ペインの右クリックから、ツールバーと同じ道具を呼べるようにする。
        _ce = getattr(self.preview_dock, "chart_edit", None)
        if _ce is not None:
            _ce.set_tool_cb(self.run_chart_tool)
            # 上の目盛りで選んだ小節を Delete で消す(利用者の希望 2026-11-05)。
            _ce.measuresDeleteRequested.connect(self.delete_pane_measures)
        # マスターが先。曲/SE はマスターに対する「比率」なので、マスターを
        # 決めてから比率を入れたほうが途中の一瞬だけ音量が飛ぶことがない。
        self.preview_dock.set_master_volume(self.config_data.get("master_volume", 1.0))
        self.preview_dock.set_volume(self.config_data.get("preview_volume", 0.8))
        self.preview_dock.set_sfx_volume(self.config_data.get("sfx_volume", 0.9))
        self._apply_wireless_offset()
        # 打音は System(TNDE-R/Sounds/Taiko の dong.ogg / ka.ogg)が既定。
        # 環境設定で「自分で選んだ WAV を使う」をオンにしたときだけ指定の
        # ファイルを使う(選ぶ順は settings.effective_hit_sound_paths)。
        don, ka = settings_mod.effective_hit_sound_paths(self.config_data)
        self.preview_dock.set_hit_sound_files(don, ka)
        # 打音は System(TNDE-R/Sounds/Taiko)から取る。以前ここで太鼓さん次郎の
        # インストール先をディスクから探して設定へ書き込んでいたが、やめた
        # (利用者の指定。System の音を使う)。
        # No close/float/move features: the dock itself always stays docked
        # and visible. Its collapse/expand toggle lives in the status bar
        # (next to the theme switcher), so there's never a state where it
        # vanishes with no obvious way back.
        self.preview_dock.setFeatures(self.preview_dock.DockWidgetFeature(0))

    # ------------------------------------------------------------------
    # 右下の設定欄(v15 統合画面の2段目)
    # ------------------------------------------------------------------
    #: 設定欄の見た目。テーマを替えたら当て直す(_restyle_bottom_controls)。
    def _bottom_css(self):
        c = COLORS
        g = glass_colors()
        # 起動画面と同じ「ガラスと光」(利用者の承認 2026-11-12)。箱(難易度
        # カード / BPM測定 / 設定欄)は GlassCard が自前で塗るので、この中の
        # 部品はまず全部透明にして(でないと札や段の下地がテーマの地色で
        # 四角く残る)、押すもの・打つものだけ塗り直す。
        #  - ボタン: 白を薄く重ねたガラス。主役(accentButton: 再生中の ▶、
        #    ON のメトロノーム・打音)はアクセント色の縦グラデーション。
        #  - 字を打つ欄: ボタンより暗い半透明。フォーカスでアクセント色の縁。
        #    OFFSET のいまの値は常にアクセント色の縁(_restyle_bottom_controls)。
        # 選択子は #bottomRow で絞ってあるので、ほかの画面へは及ばない。
        # 部品の高さは段の余り具合で 18px の 1.0〜1.4 倍(_scale_bottom_block)。
        f = getattr(self, "_bottom_scale", 1.0)
        ch = int(round(self.BOTTOM_CTRL_H * f))
        d = self._tap_diameter()
        grad = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %s, stop:1 %s)"
                % (g["primary_top"].name(), g["primary_bottom"].name()))
        grad_hover = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %s, stop:1 %s)"
                      % (g["primary_top"].lighter(108).name(),
                         g["primary_bottom"].lighter(108).name()))
        return (
            "#bottomRow, #bottomRow QWidget {{ background: transparent; }}"
            "#bottomRow QScrollArea {{ border: none; }}"
            # コンボの一覧は別の窓のように浮くので、透明にしない。
            "#bottomRow QComboBox QAbstractItemView {{ background-color: {surface};"
            " color: {fg}; border: 1px solid {border};"
            " selection-background-color: {select}; }}"
            "#bottomRow QPushButton {{ background-color: {btn}; color: {fg};"
            " border: 1px solid {btn_border}; border-radius: 6px;"
            " padding: 2px 10px; min-height: {ch}px; }}"
            "#bottomRow QPushButton:hover {{ background-color: {btn_hover}; }}"
            "#bottomRow QPushButton:pressed {{ background-color: {fill}; }}"
            "#bottomRow QPushButton:disabled {{ color: {fg_dim}; }}"
            "#bottomRow QPushButton#accentButton {{ background-color: {grad};"
            " color: {primary_fg}; border: 1px solid {primary_border};"
            " font-weight: bold; }}"
            "#bottomRow QPushButton#accentButton:hover {{ background-color: {grad_hover}; }}"
            "#bottomRow QPushButton#offsetStep {{ padding: 2px 2px; }}"
            "#bottomRow QPushButton#demostartBtn {{ padding: 2px 6px; }}"
            # BPM の上下矢印。2 段重ねで欄の高さに収める。
            "#bottomRow QToolButton#bpmArrow {{ padding: 0px; margin: 0px;"
            " background-color: {btn}; border: 1px solid {btn_border};"
            " border-radius: 2px; }}"
            "#bottomRow QToolButton#bpmArrow:hover {{ background-color: {btn_hover}; }}"
            "#bottomRow QLineEdit, #bottomRow QSpinBox,"
            " #bottomRow QDoubleSpinBox, #bottomRow QComboBox"
            " {{ background-color: {field}; color: {fg};"
            " border: 1px solid {field_border}; border-radius: 6px;"
            " padding: 2px 4px; min-height: {ch}px; }}"
            "#bottomRow QLineEdit:focus, #bottomRow QSpinBox:focus,"
            " #bottomRow QDoubleSpinBox:focus, #bottomRow QComboBox:focus"
            " {{ border: 1px solid {accent}; }}"
            "#bottomRow QLabel#fieldLabel {{ color: {fg}; }}"
            "#bottomRow QFrame#rowSeparator {{ border: none; background: {border_g}; }}"
            # タップはガラスの丸。乗せると薄い、押すと濃いアクセント色の輪。
            # min-width/height も書く(padding 0 で、枠を除いた中身が直径)。
            # min-height: 18px(上のボタンの指定)が setFixedSize の最小値より
            # 優先されて、低い窓(1400x900)では丸が 110x100 に潰れ、角丸が
            # 消えて四角になっていた(実測 2026-11-12)。
            "#bottomRow QPushButton#bpmTap {{ border-radius: {r}px;"
            " border: 1px solid {btn_border}; background-color: {btn};"
            " font-size: 15px; padding: 0px; min-width: {d}px; min-height: {d}px; }}"
            "#bottomRow QPushButton#bpmTap:hover {{ border: 2px solid {accent_border};"
            " background-color: {btn_hover}; }}"
            "#bottomRow QPushButton#bpmTap:pressed {{ border: 2px solid {accent};"
            " background-color: {accent_fill}; }}"
            "#bottomRow QLabel#bpmValue {{ font-size: {vfs}px; font-weight: bold; }}"
        ).format(border=c["border"], fg=c["fg"], fg_dim=c["fg_dim"],
                 surface=c["surface"], select=c["select"], accent=c["accent"],
                 btn=_rgba(g["btn_fill"]), btn_hover=_rgba(g["btn_fill_hover"]),
                 btn_border=_rgba(g["btn_border"]), fill=_rgba(g["fill"]),
                 field=_rgba(g["field_fill"]), field_border=_rgba(g["field_border"]),
                 border_g=_rgba(g["border"]), accent_border=_rgba(g["accent_border"]),
                 accent_fill=_rgba(_c(c["accent"], 0.28)),
                 grad=grad, grad_hover=grad_hover,
                 primary_fg=g["primary_fg"].name(),
                 primary_border=_rgba(g["primary_border"]),
                 r=d // 2, d=d, ch=ch, vfs=int(round(18 * f)))

    #: BPM測定の「タップ」の直径(px、等倍)。右下の段(設定欄 5 段ぶん)に
    #: 収まり、通常再生(720 高)を 1920x1040 で原寸のまま置ける大きさ。
    TAP_DIAMETER = 110
    #: BPM測定の箱の幅と難易度カードの幅(px、等倍)。
    BPM_BOX_W = 236
    CARD_W = 270
    #: 設定欄の部品(ボタン・欄)の中身の高さ(px、等倍)。
    BOTTOM_CTRL_H = 18
    #: 下の段の部品を大きくする上限。これ以上は間延びして見える。
    BOTTOM_SCALE_MAX = 1.4
    #: 箱と箱のあいだの余白(px)。箱はガラスの札(縁つき)なので線は無い。
    BLOCK_GAP = 12

    def _tap_diameter(self):
        return int(round(self.TAP_DIAMETER * getattr(self, "_bottom_scale", 1.0)))

    def _bottom_min_h(self):
        """下の段の、等倍のときの最小の高さ(px)。

        等倍で見えているあいだに測り直して覚える(スタイルが当たり切る前の
        値や、テーマ替えで変わる値を持ち越さないため)。拡大中は覚えている
        値を返す — 拡大後の minimumSizeHint は部品ぶん伸びているので、
        再生画面の倍率の計算(_fit_player_to_pane)には使えない。"""
        if getattr(self, "_bottom_scale", 1.0) <= 1.0 or not hasattr(self, "_bottom_base_h"):
            self._bottom_base_h = self.bottom_row.minimumSizeHint().height()
            self._bottom_base_w = self.bottom_row.minimumSizeHint().width()
        return self._bottom_base_h

    def _scale_bottom_block(self):
        """下の段の高さに合わせて部品を大きくする(等倍〜1.4 倍)。

        再生画面の高さはモードで違う(作譜 360+ペイン、通常 720)。前は
        低いモードで再生画面の下を暗い地で埋めて段の位置をそろえていたが、
        シークバーと情報のあいだの空き地が気になる(利用者の指定
        2026-11-12)ので、埋めるのをやめて余りを段に渡し、段の部品を
        大きくして使う。モード切替で段が上下に動くのは承知のうえ。

        縦(部品の高さ・段間・タップの直径・カードの字)は余りの高さの比で、
        横(BPM測定の箱とカードの幅)は余りの幅の比で決める。横は別に見る
        — 1400 幅の窓では右ペインに横の余りが無く、箱を広げると設定欄が
        右で切れる。"""
        row = getattr(self, "bottom_row", None)
        if row is None or not hasattr(self, "bpm_box") or row.height() <= 0:
            return
        base_h = self._bottom_min_h()
        base_w = self._bottom_base_w
        if base_h <= 0:
            return
        f = max(1.0, min(self.BOTTOM_SCALE_MAX, row.height() / float(base_h)))
        # 横に増やせるのは箱とカードの幅の合計ぶんまで。
        grow = float(self.BPM_BOX_W + self.CARD_W)
        # 切り捨て(丸めると 1px 溢れることがあった。実測 2026-11-12)。
        fw = max(1.0, min(f, int((1.0 + (row.width() - base_w) / grow) * 100) / 100.0))
        self._apply_bottom_scale(f, fw)
        # 見積もりが数 px 外れて段から溢れたら、その比で一段戻す。
        need = row.minimumSizeHint()
        if need.height() > row.height() and f > 1.0:
            f = max(1.0, f * row.height() / float(need.height()))
            self._apply_bottom_scale(f, min(fw, f))
            need = row.minimumSizeHint()
        if need.width() > row.width() and fw > 1.0:
            fw = max(1.0, fw - (need.width() - row.width()) / grow - 0.01)
            self._apply_bottom_scale(f, fw)

    def _apply_bottom_scale(self, f, fw):
        f = round(float(f), 2)
        fw = round(float(fw), 2)
        if (f, fw) == (getattr(self, "_bottom_scale", 1.0),
                       getattr(self, "_bottom_scale_w", 1.0)):
            return
        self._bottom_scale = f
        self._bottom_scale_w = fw
        pd = self.preview_dock
        d = self._tap_diameter()
        pd.btn_tap.setFixedSize(d, d)
        # BPM の上下矢印は 2 段で欄の高さ(中身 + 余白 4 + 枠 2)に合わせる。
        ah = int(round((self.BOTTOM_CTRL_H * f + 6) / 2.0))
        for b in (self.btn_bpm_up, self.btn_bpm_down):
            b.setFixedSize(16, ah)
        self.bpm_box.setFixedWidth(int(round(self.BPM_BOX_W * fw)))
        self.sb_outer.setFixedWidth(int(round(self.CARD_W * fw)))
        for card in self._course_cards.values():
            card.set_scale(fw)
        # 段と段のあいだ(4px)も同じ比で。
        self.settings_panel.layout().setSpacing(int(round(4 * f)))
        self.bpm_box.layout().setSpacing(int(round(3 * f)))
        self._restyle_bottom_controls()

    def _restyle_bottom_controls(self):
        # 段ぜんたいに当てる。選択子は #bottomRow で絞ってあるので、他へは
        # 及ばない。ガラスの箱(GlassCard)と地の光(BottomRow)は描くときに
        # 今のテーマの色を引くので、描き直すだけでよい。
        row = getattr(self, "bottom_row", None)
        if row is not None and hasattr(self, "bpm_box"):
            row.setStyleSheet(self._bottom_css())
            for card in self._course_cards.values():
                card.refresh_theme()
            row.update()
        # OFFSET の値の箱は常に青い枠(メトロノーム ON の枠と同じ色)。
        # 部品に直に当てるのは、テーマの QDoubleSpinBox の枠線より
        # 優先させるため。
        spin = getattr(self.preview_dock, "spin_offset", None)
        if spin is not None:
            spin.setStyleSheet(
                "QDoubleSpinBox { border: 1px solid %s; }" % COLORS["accent"])

    def _build_bottom_controls(self):
        """右下: BPM測定の箱と、譜面ヘッダ・再生まわりの設定欄。

        見本は v15-design/settings.png。部品のうち音量・OFFSET・メトロノーム・
        打音・タップ・音声を再接続・再現FPS は PreviewDock が持っている
        ものをそのまま付け替える(配線を二重に持たないため)。ヘッダへの
        書き込みは replace_header_line / set_header_line / _set_course_level
        を通す — 元に戻すが行単位で効くように、本文は丸ごと組み直さない。"""
        pd = self.preview_dock
        hb = self.bottom_row.layout()
        self._bottom_scale = 1.0
        self._bottom_scale_w = 1.0

        # ---- BPM測定 ----------------------------------------------------
        # ガラスの箱(見出し + 丸いボタン + リセット)。箱と箱のあいだは
        # 段のレイアウトの spacing(BLOCK_GAP)。
        self.bpm_box = GlassCard(radius=12)
        self.bpm_box.setObjectName("bpmBox")
        self.bpm_box.setFixedWidth(self.BPM_BOX_W)
        bv = QVBoxLayout(self.bpm_box)
        bv.setContentsMargins(10, 6, 10, 8)
        bv.setSpacing(3)
        bv.addWidget(QLabel("BPM測定"))
        mid = QHBoxLayout()
        mid.setSpacing(10)
        pd.btn_tap.setFixedSize(self.TAP_DIAMETER, self.TAP_DIAMETER)
        pd.btn_tap.setObjectName("bpmTap")
        pd.btn_tap.setFocusPolicy(Qt.NoFocus)
        pd.btn_tap.setToolTip("拍に合わせて押すと BPM を測ります。"
                              "譜面の 基本BPM とは別の値です。")
        mid.addWidget(pd.btn_tap)
        val = QVBoxLayout()
        val.addStretch()
        pd.lbl_tap_bpm.setObjectName("bpmValue")
        pd.lbl_tap_bpm.setAlignment(Qt.AlignCenter)
        val.addWidget(pd.lbl_tap_bpm)
        cap = QLabel("BPM")
        cap.setAlignment(Qt.AlignCenter)
        val.addWidget(cap)
        val.addStretch()
        mid.addLayout(val, 1)
        bv.addLayout(mid, 1)
        self.btn_tap_reset = QPushButton("リセット")
        self.btn_tap_reset.setFocusPolicy(Qt.NoFocus)
        self.btn_tap_reset.clicked.connect(self._reset_bpm_tap)
        bv.addWidget(self.btn_tap_reset)
        hb.addWidget(self.bpm_box)

        # ---- 設定欄 -----------------------------------------------------
        self.settings_panel = GlassCard(radius=12)
        self.settings_panel.setObjectName("settingsPanel")
        pv = QVBoxLayout(self.settings_panel)
        pv.setContentsMargins(6, 5, 6, 5)
        pv.setSpacing(4)

        def row():
            # 枠の無いただの入れ物。段の区切りは上下の余白(2px + 段間 4px)。
            fr = QWidget()
            fr.setObjectName("settingsRow")
            h = QHBoxLayout(fr)
            h.setContentsMargins(6, 2, 6, 2)
            # 札と欄は近く(5px)、項目と項目のあいだは広く(gap())。
            h.setSpacing(5)
            pv.addWidget(fr)
            return h

        def label(text):
            lb = QLabel(text)
            lb.setObjectName("fieldLabel")
            return lb

        def gap(h, n=1):
            """項目と項目のあいだ。固定の 8px に加えて伸びる余白を置く。
            固定ぶんは段の最小幅に効くので控えめに(1400 幅の窓でも
            右ペインが収まるように)。広い窓では余白が伸びて間が空く。"""
            h.addSpacing(8)
            h.addStretch(n)

        def vline(h):
            """段の中の縦の仕切り線(再生速度 │ 再現FPS)。"""
            # VLine の形は使わない — スタイルシートを当てると枠の描き方が
            # 変わって線が出ないことがあるので、1px 幅の箱を塗って線にする。
            ln = QFrame()
            ln.setObjectName("rowSeparator")
            ln.setFixedWidth(1)
            h.addSpacing(10)
            h.addWidget(ln)
            h.addSpacing(10)

        #: ヘッダ→欄 の反映中(欄→ヘッダ の書き戻しを跳ね返さない)。
        self._syncing_fields = False

        # 段は 5 つ(利用者の指定 2026-11-12)。7 段だと通常再生(720 高)の
        # とき下がはみ出して再生画面を縮めるしかなかった。
        # 1. TITLE / SUBTITLE
        h = row()
        h.addWidget(label("TITLE"))
        self.fld_title = QLineEdit()
        self.fld_title.editingFinished.connect(
            lambda: self._apply_text_field("TITLE", self.fld_title))
        # 2 つの欄で段の幅を使い切る(利用者の指定 2026-11-12)。gap() の
        # 伸びる余白を挟むと欄が縮むので、ここだけ固定の間隔にする。
        h.addWidget(self.fld_title, 1)
        h.addSpacing(14)
        h.addWidget(label("SUBTITLE"))
        self.fld_subtitle = QLineEdit()
        self.fld_subtitle.setToolTip("先頭の -- / ++ は打ったとおりに残します。")
        self.fld_subtitle.editingFinished.connect(
            lambda: self._apply_text_field("SUBTITLE", self.fld_subtitle))
        h.addWidget(self.fld_subtitle, 1)

        # 2. コース / 難易度 / BPM / メトロノーム
        h = row()
        h.addWidget(label("コース"))
        self.btn_course = QPushButton("-")
        self.btn_course.setFocusPolicy(Qt.NoFocus)
        self.btn_course.setMinimumWidth(120)
        self.btn_course.setToolTip("押すとコースを順に切り替えます"
                                   "(レーン上のコース切替と同じ)")
        self.btn_course.clicked.connect(pd._on_course_button)
        h.addWidget(self.btn_course, 1)
        gap(h)
        h.addWidget(label("難易度"))
        self.cmb_level = QComboBox()
        for n in range(1, 11):
            self.cmb_level.addItem("★%d" % n, n)
        self.cmb_level.setFixedWidth(84)
        self.cmb_level.setFocusPolicy(Qt.NoFocus)
        self.cmb_level.setToolTip("いま映しているコースの LEVEL: を書き換えます")
        self.cmb_level.currentIndexChanged.connect(self._on_level_combo)
        h.addWidget(self.cmb_level)
        gap(h)
        h.addWidget(label("BPM"))
        # 数字の欄 + 上下の矢印(命令パネルの BPM の箱と同じ操作感。
        # 利用者の指定 2026-11-12)。QDoubleSpinBox にしないのは、打った
        # 字をそのまま残したいから(137.250 の末尾の 0 を消さない)。
        self.fld_bpm = _ArrowLineEdit()
        self.fld_bpm.setAlignment(Qt.AlignCenter)
        self.fld_bpm.setFixedWidth(96)
        self.fld_bpm.setToolTip("ヘッダの BPM:。小数は打ったとおりに残します"
                                "(137.250 など)。矢印・↑↓キー・ホイールで ±1。")
        self.fld_bpm.editingFinished.connect(self._apply_bpm_field)
        self.fld_bpm.stepped.connect(self._step_bpm)
        # 欄と矢印は 1px で密着させる(段の 5px だと別の部品に見えた)。
        bpm_h = QHBoxLayout()
        bpm_h.setContentsMargins(0, 0, 0, 0)
        bpm_h.setSpacing(1)
        bpm_h.addWidget(self.fld_bpm)
        arrows = QWidget()
        av = QVBoxLayout(arrows)
        av.setContentsMargins(0, 0, 0, 0)
        av.setSpacing(0)
        self.btn_bpm_up = QToolButton()
        self.btn_bpm_up.setArrowType(Qt.UpArrow)
        self.btn_bpm_down = QToolButton()
        self.btn_bpm_down.setArrowType(Qt.DownArrow)
        for b, d in ((self.btn_bpm_up, 1), (self.btn_bpm_down, -1)):
            b.setObjectName("bpmArrow")
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoRepeat(True)
            b.setFixedSize(16, 12)
            b.clicked.connect(lambda _=False, d=d: self._step_bpm(d))
            av.addWidget(b)
        bpm_h.addWidget(arrows)
        h.addLayout(bpm_h)
        gap(h)
        pd.btn_metronome.setFocusPolicy(Qt.NoFocus)
        pd.btn_metronome.setMinimumWidth(120)
        h.addWidget(pd.btn_metronome, 1)

        # 3. OFFSET / DEMOSTART
        h = row()
        h.addWidget(label("OFFSET"))
        for b in pd.offset_buttons:
            b.setFocusPolicy(Qt.NoFocus)
            b.setFixedWidth(44)
            b.setObjectName("offsetStep")
        for b in pd.offset_buttons[:3]:
            h.addWidget(b)
        pd.spin_offset.setFixedWidth(80)
        pd.spin_offset.setAlignment(Qt.AlignCenter)
        # いまの値の箱は常に青(アクセント色)の枠。± ボタンに挟まれて
        # どれが値か分かりにくいので(利用者の指定 2026-11-12)。色は
        # _restyle_bottom_controls が当てる(テーマ替えでも追従)。
        h.addWidget(pd.spin_offset)
        for b in pd.offset_buttons[3:]:
            h.addWidget(b)
        gap(h)
        # 札もツールチップも付けない。ボタンの字だけで足りる
        # (利用者の指定 2026-11-12)。
        self.btn_demostart = QPushButton("カーソル位置をDEMOSTARTに設定")
        self.btn_demostart.setObjectName("demostartBtn")
        self.btn_demostart.setFocusPolicy(Qt.NoFocus)
        self.btn_demostart.clicked.connect(pd.demostart_here)
        h.addWidget(self.btn_demostart)

        # 4. 音声を再接続 / 再生速度 │ 再現FPS / 連打秒速
        h = row()
        pd.btn_reopen_audio.setFocusPolicy(Qt.NoFocus)
        h.addWidget(pd.btn_reopen_audio)
        gap(h)
        h.addWidget(label("再生速度"))
        self.btn_speed_cycle = QPushButton("x1.0")
        self.btn_speed_cycle.setFocusPolicy(Qt.NoFocus)
        self.btn_speed_cycle.setFixedWidth(72)
        self.btn_speed_cycle.setToolTip(
            "再生速度。押すたび x1.0 → x0.75 → x0.5 → x0.25 → x1.0 と回ります。")
        self.btn_speed_cycle.clicked.connect(pd._cycle_speed)
        pd.speed_slider.valueChanged.connect(self._sync_speed_button)
        self._sync_speed_button(pd.speed_slider.value())
        h.addWidget(self.btn_speed_cycle)
        # 左は音(再接続・速度)、右は譜面の見え方(FPS・連打の数え方)。
        # 別の種類だと分かるように線で仕切る。
        vline(h)
        h.addWidget(label("再現FPS"))
        # レーンの上にあったコンボをそのまま付け替える(配線は PreviewDock)。
        cmb = pd.sim_fps_combo
        cmb.setStyleSheet("")
        cmb.setFont(QFont())
        cmb.setMinimumSize(0, 0)
        cmb.setMaximumSize(16777215, 16777215)
        cmb.setFixedWidth(84)
        cmb.show()
        h.addWidget(cmb)
        gap(h)
        h.addWidget(label("連打秒速"))
        # 設定 roll_speed。以前は左の難易度パネルの上にあった。
        self.roll_speed_spin = QSpinBox()
        self.roll_speed_spin.setRange(1, 100)
        self.roll_speed_spin.setSuffix(" 打/s")
        self.roll_speed_spin.setAlignment(Qt.AlignCenter)
        self.roll_speed_spin.setFixedWidth(96)
        self.roll_speed_spin.setKeyboardTracking(False)
        self.roll_speed_spin.setToolTip("連打を1秒に何回叩いた想定で数えるか")
        self.roll_speed_spin.setValue(self.config_data.get("roll_speed", 45))
        self.roll_speed_spin.valueChanged.connect(self._on_roll_speed_changed)
        h.addWidget(self.roll_speed_spin)
        h.addStretch(1)

        # 5. 音量(利用者の指定 2026-11-11「音なかったね、追加しておいて」)
        h = row()
        h.addWidget(label("音量調整:"))
        h.addSpacing(6)
        for text, slider, lbl in (("マスター", pd.master_volume_slider, pd.lbl_master_volume),
                                  ("曲(比率)", pd.volume_slider, pd.lbl_volume),
                                  ("SE(比率)", pd.sfx_volume_slider, pd.lbl_sfx_volume)):
            h.addWidget(label(text))
            slider.setFocusPolicy(Qt.NoFocus)
            # ドック時代の固定幅(120)をやめて、段の幅に合わせて伸び縮み。
            slider.setMinimumWidth(40)
            slider.setMaximumWidth(16777215)
            h.addWidget(slider, 1)
            lbl.setMinimumWidth(40)
            lbl.setAlignment(Qt.AlignCenter)
            h.addWidget(lbl)
            gap(h, 0)
        pd.btn_hit_sounds.setFocusPolicy(Qt.NoFocus)
        h.addWidget(pd.btn_hit_sounds)

        hb.addWidget(self.settings_panel, 1)
        self._restyle_bottom_controls()
        # 右ペインは下の段が切れない幅まで。分割バーはこれより左へ寄らない。
        self._bottom_min_h()   # 等倍の最小の大きさを覚える
        self.right_pane.setMinimumWidth(max(320, self._bottom_base_w))

    def _reset_bpm_tap(self):
        self.preview_dock.tapper.reset()
        self.preview_dock.lbl_tap_bpm.setText("--")

    def _sync_speed_button(self, index):
        from neotja.preview_dock import SPEED_STEPS
        index = max(0, min(len(SPEED_STEPS) - 1, int(index)))
        s = ("%.2f" % SPEED_STEPS[index]).rstrip("0")
        if s.endswith("."):
            s += "0"
        self.btn_speed_cycle.setText("x" + s)

    def _on_lane_course_label(self, label, color):
        """レーンが映すコースが変わった。右下の「コース」と難易度カードを合わせる。"""
        self.btn_course.setText(label or "-")
        self.btn_course.setStyleSheet(
            f"color: {color}; font-weight: bold;" if color else "")
        self._lane_course_label = label
        self._sync_course_card()
        self._sync_header_fields()

    def _shown_course_key(self):
        """いま再生画面に出ているコースの鍵。"""
        label = getattr(self, "_lane_course_label", None)
        for c in self.courses_info:
            if label and c.get("label") == label:
                return c.get("key")
        return self._preview_course_key()

    def _sync_course_card(self):
        """難易度カードは**いま映しているコースの1枚だけ**出す
        (利用者の指定 2026-11-11)。ほかの札は作ったまま隠しておく。"""
        key = self._shown_course_key()
        if key not in self._course_cards and self._course_cards:
            key = next(iter(self._course_cards))
        for k, card in self._course_cards.items():
            card.setVisible(k == key)

    @staticmethod
    def _bpm_display(s):
        """ヘッダの BPM の字 → 欄に出す字。整数なら .0 を添え、小数は
        打ったとおり(137.250 の末尾の 0 も)残す。数でなければそのまま。"""
        s = (s or "").strip()
        try:
            float(s)
        except ValueError:
            return s
        return s if ("." in s or not s) else s + ".0"

    def _sync_header_fields(self):
        """本文のヘッダ → 右下の欄。打っている最中の欄は触らない。"""
        if not hasattr(self, "fld_title"):
            return
        vals = self.header_values()
        self._syncing_fields = True
        try:
            for fld, key in ((self.fld_title, "TITLE"),
                             (self.fld_subtitle, "SUBTITLE")):
                if not fld.hasFocus() and fld.text() != vals[key]:
                    fld.setText(vals[key])
            if not self.fld_bpm.hasFocus():
                shown = self._bpm_display(vals["BPM"])
                if self.fld_bpm.text() != shown:
                    self.fld_bpm.setText(shown)
            try:
                lv = int(float(vals["LEVEL"]))
            except (TypeError, ValueError):
                lv = None
            idx = self.cmb_level.findData(lv) if lv is not None else -1
            if self.cmb_level.currentIndex() != idx:
                self.cmb_level.setCurrentIndex(idx)
        finally:
            self._syncing_fields = False

    def _apply_text_field(self, key, fld):
        """TITLE / SUBTITLE の欄を Enter(か欄を離れた)で本文へ。"""
        if self._syncing_fields:
            return
        text = fld.text().strip()
        if text == self.header_values()[key]:
            return
        self.set_header_line(key, text)
        self._sync_header_fields()

    def _apply_bpm_field(self):
        if self._syncing_fields:
            return
        text = self.fld_bpm.text().strip()
        try:
            v = float(text)
        except ValueError:
            v = None
        if v is None or v <= 0 or not text:
            # 数でないものは本文へ入れない。欄を元の値へ戻す。
            self.fld_bpm.setText(self._bpm_display(self.header_values()["BPM"]))
            return
        if text != self.header_values()["BPM"]:
            self.set_header_line("BPM", text)
        self._sync_header_fields()

    @staticmethod
    def _bpm_stepped(text, delta):
        """"137.250" に ±1 → "138.250"。小数の桁数は元の字に合わせる。

        命令パネルの BPM の箱は 1 刻み(QDoubleSpinBox の既定)で、本文へは
        末尾の 0 を落として書く(note_edit._fmt_number: 150.0 → 150)。
        ここも刻みは同じ 1 にするが、書き方は元の字の桁数を守る —
        "137.250" を "138.25" に縮めると「打ったとおり残す」約束が
        矢印を押した途端に破れるから。整数なら整数のまま(120 → 121)。
        数でなければ None。"""
        s = (text or "").strip()
        try:
            v = float(s)
        except ValueError:
            return None
        dec = len(s.split(".", 1)[1]) if "." in s else 0
        v = max(1.0, v + delta)      # 命令パネルと同じ下限(1)
        return "%.*f" % (dec, v)

    def _step_bpm(self, delta):
        """矢印・↑↓・ホイールで BPM を ±1。打ちかけの字があればそこから。
        本文へは set_header_line で書く(Enter で確定したときと同じ道)。"""
        if self._syncing_fields:
            return
        cur = self.header_values()["BPM"]
        base = self.fld_bpm.text()
        if base.strip() == self._bpm_display(cur):
            # 欄は本文の写し(整数には .0 を添えて見せている)。桁数は
            # 本文の字に合わせる — 120 → 121 であって 121.0 ではない。
            base = cur
        new = self._bpm_stepped(base, delta)
        if new is None:
            new = self._bpm_stepped(cur, delta)
        if new is None:
            return
        if new != self.header_values()["BPM"]:
            self.set_header_line("BPM", new)
        # 欄にフォーカスがあると _sync_header_fields は欄を触らないので
        # ここで出し直す(打ちかけの字を確定した扱い)。
        self.fld_bpm.setText(self._bpm_display(new))
        self._sync_header_fields()

    def _on_level_combo(self, idx):
        if self._syncing_fields or idx < 0:
            return
        n = self.cmb_level.itemData(idx)
        if n is None:
            return
        cur = self.header_values()["LEVEL"]
        if cur.strip() == str(n):
            return
        self._set_course_level(str(n))

    def _set_checkpoint_lines(self, lines):
        """譜面プレビューで P を押した結果をエディタのチェックポイントへ。

        チェックポイントはエディタの行が正で、行番号の横に ▶ が出るのも
        ファイルを開き直しても残るのもこちら。プレビュー側は時刻で持って
        いるので、行へ直したものをここで受け取る。"""
        new = {int(ln) for ln in (lines or [])}
        if new == self.editor.checkpoints:
            return
        self._syncing_checkpoints = True
        try:
            self.editor.checkpoints = new
            self.editor.gutter.update()
            self.editor.checkpointsChanged.emit()
        finally:
            self._syncing_checkpoints = False

    def _push_checkpoints_to_preview(self):
        """エディタ側(Alt+P)で変わったチェックポイントをプレビューへ。"""
        if getattr(self, "_syncing_checkpoints", False):
            return      # プレビュー発の変更を跳ね返さない
        dock = getattr(self, "preview_dock", None)
        if dock is not None:    # 起動途中に飛んでくることがある
            dock.set_checkpoint_lines(self.editor.checkpoints)

    def _save_config(self):
        """プレビュー側が config_data を書き換えたときの保存口。"""
        settings_mod.save_settings(self.config_data)

    def _save_preview_volume(self, volume: float):
        self.config_data["preview_volume"] = volume
        settings_mod.save_settings(self.config_data)

    def _save_waveform_stereo(self, stereo: bool):
        self.config_data["waveform_stereo"] = bool(stereo)
        settings_mod.save_settings(self.config_data)

    def _save_sfx_volume(self, volume: float):
        self.config_data["sfx_volume"] = volume
        settings_mod.save_settings(self.config_data)

    def _save_master_volume(self, volume: float):
        self.config_data["master_volume"] = volume
        settings_mod.save_settings(self.config_data)

    def _apply_wireless_offset(self):
        """ワイヤレス調整(出力遅延の補正)を再生側へ流す。オフのときは 0ms、
        つまり従来とまったく同じ挙動になる。"""
        ms = 0.0
        if self.config_data.get("wireless_offset_enabled", False):
            ms = float(self.config_data.get("wireless_offset_ms", 0.0) or 0.0)
        self.preview_dock.set_output_offset_ms(ms)

    # 機能1(ノーツ入力音): エディタでノーツ文字を打鍵した瞬間に対応する
    # 打音を即座に鳴らす。1/3=ドン、2/4=カツ。5/6(連打開始)・7(風船開始)・
    # 9(クスダマ開始)はいずれも本アプリの連打/風船/クスダマ判定音が
    # 「ドンのみ(交互に鳴らさない)」という既存方針(#94)に合わせてドン
    # ティックを鳴らす。8(連打/風船の終端マーカー)は打音そのものを表さない
    # ため無音、0(空白)は _NOTE_SOUND_CHARS の時点で除外済み。
    _NOTE_INPUT_SOUND_KIND = {
        "1": "don", "3": "don", "2": "ka", "4": "ka",
        "5": "don", "6": "don", "7": "don", "9": "don",
    }

    def _on_note_typed(self, char: str, line_no: int):
        """TJAEditor.set_note_typed_cb から、ノーツ文字が実際に1回キー入力
        された直後に呼ばれる(貼り付けやプログラムによる挿入では呼ばれない)。
        設定・コース範囲・F1トグルをすべて満たしたときだけ、現在のバックエンド
        (ミキサー優先/レガシー QSoundEffect フォールバック)経由で打音を
        1回だけ鳴らす。スケジュール済み打音や再生位置には一切触れない。"""
        if not self.config_data.get("note_input_sound", True):
            return
        kind = self._NOTE_INPUT_SOUND_KIND.get(char)
        if kind is None:
            return
        content = self.editor.toPlainText()
        if not self.analyzer.line_in_course_body(content, line_no):
            return
        # 命令文(#MEASURE 4/4, #BPMCHANGE 120, #SCROLL 1 ...)の中の数字では
        # 鳴らさない - 譜面本文としてのノーツ入力だけを対象にする。
        line_text = self.editor.document().findBlockByNumber(line_no - 1).text()
        if line_text.lstrip().startswith("#"):
            return
        hit_sounds = getattr(self.preview_dock, "hit_sounds", None)
        if hit_sounds is not None:
            hit_sounds.play_once(kind)

    def _on_preview_course_selected(self, course_key):
        # course_key is None for "follow the editor cursor" (the default
        # behavior); otherwise it pins the game-style preview to that course
        # regardless of where the cursor is, until picked again.
        self._preview_course_override = course_key
        self._update_metronome_schedule()
        self._sync_course_card()

    def _on_preview_branch_selected(self, level):
        # Static branch choice for the whole course - see build_preview_timeline's
        # docstring for why this preview doesn't simulate dynamic switching.
        self._preview_branch_level = level
        self._update_metronome_schedule()

    def _cursor_preview_seconds(self):
        """Returns the audio-file seek position (seconds) for the measure
        under the editor cursor, or None if the cursor isn't inside a
        course's #START..#END body."""
        line_no = self.editor.textCursor().blockNumber() + 1
        content = self.editor.toPlainText()
        chart_time = self.analyzer.time_at_cursor(content, line_no)
        if chart_time is None:
            return None
        # OFFSET convention: chart_time = audio_time + OFFSET -> audio_time = chart_time - OFFSET.
        offset = self.preview_dock.spin_offset.value()
        return max(0.0, chart_time - offset)

    def replace_header_line(self, key: str, value: str) -> bool:
        prefix = f"{key}:"
        block = self.editor.document().firstBlock()
        while block.isValid():
            if block.text().startswith(prefix):
                cursor = QTextCursor(block)
                cursor.select(QTextCursor.SelectionType.LineUnderCursor)
                cursor.insertText(f"{prefix}{value}")
                return True
            block = block.next()
        return False

    def set_header_line(self, key: str, value: str) -> bool:
        """ヘッダの1行を書く。**無ければ作る。**

        replace_header_line は在る行を置き換えるだけなので、DEMOSTART の
        ように最初から無いことのある行を書けない(利用者の指定 2026-10-25)。
        入れる場所は OFFSET: の次 — 譜面の本体(COURSE: や #START)より前で
        あればどこでもよいが、近い意味の行の隣に置くと見つけやすい。"""
        if self.replace_header_line(key, value):
            return True
        doc = self.editor.document()
        block = doc.firstBlock()
        anchor = None
        while block.isValid():
            s = block.text().strip()
            if block.text().startswith("OFFSET:"):
                anchor = block
            up = s.upper()
            if up.startswith("#START") or up.startswith("COURSE:"):
                break
            if s:
                anchor = anchor if anchor is not None else block
            block = block.next()
        if anchor is None:
            anchor = doc.firstBlock()
        cursor = QTextCursor(anchor)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cursor.insertText("%s%s:%s" % (chr(10), key, value))
        return True

    #: 譜面プレビューから直せるヘッダ(曲ぜんたいのもの)。
    #: LEVEL はコースごとなので別扱い(_set_course_level)。
    PREVIEW_HEADERS = ("TITLE", "SUBTITLE", "BPM", "WAVE", "OFFSET",
                       "DEMOSTART")

    def _preview_course_key(self):
        """いま譜面プレビューが見ているコースの鍵。"""
        text = self.editor.toPlainText()
        return (self._preview_course_override
                or self._course_key_at_cursor(text))

    def _course_level_block(self):
        """いま見ているコースの LEVEL: の行(QTextBlock)。無ければ None と
        「入れるならこの行の前」の #START の行を返す。"""
        text = self.editor.toPlainText()
        key = self._preview_course_key()
        rng = self.analyzer.course_line_range(text, key) if key else None
        doc = self.editor.document()
        if rng is None:
            return None, None
        start_line = rng[0]                      # #START の行(1 始まり)
        start_block = doc.findBlockByNumber(start_line - 1)
        block = start_block.previous()
        while block.isValid():
            s = block.text().strip().upper()
            if s.startswith("#END"):
                break
            if s.startswith("LEVEL:"):
                return block, start_block
            block = block.previous()
        return None, start_block

    def _set_course_level(self, value):
        """いま見ているコースの LEVEL: を書く。無ければ #START の前に作る。"""
        block, start_block = self._course_level_block()
        if block is not None:
            cursor = QTextCursor(block)
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            cursor.insertText("LEVEL:%s" % value)
            return True
        if start_block is None or not start_block.isValid():
            return False
        cursor = QTextCursor(start_block)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.insertText("LEVEL:%s%s" % (value, chr(10)))
        return True

    def header_values(self):
        """いま本文に入っているヘッダの値。LEVEL は見ているコースのもの。"""
        vals = {k: "" for k in self.PREVIEW_HEADERS}
        for line in self.editor.toPlainText().split(chr(10)):
            s = line.strip()
            up = s.upper()
            if up.startswith("#START") or up.startswith("COURSE:"):
                break
            for k in self.PREVIEW_HEADERS:
                if up.startswith(k + ":"):
                    vals[k] = s.split(":", 1)[1].strip()
        block, _start = self._course_level_block()
        vals["LEVEL"] = (block.text().split(":", 1)[1].strip()
                         if block is not None else "")
        return vals

    def apply_header_values(self, values):
        """ヘッダの窓から返ってきた「変えたものだけ」を本文へ書く。"""
        if not values:
            return
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        try:
            for k in self.PREVIEW_HEADERS:
                if k in values:
                    self.set_header_line(k, values[k])
            if "LEVEL" in values:
                self._set_course_level(values["LEVEL"])
        finally:
            cursor.endEditBlock()
        self.setWindowModified(True)
        self._force_update()

    def open_header_dialog(self):
        """譜面プレビューの〔ヘッダ〕。"""
        from neotja.dialogs.header_dialog import HeaderDialog
        HeaderDialog(self.header_values(), self.apply_header_values,
                     self).exec()

    def demostart_seconds(self):
        """いま本文に入っている DEMOSTART の秒。無ければ None。"""
        for line in self.editor.toPlainText().splitlines():
            up = line.strip().upper()
            if up.startswith("#START") or up.startswith("COURSE:"):
                break
            if up.startswith("DEMOSTART:"):
                try:
                    return float(line.split(":", 1)[1].strip())
                except (ValueError, IndexError):
                    return None
        return None

    def _build_statusbar(self):
        """左下: 文字コード / テーマ切替 / ウィンドウ倍率(表示のみ)。

        窓いっぱいの QStatusBar ではなく、TJA 側(editor_container)の
        いちばん下に細い帯として置く(利用者の指定 2026-11-12)。右ペインは
        帯のぶん高くなる。QStatusBar 自体は隠すが残す — showMessage の
        呼び手が多いので、文はそこからレーンのトーストへ回す。

        「行 1 文字数 0 │ 第n小節…」の表示(status_label)は帯には出さない
        (帯は 3 つだけ、という指定)。不正文字数は行番号の色で分かる。
        組み立てだけは残して _update_status が今までどおり動くようにする。"""
        self.setStatusBar(_ToastStatusBar(self))
        self.statusBar().toast_cb = self._toast_status_message
        self.statusBar().hide()

        self.info_strip = QWidget()
        self.info_strip.setObjectName("infoStrip")
        hs = QHBoxLayout(self.info_strip)
        hs.setContentsMargins(6, 2, 6, 2)
        hs.setSpacing(10)
        self.encoding_label = QLabel("ANSI (cp932)")
        hs.addWidget(self.encoding_label)
        self.btn_theme = QPushButton("テーマ切替")
        self.btn_theme.clicked.connect(self.toggle_theme)
        hs.addWidget(self.btn_theme)
        self.lbl_scale = QLabel("ウィンドウ倍率 100%")
        self.lbl_scale.setToolTip("再生画面の表示倍率(右ペインの幅から自動で決まります)")
        hs.addWidget(self.lbl_scale)
        hs.addStretch(1)
        self.editor_container.layout().addWidget(self.info_strip)

        self.status_label = QLabel("")
        self.status_label.hide()
        self._on_player_refit()

    def _toast_status_message(self, text, secs):
        """statusBar().showMessage の文をレーン左上のトーストに出す。"""
        dock = getattr(self, "preview_dock", None)
        lane = getattr(dock, "chart_preview", None) if dock is not None else None
        if lane is not None:
            lane.show_toast(text, secs)

    def _on_preview_expanded_changed(self, expanded):
        pass

    def _on_game_preview_visibility_changed(self, visible):
        pass

    def _on_f1(self):
        """F1: launch the built-in game-style preview (NeoTJAPlayer)
        if it's not open yet; while it's open, F1 instead toggles hit
        sounds so the key never relaunches/duplicates the preview."""
        if not self.preview_dock.is_game_preview_visible():
            self.preview_dock.set_game_preview_visible(True)
        else:
            self.preview_dock.toggle_hit_sounds()

    def _build_menu(self):
        mb = self.menuBar()

        fm = mb.addMenu("ファイル")
        fm.addAction("新規作成", self.new_file_dialog)
        fm.addAction("開く", self.open_file)
        self._recent_menu = fm.addMenu("最近使ったファイル")
        self._rebuild_recent_menu()
        fm.addAction("別ウィンドウで開く", self.open_new_window)
        fm.addSeparator()
        fm.addAction("上書き保存  Ctrl+S", self.save_file)
        fm.addAction("名前を付けて保存", self.save_file_as)
        fm.addSeparator()
        fm.addAction("終了", self.close)

        tm = mb.addMenu("ツール")
        self._menu_tools = tm
        tm.addAction("検索  Ctrl+F", self.find_bar.show_find)
        tm.addAction("置換  Ctrl+H", self.find_bar.show_replace)
        mm = tm.addMenu("小節編集")
        mm.addAction("小節を複製  Ctrl+D", lambda: self._measure_op("dup"))
        mm.addAction("小節を削除  Ctrl+Shift+D", lambda: self._measure_op("del"))
        mm.addAction("空の小節を挿入  Ctrl+Shift+N", lambda: self._measure_op("ins"))
        mm.addAction("小節を上へ移動  Ctrl+Shift+↑", lambda: self._measure_op("move", "up"))
        mm.addAction("小節を下へ移動  Ctrl+Shift+↓", lambda: self._measure_op("move", "down"))
        tm.addSeparator()
        tm.addAction("難易度を追加", self.open_add_course)
        tm.addAction("TJA校正", self.open_tja_tidy)
        tm.addSeparator()
        tm.addAction("ハイスピ変換", self.open_scroll_splitter)
        tm.addAction("ノーツ間隔リサイズ", self.open_measure_converter)
        tm.addSeparator()
        tm.addAction("あべこべ反転  Ctrl+M", self.reverse_don_ka)
        tm.addSeparator()
        # 「同時再生」はここには置かない。再生ウィンドウの**モード**なので、
        # 入り口はモード切替ボタン1つに寄せる(利用者の指定 2026-10-04)。
        tm.addAction("BPM/OFFSET自動検出(実験的)", self.auto_detect_bpm_offset)
        tm.addAction("AI譜面生成(実験的)", self.open_auto_chart_generator)

        # 窓の「作譜」メニューは置かない(利用者の指定 2026-10-04)。同じ中身は
        # 作譜ペインの右クリックから出る(populate_command_menu)ので、
        # メニューバーに常駐させる必要がない。
        # 「合作」メニューは置かない(利用者の指定 2026-11-12「合作を一回
        # 削除」)。14.0.0 からあった ルームを作成/入室・編集モード切替・
        # 編集権の委譲・視点追従・退室 の項目は、仕組みごと collab_* の
        # メソッドに残してある — メニューから呼ぶ道だけ無くしてある。
        self._menu_collab = None

        rm = mb.addMenu("起動")
        self._run_actions = {}
        # F1 is reserved for the built-in preview (see _bind_shortcuts /
        # _on_f1), not an external simulator, so its label is fixed rather
        # than sourced from run_config and it isn't refreshed in
        # open_settings() below.
        f1_action = rm.addAction("F1: NeoTJAPlayer(内蔵プレビュー)", self._on_f1)
        self._run_actions["F1"] = f1_action
        for k in ("F2", "F3"):
            action = rm.addAction(f"{k}: {self.config_data['run_config'][k]['name']}", lambda key=k: self.run_simulator(key))
            self._run_actions[k] = action
        rm.addSeparator()
        rm.addAction("NeoTJAPlayer を別ウィンドウで開く", self.launch_player)
        rm.addAction("NeoTJAPlayer を入れ直す",
                     lambda: self._offer_player_download(repair=True))

        sm = mb.addMenu("設定")
        sm.addAction("環境設定...", self.open_settings)
        mb.addAction("バージョン変更", self.open_version_change)

        hm = mb.addMenu("ヘルプ")
        hm.addAction("ヘルプを表示", self.open_help)
        hm.addAction("更新を確認", lambda: self.check_for_updates(manual=True))
        hm.addAction("バージョン情報", self._show_about)

    def _bind_shortcuts(self):
        def ins_space(cmd):
            self.editor.insert_at_cursor(cmd + " ")
            self._force_update()

        def ins_nl(cmd):
            self.editor.insert_at_cursor(cmd + "\n")
            self._force_update()

        bindings_space = {
            "Alt+B": "#BPMCHANGE", "Alt+S": "#SCROLL", "Alt+D": "#DELAY",
            "Alt+R": "#BRANCHSTART", "Alt+U": "#MEASURE",
        }
        for seq, cmd in bindings_space.items():
            sc = QShortcut(QKeySequence(seq), self.editor)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda c=cmd: ins_space(c))

        bindings_nl = {
            "Alt+G": "#GOGOSTART", "Ctrl+G": "#GOGOSTART",
            "Alt+O": "#GOGOEND", "Ctrl+O": "#GOGOEND",
            "Alt+L": "#BARLINEON", "Alt+I": "#BARLINEOFF",
        }
        for seq, cmd in bindings_nl.items():
            sc = QShortcut(QKeySequence(seq), self.editor)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda c=cmd: ins_nl(c))

        # 上書き保存は**どこにフォーカスがあっても**効かせる。本文エディタの
        # 中だけだったので、作譜ペインや波形、別窓のプレビューを触っている
        # あいだは Ctrl+S がどこにも届いていなかった(利用者の報告
        # 2026-11-05)。F1 と同じ ApplicationShortcut なら別窓にも届く。
        sc_save = QShortcut(QKeySequence("Ctrl+S"), self.editor)
        sc_save.setContext(Qt.ApplicationShortcut)
        sc_save.activated.connect(self.save_file)

        # 取り消し / やり直しも同じ理由で届いていなかった。別窓のプレビューに
        # だけ手当てがあった(2026-09-28 の報告)ので、ここで**アプリ全体**に
        # 1組だけ張る。本体の窓(作譜ペイン・波形・レーン)でも別窓でも同じ
        # ものが効く。別窓に張ってあったぶんは外した — 同じキーが2本同時に
        # 当てはまると、Qt は「どちらか分からない」として両方止めるため。
        for _seq, _redo in (("Ctrl+Z", False), ("Ctrl+Y", True),
                            ("Ctrl+Shift+Z", True)):
            sc_un = QShortcut(QKeySequence(_seq), self)
            sc_un.setContext(Qt.ApplicationShortcut)
            sc_un.activated.connect(lambda r=_redo: self._undo_here(r))

        sc_cut = QShortcut(QKeySequence("Ctrl+T"), self.editor)
        sc_cut.setContext(Qt.WidgetWithChildrenShortcut)
        sc_cut.activated.connect(self.editor.cut)

        sc_reverse = QShortcut(QKeySequence("Ctrl+M"), self.editor)
        sc_reverse.setContext(Qt.WidgetWithChildrenShortcut)
        sc_reverse.activated.connect(self.reverse_don_ka)

        # 検索・置換。
        sc_find = QShortcut(QKeySequence("Ctrl+F"), self.editor)
        sc_find.setContext(Qt.WidgetWithChildrenShortcut)
        sc_find.activated.connect(self.find_bar.show_find)
        sc_repl = QShortcut(QKeySequence("Ctrl+H"), self.editor)
        sc_repl.setContext(Qt.WidgetWithChildrenShortcut)
        sc_repl.activated.connect(self.find_bar.show_replace)

        # 小節編集(カーソルのある小節が対象)。
        measure_ops = {
            "Ctrl+D": ("dup", None),
            "Ctrl+Shift+D": ("del", None),
            "Ctrl+Shift+N": ("ins", None),
            "Ctrl+Shift+Up": ("move", "up"),
            "Ctrl+Shift+Down": ("move", "down"),
        }
        for seq, (op, arg) in measure_ops.items():
            sc = QShortcut(QKeySequence(seq), self.editor)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda o=op, a=arg: self._measure_op(o, a))

        sc_toggle_cp = QShortcut(QKeySequence("Alt+P"), self.editor)
        sc_toggle_cp.setContext(Qt.WidgetWithChildrenShortcut)
        sc_toggle_cp.activated.connect(self.editor.toggle_checkpoint)

        for seq, direction in (("Alt+Up", "up"), ("Ctrl+Up", "up"), ("Alt+Down", "down"), ("Ctrl+Down", "down")):
            sc = QShortcut(QKeySequence(seq), self.editor)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda d=direction: self.editor.jump_checkpoint(d))

        for k in "1234567890":
            sc = QShortcut(QKeySequence(f"Alt+{k}"), self.editor)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda key=k: self._insert_custom(key))

        for k in ("F2", "F3"):
            sc = QShortcut(QKeySequence(k), self)
            sc.activated.connect(lambda key=k: self.run_simulator(key))

        # F1 is reserved for the built-in game-style preview: first press
        # launches it, subsequent presses (while it's open) toggle hit
        # sounds instead of relaunching. ApplicationShortcut so it fires
        # whether focus is in the editor or the (separate) preview window.
        sc_f1 = QShortcut(QKeySequence("F1"), self)
        sc_f1.setContext(Qt.ApplicationShortcut)
        sc_f1.activated.connect(self._on_f1)

    def _insert_custom(self, key):
        text = self.config_data["custom_shortcuts"].get(key, "")
        if text:
            self.editor.insert_at_cursor(text)
            self._force_update()

    def _on_roll_speed_changed(self, value):
        self.config_data["roll_speed"] = value
        self._force_update()

    # ------------------------------------------------------------------
    # Debounced analysis pass (mirrors the original's after(400, ...) pattern)
    # ------------------------------------------------------------------
    def _on_text_changed(self):
        # タイトルバー/タスクバーに未保存(*)を出す。dirty 判定はアプリ独自の
        # modified_lines(本文が実際に変わったときだけ増える)に合わせる。
        # 読み込み(setPlainText)直後は _open_path / new_file 側で clear して
        # いるので、開いた瞬間に * が付いてしまうことはない。
        # setWindowModified は タイトル内の [*] プレースホルダを * / 空 に
        # 切り替える。
        # setWindowModified は毎回呼ぶとウィンドウタイトルの組み立てが走るので
        # (打鍵ごとに 0.2ms ほど)、状態が変わったときだけ呼ぶ。
        dirty = bool(self.editor.modified_lines)
        if dirty != self.isWindowModified():
            self.setWindowModified(dirty)
        # On large real-world charts, one heavy pass (full re-highlight +
        # course analysis) can take 100ms+, so a longer debounce keeps it
        # from re-triggering on every short pause while actively typing.
        self._heavy_timer.start(600)
        # 打鍵が続いているあいだは、前回の解析パスが予約したプレビュー組み立てを
        # 取り消す。入力中に 300ms の停止が割り込むのを防ぐため(次の解析パスが
        # 改めて予約し直す)。
        self._preview_timer.stop()
        # 走行中の解析も同じ理由で取り消す。内容が変わった以上その結果は
        # 古く、貼ると画面が一瞬前の状態へ巻き戻る。600ms 静まれば
        # _heavy_tasks が新しい内容で投げ直す。貼るのを待たせていた
        # プレビュー結果も同じ理由で捨てる。
        self._pending_preview = None
        self._cancel_analysis()

    # ------------------------------------------------------------------
    # タイトル / 最近使ったファイル / ウィンドウ状態 (クイックウィン群)
    # ------------------------------------------------------------------
    def _refresh_title(self):
        """タイトルを現在のファイル名から組み立て直す。末尾の [*] は Qt の
        未保存インジケータで、setWindowModified(True) のとき * になる。"""
        base = f"{APP_NAME}  v{VERSION}"
        if self.current_file:
            base += f"  —  {os.path.basename(self.current_file)}"
        # 合作しているあいだは、誰がペンを持っているかを肩書きに出す
        # (読むだけなのに打ってしまう事故を防ぐため、常に見える所へ)。
        base += getattr(self, "_collab_tag", "") or ""
        self.setWindowTitle(base + " [*]")

    def _mark_saved(self):
        """保存済み/新規/読込直後に呼ぶ。タイトルの * を消す。"""
        self.setWindowModified(False)

    def _push_recent(self, path):
        if not path:
            return
        path = os.path.abspath(path)
        recent = [p for p in self.config_data.get("recent_files", []) if p != path]
        recent.insert(0, path)
        self.config_data["recent_files"] = recent[:10]
        settings_mod.save_settings(self.config_data)
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        menu = getattr(self, "_recent_menu", None)
        if menu is None:
            return
        menu.clear()
        recent = [p for p in self.config_data.get("recent_files", []) if os.path.exists(p)]
        # 存在しなくなったパスは掃除して保存し直す。
        if recent != self.config_data.get("recent_files", []):
            self.config_data["recent_files"] = recent
            settings_mod.save_settings(self.config_data)
        if not recent:
            act = menu.addAction("(履歴なし)")
            act.setEnabled(False)
            if self.is_start_screen_visible():
                self.start_screen.set_recent([])
            return
        for p in recent:
            menu.addAction(os.path.basename(p), lambda checked=False, path=p: self._open_recent(path))
        menu.addSeparator()
        menu.addAction("履歴を消去", self._clear_recent)
        # 起動画面にも同じ一覧を出す。見えている間だけ並べ直せば足りる
        # (ヘッダを読み直す手間を、消えたあとにまでかけない)。
        if self.is_start_screen_visible():
            self.start_screen.set_recent(recent)

    def _clear_recent(self):
        self.config_data["recent_files"] = []
        settings_mod.save_settings(self.config_data)
        self._rebuild_recent_menu()

    def _open_recent(self, path):
        if not os.path.exists(path):
            QMessageBox.information(self, "情報", "このファイルは見つかりませんでした。")
            self._rebuild_recent_menu()
            return
        if not self._unsaved_check():
            return
        self._open_path(path)

    def _restore_window_state(self):
        try:
            geo = self.config_data.get("window_geometry", "")
            if geo:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            st = self.config_data.get("splitter_state_v15", "")
            if st and hasattr(self, "splitter"):
                self.splitter.restoreState(QByteArray.fromBase64(st.encode("ascii")))
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _save_window_state(self):
        try:
            self.config_data["window_geometry"] = bytes(
                self.saveGeometry().toBase64()).decode("ascii")
            if hasattr(self, "splitter"):
                self.config_data["splitter_state_v15"] = bytes(
                    self.splitter.saveState().toBase64()).decode("ascii")
            settings_mod.save_settings(self.config_data)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _force_update(self):
        """解析とプレビュー更新を今すぐ・同期でやり切る。開く/保存/一括編集など
        「戻ってきた時点で解析が終わっている」ことを前提にした呼び出し元が多い
        ので、ここだけはワーカーへ回さない。走りかけの非同期ジョブは結果が
        古くなるため取り消す。"""
        self._heavy_timer.stop()
        self._preview_timer.stop()
        # 待たせてあるワーカーの結果は、いまここで作り直す内容より古い。
        self._pending_preview = None
        self._cancel_analysis()
        self._heavy_tasks_sync()
        self._preview_tick()

    # -- 非同期パス(デバウンス経由) -------------------------------------
    def _shutdown_analysis_thread(self):
        """解析スレッドを畳む。何度呼んでも安全(閉じる経路が複数あるため)。"""
        th = getattr(self, "_analysis_thread", None)
        if th is None or not th.isRunning():
            return
        self._analysis_worker.drop_pending()
        th.quit()
        if not th.wait(5000):
            # 5 秒待っても終わらないのは想定外。ここで terminate せずに
            # 放置すると終了時にクラッシュするので、状況を残して打ち切る。
            print("AnalysisWorker thread did not stop in 5s; terminating", file=sys.stderr)
            th.terminate()
            th.wait(1000)

    def _cancel_analysis(self):
        """保留中/実行中の解析ジョブの結果を捨てる。まだ始まっていなければ
        本当に捨てられ、既に走っていれば計算自体は止まらないが、返ってきた
        時点で番号が合わずに _on_analysis_done で落ちる。"""
        self._analysis_worker.drop_pending()
        self._analysis_job_id += 1
        self._analysis_inflight = 0

    def _request_analysis(self, kind):
        """解析をワーカースレッドへ依頼する。
        kind="full"    … 内容が変わった(コース解析・ハイライト・索引・プレビュー)
        kind="preview" … 内容は同じで、カーソルの居るコースや分岐だけが変わった
        """
        self._analysis_job_id += 1
        self._analysis_inflight = self._analysis_job_id
        self._analysis_worker.submit(self._analysis_job_id, {
            "kind": kind,
            "content": self.editor.toPlainText(),
            "cursor_line": self.editor.textCursor().blockNumber() + 1,
            "course_override": self._preview_course_override,
            "branch_level": self._preview_branch_level,
            "duration": self.preview_dock.duration_seconds(),
        })
        self.analysis_requested.emit()

    def _heavy_tasks(self):
        """打鍵デバウンス(600ms)の着地点。計算はワーカーへ投げるだけなので
        ここでは止まらない。自動保存は解析結果に依存しないので、待たずに
        今やる(以前は解析の後ろに置いていたため、非同期化すると保存だけが
        遅れることになる)。"""
        self._request_analysis("full")
        self._auto_save_tick()

    def _on_analysis_done(self, job_id, result):
        """ワーカーの結果を画面へ反映する(GUIスレッド)。ウィジェットに触るのは
        必ずここ以降。番号が最新でなければ、内容が変わったあとの古い結果なので
        捨てる(貼ると画面が一瞬巻き戻る)。"""
        if job_id != self._analysis_job_id:
            return
        self._analysis_inflight = 0

        err = result.get("error")
        if err:
            if err != self._last_heavy_error:
                self._last_heavy_error = err
                self.statusBar().showMessage(f"解析中にエラーが発生しました: {err}", 8000)
            return
        self._last_heavy_error = None

        try:
            self._apply_analysis_result(result)
        except Exception as e:  # noqa: BLE001
            # 反映側で転んでも自動保存や編集を巻き込まない(従来と同じ扱い)。
            traceback.print_exc()
            sig = f"{type(e).__name__}: {e}"
            if sig != self._last_heavy_error:
                self._last_heavy_error = sig
                self.statusBar().showMessage(f"解析結果の反映中にエラーが発生しました: {sig}", 8000)

    def _apply_analysis_result(self, result):
        """計算済みの結果を画面へ貼る。ここは GUI スレッドなので、この所要時間が
        そのまま「操作が効かない時間」になる。だから貼る作業も
          (1) 構文ハイライト/サイドバー/索引
          (2) プレビュー(NeoTJAPlayer/情報/打音)
        の二つに分け、重い譜面では (2) を少し後ろへ回して一度の停止を半分にする。
        軽い譜面(下の PREVIEW_DEFER_SEC 未満)はこれまでどおり一度に貼る。"""
        content = self.editor.toPlainText()
        cursor_line = result["cursor_line"]

        if result["kind"] == "full":
            t0 = time.perf_counter()
            self.courses_info = result["courses_info"]
            self._refresh_sidebar(content)
            data = result["highlight"]
            self.editor.highlight_data = data
            self.editor.invalid_lines = data.invalid_lines
            self._global_warnings = data.global_warnings
            self.highlighter.apply_data(data)
            self.editor.gutter.update()
            # ステータスバーが使う「行 -> 小節/時刻」索引。
            self._cursor_index = result["cursor_index"]
            self._update_status()
            self._highlight_apply_sec = time.perf_counter() - t0

            # 区間ごとの系統。作譜モードの書き込み先に要る(本家と同じ流れ方で
            # 編集するため)。
            self._preview_branch_path = list(result["preview"].get("branch_path") or [])
            payload = (content, result["clicks"], result["preview"], cursor_line)
            if self._preview_build_sec > self.PREVIEW_DEFER_SEC:
                self._pending_preview = payload
                self._preview_timer.start(120)
                return
            self._apply_preview_payload(payload)
        else:
            # プレビューだけの差し替えは元から軽い(貼るだけ)ので分割しない。
            t0 = time.perf_counter()
            self.preview_dock.set_metronome_clicks(result["clicks"])
            self._preview_branch_path = list(result["preview"].get("branch_path") or [])
            self.preview_dock.set_preview_data(
                result["preview"], self._find_course_stats(result["preview"].get("course_key")),
            )
            self._preview_build_sec = time.perf_counter() - t0
            self.preview_dock.set_demostart(self.demostart_seconds())
            self._settle_preview_key(cursor_line)

    def _apply_preview_payload(self, payload):
        """プレビュー側の反映本体。_apply_analysis_result から直接、または
        重い譜面のときは _preview_tick から少し遅れて呼ばれる。"""
        content, clicks, preview, cursor_line = payload
        t0 = time.perf_counter()
        self.preview_dock.refresh_from_content(
            content, self.current_file, clicks, preview,
            self._find_course_stats(preview.get("course_key")),
        )
        self._preview_build_sec = time.perf_counter() - t0
        self._settle_preview_key(cursor_line)

    def _settle_preview_key(self, cursor_line):
        # 組み立てた条件を控える。この直後にカーソル移動で走る
        # _update_metronome_schedule が、同じ条件なら丸ごと省けるようにする。
        self._preview_key = self._current_preview_key(cursor_line)
        # ジョブを投げてから結果が返るまでにカーソルが別のコースへ移っていたら、
        # いま貼ったプレビューはもう古いので取り直す。ここから直接
        # _request_analysis() を呼ぶと「反映 -> 依頼 -> 反映」がイベントループを
        # 埋め尽くして他の処理が一切通らなくなるため、必ず 150ms のデバウンスを
        # 挟んで _update_metronome_schedule 経由にする(あちらの鍵の一致判定で
        # 空振りなら何も起きない)。
        now_line = self.editor.textCursor().blockNumber() + 1
        if self._current_preview_key(now_line) != self._preview_key:
            self._metronome_timer.start(150)

    # -- 同期パス(_force_update 専用) -----------------------------------
    def _heavy_tasks_sync(self):
        content = self.editor.toPlainText()
        # 解析/プレビュー更新のどこかで想定外の例外が出ても、自動保存まで
        # 道連れにしない。以前は解析が一度でも例外を投げると、この後ろにある
        # _auto_save_tick() へ到達しなくなり「入力しているのに保存されず、
        # プレビューも止まったまま、エラー表示も無い」という無言の失敗になった。
        # 握りつぶさず、標準エラーへトレースを出しステータスバーにも表示する。
        try:
            self._heavy_analysis(content)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            sig = f"{type(e).__name__}: {e}"
            if sig != self._last_heavy_error:
                self._last_heavy_error = sig
                self.statusBar().showMessage(f"解析中にエラーが発生しました: {sig}", 8000)
        else:
            self._last_heavy_error = None
        self._auto_save_tick()

    def _precompute_for_load(self, content):
        """これから setPlainText で流し込む内容の解析を**先に**済ませ、
        ハイライタへ結果を差しておく。

        **なぜ順番を入れ替えるのか**
        QSyntaxHighlighter は文書が差し替わると、その場で全ブロックぶん
        highlightBlock() を回す。従来は「setPlainText(まだ古い/空の
        HighlightData で全行を塗る) -> 解析 -> apply_data(正しいデータで
        もう一度全行を塗り直す)」となっていて、数万行の譜面では全塗りを
        二度払っていた。29,138 行の実測で setPlainText 221ms +
        apply_data 1,258ms = 1.48 秒。

        先に正しいデータを入れておけば、setPlainText のときの1回で最終的な
        色がそのまま乗る。あとから来る apply_data は同じオブジェクトを
        受け取るので「完全一致 = 塗り直し不要」で即座に抜ける。
        塗る回数が 2 回から 1 回になるだけで、塗り方も結果も変わらない。

        解析に失敗したら黙って何もしない(従来経路がそのまま走るだけ)。"""
        try:
            courses = self.analyzer.parse_courses(content)
            data = compute_highlight_data(content, courses)
        except Exception:  # noqa: BLE001
            # ここで転んでも読み込み自体は続行させる。_heavy_analysis が
            # 従来どおり作り直し、そちらでエラー表示まで面倒を見る。
            self._preload_analysis = None
            return
        self._preload_analysis = (content, courses, data)
        # setPlainText が走らせる自動ハイライトに、最終的なデータを見せる。
        self.highlighter.data = data
        self.editor.highlight_data = data
        self.editor.invalid_lines = data.invalid_lines
        self._global_warnings = data.global_warnings

    def _heavy_analysis(self, content):
        # _open_path が setPlainText の前に同じ内容で作っておいた解析結果が
        # あればそれを使う(下の _precompute_for_load を参照)。無ければ従来通り
        # ここで作る。内容が1文字でも違えばキャッシュは捨てる。
        cached = self._preload_analysis
        self._preload_analysis = None
        if cached is not None and cached[0] == content:
            self.courses_info, data = cached[1], cached[2]
            self._refresh_sidebar(content)
        else:
            self.courses_info = self.analyzer.parse_courses(content)
            self._refresh_sidebar(content)
            data = compute_highlight_data(content, self.courses_info)
        self.editor.highlight_data = data
        self.editor.invalid_lines = data.invalid_lines
        self._global_warnings = data.global_warnings
        self.highlighter.apply_data(data)
        self.editor.gutter.update()
        # ステータスバーが使う「行 -> 小節/時刻」索引をここで作り直す。
        self._cursor_index = self.analyzer.build_cursor_index(content)
        self._update_status()

    def _preview_tick(self):
        """プレビュー反映(遅延実行の入口)。ワーカーの結果が待たされている
        ときはそれを貼るだけ。そうでなければ(_force_update の同期経路)ここで
        組み立てから行う。解析側と同じく、例外が出ても自動保存や編集を
        巻き込まないようここで受け止める。"""
        self._preview_timer.stop()
        payload = self._pending_preview
        self._pending_preview = None
        t0 = time.perf_counter()
        try:
            if payload is not None:
                self._apply_preview_payload(payload)
                return
            self._refresh_preview()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            sig = f"{type(e).__name__}: {e}"
            if sig != self._last_heavy_error:
                self._last_heavy_error = sig
                self.statusBar().showMessage(f"プレビュー更新中にエラーが発生しました: {sig}", 8000)
        self._preview_build_sec = time.perf_counter() - t0
        self.preview_dock.set_demostart(self.demostart_seconds())

    def _set_preview_content(self, content):
        """プレビューが映す譜面スナップショットを更新する(保存/開く/新規時)。"""
        self._preview_content = content
        # 開くときは _precompute_for_load が同じ内容をもう解析済みなので使い回す
        # (重い譜面では parse_courses だけで 48ms かかり、開く経路ではこれを
        # 二度払っていた)。中身を書き換える利用者は居ないので共有して安全。
        cached = self._preload_analysis
        if cached is not None and cached[0] == content:
            self._preview_courses = cached[1]
        else:
            self._preview_courses = self.analyzer.parse_courses(content)

    def _refresh_preview(self):
        """プレビュー(NeoTJAPlayer/情報/打音/メトロノーム)を編集中(未保存)の
        エディタ内容から組み直す。保存を待たずにすぐ反映される。音源(WAVE)は
        refresh_from_content 側でパスが変わったときだけ読み直すので、編集中に
        鳴り直すことはない。OFFSET・波形の合わせ込みは別経路(OFFSETスピン
        ボックス)でライブのまま。"""
        content = self.editor.toPlainText()
        cursor_line = self.editor.textCursor().blockNumber() + 1
        metronome_clicks = self.analyzer.build_metronome_clicks(content, cursor_line, self.preview_dock.duration_seconds())
        preview_data = self.analyzer.build_preview_timeline(
            content, cursor_line, self._preview_course_override, branch_level=self._preview_branch_level,
        )
        self._preview_branch_path = list(preview_data.get("branch_path") or [])
        course_stats = self._find_course_stats(preview_data.get("course_key"))
        # 作譜の位置変換も本文のBPM・拍子・DELAYを使う。小節内の線形補間はしない。
        from .note_timing import NoteTiming
        rng = self.analyzer.course_line_range(content, preview_data.get('course_key'))
        try:
            with note_edit.editing_branch(self._preview_branch_level, self._preview_branch_path):
                body = note_edit.course_body_span(content, rng)
                timing = NoteTiming(content, body, note_edit.measure_spans(content, body)) if body else None
        except (ValueError, ZeroDivisionError):
            timing = None
        self.preview_dock.chart_edit.note_timing = timing
        self.preview_dock.refresh_from_content(content, self.current_file, metronome_clicks, preview_data, course_stats)
        # 今組み立てた条件を控える。この直後にカーソル移動で走る
        # _update_metronome_schedule が、同じ条件なら丸ごと省けるようにする。
        self._preview_key = self._current_preview_key(cursor_line)

    def _current_preview_key(self, cursor_line):
        """プレビュー/メトロノームの組み立て結果を決める入力の組。これが同じ
        なら再構築しても 1 ビットも変わらない。カーソル行そのものではなく
        「カーソルがどのコース本文に居るか」だけが効く点が肝で、同じコース内で
        カーソルを動かしているあいだ(=ほぼ常時)は再構築を省ける。"""
        hit = self._cursor_index.get(cursor_line)
        return (
            self.editor.document().revision(),
            hit[2] if hit is not None else None,
            self._preview_course_override,
            self._preview_branch_level,
            round(self.preview_dock.duration_seconds(), 4),
        )

    def _find_course_stats(self, course_key):
        return next((c for c in self.courses_info if c["key"] == course_key), None)

    def _preview_course_stats(self, course_key):
        return next((c for c in self._preview_courses if c["key"] == course_key), None)

    def _update_metronome_schedule(self):
        # Lighter-weight than _heavy_tasks: just re-picks which course's
        # #MEASURE/#BPMCHANGE the metronome/preview should follow when the
        # cursor moves into a different course, without re-running the
        # expensive syntax highlighter over the whole document.
        # プレビューは編集中(未保存)の内容を映すので、ここでもライブの
        # エディタ内容を使う。
        # 解析パス/プレビュー組み立てが既に予約されているなら、そちらが同じものを
        # 作り直すのでここでは何もしない。打鍵はカーソルも動かすため、これが無いと
        # 「150ms 後にここで組み直し → 600ms 後に解析パスでもう一度」と、重い譜面で
        # 同じ 200ms 超の処理を二度払っていた。
        # 解析ジョブが走行中/予約済みなら、そちらが同じものを作り直すので
        # ここでは何もしない(走行中のジョブは、終わったあとにカーソルが
        # 移っていれば _apply_analysis_result が取り直してくれる)。
        if (self._heavy_timer.isActive() or self._preview_timer.isActive()
                or self._analysis_inflight):
            return

        cursor_line = self.editor.textCursor().blockNumber() + 1
        # このパスはカーソルが動くたびに(150ms デバウンスで)走るが、結果を
        # 決めるのは「内容・カーソルが属するコース・コース上書き・分岐・曲の
        # 長さ」だけ。同じコース内でカーソルを動かしているあいだは出力が
        # まったく同じなので、数万行の譜面で 240ms かかる再構築を丸ごと省く。
        # (方向キーを押しっぱなしにすると固まる主因がこれだった)
        if self._current_preview_key(cursor_line) == self._preview_key:
            return
        # 組み直しは 240ms かかるのでワーカーへ。_preview_key はここでは進めず、
        # 結果を貼るときに更新する(途中で失敗しても取り直しが効くように)。
        self._request_analysis("preview")

    def _refresh_sidebar(self, content):
        # 曲名の札は無くした(右下の TITLE / SUBTITLE 欄が受け持つ)。
        # ここは難易度カードの作り直しだけ。
        ordered_courses = sorted(
            self.courses_info, key=lambda c: -TJACourseAnalyzer.DIFF_RANK.get(c["key"], -1)
        )
        visible_keys = [c["key"] for c in ordered_courses]
        for course in ordered_courses:
            k = course["key"]
            if k in self._course_cards:
                self._course_cards[k].update_course(course)
            else:
                card = CourseCard(course)
                card.jumpRequested.connect(self.jump_to_course)
                card.set_scale(getattr(self, "_bottom_scale_w", 1.0))
                self._course_cards[k] = card

        for k in [k for k in self._course_cards if k not in visible_keys]:
            self._course_cards[k].deleteLater()
            del self._course_cards[k]

        # Explicitly reposition every card (new or reused) each time, since a
        # card kept from a previous refresh (e.g. the blank template's single
        # "oni" course loaded at startup) would otherwise stay pinned at
        # whatever index it was first inserted at.
        for i, course in enumerate(ordered_courses):
            self.sb_cards_layout.insertWidget(i, self._course_cards[course["key"]], 1)
        self._sync_course_card()
        self._sync_header_fields()

    def _update_status(self):
        cursor = self.editor.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.positionInBlock()
        msg = f"  行 {line}  文字数 {col}"
        # カーソルがコース本文内なら、その小節番号と譜面上の時刻を出す。
        # 以前はここで measure_at_cursor()/time_at_cursor() を直接呼んでいたが、
        # どちらも文書全体を走査するため数万行の譜面ではカーソル移動1回で
        # 70ms 以上かかり、方向キーやクリックのたびに画面が固まっていた。
        # いまは解析パスで作った索引を O(1) で引くだけ。打鍵直後のごく短い間
        # (デバウンス 600ms)は 1 つ前の内容の値が出るが、表示専用なので実害は無い。
        hit = self._cursor_index.get(line)
        if hit is not None:
            measure, sec = hit[0], hit[1]
            msg += f"  │  第{measure}小節  {int(sec) // 60}:{sec % 60:06.3f}"
        msg += "  │  ANSI (CP932)"
        invalid_lines = getattr(self.editor, "invalid_lines", {})
        if line in invalid_lines:
            msg += f"  │  ⚠ 不正文字数 ({invalid_lines[line]})"
        warnings = getattr(self, "_global_warnings", [])
        if warnings:
            msg += "  │  " + "  ".join(warnings)
        self.status_label.setText(msg)

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------
    def _unsaved_check(self) -> bool:
        if not self.editor.modified_lines:
            return True
        ans = QMessageBox.question(
            self, "確認", "変更を保存しますか？",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if ans == QMessageBox.Yes:
            # 保存が中止された(cp932にできない文字がある等)なら、変更を
            # 捨てる方向へは進めない。
            return bool(self.save_file())
        return ans == QMessageBox.No

    def new_file(self, confirm=True):
        if confirm and not self._unsaved_check():
            return
        # 作った日の印を1行目に入れる(利用者の指定 2026-10-30)。
        text = new_file_text()
        self.editor.setPlainText(text)
        self.current_file = None
        self.editor.modified_lines.clear()
        self.editor.checkpoints.clear()
        # 別のファイルになったので、プレビュー側に残っている印も消す。
        self.editor.checkpointsChanged.emit()
        self._refresh_title()
        self._mark_saved()
        self._set_preview_content(text)
        self._force_update()

    def new_file_dialog(self, browse_audio=False):
        """新規作成のダイアログ。browse_audio が真なら、開いてすぐ「音源
        ファイル」の参照(ファイル選択)まで進める — 起動画面の「音源ファイル
        から」用。ダイアログが出てから選ばせるので、やめても URL の画面に
        戻るだけで済む。"""
        if not self._unsaved_check():
            return
        from neotja.dialogs.new_project_dialog import NewProjectDialog
        dlg = NewProjectDialog(self)
        if browse_audio:
            # exec で表示されたあとに開く。先に開くとダイアログより前に
            # ファイル選択が出て、どの窓の子か分からなくなる。
            QTimer.singleShot(0, dlg._browse_audio_file)
        if dlg.exec() != QDialog.Accepted:
            return
        if dlg.mode == "blank":
            self.new_file(confirm=False)
            self._leave_start_screen()
            return
        self._apply_youtube_project(
            dlg.result_title, dlg.result_subtitle, dlg.result_wave_path, dlg.result_folder,
            dlg.result_bpm, dlg.result_offset, dlg.enable_ai_gen,
        )

    @staticmethod
    def _relocate_wave_file(src: str, dst: str):
        """Copies src to dst, then best-effort removes src - not
        shutil.move(), because on Windows a just-finished QAudioDecoder (see
        BpmOffsetDetectWorker, which analyzes this exact file for BPM/OFFSET
        auto-detect right before this runs) can still hold src's file handle
        open for a moment, and move()'s internal rename/unlink step raises
        PermissionError ("WinError 32: the process cannot access the file")
        in that case - which used to abort project creation entirely before
        the .tja ever got written. Copying first means a lingering lock only
        costs a leftover duplicate .ogg (removed on a later retry, or left
        as harmless clutter) instead of losing the whole new project."""
        shutil.copy2(src, dst)
        for _ in range(5):
            try:
                os.remove(src)
                return
            except OSError:
                time.sleep(0.3)

    def _apply_youtube_project(self, title, subtitle, wave_path, folder, bpm=None, offset=None, enable_ai_gen=False):
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', title).strip() or "untitled"

        # Give every new song its own <TITLE>/ folder, with the .tja and the
        # .ogg both named to match TITLE (rather than the raw video title
        # yt-dlp downloaded it as).
        song_folder = os.path.join(folder, safe_name)
        os.makedirs(song_folder, exist_ok=True)
        wave_name = f"{safe_name}.ogg"
        new_wave_path = os.path.join(song_folder, wave_name)
        if os.path.abspath(wave_path) != os.path.abspath(new_wave_path):
            self._relocate_wave_file(wave_path, new_wave_path)

        content = new_file_text(title)
        # SUBTITLE always carries a leading "--" marker (see
        # parse_preview_headers's matching read-side convention) - the
        # actual text, if any, goes right after it, never replacing it.
        content = content.replace("SUBTITLE:--\n", f"SUBTITLE:--{subtitle}\n" if subtitle else "SUBTITLE:--\n", 1)
        content = content.replace("WAVE:\n", f"WAVE:{wave_name}\n", 1)
        # ひな形が最初から LEVEL:10 なので、ここで入れ直すことは
        # もう無い(利用者の指定 2026-10-30)。
        # BPM/OFFSET auto-detection (experimental) already ran once in the
        # new-project dialog against the downloaded audio - pre-fill the
        # headers with its result rather than leaving them blank/0.00. Best-
        # effort: a formatting hiccup on this experimental value must not
        # abort file creation before editor.setPlainText(content) below runs
        # (which would otherwise silently leave the editor on its previous,
        # untitled content).
        try:
            if bpm:
                content = content.replace("BPM:120\n", f"BPM:{bpm:g}\n", 1)
            if offset is not None:
                content = content.replace("OFFSET:0.00\n", f"OFFSET:{offset:.3f}\n", 1)
        except (TypeError, ValueError):
            pass

        tja_path = os.path.join(song_folder, f"{safe_name}.tja")

        self.editor.setPlainText(content)
        self.current_file = tja_path
        self.editor.modified_lines.clear()
        self.editor.checkpoints.clear()
        # 別のファイルになったので、プレビュー側に残っている印も消す。
        self.editor.checkpointsChanged.emit()
        self._refresh_title()
        self.save_file()
        self._mark_saved()
        self._push_recent(tja_path)
        self._leave_start_screen()
        self._force_update()

        self.preview_dock.expand()

        # Opt-in (see new_project_dialog.py's 実験的機能を使用する section) -
        # runs in the background against the template's only course (Oni)
        # and writes a separate <basename>(AI).tja next to the file just
        # created above, without touching it or the editor.
        if enable_ai_gen:
            headers = parse_preview_headers(content)
            gen_bpm = headers["bpm"] or 120.0
            gen_offset = headers["offset"] or 0.0
            self.status_label.setText("AI譜面生成を実行中(実験的)...")
            worker = ChartGenWorker(new_wave_path, gen_bpm, gen_offset, subdivision=16, density=0.5, parent=self)
            self._new_project_chart_gen_worker = worker

            def on_generated(body, _content=content, _path=tja_path):
                # Silent write, not _save_ai_chart_variant - this runs
                # unattended after project creation, so it must not pop a
                # modal QMessageBox the user never asked to dismiss.
                try:
                    new_path = self._write_ai_variant_file(_content, "Oni", body, _path)
                except UnicodeEncodeError:
                    self.status_label.setText(
                        "AI譜面生成の保存に失敗しました(実験的): "
                        "ANSI (cp932) で表せない文字が含まれています。")
                    return
                except OSError as e:
                    self.status_label.setText(f"AI譜面生成の保存に失敗しました(実験的): {e}")
                    return
                if new_path is None:
                    self.status_label.setText("AI譜面生成: 対象コースが見つかりませんでした(実験的)。")
                    return
                self.status_label.setText(f"AI譜面生成(実験的)による追加ファイルを作成しました: {os.path.basename(new_path)}")

            def on_failed(msg):
                self.status_label.setText(f"AI譜面生成に失敗しました(実験的): {msg}")

            worker.generated.connect(on_generated)
            worker.failed.connect(on_failed)
            worker.start()

    def _begin_loading(self, message: str):
        """重い読み込み処理の間、中央に「読み込み中」オーバーレイを出す。
        setPlainText(構文ハイライト)や _force_update(譜面解析)は GUI スレッド
        で同期実行されて数百 ms 固まるので、その前にこれを見せて processEvents で
        一度だけ描画しておく(処理中はこの表示のまま静止する)。"""
        lbl = getattr(self, "_loading_overlay", None)
        if lbl is None:
            lbl = QLabel(self)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                "background-color: rgba(0,0,0,160); color: #ffffff;"
                " font-size: 18px; font-weight: bold;"
            )
            self._loading_overlay = lbl
        lbl.setText(message)
        lbl.setGeometry(self.rect())
        lbl.raise_()
        lbl.show()
        QApplication.processEvents()

    def _end_loading(self):
        lbl = getattr(self, "_loading_overlay", None)
        if lbl is not None:
            lbl.hide()

    def open_file(self):
        if not self._unsaved_check():
            return
        path, _ = QFileDialog.getOpenFileName(self, "開く", "", "TJA Files (*.tja);;Text Files (*.txt)")
        if not path:
            return
        self._open_path(path)

    def _open_path(self, path):
        """指定パスの TJA を読み込んでエディタに反映する。ダイアログ経由の
        open_file、ドラッグ&ドロップ、最近使ったファイルの共通処理。呼び出し側
        で未保存確認(_unsaved_check)を済ませておくこと。"""
        try:
            with open(path, "r", encoding="cp932") as f:
                content = f.read()
        except UnicodeDecodeError:
            # utf-8-sig (not plain utf-8) so a leading BOM (U+FEFF) is stripped.
            # A UTF-8-BOM file otherwise leaves U+FEFF on line 1, and since
            # TITLE: is conventionally the first line, every startswith("TITLE:")
            # check across the app silently fails (blank title, etc.).
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()
            QMessageBox.information(
                self, "文字コード変換",
                "UTF-8で保存されたファイルを読み込みました。\n次回保存時に自動的にANSI形式で保存されます。",
            )
        except OSError as e:
            QMessageBox.critical(self, "読み込みエラー", f"ファイルを開けませんでした:\n{e}")
            return
        # Belt and braces: strip any stray leading BOM so header parsing always
        # sees a clean first line, whichever decode path ran.
        content = content.lstrip("﻿")

        self._begin_loading("TJAを読み込み中...")
        try:
            self._precompute_for_load(content)
            self.editor.setPlainText(content)
            self.current_file = path
            self.editor.modified_lines.clear()
            # 別のファイルになったので、前の譜面で付けた ▶ は消す
            # (new_file / _apply_youtube_project と同じ扱い)。残しておくと
            # 譜面Aの行番号のまま譜面Bの無関係な行に ▶ が出ていた。
            self.editor.checkpoints.clear()
            self.editor.checkpointsChanged.emit()
            self._refresh_title()
            self._mark_saved()
            # 合作の UI は外してある(2026-11-12)ので、合作中かの確認
            # (_collab_check_file)は呼ばない — neotja.collab を読み込まない。
            self._push_recent(path)
            # 起動画面から開いたときはここで分割画面へ。切り替えは解析
            # (_force_update)より前 — 右ペインが見えてから再生画面の倍率を
            # 取り直す(_fit_player_to_pane)ので、順番が逆だと一瞬縮んで映る。
            self._leave_start_screen()
            self._set_preview_content(content)
            self._force_update()
            # 開いた直後は「開く」ボタンにフォーカスが残っていて、そのまま
            # Space を押すとファイル選択の窓が開いていた(利用者の報告
            # 2026-11-05)。再生の Space を受ける側へ渡しておく。
            self.preview_dock.focus_preview()
        finally:
            self._end_loading()

    def open_folder(self):
        if self.current_file and os.path.exists(self.current_file):
            folder = os.path.dirname(self.current_file)
            if os.name == 'nt':
                os.startfile(folder)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
        else:
            QMessageBox.information(self, "情報", "ファイルがまだ保存されていません。")

    @staticmethod
    def _write_text_atomic(path: str, content: str, encoding: str):
        """同じフォルダに一時ファイルを書いてから置き換える。

        以前は元のファイルを直接 "w" で開いていた。open した瞬間に中身は
        0バイトになるので、そのあとの書き込みが失敗すると(容量不足・
        ウイルス対策ソフトの割り込み・電源断)**譜面が丸ごと消える**。
        自動保存は編集中に何度も書くぶん、当たる機会もそれだけ多い。
        os.replace は同一ドライブなら不可分なので、失敗しても元のファイルは
        そのまま残る。"""
        d = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".neotja_", suffix=".tmp", dir=d)
        os.close(fd)
        try:
            # newline は既定のまま(Windows なら \n -> \r\n)。ここを変えると
            # 保存のたびに改行コードが入れ替わってしまう。
            with open(tmp, "w", encoding=encoding) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def save_file(self):
        """上書き保存。cp932 で表せない文字が含まれる場合は書き込まず、何が
        問題かを知らせて利用者に判断してもらう。戻り値は「保存できたか」で、
        _unsaved_check はこれを見て終了/破棄を中止する。"""
        if not self.current_file:
            return self.save_file_as()
        content = self.editor.toPlainText()

        bad = find_non_cp932_chars(content)
        if bad:
            # 以前はここで errors="replace" のまま書いていたため、絵文字などが
            # 無言で「?」に置き換わって原文が失われていた。壊れたテキストは
            # 絶対に書かない。
            if not self._confirm_non_cp932_save(bad):
                return False
            try:
                self._write_text_atomic(self.current_file, content, "utf-8")
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "保存エラー", str(e))
                return False
            self.editor.modified_lines.clear()
            self.editor.gutter.update()
            self._mark_saved()
            self._set_preview_content(content)
            self._refresh_preview()
            self.statusBar().showMessage(
                "UTF-8で保存しました。TJAシミュレータによっては読み込めないことがあります。", 8000)
            return True

        try:
            # ここに来た時点で cp932 で完全に表せることを確認済みなので、
            # errors は strict のままでよい(想定外は握りつぶさず例外にする)。
            self._write_text_atomic(self.current_file, content, "cp932")
            self.editor.modified_lines.clear()
            self.editor.gutter.update()
            self._mark_saved()
            self._set_preview_content(content)
            self._refresh_preview()
        except Exception as e:
            QMessageBox.critical(self, "保存エラー", str(e))
            return False
        return True

    def _confirm_non_cp932_save(self, bad):
        """cp932 で保存できない文字を一覧で見せ、どうするか尋ねる。
        既定は「保存しない」(データを壊さない側)。UTF-8 で保存してよいと
        明示的に選ばれた場合だけ True を返す。"""
        chars = []
        for _line_no, ch in bad:
            if ch not in chars:
                chars.append(ch)
        detail_lines = []
        for ch in chars[:10]:
            lines = sorted({ln for ln, c in bad if c == ch})
            shown = ", ".join(str(ln) for ln in lines[:5])
            if len(lines) > 5:
                shown += " ほか"
            detail_lines.append(f"  「{ch}」 (U+{ord(ch):04X})  {shown} 行目")
        if len(chars) > 10:
            detail_lines.append(f"  ...ほか {len(chars) - 10} 種類")

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("保存できない文字があります")
        box.setText(
            "ANSI (cp932) で表せない文字が含まれているため、このまま保存すると"
            "その文字が失われます。\n\n" + "\n".join(detail_lines) +
            "\n\nこれらの文字を消すか別の文字に置き換えてから保存し直してください。\n"
            "UTF-8で保存することもできますが、TJAシミュレータによっては"
            "読み込めない場合があります。"
        )
        btn_cancel = box.addButton("保存しない", QMessageBox.RejectRole)
        btn_utf8 = box.addButton("UTF-8で保存する", QMessageBox.DestructiveRole)
        box.setDefaultButton(btn_cancel)
        box.setEscapeButton(btn_cancel)
        box.exec()
        return box.clickedButton() is btn_utf8

    def _auto_save_tick(self):
        if not self.config_data.get("auto_save_enabled", False):
            return
        if not self.current_file or not self.editor.modified_lines:
            return

        # 自動保存はデバウンスタイマ上(=入力中)に走るので、モーダルは絶対に
        # 出さない。cp932 で表せない文字があるときは書き込みを見送り、
        # ステータスバーで知らせるだけにする。同じ理由でスキップし続ける間は
        # 毎tick出し直さない。
        bad = find_non_cp932_chars(self.editor.toPlainText())
        if bad:
            chars = []
            for _ln, ch in bad:
                if ch not in chars:
                    chars.append(ch)
            sig = "".join(chars)
            if sig != self._last_autosave_encode_warning:
                self._last_autosave_encode_warning = sig
                shown = " ".join(f"「{c}」" for c in chars[:5])
                more = f" ほか{len(chars) - 5}種類" if len(chars) > 5 else ""
                self.statusBar().showMessage(
                    f"自動保存を見送りました: ANSI (cp932) で保存できない文字があります: {shown}{more}",
                    10000)
            return
        self._last_autosave_encode_warning = None

        if self.save_file():
            self.statusBar().showMessage("自動保存しました", 2000)

    def _on_auto_save_toggled(self, checked):
        self.config_data["auto_save_enabled"] = checked
        settings_mod.save_settings(self.config_data)
        self._sync_auto_save_button(checked)

    def _sync_auto_save_button(self, checked):
        self.btn_auto_save.setText("自動保存: ON" if checked else "自動保存: OFF")
        self.btn_auto_save.setObjectName("accentButton" if checked else "")
        self.btn_auto_save.style().unpolish(self.btn_auto_save)
        self.btn_auto_save.style().polish(self.btn_auto_save)

    def save_file_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "名前を付けて保存", "", "TJA Files (*.tja);;All Files (*.*)")
        if not path:
            return False
        if not path.lower().endswith(".tja") and "." not in os.path.basename(path):
            path += ".tja"
        self.current_file = path
        self._refresh_title()
        ok = self.save_file()
        if ok:
            self._push_recent(path)
            # 起動画面のまま「名前を付けて保存」した(メニューからはできる)
            # なら、もうファイルがあるのでエディタへ。
            self._leave_start_screen()
        return ok

    def open_new_window(self):
        # env=child_env() が要る。**同じパスの exe** を起動するので、渡さないと
        # 2つ目の窓が1つ目の展開先に相乗りし、1つ目を閉じた時点で足元が消える
        # (neotja/child_env.py)。
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable], env=child_env())
        else:
            subprocess.Popen([sys.executable, os.path.abspath(sys.argv[0])],
                             env=child_env())

    # ------------------------------------------------------------------
    # ドラッグ&ドロップで開く
    # ------------------------------------------------------------------
    @staticmethod
    def _dropped_tja_path(event):
        md = event.mimeData()
        if not md.hasUrls():
            return None
        for url in md.urls():
            p = url.toLocalFile()
            if p and p.lower().endswith((".tja", ".txt")):
                return p
        return None

    def dragEnterEvent(self, event):
        if self._dropped_tja_path(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        path = self._dropped_tja_path(event)
        if not path:
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        if not self._unsaved_check():
            return
        self._open_path(path)

    def paintEvent(self, event):
        """最初にウィンドウが描けた時点で、遅らせてある素材の読み込みを予約する。

        ゲーム風プレビューの素材(実測 400ms 強)は起動の道から外してあるが、
        利用者が開いたときに待たされては意味が無いので、**画面が出たあとの
        手すきに**読ませる。0ms のタイマーにするのは、この paintEvent から
        直に呼ぶと今描いているコマがそのぶん止まってしまうため。

        予約の起点を showEvent ではなく paintEvent にしているのは、
        showEvent は「まだ1画素も出ていない」時点でも来るため — そこで
        予約すると、最初の描画より先に 400ms の読み込みが割り込みかねない。"""
        super().paintEvent(event)
        if not self._skin_warm_armed:
            self._skin_warm_armed = True
            QTimer.singleShot(0, self._warm_preview_skin)

    def _warm_preview_skin(self):
        # 起動直後に1回だけ。以降は PreviewDock 側が「もう読んだ」を見て
        # 素通りするので、取りこぼしても二重読みにはならない。
        try:
            self.preview_dock.warm_skin()
        except Exception:  # noqa: BLE001
            # 先読みは速さのためだけの仕掛けなので、ここで転んでも起動は
            # 続けさせる。必要になった時点で描画側が読み直す。
            traceback.print_exc()
        self._open_preview_on_start()

    def _open_preview_on_start(self):
        """起動したら譜面プレビューも一緒に開く(利用者の指定 2026-10-31)。

        素材を読み終えたあとに1回だけ。開く手順は、利用者がボタンを押した
        ときとまったく同じ道を通す。"""
        if self._preview_opened_on_start:
            return
        self._preview_opened_on_start = True
        try:
            if not self.preview_dock.is_game_preview_visible():
                self.preview_dock.set_game_preview_visible(True)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def eventFilter(self, obj, event):
        # Alt はドン/カの大音符入力(Alt+F/J)、Alt+0〜9 のカスタムショートカット、
        # 命令挿入(Alt+B/D/G/L/R)など、このアプリでは修飾キーとして多用している。
        # Qt の既定では Alt を単独で押して離すとメニューバーがハイライトされ、
        # 矢印キーでの操作待ちになってしまう(=キー入力の邪魔)。ここでは
        # Key_Alt そのものの生イベント(押下/離し)だけを握りつぶす。
        # Alt+何か の組み合わせは、組み合わせ先のキー(F/J/0〜9 等)のイベント
        # 側に AltModifier が乗って別に届くので、これを止めても影響しない。
        # F10 での経路(F10 は Key_Alt ではない)にも影響しない。
        if event.type() in (QEvent.KeyPress, QEvent.KeyRelease) and event.key() == Qt.Key_Alt:
            return True
        # マウスのサイドボタンで作譜の分割を変える(利用者の希望
        # 2026-11-02)。「どこでも反応してほしい」とのことなので、作譜
        # ペインの上だけでなくゲーム画面でもエディタでも効かせる。ここで
        # 握りつぶすので、作譜ペイン自身の mousePressEvent までは届かない
        # (二重に変わらない)。
        if (event.type() == QEvent.MouseButtonPress
                and event.button() in (Qt.BackButton, Qt.ForwardButton)
                and self._grid_side_button(event.button())):
            return True
        return super().eventFilter(obj, event)

    def _grid_side_button(self, button):
        """サイドボタンを作譜ペインの分割へ渡す。渡せたら True。

        進む(前)で細かく、戻る(後ろ)で粗く — ↑ ↓ と同じ向き。
        オフセット合わせ中だけは触らない(そのとき分割は使わない)。
        """
        dock = getattr(self, "preview_dock", None)
        w = getattr(dock, "chart_edit", None) if dock is not None else None
        if w is None or w.offset_mode:
            return False
        w.change_grid(1 if button == Qt.ForwardButton else -1)
        return True

    def closeEvent(self, event):
        # _updating means the unsaved check already ran and the updater batch is
        # armed and waiting on this process to exit - vetoing here would hang it.
        if self._updating or self._unsaved_check():
            # 動画の書き出しは裏で走っている(RecordDialog はモーダルではない)
            # ので、気づかずに閉じようとすることがある。中止してよいか聞く。
            # 保存の確認より後に聞くのは、そちらで終了を取りやめたときに
            # 書き出しだけ道連れにしないため。
            if not self._updating and self._is_recording():
                if QMessageBox.question(
                        self, "動画を書き出す",
                        "動画の書き出しが進行中です。中止して終了しますか？\n"
                        "（書きかけの動画は残りません）") != QMessageBox.Yes:
                    event.ignore()
                    return
            # ダイアログが開いていれば、走っていてもいなくてもここで畳む。
            # abort_now() はワーカースレッドの終わりまで見届けるので、
            # 窓は消えたのにプロセスが残る、という状態にはならない。
            if self._record_dialog is not None:
                self._record_dialog.abort_now()
                self._record_dialog.close()
                self._record_dialog = None
            # 終了が確定したこの時点で音声デバイスを確定的に閉じる。以前は
            # MixerAudioEngine.close() を誰も呼んでおらず、PortAudio の
            # ストリームがプロセス終了任せになっていた。
            self._save_window_state()
            try:
                self.preview_dock.shutdown_audio()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            # 実行中かもしれない自前ワーカー(BPM/OFFSET検出・更新確認・更新DL・
            # AI譜面生成)を待機所へ退避する。これをしないと、走行中に閉じたとき
            # "QThread: Destroyed while thread is still running" でアプリごと
            # 落ちうる。detach_worker は終了済み/None を安全に無視する。
            from neotja.worker_util import detach_worker
            for attr in ("_bpm_detect_worker", "_update_check_worker",
                         "_update_download_worker", "_new_project_chart_gen_worker"):
                detach_worker(getattr(self, attr, None))
            # 解析スレッドは常駐なので必ずここで畳む。走行中のジョブは最長でも
            # 1 パスぶん(数百 ms)で終わるので、その完了を待ってから抜ける。
            # 待たずに破棄すると "QThread: Destroyed while thread is still
            # running" でプロセスごと落ちる。
            self._shutdown_analysis_thread()
            # 合作の入口(cloudflared)は子プロセスなので、置いて行くと
            # アプリを閉じたあとも外から入れる口が開いたままになる。
            # UI を外してある(2026-11-12)ので始まることは無いが、万一
            # 動いていたら畳む。
            if self._collab is not None or self._collab_tunnel is not None:
                try:
                    self.collab_stop()
                except Exception:  # noqa: BLE001
                    pass
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------------
    # Simulator launch
    # ------------------------------------------------------------------
    def launch_player(self):
        """NeoTJAPlayer(別プロセス)を、いま開いている譜面で起動する。

        **保存済みのファイルが要る。** Player は別プロセスなので、渡せるのは
        パスだけ。編集中(未保存)の内容は渡らない。黙って古い内容を再生すると
        「直したはずが直っていない」と見えるので、先に保存してもらう。

        コースと再生位置も渡す。鑑賞会や録画のために開くとき、また頭から
        探し直すのは無駄なので、いま見ているところがそのまま出る。
        """
        if not self.current_file or not os.path.exists(self.current_file):
            QMessageBox.information(
                self, "NeoTJAPlayer",
                "先に譜面を保存してください。\n\n"
                "NeoTJAPlayer は別のプログラムなので、渡せるのは保存された"
                "ファイルだけです。編集中の内容はそのままでは開けません。")
            return
        exe = self._find_player()
        if not exe:
            if not self._offer_player_download():
                return
            exe = self._find_player()
            if not exe:
                return
        args = list(exe) + [self.current_file]
        course = self._preview_course_override
        if course:
            args += ["--course", str(course)]
        try:
            at = self.preview_dock.audio.position() / 1000.0
        except Exception:  # noqa: BLE001
            at = 0.0
        if at > 0.5:
            args += ["--at", "%.3f" % at]
        try:
            subprocess.Popen(args, env=child_env(),
                             cwd=self._exe_dir(exe[0]) if exe else None)
        except OSError as exc:
            QMessageBox.warning(self, "NeoTJAPlayer",
                                "起動できませんでした:\n%s" % exc)

    def _offer_player_download(self, repair=False):
        """NeoTJAPlayer.exe を落とすか尋ねる。落とせたら True。

        repair=True は「もう有るが古い/壊れている」ときの入れ直し。


        **なぜここで落とすのか**: 更新で2本まとめて落とす仕組みを入れても、
        その処理を走らせるのは利用者の環境に既に入っている**古い版**の
        updater で、そちらは NeoTJAEditor.exe しか見ていない。つまり
        「Player を配り始める版」への更新そのものでは Player が届かず、
        届くのはその次の更新から。ここで拾えるようにしておかないと、
        いま使っている人の手元にはいつまでも Player が来ない。

        押した人だけが払う形なので、Player を使わない人に 84MB を
        負わせることもない。"""
        from neotja.updater import PlayerFetchWorker, RELEASES_PAGE_URL

        if not getattr(sys, "frozen", False):
            QMessageBox.information(
                self, "NeoTJAPlayer",
                "ソースから実行中なので、python -m neotja.player で開けます。")
            return False
        dest_dir = os.path.dirname(sys.executable)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("NeoTJAPlayer")
        box.setText(
            ("NeoTJAPlayer が NeoTJAEditor より古いままです。" + "\n"
             "入れ直しますか？(約 84MB)"
             if repair else
             "NeoTJAPlayer がまだ入っていません。" + "\n"
             "いまダウンロードしますか？(約 84MB)")
            + "\n\n置き場所:\n　%s" % dest_dir)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        if box.exec() != QMessageBox.Yes:
            return False

        progress = QProgressDialog("NeoTJAPlayer をダウンロード中...",
                                   "キャンセル", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        result = {"ok": False}
        worker = PlayerFetchWorker(dest_dir, self)

        def on_progress(pct):
            if pct >= 0:
                progress.setRange(0, 100)
                progress.setValue(pct)
            else:
                progress.setRange(0, 0)

        def on_ok(_path):
            result["ok"] = True
            # 入れ直せたので「古いまま」の印を消す。
            if self.config_data.pop("player_update_pending", None) is not None:
                settings_mod.save_settings(self.config_data)
            progress.close()

        def on_failed(msg):
            progress.close()
            box2 = QMessageBox(self)
            box2.setIcon(QMessageBox.Warning)
            box2.setWindowTitle("NeoTJAPlayer")
            box2.setText(
                "ダウンロードできませんでした:\n%s\n\n"
                "「実行 → NeoTJAPlayer を入れ直す」でやり直せます。\n"
                "リリースページから NeoTJAPlayer.exe を手に入れて、"
                "NeoTJAEditor.exe と同じ場所へ置くこともできます。" % msg)
            box2.addButton("リリースページを開く", QMessageBox.ActionRole)
            box2.addButton(QMessageBox.Close)
            box2.exec()
            if box2.clickedButton() and box2.clickedButton().text().startswith("リリース"):
                import webbrowser
                webbrowser.open(RELEASES_PAGE_URL)

        worker.progress.connect(on_progress)
        worker.finished_ok.connect(on_ok)
        worker.failed.connect(on_failed)
        worker.cancelled.connect(progress.close)
        progress.canceled.connect(worker.cancel)
        self._player_fetch_worker = worker
        worker.start()
        progress.exec()
        worker.wait(5000)
        return result["ok"]

    @staticmethod
    def _find_player():
        """Player の起動コマンドを組み立てる。見つからなければ空。

        配布物では exe の隣に NeoTJAPlayer.exe が居る。開発中(ソースから
        動かしているとき)は同じ Python で -m neotja.player を叩く。"""
        if getattr(sys, "frozen", False):
            here = os.path.dirname(sys.executable)
            exe = os.path.join(here, "NeoTJAPlayer.exe")
            return [exe] if os.path.exists(exe) else []
        return [sys.executable, "-m", "neotja.player"]

    @staticmethod
    def _exe_dir(path):
        """その exe のフォルダ。渡せないときは None。

        外のプログラムは**カレントが自分のフォルダ**である前提で、必要な
        ものを相対パスで探すことがある。TNDE は avcodec-59.dll などを
        `dll` サブフォルダに置いていて、そこを相対で見に行くので、カレントが
        別の場所のままだと「avcodec-59.dll が存在しません」で止まっていた
        (利用者の報告 2026-11-06「Neo から TNDE を開こうとする時だけ」)。
        ダブルクリックしたときと同じ条件にする。"""
        if not path:
            return None
        try:
            d = os.path.dirname(os.path.abspath(path))
        except (TypeError, ValueError):
            return None
        return d if d and os.path.isdir(d) else None

    def run_simulator(self, key):
        path = self.config_data["run_config"][key]["path"]
        if path and os.path.exists(path):
            subprocess.Popen([path], env=child_env(),
                             cwd=self._exe_dir(path))
        else:
            QMessageBox.warning(
                self, "警告",
                f"{key} のパスが未設定です。\nメニュー「設定 → 環境設定...」で設定してください。",
            )

    # ------------------------------------------------------------------
    # Text tools
    # ------------------------------------------------------------------
    def _get_selection(self):
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            QMessageBox.warning(self, "警告", "範囲を選択してください。")
            return None, None
        text = cursor.selectedText().replace(" ", "\n")
        return cursor, text

    # 譜面本文の音符文字だけを反転する変換表(ドン<->カ、大ドン<->大カ)。
    _REVERSE_NOTE_MAP = str.maketrans("1234", "2143")

    def reverse_don_ka(self):
        """あべこべ反転: 選択範囲の**譜面行のみ**ドン/カを入れ替える。

        以前は選択範囲全体に translate をかけていたため、`#BPMCHANGE 180` の
        `180` が `280` に、`#SCROLL 1.5` が `2.5` に化けるなど、命令の数値まで
        書き換わって譜面が壊れていた。命令行(#で始まる)とヘッダ行(TITLE: 等)は
        対象外にする。"""
        cursor, txt = self._get_selection()
        if txt is None:
            return
        out = []
        for line in txt.split("\n"):
            s = line.lstrip()
            body = line.split("//")[0]
            if s.startswith("#") or (":" in body and not s[:1].isdigit()):
                out.append(line)          # 命令行/ヘッダ行はそのまま
            else:
                out.append(line.translate(self._REVERSE_NOTE_MAP))
        start = cursor.selectionStart()
        cursor.insertText("\n".join(out))
        # プログラム編集なので dirty と自動保存の対象を明示的に立てる。
        self.editor.mark_edited(start, cursor.position())
        self.setWindowModified(True)
        self._force_update()

    # ------------------------------------------------------------------
    # 小節編集(複製/削除/空小節挿入/前後入れ替え)
    # ------------------------------------------------------------------
    _MEASURE_OPS = {
        "dup": measure_edit.op_duplicate,
        "del": measure_edit.op_delete,
        "ins": measure_edit.op_insert_after,
    }

    def _undo_here(self, redo=False):
        """取り消し / やり直し。

        入力欄(命令の値を入れる小さな欄や、検索の欄)に居るときは**その欄**の
        ものを先に効かせる。窓ぜんたいのショートカットにしたぶん、そうしないと
        欄の打ち間違いを戻したいのに本文が巻き戻ってしまう。"""
        fw = QApplication.focusWidget()
        if isinstance(fw, QLineEdit) and fw.isEnabled() and not fw.isReadOnly():
            (fw.redo if redo else fw.undo)()
            return
        (self.editor.redo if redo else self.editor.undo)()

    def _measure_op(self, op, arg=None, pos=None):
        """小節まるごとの操作(複製/削除/空を1つ)。

        pos を渡すと**そこの小節**に効く。合作でスマホから届いた「3小節目を
        消して」は、こちらのカーソルとは関係ない所を指すので明に渡す。"""
        text = self.editor.toPlainText()
        if pos is None:
            pos = self.editor.textCursor().position()
        if op == "move":
            result = measure_edit.op_move(text, pos, arg)
        else:
            result = self._MEASURE_OPS[op](text, pos)
        if result is None:
            self.statusBar().showMessage("小節編集: カーソルを譜面本文(#START〜#END内)の小節に置いてください", 4000)
            return
        start, end, replacement, cursor = result
        tc = self.editor.textCursor()
        tc.beginEditBlock()
        tc.setPosition(start)
        tc.setPosition(end, QTextCursor.KeepAnchor)
        tc.insertText(replacement)
        tc.endEditBlock()
        # カーソルを移動先へ。プログラム編集なので dirty を明示的に立てる。
        nc = self.editor.textCursor()
        nc.setPosition(max(0, min(cursor, len(self.editor.toPlainText()))))
        self.editor.setTextCursor(nc)
        self.editor.modified_lines.add(nc.blockNumber() + 1)
        self.setWindowModified(True)
        self._force_update()

    def _measure_text_pos(self, m):
        """作譜ペインの小節番号 → 本文のその小節の先頭。無ければ None。

        _select_pane_measures と同じ道で小節の切れ目を出す(表示している
        コース・系統の中での 0 始まりの番号)。"""
        text = self.editor.toPlainText()
        course_key = (self._preview_course_override
                      or self._course_key_at_cursor(text))
        rng = (self.analyzer.course_line_range(text, course_key)
               if course_key else None)
        if rng is None:
            return None
        with note_edit.editing_branch(self._preview_branch_level,
                                      self._preview_branch_path):
            body = note_edit.course_body_span(text, rng)
            spans = note_edit.measure_spans(text, body)
        if not spans or not (0 <= int(m) < len(spans)):
            return None
        return spans[int(m)][0]

    def delete_pane_measures(self, measures):
        """作譜ペインの上の目盛りで選んだ小節を、本文からまるごと消す。

        消すのは既存の「小節を削除」(Ctrl+Shift+D)とまったく同じ道具。
        **後ろから消す** — 前を消すと後ろの小節番号がずれる。"""
        ms = sorted({int(m) for m in (measures or [])}, reverse=True)
        if not ms:
            return
        done = 0
        for m in ms:
            pos = self._measure_text_pos(m)
            if pos is None:
                continue
            before = self.editor.toPlainText()
            self._measure_op("del", pos=pos)
            if self.editor.toPlainText() != before:
                done += 1
        ce = getattr(self.preview_dock, "chart_edit", None)
        if ce is not None and hasattr(ce, "set_selected_measures"):
            ce.set_selected_measures([])
        if done:
            self.statusBar().showMessage(
                "%d 小節を削除しました" % done, 3000)
        else:
            self.statusBar().showMessage("小節を削除できませんでした", 4000)

    def _apply_note_edit(self, m_index, slot, grid, char):
        """作譜モードで音符が置かれた。テキストへ書き戻す。

        _measure_op と同じく QTextCursor で1回置換するので Undo も1操作。
        ただし **_force_update() は呼ばない** — ここを同期にすると1音置く
        たびに全文再解析(大譜面で 600ms 前後)が走って打ち込みにならない。
        呼ばなければ textChanged の 600ms デバウンスに乗り、手が止まってから
        まとめて解析される。それまでの見た目は編集ペインが暫定表示で埋める。"""
        text = self.editor.toPlainText()
        course_key = (self._preview_course_override
                      or self._course_key_at_cursor(text))
        rng = self.analyzer.course_line_range(text, course_key) if course_key else None
        body = note_edit.course_body_span(text, rng)
        if body is None:
            self.statusBar().showMessage("作譜: 編集対象のコースが見つかりません", 4000)
            return
        result = note_edit.set_slot(text, body, m_index, slot, grid, char)
        if result is None:
            return          # 変化なし / 置けない位置
        start, end, replacement, cursor = result
        tc = self.editor.textCursor()
        tc.beginEditBlock()
        tc.setPosition(start)
        tc.setPosition(end, QTextCursor.KeepAnchor)
        tc.insertText(replacement)
        tc.endEditBlock()
        # プログラム編集なので dirty と自動保存の対象を明示的に立てる。
        # エディタのカーソルは動かさない — 打ち込み中に本文が飛ぶと使いづらい。
        block = self.editor.document().findBlock(min(start, len(text)))
        self.editor.modified_lines.add(block.blockNumber() + 1)
        self.setWindowModified(True)
        # 置いた音の打音を鳴らす(エディタで数字を打ったときと同じ音)。
        if self.config_data.get("note_input_sound", True):
            kind = self._NOTE_INPUT_SOUND_KIND.get(char)
            hit_sounds = getattr(self.preview_dock, "hit_sounds", None)
            if kind is not None and hit_sounds is not None:
                hit_sounds.play_once(kind)

    def _apply_chart_op(self, op, course_key=None):
        """作譜モードの PeepoDrumKit 式の操作(置く/消す/連打/反転)。

        course_key を渡すと**そのコースへ**書く。合作でスマホから届いた
        操作は送り主が見ているコースへ入れたいので明に指す(こちらの表示が
        別のコースに移っていても、相手の打ち込み先は動かない)。

        計算は note_edit.run_op(テキストだけを見る純ロジック)。ここでは
        結果のテキストを **変わった範囲だけ** 1回で置き換える。風船を足すと
        本文と BALLOON: 行の2か所が変わるが、1回の置換にまとめるので Undo も
        1操作で戻る。

        戻り値は run_op の結果 dict(画面の暫定表示に使う)か None。
        長い音符・範囲の操作は見た目を先回りで作れないので、その場で解析し
        直す(1音ずつの打ち込みではないので、同期の解析でも引っかからない)。"""
        # 合作でペンを持っていない間は書かせない。手元だけ変わって誰にも
        # 配られないと、そこから本文が分岐する(文字の編集は setReadOnly で
        # 止まるが、作譜ペインの打ち込みはこちらを通るので別に止める)。
        cs = self._collab
        # 相手に代わって書いているあいだ(_collab_relaying)は通す。スマホから
        # 届いた打ち込みを書いているので、持ち主はこちらではない。
        if (cs is not None and cs.is_on() and not cs.holds_pen()
                and not self._collab_relaying):
            self.statusBar().showMessage(
                "合作: いまはプレビューモードです"
                "(編集するにはメニューの「編集モードに切り替え」)", 4000)
            return None
        text = self.editor.toPlainText()
        course_key = (course_key
                      or self._preview_course_override
                      or self._course_key_at_cursor(text))
        rng = self.analyzer.course_line_range(text, course_key) if course_key else None
        if rng is None:
            self.statusBar().showMessage("作譜: 編集対象のコースが見つかりません", 4000)
            return None
        # 分岐のある譜面では、編集も**いま見ている系統**に効かせる。作譜ペインの
        # 「何小節目」は選んだ系統の中での番号なので(tja_analyzer が選んだ系統
        # だけを数える)、書く側も同じ数え方にしないと別の小節へ書いてしまう。
        with note_edit.editing_branch(self._preview_branch_level,
                                      self._preview_branch_path):
            return self._chart_op_in_branch(text, rng, op)

    #: 音符を置く操作(置けなかった理由を出す相手)。
    _PLACING_OPS = ("key", "long", "fill")

    def _warn_if_too_fine(self, text, rng, op):
        """置けなかったのが「細かすぎる」せいなら、黙らずに理由を出す。

        以前は何も起きず、キーが効かないように見えていた(利用者の報告
        2026-11-07「32分だけ入力が受け付けず、配置することができません」)。
        """
        if op.get("kind") not in self._PLACING_OPS:
            return
        a = op.get("a")
        if a is None:
            return
        try:
            need = note_edit.division_needed(
                text, rng, int(a[0]), int(op.get("grid", 16)))
        except Exception:  # noqa: BLE001
            return
        if need is None or need <= note_edit.MAX_DIVISION:
            return
        self.statusBar().showMessage(
            "この小節に 1/%d は置けません："
            "すでに入っている音符と合わせると1小節を %d 分割する必要があり、"
            "上限の %d 分割を超えます（同じ小節に細かさの違う音符を"
            "混ぜすぎています）"
            % (int(op.get("grid", 16)), need, note_edit.MAX_DIVISION), 6000)

    def _chart_op_in_branch(self, text, rng, op):
        """_apply_chart_op の中身(系統を決めたあと)。"""
        from .note_timing import TimingError
        if op.get('kind') == 'peek_move_items':
            try:
                anchor = op.get('anchor')
                plan = note_edit.plan_move_items(
                    text, rng, op.get('items') or [], int(op.get('steps', 0)),
                    note_edit._Grids(int(op.get('grid', 16)), op.get('grids')),
                    note_edit.Fraction(*anchor) if anchor is not None else None)
                return {'positions': plan['positions']} if plan else {}
            except (TimingError, ValueError, ZeroDivisionError):
                return {}
        if op.get("kind") == "peek_command":
            # 入力欄に出す今の値を読むだけ。テキストは変えない。
            a = op.get("a") or (0, 0)
            return {"value": note_edit.command_at(
                text, rng, int(a[0]), int(a[1]), int(op.get("grid", 16)),
                str(op.get("name", "")).upper())}
        if op.get("kind") == "peek_marker":
            # カーソル位置まわりのゴーゴー / 小節線の状態を読むだけ。
            a = op.get("a") or (0, 0)
            return note_edit.marker_info(
                text, rng, int(a[0]), int(a[1]), int(op.get("grid", 16)),
                str(op.get("region", "")).upper())
        if op.get("kind") == "peek_copy":
            # 選んだもの(または範囲)の中身を控えるだけ。テキストは変えない。
            a = op.get("a")
            b = op.get("b")
            return {"clip": note_edit.copy_items(
                text, rng, op.get("items"),
                tuple(a) if a is not None else None,
                tuple(b) if b is not None else None,
                int(op.get("grid", 16)), op.get("grids"))}
        if op.get("kind") == "peek_region":
            # 範囲が全部ゴーゴー中 / 小節線を隠しているかを読むだけ。
            if op.get("a") is None or op.get("b") is None:
                return {"value": None}
            return {"value": note_edit.region_state(
                text, rng, tuple(op["a"]), tuple(op["b"]), int(op.get("grid", 16)),
                str(op.get("region", "")).upper())}
        try:
            res = note_edit.run_op(text, rng, op)
        except TimingError as exc:
            self.statusBar().showMessage(str(exc), 8000)
            return None
        if res is None:
            self._warn_if_too_fine(text, rng, op)
            return None
        new = res["text"]
        # 末尾には空の小節を常に1つ置いておく(利用者の指定 2026-10-04)。
        # 打つ場所が見えていないと、譜面の続きを書き始められない。
        if self.config_data.get("chart_edit_tail_measure", True):
            rng2 = (rng[0], rng[1] + new.count(chr(10)) - text.count(chr(10)))
            tail = note_edit.ensure_trailing_measure(new, rng2)
            if tail is not None:
                new = tail
                res = dict(res)
                res["text"] = new
                res["reparse"] = True
        # 先頭と末尾の一致を削って、変わった範囲だけを置き換える。
        p = 0
        limit = min(len(text), len(new))
        while p < limit and text[p] == new[p]:
            p += 1
        s = 0
        while (s < limit - p and text[len(text) - 1 - s] == new[len(new) - 1 - s]):
            s += 1
        tc = self.editor.textCursor()
        tc.beginEditBlock()
        tc.setPosition(p)
        tc.setPosition(len(text) - s, QTextCursor.KeepAnchor)
        tc.insertText(new[p:len(new) - s])
        tc.endEditBlock()
        block = self.editor.document().findBlock(min(p, len(new)))
        self.editor.modified_lines.add(block.blockNumber() + 1)
        self.setWindowModified(True)
        if self.config_data.get("note_input_sound", True) and res.get("sound"):
            kind = self._NOTE_INPUT_SOUND_KIND.get(res["sound"])
            hit_sounds = getattr(self.preview_dock, "hit_sounds", None)
            if kind is not None and hit_sounds is not None:
                hit_sounds.play_once(kind)
        if res.get("reparse"):
            self._force_update()
        return res

    # ------------------------------------------------------------------
    # 作譜ペインの右クリックから、エディタの道具を呼ぶ
    # ------------------------------------------------------------------
    #: 鍵 → 実際に呼ぶもの。道具そのものは今までどおりエディタの選択を見る。
    _CHART_TOOL_FUNCS = ("highspeed", "resize", "reverse", "strobe")

    def run_chart_tool(self, name):
        """作譜ペインの範囲(無ければカーソルの小節)をエディタの選択へ移してから、
        ツールバーと同じ道具を呼ぶ。

        道具の中身は1行も変えていない — ハイスピ変換もリサイズも反転も、
        もともと「エディタで選んだ範囲」に働くので、選び直すだけで作譜モード
        からも使える。ストロボ生成はカーソルの位置に作るので、選ばずに
        カーソルだけ動かす。"""
        funcs = {"highspeed": self.open_scroll_splitter,
                 "resize": self.open_measure_converter,
                 "reverse": self.reverse_don_ka,
                 "strobe": self.open_strobe_tool}
        fn = funcs.get(name)
        if fn is None:
            return
        if name == "reverse":
            # **帯/選んだ音符にそのまま効かせる**(W キーと同じ道)。本文側で
            # 「小節まるごと」を選び直していたので、囲んだ所だけでなくその
            # 小節が全部反転していた(利用者の報告 2026-11-07
            # 「範囲指定して反転すると、小節全部反転する」)。
            ce = getattr(self.preview_dock, "chart_edit", None)
            fn2 = getattr(ce, "apply_transform", None)
            if fn2 is not None:
                fn2("flip")
                return
        got = self._select_pane_measures(whole=(name != "strobe"))
        if got is None:
            return
        if name == "highspeed":
            # ハイスピ変換だけは「選んだ音符だけ」にかける(利用者の指定
            # 2026-10-01)。小節まるごとの本文を渡したうえで、その中のどこから
            # どこまでが対象かを割合で添える。
            # **HS の札を選んでいるときは札のほうが相手**(利用者の報告
            # 2026-11-04)。音符ではなく、その札の値だけを刻み直す。
            ce = getattr(self.preview_dock, "chart_edit", None)
            getter = getattr(ce, "selection_scroll_positions", None)
            on_scrolls = bool(getter()) if getter is not None else False
            fn(span=self._pane_span(got), on_scrolls=on_scrolls)
        else:
            fn()

    #: 位置を比べるときの余裕。画面の位置は 1/192 に丸めてあるのに、本文側は
    #: 「小節の何個目 / 全部で何個」なので、小節を 10 等分したような所では
    #: 両者が 1/192 未満だけ食い違う。端の音符が落ちないよう、その幅ぶん
    #: 外へ広げて渡す(1/384 = 1/192 の半分。隣の目盛は拾わない)。
    _SPAN_TOL = 1.0 / 384.0

    def _pane_span(self, measures):
        """作譜ペインの範囲 → ハイスピ変換へ渡す ((小節, 割合), (小節, 割合))。

        小節の番号は**渡した本文の中での** 0 始まり。割合にしているのは、
        画面のグリッドと小節の分割数が違っても同じ所を指せるようにするため。

        音符を選んでいればそれが相手。クリックで選んだときは時間の帯が
        張られないので、帯だけを見ていると「選んだのに効かない」
        (利用者の報告 2026-10-08)。"""
        ce = getattr(self.preview_dock, "chart_edit", None)
        if ce is None:
            return None
        m0 = measures[0]
        # HS の札を選んでいるなら、**その札の端から端まで**が範囲。音符しか
        # 見ていなかったので、札を選んでも下の帯(右ドラッグで張った時間の帯)に
        # 落ちていた(利用者の報告 2026-11-04)。
        getter = getattr(ce, "selection_scroll_positions", None)
        sp = getter() if getter is not None else []
        sel = (sp[0], sp[-1]) if sp else None
        if sel is None:
            sel = (ce.selection_pos_span()
                   if hasattr(ce, "selection_pos_span") else None)
        if sel is not None:
            a, b = sel
            am, bm = int(a), int(b)
            return ((am - m0, float(a - am) - self._SPAN_TOL),
                    (bm - m0, float(b - bm) + self._SPAN_TOL))
        addrs = ce._range_addresses()
        if addrs is None:
            return None
        (a_m, a_s), (b_m, b_s) = addrs
        if (b_m, b_s) < (a_m, a_s):
            (a_m, a_s), (b_m, b_s) = (b_m, b_s), (a_m, a_s)
        ga = max(1, ce._measure_grid(a_m))
        gb = max(1, ce._measure_grid(b_m))
        return ((a_m - m0, a_s / ga), (b_m - m0, b_s / gb))

    def _select_pane_measures(self, whole=True):
        """作譜ペインの範囲 → エディタの選択。選んだ小節の (最初, 最後) を返す。
        できなければ None。

        選ぶのは**小節まるごと**。ハイスピ変換もリサイズも「小節の切れ目で
        区切られた本文」を受け取る作りなので、小節の途中で切ると読めない。"""
        ce = getattr(self.preview_dock, "chart_edit", None)
        if ce is None:
            return None
        text = self.editor.toPlainText()
        course_key = (self._preview_course_override
                      or self._course_key_at_cursor(text))
        rng = self.analyzer.course_line_range(text, course_key) if course_key else None
        if rng is None:
            self.statusBar().showMessage("作譜: 編集対象のコースが見つかりません", 4000)
            return None
        # 相手は「選んだ HS の札 → 選んだ音符 → 時間の帯 → カーソルの小節」の
        # 順。音符をクリックで選んだときは帯が張られないので、帯だけを見て
        # いるとカーソルの1小節しか渡らなかった(利用者の報告 2026-10-08)。
        # 札も同じで、選んでも音符として数えられず1小節しか渡らなかった
        # (利用者の報告 2026-11-04)。
        getter = getattr(ce, "selection_scroll_positions", None)
        sp = getter() if getter is not None else []
        sel = (sp[0], sp[-1]) if sp else None
        if sel is None:
            sel = (ce.selection_pos_span()
                   if hasattr(ce, "selection_pos_span") else None)
        addrs = ce._range_addresses()
        if sel is not None:
            m0, m1 = int(sel[0]), int(sel[1])
        elif addrs is not None:
            m0, m1 = int(addrs[0][0]), int(addrs[1][0])
        else:
            m0 = m1 = int(ce._cursor_addr()[0])
        if m1 < m0:
            m0, m1 = m1, m0
        with note_edit.editing_branch(self._preview_branch_level,
                                      self._preview_branch_path):
            body = note_edit.course_body_span(text, rng)
            spans = note_edit.measure_spans(text, body)
        if not spans:
            self.statusBar().showMessage("作譜: 小節が見つかりません", 4000)
            return None
        m0 = max(0, min(m0, len(spans) - 1))
        m1 = max(0, min(m1, len(spans) - 1))
        start = spans[m0][0]
        tc = self.editor.textCursor()
        if whole:
            end = spans[m1][1]
            # 末尾の改行は選ばない。道具のほうが書き戻すときに改行を足すので、
            # 一緒に選ぶと空行が1つずつ増えていく。
            while end > start and text[end - 1] in ("\n", "\r"):
                end -= 1
            tc.setPosition(start)
            tc.setPosition(end, QTextCursor.KeepAnchor)
        else:
            tc.setPosition(start)
        self.editor.setTextCursor(tc)
        self.editor.ensureCursorVisible()
        return (m0, m1)

    def _collab_course_key(self):
        """合作で相手へ伝える「いま見ているコース」。

        作譜で書く先を決めるときと**同じ規則**(表示の指定が優先、無ければ
        カーソル行の属するコース)。スマホの画面は譜面を1コースだけ映すので、
        これに合わせてもらう(利用者の指定 2026-10-22「ホストに合わせる」)。"""
        try:
            return (self._preview_course_override
                    or self._course_key_at_cursor(self.editor.toPlainText())
                    or "")
        except Exception:  # noqa: BLE001
            return ""

    def _collab_branch_now(self):
        """合作で相手へ伝える「いま流している分岐の系統」。

        作譜で書く先を決めるとき(_apply_chart_op の editing_branch)と
        **同じものを渡す**。これが揃っていないと、分岐のある譜面で
        小節の数え方が食い違う(実測 2026-10-23: Neo 3小節 / スマホ
        5小節)。"""
        try:
            return (self._preview_branch_level,
                    list(self._preview_branch_path or []))
        except Exception:  # noqa: BLE001
            return None

    def _course_key_at_cursor(self, text):
        """カーソル行が属するコース。プレビューが見ているコースと同じ規則。"""
        hit = self._cursor_index.get(self.editor.textCursor().blockNumber() + 1)
        if hit is not None and self.courses_info:
            idx = hit[2]
            if idx is not None and 0 <= idx < len(self.courses_info):
                return self.courses_info[idx].get("key")
        return self.courses_info[0].get("key") if self.courses_info else None

    # ------------------------------------------------------------------
    # BPM/OFFSET auto-detect (experimental)
    # ------------------------------------------------------------------
    def auto_detect_bpm_offset(self):
        if not self.current_file:
            QMessageBox.warning(self, "警告", "先にファイルを保存し、WAVE:に音源ファイルを指定してください。")
            return
        headers = parse_preview_headers(self.editor.toPlainText())
        wave = headers["wave"]
        if not wave:
            QMessageBox.warning(self, "警告", "WAVE:に音源ファイルが指定されていません。")
            return
        wave_path = os.path.join(os.path.dirname(self.current_file), wave)
        if not os.path.exists(wave_path):
            QMessageBox.warning(self, "警告", f"音源ファイルが見つかりません: {wave}")
            return
        self.status_label.setText("BPM/OFFSETを自動検出中(実験的)...")
        self._bpm_detect_worker = BpmOffsetDetectWorker(wave_path, self)
        self._bpm_detect_worker.detected.connect(self._on_auto_detect_bpm_offset_ok)
        self._bpm_detect_worker.failed.connect(self._on_auto_detect_bpm_offset_failed)
        self._bpm_detect_worker.start()

    def _on_auto_detect_bpm_offset_ok(self, bpm, offset):
        self.replace_header_line("BPM", f"{bpm:g}")
        self.replace_header_line("OFFSET", f"{offset:.3f}")
        self.status_label.setText(f"BPM/OFFSETを自動検出しました(実験的): BPM {bpm:g} / OFFSET {offset:.3f}")
        self._force_update()

    def _on_auto_detect_bpm_offset_failed(self, msg):
        self.status_label.setText(f"BPM/OFFSET自動検出に失敗しました(実験的): {msg}")

    # ------------------------------------------------------------------
    # AI譜面生成 (experimental) - always writes a separate <basename>(AI).tja
    # rather than touching the currently open file/editor, so a rough or
    # outright bad generation result can never clobber real work.
    # ------------------------------------------------------------------
    def open_auto_chart_generator(self):
        if not self.current_file:
            QMessageBox.warning(self, "警告", "先にファイルを保存し、WAVE:に音源ファイルを指定してください。")
            return
        content = self.editor.toPlainText()
        headers = parse_preview_headers(content)
        wave = headers["wave"]
        if not wave:
            QMessageBox.warning(self, "警告", "WAVE:に音源ファイルが指定されていません。")
            return
        wave_path = os.path.join(os.path.dirname(self.current_file), wave)
        if not os.path.exists(wave_path):
            QMessageBox.warning(self, "警告", f"音源ファイルが見つかりません: {wave}")
            return

        from neotja.dialogs.auto_chart_dialog import AutoChartDialog

        cursor_line = self.editor.textCursor().blockNumber() + 1

        def apply(course_key, generated_body):
            self._save_ai_chart_variant(content, course_key, generated_body)

        AutoChartDialog(self, content, wave_path, cursor_line, apply).exec()

    def _write_ai_variant_file(self, content: str, course_key: str, generated_body: str, base_path: str):
        """Builds and writes the `<basename>(AI).tja` variant - no dialogs,
        no interaction with the live editor. Returns the path written, or
        None if `course_key` doesn't exist in `content`. Shared by both the
        interactive toolbar dialog (_save_ai_chart_variant, below) and the
        silent post-creation step in _apply_youtube_project - the latter
        runs unattended in the background, so it must never pop a modal
        dialog to report its result."""
        course_range = self.analyzer.course_line_range(content, course_key)
        if course_range is None:
            return None
        new_content = build_ai_variant_content(content, course_range, generated_body)
        base, ext = os.path.splitext(base_path)
        new_path = f"{base}(AI){ext}"
        # errors="replace" だと cp932 に無い文字(元ファイルのTITLE等に含まれる
        # 絵文字など)が無言で「?」に化けるので strict のまま書き、
        # 呼び出し側に UnicodeEncodeError として伝える。
        self._write_text_atomic(new_path, new_content, "cp932")
        return new_path

    def _save_ai_chart_variant(self, content: str, course_key: str, generated_body: str, base_path: str = None):
        """Interactive save used by the toolbar's AI譜面生成 dialog - confirms
        overwrite and reports success/failure via QMessageBox."""
        base_path = base_path or self.current_file
        base, ext = os.path.splitext(base_path)
        prospective_path = f"{base}(AI){ext}"
        if os.path.exists(prospective_path):
            ans = QMessageBox.question(
                self, "確認", f"既にファイルが存在します。上書きしますか？\n{prospective_path}",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        try:
            new_path = self._write_ai_variant_file(content, course_key, generated_body, base_path)
        except UnicodeEncodeError:
            QMessageBox.critical(
                self, "保存エラー",
                "ANSI (cp932) で表せない文字が含まれているため保存できませんでした。\n"
                "TITLE などに絵文字や特殊な文字が入っていないか確認してください。")
            return
        except OSError as e:
            QMessageBox.critical(self, "保存エラー", str(e))
            return
        if new_path is None:
            QMessageBox.critical(self, "エラー", "対象コースが見つかりませんでした。")
            return
        QMessageBox.information(self, "AI譜面生成(実験的)", f"生成しました:\n{new_path}\n\n現在開いているファイルは変更されていません。")

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def toggle_theme(self):
        current = self.config_data.get("theme", "dark")
        self.config_data["theme"] = "light" if current == "dark" else "dark"
        settings_mod.save_settings(self.config_data)
        apply_theme(self.app, self.config_data["theme"])
        self.highlighter.rebuild_formats()
        self.highlighter.rehighlight()
        self.editor.gutter.update()
        self.ruler.refresh_theme()
        self.preview_dock.refresh_theme()
        self._restyle_bottom_controls()
        self.start_screen.refresh_theme()

    def _show_about(self):
        QMessageBox.information(self, "バージョン情報", f"{APP_NAME}\nVersion: {VERSION}\n\nRedesigned & Optimized Edition (PySide6)")

    # ------------------------------------------------------------------
    # Auto-update
    # ------------------------------------------------------------------
    def _report_failed_update(self):
        """Tell the user when the previous run's update didn't actually get
        applied. Without this the app just relaunched on the old version with
        no explanation, which read as "the updater does nothing"."""
        from neotja.updater import pop_update_error

        info = pop_update_error()
        if not info:
            return
        update_exe = ""
        for line in info.splitlines():
            if line.startswith("update_exe=") or line.startswith("update_player="):
                update_exe = line.split("=", 1)[1].strip()

        # Player だけ置けなかった場合。Editor は入れ替わっているので、
        # 「更新に失敗しました」と言ってしまうと嘘になる。
        if info.startswith("player_copy_failed"):
            # コピーできなかった = Player は前のまま。次の起動で尋ねる。
            self.config_data["player_update_pending"] = True
            settings_mod.save_settings(self.config_data)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("NeoTJAPlayer を置けませんでした")
            box.setText(
                f"NeoTJAEditor は v{VERSION} に更新できましたが、"
                "NeoTJAPlayer.exe を置き換えられませんでした。\n\n"
                "更新のときに NeoTJAPlayer を開いたままにしていると、"
                "ファイルを掴まれていて上書きできません。\n\n"
                "ダウンロード済みのファイルは残してあるので、"
                "NeoTJAPlayer を閉じてから手動で上書きするか、"
                "「ヘルプ → 更新の確認」からやり直してください。")
            box.setDetailedText(info)
            if update_exe and os.path.exists(update_exe):
                box.addButton("ファイルの場所を開く", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Close)
            box.exec()
            clicked = box.clickedButton()
            if clicked and clicked.text().startswith("ファイル"):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(update_exe)])
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("更新に失敗しました")
        box.setText(
            f"更新ファイルの適用に失敗したため、v{VERSION} のままです。\n\n"
            "NeoTJAEditor.exe を上書きできませんでした。\n"
            "以下が原因として考えられます:\n"
            "・ウイルス対策ソフトが更新ファイルをブロックしている\n"
            "・exeが Program Files など書き込み権限のない場所にある\n\n"
            "ダウンロード済みの更新ファイルは残してあるので、"
            "手動で上書きすれば更新できます。"
        )
        box.setDetailedText(info)
        if update_exe and os.path.exists(update_exe):
            box.addButton("更新ファイルの場所を開く", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        clicked = box.clickedButton()
        if clicked and clicked.text().startswith("更新ファイル"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(update_exe)])

    def check_player_pending(self):
        """前回 Player だけ入れ替えられなかったなら、いま入れ直すか尋ねる。

        **これが無いと復旧できない。** NeoTJAEditor のほうは新しくなって
        いるので「ヘルプ → 更新の確認」は「最新です」と答えるだけで、
        Player を取りに行かない。印を見てここから拾う。"""
        if not self.config_data.get("player_update_pending"):
            return
        if not getattr(sys, "frozen", False):
            self.config_data.pop("player_update_pending", None)
            return
        self._offer_player_download(repair=True)

    def open_version_change(self):
        from neotja.dialogs.version_change_dialog import VersionChangeDialog
        from neotja.updater import ASSET_NAME, PLAYER_ASSET_NAME
        dialog = VersionChangeDialog(self)
        if not dialog.exec() or not dialog.selected_release:
            return
        if not getattr(sys, 'frozen', False):
            QMessageBox.information(self, 'バージョン変更',
                                    'バージョン変更はexe版で利用できます。')
            return
        if self._is_recording():
            QMessageBox.information(self, 'バージョン変更', '録画を終了してから変更してください。')
            return
        release = dialog.selected_release
        urls = {a.get('name'): a.get('browser_download_url', '')
                for a in release.get('assets', [])}
        if not urls.get(ASSET_NAME) or not urls.get(PLAYER_ASSET_NAME):
            return
        self._run_update_download(urls[ASSET_NAME], urls[PLAYER_ASSET_NAME],
                                  version_change=True)

    def check_for_updates(self, manual=False):
        from neotja.updater import UpdateCheckWorker

        worker = UpdateCheckWorker(self)
        worker.update_available.connect(
            lambda tag, notes, url, purl: self._prompt_update(tag, notes, url, purl))
        if manual:
            worker.up_to_date.connect(lambda: QMessageBox.information(self, "更新の確認", f"現在のバージョン v{VERSION} は最新です。"))
            worker.failed.connect(lambda msg: QMessageBox.warning(self, "更新エラー", f"更新の確認に失敗しました:\n{msg}"))
        self._update_check_worker = worker
        worker.start()

    def _prompt_update(self, tag, notes, asset_url, player_url=""):
        box = QMessageBox(self)
        box.setWindowTitle("新しいバージョンがあります")
        box.setText(f"新しいバージョン {tag} が利用可能です。(現在: v{VERSION})\n\n更新しますか？")
        if notes:
            box.setDetailedText(notes)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return

        if not getattr(sys, "frozen", False):
            import webbrowser
            from neotja.updater import RELEASES_PAGE_URL
            QMessageBox.information(self, "更新", "ソースから実行中のため自動更新はできません。リリースページを開きます。")
            webbrowser.open(RELEASES_PAGE_URL)
            return

        if not asset_url:
            QMessageBox.warning(self, "更新エラー", "ダウンロード用のファイルが見つかりませんでした。")
            return
        self._run_update_download(asset_url, player_url)

    def _run_update_download(self, asset_url, player_url="", version_change=False):
        from neotja.updater import UpdateDownloadWorker, apply_update

        progress = QProgressDialog("更新をダウンロード中...", "キャンセル", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        worker = UpdateDownloadWorker(asset_url, player_url, self,
                                      require_player=version_change)

        def on_progress(pct):
            if pct >= 0:
                progress.setRange(0, 100)
                progress.setValue(pct)
            else:
                progress.setRange(0, 0)

        def on_ok(path, player_path):
            progress.close()
            # Player だけ落とせなかった場合。Editor の更新はこのまま進める
            # (落とせている以上、そちらを止める理由が無い)。片方だけ新しく
            # なることになるので、そうなったことは必ず伝える。
            if worker.player_error:
                # **印を残す。** これを残さないと、次に起動したとき
                # NeoTJAEditor は最新なので「ヘルプ → 更新の確認」は
                # 「最新です」と答えるだけで、Player を取りに行く道が
                # どこにも無くなる(最初の版はここを取り違えていた)。
                self.config_data["player_update_pending"] = True
                settings_mod.save_settings(self.config_data)
                QMessageBox.warning(
                    self, "更新",
                    "NeoTJAPlayer をダウンロードできませんでした。\n"
                    "NeoTJAEditor だけを更新します。\n\n"
                    "%s\n\n"
                    "NeoTJAPlayer は前のままになるので、"
                    "「実行 → NeoTJAPlayer を別ウィンドウで開く」の動きが"
                    "エディタ側とずれることがあります。\n\n"
                    "次の起動で入れ直すかお尋ねします。"
                    "「実行 → NeoTJAPlayer を入れ直す」からいつでもやり直せます。"
                    % worker.player_error)
            # Settle the unsaved-changes question BEFORE arming the batch:
            # apply_update() starts a script that busy-waits for this PID to
            # exit. If closeEvent then vetoed the exit (Cancel is the default
            # button in _unsaved_check), that script would spin forever and
            # later overwrite+relaunch the app the next time it was closed -
            # which looked exactly like "the update silently does nothing".
            if not self._unsaved_check():
                QMessageBox.information(
                    self, "更新",
                    "更新を中止しました。\n次回起動時に再度お知らせします。",
                )
                return
            if version_change:
                # 旧版による設定書き換えに備え、切替前の設定を別ファイルへ残す。
                try:
                    from neotja.updater import backup_version_settings
                    backup_version_settings(settings_mod.settings_path(), self.config_data)
                except OSError as exc:
                    QMessageBox.warning(self, 'バージョン変更',
                                        '設定を退避できませんでした。\n' + str(exc))
                    return
            self._updating = True
            apply_update(path, player_path)
            self.close()

        def on_failed(msg):
            progress.close()
            QMessageBox.warning(self, "更新エラー", f"ダウンロードに失敗しました:\n{msg}")

        worker.progress.connect(on_progress)
        worker.stage.connect(progress.setLabelText)
        worker.finished_ok.connect(on_ok)
        worker.failed.connect(on_failed)
        worker.cancelled.connect(progress.close)
        # Cooperative cancel (a flag checked between chunks), NOT QThread.
        # terminate() - a forced kill mid-write could corrupt interpreter state
        # or leave the fixed dest exe locked, breaking the next update attempt.
        progress.canceled.connect(worker.cancel)
        self._update_download_worker = worker
        worker.start()
        progress.exec()

    # ------------------------------------------------------------------
    # Dialogs wired up in a later pass (Phase 1 dialog port)
    # ------------------------------------------------------------------
    def _not_yet_available(self, name):
        QMessageBox.information(self, name, f"{name} は準備中です。")

    def open_help(self):
        from neotja.dialogs.help_window import HelpWindow
        HelpWindow(self).exec()

    def _is_recording(self):
        """動画の書き出しが進行中か(ダイアログが開いているだけは含まない)。"""
        dlg = self._record_dialog
        if dlg is None:
            return False
        try:
            return bool(dlg.is_busy())
        except RuntimeError:
            # C++ 側が既に破棄されていた。
            self._record_dialog = None
            return False

    def open_video_recorder(self):
        """NeoTJAPlayer の画面を動画(mp4)として書き出す。

        書き出すのは「いまプレビューで見ているコース/分岐」。画面キャプチャでは
        なく1コマずつ描き直すので、書き出し中にアプリを触っても影響しない。

        ダイアログはモーダルにしない(exec() ではなく show())。書き出しは
        始めた時点の譜面の写しに対して行われるので、走らせたまま編集や再生を
        続けられる。"""
        if self._is_recording():
            # 2本同時には走らせない。同じ描画用ウィジェットと ffmpeg を
            # 取り合って、どちらの動画も壊れるため。
            self._record_dialog.raise_()
            self._record_dialog.activateWindow()
            QMessageBox.information(
                self, "動画を書き出す",
                "すでに動画の書き出しが進行中です。\n"
                "終わるか、中止してからもう一度お試しください。")
            return
        if self._record_dialog is not None:
            # 開きっぱなしだが走ってはいない。ここで作り直すのは、
            # 書き出す譜面が「メニューを選んだ時点の内容」であってほしいため。
            self._record_dialog.close()
            self._record_dialog = None
        content = self.editor.toPlainText()
        if not self.courses_info:
            QMessageBox.warning(self, "動画を書き出す", "有効なコースが見つかりません。")
            return
        wave = self.preview_dock.wave_path()
        if not wave or not os.path.exists(wave):
            QMessageBox.warning(
                self, "動画を書き出す",
                "音源(WAVE)が見つかりません。TJA の WAVE: を確認してください。")
            return
        cursor_line = self.editor.textCursor().blockNumber() + 1
        preview_data = self.analyzer.build_preview_timeline(
            content, cursor_line, self._preview_course_override,
            branch_level=self._preview_branch_level,
        )
        # ここで渡すのは最後の逃げ道(TJA と同じフォルダ、無ければホーム)だけ。
        # 環境設定の保存先や前回使った場所との優先順は RecordDialog 側の
        # _resolve_out_dir がまとめて決める。
        out_dir = os.path.dirname(self.current_file) if self.current_file else ""
        if not out_dir or not os.path.isdir(out_dir):
            out_dir = os.path.expanduser("~")
        from neotja.dialogs.record_dialog import RecordDialog
        # 録画を始めたときのモードで、何を録るかが決まる。音声波形モードなら
        # 画面と同じ「上=ゲーム画面 / 下=波形・譜面・命令」で録る。
        pd = self.preview_dock
        layout = "wave" if pd.bottom_stack.currentIndex() == pd.MODE_WAVE else "game"
        # 同時再生モードのときは、その並びのまま書き出す(利用者の指定
        # 2026-10-03)。画面に出ている帯は使い回さず、同じ難易度の譜面から
        # 録画用の写しを作る(RecordDialog の説明を参照)。
        multi_previews = pd.multi_previews()
        if multi_previews:
            layout = "multi"
        dlg = RecordDialog(
            self, preview_data, pd.spin_offset.value(), wave,
            pd.song_duration_seconds(), out_dir, layout=layout,
            multi_previews=multi_previews,
        )
        # 閉じられたら参照を手放す。持ったままだと画面外の描画用ウィジェット
        # (スキンのピクスマップ一式)が居座る。
        dlg.finished.connect(self._on_record_dialog_finished)
        self._record_dialog = dlg
        dlg.show()

    def _on_record_dialog_finished(self, _result=0):
        dlg, self._record_dialog = self._record_dialog, None
        if dlg is not None:
            dlg.deleteLater()

    def open_scroll_splitter(self, span=None, on_scrolls=False):
        # ツールバーのボタンとメニューからは clicked(bool) / triggered(bool) が
        # 第1引数に来る。範囲の指定と取り違えないよう、形で弾く。
        if not isinstance(span, (tuple, list)):
            span = None
        if span is None:
            # 範囲を渡されずに来た = ツールバーかメニューから。作譜ペインで
            # **HS の札を選んでいるなら、そちらが相手**。この入口は札を
            # 一切見ていなかったので、札を選んでボタンを押すと古い道
            # (音符ぜんぶに #SCROLL、開始は範囲の手前の値)へ落ちていた
            # (利用者の報告 2026-11-05「開始SCROLLが1個前 / 終了が最後に
            # 反映されない / 選択した最初のSCROLLが消える」)。
            ce = getattr(self.preview_dock, 'chart_edit', None)
            getter = getattr(ce, 'selection_scroll_positions', None)
            if getter is not None and getter():
                self.run_chart_tool('highspeed')
                return
        cursor, txt = self._get_selection()
        if txt is None:
            return
        from neotja.dialogs.highspeed_dialog import HighSpeedDialog

        def apply(new_text):
            start = cursor.selectionStart()
            cursor.insertText(new_text + "\n")
            # プログラム編集なので dirty と自動保存の対象を明示的に立てる。
            self.editor.mark_edited(start, cursor.position())
            self.setWindowModified(True)
            self._force_update()
        # なめらかハイスピは「範囲の中の時間」で刻むので、範囲の**手前**で
        # 効いている BPM・拍子・スクロールが要る(選んだ本文だけでは
        # 分からない)。利用者の指定 2026-10-15。
        bpm, meter, scroll = self._state_before(cursor.selectionStart())
        HighSpeedDialog(self, txt, apply, span=span, base_bpm=bpm,
                        base_meter=meter, base_scroll=scroll,
                        on_scrolls=on_scrolls).exec()

    #: ヘッダの BPM を探す行。
    _HDR_BPM = "BPM:"

    def _state_before(self, pos):
        """本文の pos 文字目の手前で効いている (BPM, 拍子, スクロール)。

        #START より前のヘッダ BPM から始めて、そこまでの命令行を順に当てる。
        見つからなければ (120.0, (4,4), None)。"""
        import re as _re
        text = self.editor.toPlainText()[:max(0, int(pos))]
        bpm, meter, scroll = 120.0, (4, 4), None
        for line in text.replace("\r\n", "\n").split("\n"):
            s = line.strip()
            if s.upper().startswith(self._HDR_BPM):
                try:
                    bpm = float(s.split(":", 1)[1])
                except (TypeError, ValueError):
                    pass
                continue
            m = _re.match(r"^#\s*([A-Za-z]+)\s*(.*)$", s)
            if not m:
                continue
            name, val = m.group(1).upper(), m.group(2).strip()
            try:
                if name == "BPMCHANGE":
                    bpm = float(val) or bpm
                elif name == "SCROLL":
                    scroll = float(val)
                elif name == "MEASURE":
                    n, d = (float(x) for x in val.replace(" ", "").split("/"))
                    if n > 0 and d > 0:
                        meter = (n, d)
            except (TypeError, ValueError):
                continue
        return bpm, meter, scroll

    # ------------------------------------------------------------------
    # 合作モード(同じ譜面を複数の端末で一緒に書く / 利用者の案 2026-10-16)
    #
    # **ペンは1本しか回らない。** 書けるのは常に1人で、ほかの人は同じ譜面を
    # 見ている。衝突が起きないので、本文の同期は「○文字目から○文字を、この
    # 文字列へ差し替える」を順に流すだけで足りる(neotja/collab.py)。
    #
    # 送る側の口はここ1つ。QTextDocument の contentsChange は、**手打ちも
    # 作譜モードの打ち込みも TJA校正も Ctrl+Z も**通るので、拾い漏らしが無い
    # (作譜の編集も最後は QTextCursor の置換1回になっている)。
    # ------------------------------------------------------------------
    #: 合作の1回ぶん。使うまで作らない(窓を開くだけの人に網を持たせない)。
    _collab = None
    #: 受け取った差し替えを当てている最中か。**自分で送り返さないための札。**
    _collab_applying = False

    def collab(self):
        """合作の入れ物。無ければ作って、本文の出し入れを繋ぐ。"""
        if self._collab is not None:
            return self._collab
        from neotja.collab import CollabSession
        cs = CollabSession(
            str(self._collab_default_name() or "だれか"), self)
        cs.read_text = self.editor.toPlainText
        cs.read_course = self._collab_course_key
        cs.read_branch = self._collab_branch_now
        cs.fullTextReceived.connect(self._collab_take_full)
        cs.patchReceived.connect(self._collab_take_patch)
        cs.penChanged.connect(self._collab_pen_changed)
        cs.penRequested.connect(self._collab_pen_requested)
        cs.opReceived.connect(self._collab_take_op)
        cs.presenceReceived.connect(self._collab_take_presence)
        cs.songProgress.connect(self._collab_song_progress)
        cs.songReceived.connect(self._collab_song_arrived)
        cs.playbackReceived.connect(self._collab_take_playback)
        # 編集者の再生/一時停止を配る。
        self.preview_dock.audio.playingChanged.connect(
            self._collab_send_playback)
        # 自分の居場所を流す。文字のカーソルと作譜ペインのカーソル、どちらが
        # 動いても同じ所へ集める。
        self.editor.cursorPositionChanged.connect(self._collab_queue_here)
        try:
            self.preview_dock.chart_edit.cursorMoved.connect(
                lambda _t: self._collab_queue_here())
        except Exception:  # noqa: BLE001
            pass
        cs.notice.connect(lambda s: self.statusBar().showMessage(s, 6000))
        cs.stateChanged.connect(self._collab_update_title)
        cs.stateChanged.connect(self._collab_paint_peers)
        cs.stateChanged.connect(self._collab_here_keepalive)
        self.editor.document().contentsChange.connect(self._collab_on_change)
        self._collab = cs
        return cs

    def _collab_song_tag(self):
        """いま開いている音源の見分け(名前と大きさ)。違う曲なら知らせる。"""
        try:
            w = self.preview_dock.wave_path()
        except Exception:  # noqa: BLE001
            w = None
        if not w or not os.path.exists(w):
            return ""
        return "%s/%d" % (os.path.basename(w), os.path.getsize(w))

    #: 離れた相手への入口(cloudflared)。ルームを閉じたら必ず止める。
    _collab_tunnel = None
    _collab_room_dialog = None

    def _collab_ask(self, mode):
        """名前(と合言葉)を聞く。やめたら None。"""
        from neotja.dialogs.collab_enter_dialog import CollabEnterDialog
        dlg = CollabEnterDialog(mode, self._collab_default_name(), self)
        if dlg.exec() != QDialog.Accepted:
            return None
        name = dlg.name()
        # 次からの初期値にする。
        self.config_data["collab_name"] = name
        settings_mod.save_settings(self.config_data)
        return (name, dlg.code())

    def _collab_default_name(self):
        """名前の欄に最初から入れておくもの。"""
        return (self.config_data.get("collab_name")
                or self.config_data.get("nameplate_name")
                or os.environ.get("USERNAME") or "")

    # ---- 合作フォルダ(アプリの隣の「合作」。ここの中しか触らない) ----
    def _collab_move_into_dir(self):
        """いま開いている譜面を合作フォルダへ用意する。道筋を返す。

        **合作はこのフォルダの中だけで行う**(利用者の指定 2026-10-16)。
        よそのファイルのまま始めると、相手から来た書き換えが自分の本番の
        譜面へそのまま入ってしまう。音源も隣へ写して、2人の持ち物をそろえる。
        """
        from neotja.collab import (collab_dir, collab_file_name,
                                   in_collab_dir)
        root = collab_dir()
        if not root:
            QMessageBox.warning(self, "合作",
                                "合作フォルダを作れませんでした。")
            return None
        cur = self.current_file
        if cur and in_collab_dir(cur):
            if self.isWindowModified():
                self.save_file()
            return cur
        title = ""
        for line in self.editor.toPlainText().split("\n")[:40]:
            if line.upper().startswith("TITLE:"):
                title = line.split(":", 1)[1].strip()
                break
        # 元のファイル名を先に見る(無ければ曲名)。どの譜面から始めた
        # 合作なのかが名前で分かるように。
        stem = (os.path.splitext(os.path.basename(cur))[0] if cur
                else "") or title or "合作"
        base = collab_file_name(stem)
        dst = os.path.join(root, base + ".tja")
        n = 2
        while os.path.exists(dst):
            dst = os.path.join(root, "%s_%d.tja" % (base, n))
            n += 1
        # 音源も隣へ写す(無ければ写さない)。
        wave_name = ""
        try:
            src_wave = self.preview_dock.wave_path()
            if src_wave and os.path.exists(src_wave):
                wave_name = os.path.basename(src_wave)
                dst_wave = os.path.join(root, wave_name)
                if not os.path.exists(dst_wave):
                    shutil.copy2(src_wave, dst_wave)
        except Exception:  # noqa: BLE001
            pass
        try:
            with open(dst, "w", encoding="cp932", errors="replace",
                      newline="") as f:
                f.write(self.editor.toPlainText())
        except OSError as e:
            QMessageBox.warning(self, "合作",
                                "合作フォルダへ写せませんでした:\n%s" % e)
            return None
        self._open_path(dst)
        self.statusBar().showMessage(
            "合作: 「%s」フォルダへ写しました%s"
            % (os.path.basename(root), "(音源も)" if wave_name else ""), 8000)
        return dst

    def collab_host(self):
        """ルームを作る。家の中の招待コードはすぐ、ネット越しは出来たら足す。"""
        got = self._collab_ask("create")
        if got is None:
            return
        if self._collab_move_into_dir() is None:
            return
        cs = self.collab()
        cs.name = got[0]
        code = cs.start_host()
        if not code:
            return
        # 合作フォルダへ写した音源を、入ってきた人へ送れるようにする。
        # **start_host() のあとで登録する** — あれは中で stop() を呼び、
        # 部屋ごとの持ち物を忘れるので、先に登録すると消される。
        cs.song_offer(self._collab_wave_in_dir())
        from neotja.dialogs.collab_room_dialog import CollabRoomDialog
        dlg = CollabRoomDialog(cs.codes() or [code], self)
        dlg.finished.connect(lambda _r: self._collab_forget_room_dialog())
        self._collab_room_dialog = dlg
        dlg.show()
        self._collab_start_tunnel(cs)
        self._collab_update_title()

    def _collab_forget_room_dialog(self):
        dlg, self._collab_room_dialog = self._collab_room_dialog, None
        if dlg is not None:
            dlg.deleteLater()

    def _collab_start_tunnel(self, cs):
        """離れた相手のための入口を立てる。

        **立たなくても合作は始まっている。** 入口は「遠くからも入れる」を
        足すだけなので、作れなければ家の中だけの合作として続ける。"""
        from neotja.tunnel import Tunnel
        self._collab_stop_tunnel()
        tun = Tunnel(self)
        tun.ready.connect(self._collab_tunnel_ready)
        tun.failed.connect(self._collab_tunnel_failed)
        tun.waiting.connect(self._collab_tunnel_waiting)
        self._collab_tunnel = tun
        tun.start(cs.port())

    def _collab_stop_tunnel(self):
        tun, self._collab_tunnel = self._collab_tunnel, None
        if tun is not None:
            tun.stop()
            tun.deleteLater()

    def _collab_tunnel_ready(self, url):
        from neotja.collab import CODE_PREFIX
        code = CODE_PREFIX + url
        if self._collab_room_dialog is not None:
            self._collab_room_dialog.set_net_code(code)
        self.statusBar().showMessage(
            "合作: 離れた相手も入れるようになりました", 6000)

    def _collab_tunnel_waiting(self, _url, secs):
        if self._collab_room_dialog is not None:
            self._collab_room_dialog.set_net_waiting(secs)

    def _collab_tunnel_failed(self, why):
        if self._collab_room_dialog is not None:
            self._collab_room_dialog.set_net_error(why)
        self.statusBar().showMessage("合作: %s" % why, 8000)

    def collab_join(self):
        got = self._collab_ask("join")
        if got is None:
            return
        name, code = got
        cs = self.collab()
        cs.name = name
        cs.song = self._collab_song_tag()
        cs.join(code)
        self._collab_update_title()

    def collab_take_edit(self):
        """自分が編集モードになる。

        誰も編集していなければそのまま切り替わる。ほかの人が編集中のときは
        その人に代われるか尋ねる(断られたらプレビューのまま)。"""
        cs = self.collab()
        if not cs.is_on():
            self.statusBar().showMessage(
                "合作: ルームに入っていません", 4000)
            self._collab_sync_menu()
            return
        if cs.holds_pen():
            self.statusBar().showMessage("合作: もう編集モードです", 3000)
            return
        cs.request_pen()
        if not cs.holds_pen():
            self.statusBar().showMessage(
                "合作: %s へ編集権を要求しました" % (cs.pen or "編集者"), 4000)
        self._collab_sync_menu()

    def collab_leave_edit(self):
        """プレビューモードへ下りる。誰も編集していない状態になる。"""
        cs = self.collab()
        if not cs.is_on():
            self._collab_sync_menu()
            return
        if not cs.holds_pen():
            self.statusBar().showMessage("合作: もうプレビューモードです", 3000)
            return
        cs.release_pen()
        self._collab_sync_menu()

    def _collab_sync_menu(self):
        """メニューの印と押せる/押せないを、いまの様子に合わせる。"""
        if getattr(self, "_menu_collab", None) is None:
            # メニューを置いていない(2026-11-12 以降)。帯も無い。
            return
        cs = self._collab
        on = cs is not None and cs.is_on()
        editing = on and cs.holds_pen()
        for act, want in ((self._act_collab_edit, editing),
                          (self._act_collab_view, on and not editing)):
            act.setEnabled(on)
            act.setChecked(bool(want))
        # 渡せるのは**いまの編集者**(ホストに限らない / 利用者の指定
        # 2026-10-17)。相手が居なければ渡す先が無い。
        self._act_collab_hand.setEnabled(
            on and cs.holds_pen() and bool(cs.peer_names()))
        # 画面の上のボタンも同じ状態にする。合作していなければ出さない。
        pd = getattr(self, "preview_dock", None)
        bar = getattr(pd, "collab_bar", None) if pd else None
        if bar is not None:
            bar.setVisible(bool(on))
            if on:
                bar.raise_()
                pd.btn_collab_pen.setEnabled(
                    cs.holds_pen() and bool(cs.peer_names()))
                pd.btn_collab_follow.setEnabled(not cs.holds_pen())
                # 誰が編集権を持っているかを常に出す。
                who = cs.pen or ""
                pd.lbl_collab_who.setText(
                    "編集モード: %s" % ("あなた" if cs.holds_pen()
                                        else (who or "なし")))
                if pd.btn_collab_follow.isChecked() != self.collab_follow:
                    pd.btn_collab_follow.blockSignals(True)
                    pd.btn_collab_follow.setChecked(self.collab_follow)
                    pd.btn_collab_follow.blockSignals(False)

    def _collab_give_menu(self, anchor=None):
        """画面の「編集を渡す」。相手が1人ならそのまま、複数なら選ぶ。"""
        cs = self._collab
        if cs is None or not cs.is_on() or not cs.holds_pen():
            return
        others = [w for w in cs.everyone() if w != cs.name]
        if not others:
            self.statusBar().showMessage("合作: 委譲先が居ません", 4000)
            return
        if len(others) == 1:
            cs.give_pen_to(others[0])
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        for who in others:
            menu.addAction(who, lambda w=who: cs.give_pen_to(w))
        if anchor is not None:
            menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        else:
            menu.exec()

    def _collab_fill_hand_menu(self):
        """「編集を任せる」の中身を、いま居る人で作り直す。"""
        menu = self._act_collab_hand
        menu.clear()
        cs = self._collab
        if cs is None or not cs.is_on() or not cs.holds_pen():
            return
        for who in cs.everyone():
            if who == cs.name:
                continue              # いま自分が持っている
            menu.addAction(who, lambda w=who: cs.give_pen_to(w))

    def _collab_check_file(self, path):
        """合作中によその譜面を開いたら、巻き込まないように退室する。

        合作はフォルダの中だけの約束(利用者の指定 2026-10-16)。開いたまま
        繋がっていると、相手の書き換えがその譜面へ入ってしまう。"""
        from neotja.collab import in_collab_dir
        cs = self._collab
        if cs is None or not cs.is_on() or in_collab_dir(path):
            return
        self.collab_stop()
        QMessageBox.information(
            self, "合作",
            "合作フォルダの外の譜面を開いたので、ルームから出ました。\n"
            "合作で触れるのはアプリの隣の「合作」フォルダの中だけです。")

    def collab_stop(self):
        """退室。**入口も必ず閉じる**(開けたままだと外から入れてしまう)。"""
        self._collab_stop_tunnel()
        if self._collab_room_dialog is not None:
            self._collab_room_dialog.close()
        if self._collab is not None:
            self._collab.stop()
        self._collab_update_title()

    # ---- 音源の受け渡し ----
    def _collab_wave_in_dir(self):
        """合作フォルダにある、いまの譜面の音源。無ければ空。"""
        from neotja.collab import collab_dir, in_collab_dir
        try:
            w = self.preview_dock.wave_path()
        except Exception:  # noqa: BLE001
            w = None
        if w and os.path.exists(w) and in_collab_dir(w):
            return w
        root = collab_dir()
        if not (root and w):
            return ""
        cand = os.path.join(root, os.path.basename(w))
        return cand if os.path.exists(cand) else ""

    def _collab_need_song(self):
        """ゲスト: 譜面が指す音源が手元に無ければ、貰いに行く。"""
        cs = self._collab
        if cs is None or cs.role != "guest":
            return
        from neotja.collab import collab_dir
        name = ""
        for line in self.editor.toPlainText().split(chr(10))[:60]:
            if line.upper().startswith("WAVE:"):
                name = line.split(":", 1)[1].strip()
                break
        root = collab_dir()
        if not name or not root:
            return
        if os.path.exists(os.path.join(root, os.path.basename(name))):
            return                       # もう持っている
        self.statusBar().showMessage("合作: 音源を貰っています…", 6000)
        cs.request_song(os.path.basename(name))

    def _collab_song_progress(self, done, total, sending):
        if total <= 0:
            return
        pct = int(100.0 * done / total)
        self.statusBar().showMessage(
            "合作: 音源を%s %d%%  (%.1f / %.1f MB)"
            % ("送っています" if sending else "貰っています", pct,
               done / 1048576.0, total / 1048576.0), 3000)

    def _collab_song_arrived(self, path):
        """音源が届いた。譜面を読み直して鳴るようにする。"""
        self.statusBar().showMessage(
            "合作: 音源を受け取りました → %s" % os.path.basename(path), 8000)
        # **道筋は最初から決まっている。** 素通りさせずに読み直させる。
        self.preview_dock.reload_wave()

    # ---- 再生の同期と、編集者への追従 ----
    #: 受け取った再生指示を当てている最中か(送り返さないための札)。
    _collab_playing_apply = False
    #: 編集者の見ている所へ付いていくか(利用者の指定 2026-10-17: 既定で入)。
    collab_follow = True

    def _collab_send_playback(self, playing):
        """自分が再生/一時停止した。編集者なら相手にも同じことをさせる。"""
        cs = self._collab
        if cs is None or not cs.is_on() or self._collab_playing_apply:
            return
        try:
            at = self.preview_dock.audio.position() / 1000.0
        except Exception:  # noqa: BLE001
            at = 0.0
        cs.send_playback(at, bool(playing))

    def _collab_take_playback(self, seconds, playing):
        """編集者が再生/一時停止した。同じ所から同じ状態にする。"""
        cs = self._collab
        if cs is None or not cs.is_on() or cs.holds_pen():
            return
        pd = self.preview_dock
        self._collab_playing_apply = True
        try:
            pd.seek_to_seconds(max(0.0, float(seconds)), play=False)
            # **いまの状態と見比べて切り替えない。** 音を鳴らし始める
            # のは非同期なので、押した直後の is_playing() はまだ嘘を
            # つく。見比べて切り替えると取りこぼし、相手が止めたのに
            # こちらだけ鳴り続ける(2026-10-17 の試験で実際に起きた)。
            # 言われたとおりに「鳴らす/止める」と言う。
            if playing:
                pd.audio.play()
            else:
                pd.audio.pause()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._collab_playing_apply = False

    def collab_jump_to_editor(self):
        """編集者が見ている所へ飛ぶ(1回だけ)。"""
        cs = self._collab
        if cs is None or not cs.is_on():
            return
        t = None
        for who, (_pos, ct, _when) in (cs.peers_here() or {}).items():
            if who == cs.pen and ct is not None:
                t = ct
                break
        if t is None:
            self.statusBar().showMessage(
                "合作: 編集者の視点がまだ届いていません", 4000)
            return
        self.preview_dock.seek_to_seconds(max(0.0, float(t)))

    def collab_set_follow(self, on):
        """編集者に付いていくかどうか。"""
        self.collab_follow = bool(on)
        act = getattr(self, "_act_collab_follow", None)
        if act is not None and act.isChecked() != self.collab_follow:
            act.setChecked(self.collab_follow)
        self.statusBar().showMessage(
            "合作: 視点追従 %s" % ("有効" if on else "無効"),
            4000)
        if on:
            self.collab_jump_to_editor()

    # ---- 相手の居場所(「ここ見て」が通じるようにするための飾り) ----
    #: 居場所を流す間合い(ms)。カーソルは1秒に何十回も動くので間引く。
    COLLAB_HERE_MS = 80
    _collab_here_timer = None
    #: 念のため見に行く間合い(ms)。自分の操作以外でカーソルが動いたとき
    #: (視点追従・再解析)は cursorMoved が出ないので、これが要る。
    COLLAB_HERE_KEEP_MS = 2000
    _collab_here_keep = None
    #: 最後に流した居場所。同じなら流さない。
    _collab_here_last = None
    #: 相手ごとの色。名前から決めるので、席順が変わっても色は変わらない。
    _COLLAB_COLORS = ("#7aa2f7", "#9ece6a", "#e0af68", "#bb9af7", "#f7768e",
                      "#2ac3de")

    def _collab_peer_color(self, name):
        n = sum(ord(c) for c in str(name)) if name else 0
        return self._COLLAB_COLORS[n % len(self._COLLAB_COLORS)]

    def _collab_queue_here(self):
        """自分の居場所を流す(続けて動いたぶんは1回にまとめる)。"""
        cs = self._collab
        if cs is None or not cs.is_on():
            return
        t = self._collab_here_timer
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(self.COLLAB_HERE_MS)
            t.timeout.connect(self._collab_send_here)
            self._collab_here_timer = t
        if not t.isActive():
            t.start()

    def _collab_send_here(self):
        cs = self._collab
        if cs is None or not cs.is_on():
            self._collab_here_last = None
            return
        ct = None
        try:
            ce = self.preview_dock.chart_edit
            if ce is not None:
                ct = float(ce.cursor_time())
        except Exception:  # noqa: BLE001
            ct = None
        pos = self.editor.textCursor().position()
        now = (pos, None if ct is None else round(ct, 4),
               cs.read_course() if callable(cs.read_course) else None)
        if now == self._collab_here_last:
            return
        self._collab_here_last = now
        cs.send_here(pos, ct)

    def _collab_here_keepalive(self):
        """居場所を流し直す見張り。

        カーソルが**自分の操作以外**で動くと cursorMoved が出ない
        (視点追従で引っぱられたとき、再解析で貼り直されたとき)。
        それだと相手の画面に古い位置が残るので、ときどき見に行く。
        中身が同じときは送らないので、黙っていれば何も流れない。"""
        cs = self._collab
        t = self._collab_here_keep
        if cs is not None and cs.is_on():
            if t is None:
                t = QTimer(self)
                t.setInterval(self.COLLAB_HERE_KEEP_MS)
                t.timeout.connect(self._collab_send_here)
                self._collab_here_keep = t
            if not t.isActive():
                t.start()
            # 顔ぶれが変わったら名乗り直す。**後から入った人には、
            # 自分が動くまで自分の居場所が届かない**ため。
            self._collab_here_last = None
            self._collab_send_here()
        elif t is not None and t.isActive():
            t.stop()
            self._collab_here_last = None

    def _collab_take_presence(self, who, _pos, chart_t):
        """相手の居場所が届いた。作譜ペインと文字エディタの両方へ出す。"""
        if not who:
            return
        self._collab_paint_peers()
        # 追従中なら、編集者の見ている所へ付いていく。再生中は
        # 曲が進んでいるので邪魔しない(再生の同期で揃っている)。
        cs = self._collab
        if (self.collab_follow and cs is not None and who == cs.pen
                and chart_t >= 0.0 and not cs.holds_pen()
                and not self.preview_dock.audio.is_playing()):
            # **鳴らさない。** 付いていくのは見る所だけ。
            self.preview_dock.seek_to_seconds(max(0.0, float(chart_t)),
                                              play=False)

    def _collab_paint_peers(self):
        """覚えている居場所を、画面へ置き直す。"""
        cs = self._collab
        try:
            ce = self.preview_dock.chart_edit
        except Exception:  # noqa: BLE001
            ce = None
        if ce is None or not hasattr(ce, "set_peer_marks"):
            return
        if cs is None or not cs.is_on():
            ce.set_peer_marks(())
            self._collab_paint_editor_peers({})
            return
        here = cs.peers_here()
        marks = []
        for who, (_pos, ct, _when) in sorted(here.items()):
            if ct is None:
                continue
            marks.append((who, ct, self._collab_peer_color(who)))
        ce.set_peer_marks(marks)
        self._collab_paint_editor_peers(here)

    def _collab_paint_editor_peers(self, here):
        """文字エディタの側。相手の居る行に色を敷く。

        ExtraSelection なので本文には一切触らない(選択の見た目だけ)。"""
        from PySide6.QtWidgets import QTextEdit
        from PySide6.QtGui import QColor, QTextFormat
        sels = []
        doc = self.editor.document()
        last = doc.characterCount() - 1
        for who, (pos, _ct, _when) in sorted((here or {}).items()):
            sel = QTextEdit.ExtraSelection()
            c = QTextCursor(doc)
            c.setPosition(max(0, min(int(pos), last)))
            sel.cursor = c
            col = QColor(self._collab_peer_color(who))
            col.setAlpha(46)
            sel.format.setBackground(col)
            # 行ぜんたいに敷く(文字1つぶんだけ塗っても見えない)。
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sels.append(sel)
        self.editor.setExtraSelections(sels)

    # ---- 本文の出し入れ ----
    def _collab_on_change(self, pos, removed, added):
        """この窓で本文が変わった。合作していれば配る。"""
        cs = self._collab
        if cs is None or not cs.is_on() or self._collab_applying:
            return
        # **書いていいのは編集モードの人だけ。** 読むだけの側でも、ファイルを
        # 開く・貰った譜面を保存して開くといった操作で本文は入れ替わる。それを
        # そのまま流すと、相手の譜面がこちらの都合で丸ごと書き換わってしまう
        # (試験で実際にホストの本文が空になった 2026-10-16)。
        # ただし**相手に代わって書いているあいだ**は配る。スマホから届いた
        # 打ち込みをこちらが書いているので、持ち主はこちらではない。
        if not cs.holds_pen() and not self._collab_relaying:
            return
        ins = ""
        if added:
            doc = self.editor.document()
            # **終わりで1文字はみ出す。** contentsChange の数は文書の数え方
            # (段落の切れ目も1文字)なので、末尾まで届く変更では pos+added が
            # characterCount() と同じになり、**置けない位置**になる。
            # QTextCursor は黙って動かないので選び取れず、ins が空のまま
            # 「全部消して何も入れない」を配っていた(実測: 編集者が別の
            # ファイルを開くと、相手の本文が空になった 2026-10-22)。
            # 流す本文は改行で繋いだ平文で、最後の切れ目はそこに現れない。
            # だから端で1つ詰めるのが正しい。
            end = min(pos + added, doc.characterCount() - 1)
            c = QTextCursor(doc)
            c.setPosition(min(pos, end))
            c.setPosition(end, QTextCursor.KeepAnchor)
            # selectedText() は改行を U+2029 で返す。そのまま流すと相手の
            # 本文に見えない字が混ざるので、必ず戻す。
            ins = c.selectedText().replace(" ", "\n")
        cs.send_patch(pos, removed, ins)

    #: いま「相手に代わって」書いているか。立っているあいだは、自分が
    #: 編集者でなくても書いたぶんを配る(_collab_on_change)。
    _collab_relaying = False

    #: スマホから受け付ける操作。**住所(a/b)で指すものだけ。** 選んだ
    #: オブジェクトを動かす類(move_items など)は、相手がこちらの解析結果の
    #: 中身を持っていないと組み立てられないので渡さない。
    COLLAB_OPS = frozenset((
        "key", "long", "delete", "flip", "size", "move",
        "command", "measure_insert", "marker", "region",
    ))

    def _collab_take_op(self, who, op):
        """スマホから「ここに こう打って」が届いた。こちらが書く。

        **作譜モードと同じ入口へそのまま流す**(_apply_chart_op →
        note_edit.run_op)。割り直しも、連打の途中を断るのも、BALLOON: の
        並べ直しも、末尾の空小節も、既にあるものがそっくり効く。書いた結果は
        いつもの patch で全員へ流れ、Undo も1操作ぶんになる。

        相手は本文を1文字も書かないので、触っていない所(コメント・未対応の
        命令・ほかのコース)は元のまま残る。"""
        cs = self._collab
        if cs is None or cs.role != "host":
            return
        # **編集者だけ。** 誰でも書けると、読むだけのつもりの人の誤爆で
        # 相手の譜面が動く。
        if not who or who != cs.pen:
            self.statusBar().showMessage(
                "合作: %s は編集モードではないので打ち込みを見送りました" % (who or "?"),
                4000)
            return
        if not self._collab_op_fits(op):
            return
        kind = str(op.get("kind") or "")
        if kind == "measure":
            # 小節まるごとは住所ではなく**文字の位置**で指す別の仕掛け
            # (measure_edit)。ここで位置に直してから渡す。
            self._collab_relaying = True
            try:
                self._collab_measure_op(op)
            finally:
                self._collab_relaying = False
            return
        if kind not in self.COLLAB_OPS:
            return
        inner = {k: v for k, v in op.items()
                 if k not in ("t", "who", "op", "course")}
        inner["kind"] = kind
        # JSON の対応表は鍵が文字になる。小節番号は数で引くので戻す。
        if isinstance(inner.get("grids"), dict):
            try:
                inner["grids"] = {int(k): int(v)
                                  for k, v in inner["grids"].items()}
            except (TypeError, ValueError):
                inner.pop("grids", None)
        course = str(op.get("course") or "") or None
        self._collab_relaying = True
        try:
            self._apply_chart_op(inner, course_key=course)
        finally:
            self._collab_relaying = False

    def _collab_op_fits(self, op):
        """送り主が思っている譜面と、こちらの本文が食い違っていないか。

        **相手の解析がこちらとずれていたら、打ち込み先が別の場所になる。**
        実際にずれる: 分岐(#BRANCHSTART)のある譜面をスマホの解析器にかけると、
        3つの系統を**3小節として並べて**読む。同じ譜面で Neo は3小節、スマホは
        5小節と数えた(実測 2026-10-23)。そのまま「3小節目」と言われても、
        指している所が違う。

        そこで打ち込みには「その小節はこうなっているはず(expect)」を添えて
        もらい、**書く前に見比べる**。違えば書かずに本文を配り直す。分岐に
        限らず、こちらがまだ知らない命令で構造が変わる譜面でも同じように
        捕まえられる。"""
        want = op.get("expect")
        if want is None:
            return True                 # 添えていないものは素通し(古い相手)
        cs = self._collab
        a = op.get("a")
        try:
            m = int(a[0])
        except (TypeError, ValueError, IndexError):
            return True
        text = self.editor.toPlainText()
        course_key = (str(op.get("course") or "")
                      or self._preview_course_override
                      or self._course_key_at_cursor(text))
        rng = (self.analyzer.course_line_range(text, course_key)
               if course_key else None)
        # **書く側と同じ数え方で見ること。** 分岐のある譜面では、囲みを
        # 付けないと全系統を並べて数えてしまい、書く側(いま流している系統
        # だけを数える)と番号が食い違う。そのまま見比べると、ずれた
        # 打ち込みを「合っている」と通してしまう(試験で実際に通った)。
        with note_edit.editing_branch(self._preview_branch_level,
                                      self._preview_branch_path):
            body = note_edit.course_body_span(text, rng)
            if body is None:
                return False
            spans = note_edit.measure_spans(text, body)
            got = (note_edit._measure_notes(text, spans[m])
                   if 0 <= m < len(spans) else None)
        if got == str(want):
            return True
        self.statusBar().showMessage(
            "合作: 譜面の読み方が食い違ったので打ち込みを見送り、"
            "本文を送り直しました", 5000)
        if cs is not None and cs.role == "host":
            cs.send_full(text)
        return False

    def _collab_measure_op(self, op):
        """スマホからの「何小節目を 複製/削除/手前に1つ」。"""
        what = str(op.get("what") or "")
        if what not in self._MEASURE_OPS:
            return
        text = self.editor.toPlainText()
        course_key = (str(op.get("course") or "")
                      or self._preview_course_override
                      or self._course_key_at_cursor(text))
        rng = (self.analyzer.course_line_range(text, course_key)
               if course_key else None)
        body = note_edit.course_body_span(text, rng)
        if body is None:
            return
        spans = note_edit.measure_spans(text, body)
        try:
            m = int(op.get("m"))
        except (TypeError, ValueError):
            return
        if not (0 <= m < len(spans)):
            return
        self._measure_op(what, pos=spans[m][0])

    def _collab_take_patch(self, pos, removed, ins, _rev):
        """相手の差し替えを当てる。送り返さないよう札を立てる。"""
        doc = self.editor.document()
        pos = max(0, min(int(pos), doc.characterCount() - 1))
        self._collab_applying = True
        try:
            c = QTextCursor(doc)
            c.beginEditBlock()
            c.setPosition(pos)
            c.setPosition(min(pos + int(removed), doc.characterCount() - 1),
                          QTextCursor.KeepAnchor)
            c.insertText(ins)
            c.endEditBlock()
        finally:
            self._collab_applying = False
        self.setWindowModified(True)

    def _collab_take_full(self, text, _rev):
        """本文を丸ごと貰った(入った直後・ずれを直すとき)。

        **入った直後は、貰った譜面を合作フォルダへ保存してそれを開く。**
        開いている譜面の上に相手の本文を貼ると、自分の作りかけを壊して
        しまう(利用者の指定 2026-10-16: 合作はあのフォルダの中だけ)。"""
        from neotja.collab import in_collab_dir
        if text == self.editor.toPlainText():
            return
        if not in_collab_dir(self.current_file):
            if self._collab_adopt_file(text):
                return
        self._collab_applying = True
        try:
            c = QTextCursor(self.editor.document())
            c.beginEditBlock()
            c.select(QTextCursor.Document)
            c.insertText(text)
            c.endEditBlock()
        finally:
            self._collab_applying = False
        self._force_update()
        self.statusBar().showMessage("合作: 譜面を受け取りました", 4000)

    def _collab_adopt_file(self, text):
        """貰った譜面を合作フォルダへ保存して開く。開けたら True。"""
        from neotja.collab import collab_dir, collab_file_name
        root = collab_dir()
        if not root:
            return False
        title = ""
        for line in text.split("\n")[:40]:
            if line.upper().startswith("TITLE:"):
                title = line.split(":", 1)[1].strip()
                break
        base = collab_file_name(title, "合作")
        dst = os.path.join(root, base + ".tja")
        n = 2
        while os.path.exists(dst):
            dst = os.path.join(root, "%s_%d.tja" % (base, n))
            n += 1
        try:
            with open(dst, "w", encoding="cp932", errors="replace",
                      newline="") as f:
                f.write(text)
        except OSError as e:
            self.statusBar().showMessage("合作: 保存できません(%s)" % e, 8000)
            return False
        # 開く動き自体は「自分の編集」ではないので、相手へ流さない。
        self._collab_applying = True
        try:
            self._open_path(dst)
        finally:
            self._collab_applying = False
        self.statusBar().showMessage(
            "合作: 譜面を受け取り、「%s」へ保存しました → %s"
            % (os.path.basename(root), os.path.basename(dst)), 10000)
        self._collab_need_song()
        return True

    def _collab_pen_changed(self, who):
        """ペンの持ち主が変わった。持っていない間は書けなくする。

        **読むだけにしないと分岐する。** 持っていない側が手元で打つと、
        その変更は誰にも配られないまま自分の本文だけ変わってしまう。"""
        cs = self._collab
        mine = (cs is None) or cs.holds_pen()
        self.editor.setReadOnly(not mine)
        self._collab_update_title()
        if cs is not None and cs.is_on():
            self.statusBar().showMessage(
                "合作: 編集モードは %s" % (who or "だれも居ません")
                + ("(あなたです)" if mine else "(あなたはプレビュー)"), 6000)

    def _collab_pen_requested(self, who):
        cs = self._collab
        if cs is None or cs.role != "host":
            return
        if QMessageBox.question(
                self, "合作",
                "%s から編集権の要求があります。委譲しますか?\n"
                "(はい を選ぶと、あなたはプレビューモードへ移ります)" % who,
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            cs.grant_pen(who)

    def _collab_update_title(self):
        """窓の肩書きに合作の様子を出す。"""
        cs = self._collab
        if cs is None or not cs.is_on():
            self._collab_tag = ""
        else:
            if cs.holds_pen():
                mode = "編集モード"
            elif cs.pen:
                mode = "プレビュー / 編集は %s" % cs.pen
            else:
                mode = "プレビュー / だれも編集していません"
            self._collab_tag = "  [合作 %s / %s]" % (
                "ルーム主" if cs.role == "host" else "参加", mode)
        self._refresh_title()
        self._collab_sync_menu()

    def open_tja_tidy(self):
        """「TJA校正」。作譜モードが残していく散らかりを片づける。

        鳴る譜面は1つも変えない。整えた結果をもう一度読み直して、音符・
        連打・風船・ゴーゴー・小節線と、各時刻で効いている速さ/BPM が
        元と同じであることを確かめてから入れ替える(違えば入れない)。
        Ctrl+Z 1回で戻せる(本文まるごと1回の書き換え)。"""
        from neotja import tja_tidy
        old = self.editor.toPlainText()
        if "#START" not in old:
            QMessageBox.information(self, "TJA校正",
                                    "整えるコースが見つかりません。")
            return
        # 押す前の確認は出さない(利用者の指定 2026-10-15)。Ctrl+Z 1回で
        # 戻せるうえ、鳴り方が変わる結果なら下で止める。
        new, rep = tja_tidy.tidy_text(old)
        if new == old:
            QMessageBox.information(self, "TJA校正",
                                    "片づける所はありませんでした。")
            return
        ok, why = tja_tidy.same_sound(old, new)
        if not ok:
            QMessageBox.warning(
                self, "TJA校正",
                "整えた結果が元と違ってしまったので、入れ替えませんでした。\n\n"
                + why + "\n\nこの譜面の形を教えてもらえれば直します。")
            return
        n_old = len(old.splitlines())
        n_new = len(new.splitlines())
        cur = self.editor.textCursor()
        cur.beginEditBlock()
        cur.select(QTextCursor.Document)
        cur.insertText(new)
        cur.endEditBlock()
        self.editor.mark_edited(0, len(new))
        self.setWindowModified(True)
        self._force_update()
        msg = ("命令を %d 本、行を %d 行減らしました（%d → %d 行）。"
               % (rep["commands"], rep["lines"], n_old, n_new))
        # 目盛りへそろえるので、増える小節もある(8分 → 16分)。差し引きで出す。
        if rep["chars"] > 0:
            msg += "\n音符の文字も %d 減らしました。" % rep["chars"]
        elif rep["chars"] < 0:
            msg += "\n目盛りをそろえたぶん、音符の文字は %d 増えました。" % (
                -rep["chars"])
        self.statusBar().showMessage(msg.replace("\n", " "), 6000)
        QMessageBox.information(self, "TJA校正", msg)

    def open_measure_converter(self):
        cursor, txt = self._get_selection()
        if txt is None:
            return
        from neotja.dialogs.measure_convert_dialog import MeasureConvertDialog

        def apply(new_text):
            start = cursor.selectionStart()
            cursor.insertText(new_text + "\n")
            # プログラム編集なので dirty と自動保存の対象を明示的に立てる。
            self.editor.mark_edited(start, cursor.position())
            self.setWindowModified(True)
            self._force_update()
        MeasureConvertDialog(self, txt, apply).exec()

    def open_density_graph(self):
        """密度グラフ。見せるのは「いまプレビューで見ているコース」
        (譜面画像生成と同じ選び方)。窓の中でコースを切り替えられる。"""
        content = self.editor.toPlainText()
        if not self.courses_info:
            QMessageBox.warning(self, "警告", "有効なコースが見つかりません。")
            return
        cursor_line = self.editor.textCursor().blockNumber() + 1
        key = None
        try:
            data = self.analyzer.build_preview_timeline(
                content, cursor_line, self._preview_course_override,
                branch_level=self._preview_branch_level,
            )
            key = data.get("course_key")
        except Exception:  # noqa: BLE001
            key = None
        from neotja.dialogs.density_dialog import DensityGraphDialog
        DensityGraphDialog(self, content, key).exec()

    def open_image_exporter(self):
        """譜面画像生成。書き出す対象は「いまプレビューで見ているコース」
        (カーソル位置/コース切替に追従)にする。以前は常に courses_info[0]
        (ファイル先頭のコース)固定だったので、おにを編集していても
        かんたんの画像が出てしまっていた。"""
        content = self.editor.toPlainText()
        if not self.courses_info:
            QMessageBox.warning(self, "警告", "有効なコースが見つかりません。")
            return
        cursor_line = self.editor.textCursor().blockNumber() + 1
        target_label = None
        try:
            data = self.analyzer.build_preview_timeline(
                content, cursor_line, self._preview_course_override,
                branch_level=self._preview_branch_level,
            )
            key = data.get("course_key")
            target_label = next((c["label"] for c in self.courses_info if c["key"] == key), None)
        except Exception:
            target_label = None
        if not target_label:
            target_label = self.courses_info[0]["label"]
        from neotja.dialogs.image_preview_dialog import TJAImagePreviewDialog
        TJAImagePreviewDialog(self, content, target_label).exec()

    # ------------------------------------------------------------------
    # 同時再生(難易度を縦に並べて見る)
    # ------------------------------------------------------------------
    def open_strobe_tool(self):
        cursor = self.editor.textCursor()
        pos = cursor.position()
        text_before_cursor = self.editor.toPlainText()[:pos]
        lines = text_before_cursor.split("\n")
        current_bpm = "120"
        full_text = self.editor.toPlainText().split("\n")
        for line in full_text:
            if line.startswith("BPM:"):
                current_bpm = line[4:].strip()
                break
        for line in reversed(lines):
            match = re.search(r"#BPMCHANGE\s+([0-9.]+)", line)
            if match:
                current_bpm = match.group(1)
                break
        # ストロボは #MEASURE を一時的に書き換えるので、終わったら元に戻す
        # 必要がある。以前は 4/4 決め打ちで、3/4 等の曲でストロボを入れると
        # そこから拍子が 4/4 に化けていた。カーソル手前の #MEASURE を拾う。
        current_measure = "4/4"
        for line in reversed(lines):
            match = re.search(r"#MEASURE\s+(\d+\s*/\s*\d+)", line)
            if match:
                current_measure = match.group(1).replace(" ", "")
                break
        current_scroll = "1.000"
        for line in reversed(lines):
            match = re.search(r"#SCROLL\s+([0-9.]+)", line)
            if match:
                current_scroll = match.group(1)
                break

        from neotja.dialogs.strobe_dialog import StrobeGeneratorDialog

        def apply(new_text):
            start = self.editor.textCursor().position()
            self.editor.insert_at_cursor(new_text + "\n")
            # プログラム編集なので dirty と自動保存の対象を明示的に立てる。
            self.editor.mark_edited(start, self.editor.textCursor().position())
            self.setWindowModified(True)
            self._force_update()
        StrobeGeneratorDialog(self, current_bpm, apply,
                              restore_measure=current_measure,
                              restore_scroll=current_scroll).exec()

    def open_settings(self):
        from neotja.dialogs.settings_dialog import SettingsDialog
        prev_device = self.config_data.get("audio_output_device", "")
        dlg = SettingsDialog(self)
        if dlg.exec():
            settings_mod.save_settings(self.config_data)
            apply_theme(self.app, self.config_data.get("theme", "dark"))
            self.highlighter.rebuild_formats()
            self.highlighter.rehighlight()
            self.editor.set_mono_font(self.config_data.get("font_family", "Consolas"), self.config_data.get("font_size", 12))
            # 設定のパスをそのまま渡さず、起動時と同じ選び方(System が既定、
            # 「自分で選んだ WAV を使う」がオンのときだけ指定のファイル)を通す。
            self.preview_dock.set_hit_sound_files(
                *settings_mod.effective_hit_sound_paths(self.config_data))
            self.preview_dock.refresh_theme()
            self.start_screen.refresh_theme()
            self.preview_dock.refresh_nameplate()
            self.preview_dock.set_se_text_enabled(self.config_data.get("se_text_enabled", True))
            self._apply_wireless_offset()
            # 出力デバイスを変えたときだけ開き直す(結果はプレビュー側が表示する)。
            new_device = self.config_data.get("audio_output_device", "")
            if new_device != prev_device:
                self.preview_dock.reopen_audio_output(new_device)
            self.roll_speed_spin.setValue(self.config_data.get("roll_speed", 45))
            self.btn_auto_save.blockSignals(True)
            self.btn_auto_save.setChecked(self.config_data.get("auto_save_enabled", False))
            self.btn_auto_save.blockSignals(False)
            self._sync_auto_save_button(self.btn_auto_save.isChecked())
            for k, action in self._run_actions.items():
                if k == "F1":
                    continue  # fixed label - not sourced from run_config
                action.setText(f"{k}: {self.config_data['run_config'][k]['name']}")
            self.editor.gutter.update()
            self.ruler.update()
            self._force_update()
