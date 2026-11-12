"""ダイアログを起動画面と同じ「ガラスと光」にする共通の道具(利用者の承認
2026-11-12、見本は start_style_S2)。

起動画面(start_screen)と統合画面の右下の段(main_window の BottomRow)が
先にこの見た目になった。ダイアログがテーマの地色に四角い部品を並べたまま
だと、そこだけ前の版の窓に見える。そこで窓にも同じ作りを持ち込む:

  - 地: テーマの地色の上に、やわらかい光(アクセント色)を 1 つ置く。
  - 中身: 半透明のカード(start_screen.GlassCard)にまとめる。
  - 押すもの: 白を薄く重ねたガラス。主役(objectName "accentButton")だけ
    アクセント色の縦グラデーション。
  - 打つもの: ボタンより暗い半透明。フォーカスでアクセント色の縁。

色の数字は theme.glass_colors() に集めてあり(暗い/明るいテーマで
別の数字)、ここでは名前で引くだけ。

使い方は apply_glass(dialog) を呼ぶだけ。窓の中の部品は GlassCard に
入れる(カードの外に置いた部品は光の上に直接乗る)。ほかのダイアログも
同じ呼び出しで同じ見た目にできるよう、ここに切り出してある。

光は窓の paintEvent を差し替えずに、イベントフィルタで Paint のときに
描く。基底クラスを替える必要がないので、既にある QDialog の子孫に
後から付けられる。Qt は Paint イベントを送る前に「いま paintEvent の中」
の印を付けるので、フィルタの中でも QPainter(窓) が使える。描いたあとは
False を返して、窓自身の描画(QSS の背景など)にも進ませる — QSS では
窓を透明にしてあるので、上に何も被さらない。

光は QPixmap に焼いて持ち、大きさかテーマが変わったときだけ描き直す
(BottomRow と同じ理由: 部品が描き直るたびに放射グラデーションを計算し
直さない)。"""

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel

from neotja.start_screen import GlassCard
from neotja.theme import COLORS, glass_colors
from neotja.theme import rgba as _rgba, with_alpha as _c

#: カードの角丸(px)。右下の段のカードと同じ。
CARD_RADIUS = 12
#: カードの中の余白(px)。見本のカードは余白を広めに取っている。
CARD_MARGIN = 16
#: カードの中の行と行のあいだ(px)。
CARD_SPACING = 10
#: 欄の左に置くラベルの最小幅(px)。行ごとに欄の左端がずれないように。
LABEL_MIN_W = 88


class _GlowFilter(QObject):
    """窓の Paint のたびに、焼いておいた光の絵を写す。"""

    def __init__(self, dialog):
        super().__init__(dialog)
        self._glow = None

    def _pixmap(self, w, h):
        key = (w, h, COLORS["bg"], COLORS["accent"])
        if self._glow is not None and self._glow[0] == key:
            return self._glow[1]
        g = glass_colors()
        pm = QPixmap(w, h)
        pm.fill(QColor(COLORS["bg"]))
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        q.setPen(Qt.NoPen)
        # 光は右上に 1 つ。窓は小さいので 2 つ置くと地が明るくなりすぎ、
        # 半透明のカードが地と見分けにくくなる。半径は幅に比例させ、窓の
        # 半分ほどを淡く照らす。
        cx, cy, rad = w * 0.72, h * 0.08, max(240.0, w * 0.55)
        col = COLORS["accent"]
        a = g["glow1_alpha"]
        grad = QRadialGradient(cx, cy, rad)
        grad.setColorAt(0, _c(col, a))
        grad.setColorAt(0.55, _c(col, a * 0.35))
        grad.setColorAt(1, _c(col, 0))
        q.setBrush(QBrush(grad))
        q.drawEllipse(QPointF(cx, cy), rad, rad)
        q.end()
        self._glow = (key, pm)
        return pm

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Paint and obj is self.parent():
            w, h = obj.width(), obj.height()
            if w > 0 and h > 0:
                p = QPainter(obj)
                p.drawPixmap(0, 0, self._pixmap(w, h))
                p.end()
        return False


def glass_qss():
    """ダイアログ 1 つぶんの QSS。窓の setStyleSheet に渡すので、効くのは
    その窓とその子だけ(ほかの画面へは及ばない)。

    QWidget を透明にするのは、地の光の上にカードや段の下地がテーマの
    地色で四角く残らないように。ただし窓から開く別の窓(QMessageBox /
    QMenu / ツールチップ / コンボの一覧)もこの窓の子なので同じ QSS を
    受け継ぐ。それらまで透明にすると中身が抜けて見えるので、塗りを戻す。"""
    c = COLORS
    g = glass_colors()
    grad = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %s, stop:1 %s)"
            % (g["primary_top"].name(), g["primary_bottom"].name()))
    grad_hover = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %s, stop:1 %s)"
                  % (g["primary_top"].lighter(108).name(),
                     g["primary_bottom"].lighter(108).name()))
    return (
        "QWidget {{ background: transparent; color: {fg}; }}"
        # 別の窓として開くものは塗りを戻す(上の説明)。
        "QMessageBox, QFileDialog, QDialog QDialog {{ background-color: {bg}; }}"
        "QMenu {{ background-color: {bg3}; color: {fg}; border: 1px solid {border}; }}"
        "QMenu::item:selected {{ background-color: {accent}; color: {fg_bright}; }}"
        "QToolTip {{ background-color: {bg3}; color: {fg}; border: 1px solid {border}; padding: 4px; }}"
        "QComboBox QAbstractItemView {{ background-color: {surface}; color: {fg};"
        " border: 1px solid {border}; selection-background-color: {select}; }}"
        # ラベル。欄の名前(fieldLabel)は薄く、カードの見出し(cardTitle)は
        # 濃く太く。注釈(dim)は薄い字。
        "QLabel {{ color: {fg}; }}"
        "QLabel#fieldLabel {{ color: {fg_dim}; }}"
        "QLabel#cardTitle {{ color: {fg_bright}; font-size: 14px; font-weight: bold; }}"
        "QLabel#dim {{ color: {fg_dim}; }}"
        "QLabel:disabled {{ color: {fg_dim}; }}"
        # ボタン: ガラス。押し込み(checked)はアクセント色を薄く敷く
        # (新規作成の「空のファイル / URL から」の切り替えに使う)。
        "QPushButton {{ background-color: {btn}; color: {fg};"
        " border: 1px solid {btn_border}; border-radius: 8px; padding: 7px 16px; }}"
        "QPushButton:hover {{ background-color: {btn_hover}; }}"
        "QPushButton:pressed {{ background-color: {fill}; }}"
        "QPushButton:focus {{ border: 1px solid {accent}; }}"
        "QPushButton:disabled {{ color: {fg_dim}; }}"
        "QPushButton:checked {{ background-color: {accent_fill};"
        " border: 1px solid {accent_border}; color: {fg_bright}; }}"
        # 主役のボタン: アクセント色の縦グラデーション。
        "QPushButton#accentButton {{ background-color: {grad}; color: {primary_fg};"
        " border: 1px solid {primary_border}; font-weight: bold; }}"
        "QPushButton#accentButton:hover {{ background-color: {grad_hover}; }}"
        # フォーカスの縁は白。fg_bright だと明るいテーマでは黒になり、青の
        # ボタンが黒く縁取られて重く見える。
        "QPushButton#accentButton:focus {{ border: 1px solid rgba(255, 255, 255, 0.85); }}"
        "QPushButton#accentButton:disabled {{ background-color: {btn}; color: {fg_dim};"
        " border: 1px solid {btn_border}; }}"
        # 字を打つ欄: ボタンより暗い半透明。フォーカスでアクセント色の縁。
        "QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background-color: {field};"
        " color: {fg}; border: 1px solid {field_border}; border-radius: 8px;"
        " padding: 6px 8px; }}"
        "QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus"
        " {{ border: 1px solid {accent}; }}"
        "QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled"
        " {{ color: {fg_dim}; }}"
        "QLineEdit:read-only {{ color: {fg_dim}; }}"
        # スピン/コンボの右の小さなボタン。アプリ全体の QSS は地色(bg2)で
        # 塗るので、半透明の欄の角だけ不透明な四角になる。透明にして縦線
        # だけ残す。矢印の絵(::up-arrow 等)はアプリ全体の QSS のまま。
        "QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border;"
        " subcontrol-position: top right; width: 18px; background: transparent;"
        " border-left: 1px solid {field_border}; border-top-right-radius: 8px; }}"
        "QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border;"
        " subcontrol-position: bottom right; width: 18px; background: transparent;"
        " border-left: 1px solid {field_border}; border-bottom-right-radius: 8px; }}"
        "QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,"
        " QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover"
        " {{ background: {btn_hover}; }}"
        "QComboBox::drop-down {{ subcontrol-origin: border; subcontrol-position: top right;"
        " width: 22px; background: transparent; border-left: 1px solid {field_border};"
        " border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}"
        "QCheckBox {{ color: {fg}; spacing: 6px; }}"
        "QCheckBox:disabled {{ color: {fg_dim}; }}"
        # チェックの四角。上の「QWidget を透明に」で四角の地も抜けて見えなく
        # なるので、欄と同じ地と、薄い字の色の縁で描き直す。入っているときは
        # アクセント色で塗る(印の絵は持たない — 塗りの有無で十分に分かる)。
        "QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px;"
        " background-color: {field}; border: 1px solid {fg_dim}; }}"
        "QCheckBox::indicator:hover {{ border: 1px solid {accent}; }}"
        "QCheckBox::indicator:checked {{ background-color: {accent};"
        " border: 1px solid {accent}; }}"
        "QCheckBox::indicator:disabled {{ background-color: transparent; }}"
        # 進み具合の棒: 欄と同じ地に、主役と同じグラデーションの帯。
        "QProgressBar {{ background-color: {field}; color: {fg}; border: 1px solid {field_border};"
        " border-radius: 7px; min-height: 16px; text-align: center; }}"
        "QProgressBar::chunk {{ background-color: {grad}; border-radius: 6px; }}"
    ).format(bg=c["bg"], bg3=c["bg3"], border=c["border"], fg=c["fg"],
             fg_dim=c["fg_dim"], fg_bright=c["fg_bright"], surface=c["surface"],
             select=c["select"], accent=c["accent"],
             btn=_rgba(g["btn_fill"]), btn_hover=_rgba(g["btn_fill_hover"]),
             btn_border=_rgba(g["btn_border"]), fill=_rgba(g["fill"]),
             field=_rgba(g["field_fill"]), field_border=_rgba(g["field_border"]),
             accent_border=_rgba(g["accent_border"]),
             accent_fill=_rgba(_c(c["accent"], 0.28)),
             grad=grad, grad_hover=grad_hover,
             primary_fg=g["primary_fg"].name(),
             primary_border=_rgba(g["primary_border"]))


def apply_glass(dialog):
    """窓を「ガラスと光」にする。QSS を当て、地の光を描くフィルタを付ける。
    窓の作りはじめ(部品を置く前でも後でも)に 1 回呼ぶ。"""
    dialog.setStyleSheet(glass_qss())
    # 外側の余白はカードが地の光の上に浮いて見える程度に。
    lay = dialog.layout()
    if lay is not None:
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(12)
    dialog.installEventFilter(_GlowFilter(dialog))


def field_label(text):
    """欄の左に置く薄い字のラベル。幅をそろえて欄の左端を縦に合わせる。"""
    lbl = QLabel(text)
    lbl.setObjectName("fieldLabel")
    lbl.setMinimumWidth(LABEL_MIN_W)
    return lbl


def card_title(text):
    """カードの見出し。"""
    lbl = QLabel(text)
    lbl.setObjectName("cardTitle")
    return lbl


def make_card(title=None, note=None):
    """中身を入れるガラスのカードと、その中の格子(QGridLayout)を返す。

    格子の列 0 は欄の名前(field_label)、列 1 は欄。title を渡すと行 0 に
    見出しが入るので、呼ぶ側は行 1 から詰める。note は見出しの右に薄い字で
    添える一言(説明の行を別に取ると窓が縦に伸びるので、見出しと同じ行に
    置く)。見出しの行の QHBoxLayout は card.header で引ける。"""
    card = GlassCard(radius=CARD_RADIUS)
    grid = QGridLayout(card)
    grid.setContentsMargins(CARD_MARGIN, CARD_MARGIN, CARD_MARGIN, CARD_MARGIN)
    grid.setHorizontalSpacing(CARD_SPACING)
    grid.setVerticalSpacing(CARD_SPACING)
    grid.setColumnStretch(1, 1)
    if title:
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(card_title(title))
        if note:
            lbl = QLabel(note)
            lbl.setObjectName("dim")
            head.addWidget(lbl)
        head.addStretch(1)
        grid.addLayout(head, 0, 0, 1, 2)
        # 見出しの行に後から部品を足せるように(例: 新規作成の取得した曲名)。
        card.header = head
    return card, grid
