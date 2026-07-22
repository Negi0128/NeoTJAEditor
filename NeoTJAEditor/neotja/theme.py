import os
import tempfile

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPolygon

THEMES = {
    "dark": {
        "bg":            "#0f1117",
        "bg2":           "#161b27",
        "bg3":           "#1e2535",
        "surface":       "#252d3d",
        "border":        "#2e3a50",
        "accent":        "#4f9cf9",
        "accent2":       "#f97c4f",
        "fg":            "#cdd6f4",
        "fg_dim":        "#6c7a96",
        "fg_bright":     "#ffffff",
        "don":           "#ff6b6b",
        "ka":            "#5bc8fc",
        "roll":          "#ffd166",
        "balloon":       "#ff9f43",
        "kusudama":      "#c77dff",
        "cmd":           "#a29bfe",
        "header_key":    "#74b9ff",
        "header_val":    "#55efc4",
        "comment":       "#4a5568",
        "zero":          "#2e3a50",
        "comma":         "#3d4f6b",
        "cursor":        "#4f9cf9",
        "select":        "#2a3f6f",
        "warn":          "#9d7b00",
        "err":           "#ff6b6b",
        "ok":            "#00b894",
        "checkpoint":    "#ffd166",
        "toolbar_btn":   "#1e2535",
        "toolbar_hover": "#2e3a50",
    },
    "light": {
        "bg":            "#f8f9fa",
        "bg2":           "#e9ecef",
        "bg3":           "#dee2e6",
        "surface":       "#ffffff",
        "border":        "#ced4da",
        "accent":        "#0d6efd",
        "accent2":       "#fd7e14",
        "fg":            "#212529",
        "fg_dim":        "#6c757d",
        "fg_bright":     "#000000",
        "don":           "#dc3545",
        "ka":            "#0dcaf0",
        "roll":          "#ffc107",
        "balloon":       "#fd7e14",
        "kusudama":      "#9c27b0",
        "cmd":           "#6f42c1",
        "header_key":    "#0d6efd",
        "header_val":    "#20c997",
        "comment":       "#adb5bd",
        "zero":          "#ced4da",
        "comma":         "#adb5bd",
        "cursor":        "#0d6efd",
        "select":        "#cfe2ff",
        "warn":          "#ffed4a",
        "err":           "#dc3545",
        "ok":            "#198754",
        "checkpoint":    "#fd7e14",
        "toolbar_btn":   "#ffffff",
        "toolbar_hover": "#e2e6ea",
    },
}

# Mutated in place (not reassigned) so every module that does
# `from neotja.theme import COLORS` sees live updates after apply_theme().
COLORS = THEMES["dark"].copy()


def _arrow_image_url(color_hex: str, up: bool) -> str:
    """テーマ色の小さな三角形(上/下向き)を PNG として一時ファイルに描き出し、
    QSS の image: url() で使える forward-slash 絶対パスを返す。Fusion は
    スピンボックス/コンボの矢印をパレットに依らず暗色で描くため、ダーク背景では
    「黒いブロック」に見える。これを ::up-arrow / ::down-arrow の image で明色の
    三角に上書きする。data URI は QSS の url() で確実にデコードされないため、
    実ファイル参照にしている。PNG はコア機能で描けるので SVG プラグイン非依存。"""
    key = color_hex.lstrip("#").lower()
    name = f"neotja_arrow_{'up' if up else 'down'}_{key}.png"
    path = os.path.join(tempfile.gettempdir(), "neotja_theme", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 2x スケールで描いて縮小し、小さな三角でもエッジを滑らかに。
    scale = 2
    w, h = 9 * scale, 6 * scale
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color_hex))
    if up:
        poly = QPolygon([QPoint(0, h - 1), QPoint(w // 2, 0), QPoint(w - 1, h - 1)])
    else:
        poly = QPolygon([QPoint(0, 0), QPoint(w // 2, h - 1), QPoint(w - 1, 0)])
    painter.drawPolygon(poly)
    painter.end()
    try:
        img.save(path, "PNG")
    except Exception:
        pass
    return path.replace("\\", "/")


def build_qss(p: dict) -> str:
    up_arrow = _arrow_image_url(p["fg"], up=True)
    down_arrow = _arrow_image_url(p["fg"], up=False)
    return f"""
    QMainWindow, QWidget {{
        background-color: {p['bg']};
        color: {p['fg']};
        selection-background-color: {p['select']};
    }}
    QToolBar {{
        background-color: {p['bg2']};
        border: none;
        border-bottom: 1px solid {p['border']};
        spacing: 4px;
        padding: 4px;
    }}
    QToolBar QToolButton, QPushButton {{
        background-color: {p['toolbar_btn']};
        color: {p['fg']};
        border: 1px solid {p['border']};
        border-radius: 4px;
        padding: 6px 12px;
    }}
    QToolBar QToolButton:hover, QPushButton:hover {{
        background-color: {p['toolbar_hover']};
    }}
    QPushButton:disabled {{
        color: {p['fg_dim']};
    }}
    QPushButton#accentButton {{
        background-color: {p['accent']};
        color: {p['fg_bright']};
        border: none;
        font-weight: bold;
    }}
    QPushButton#accentButton:hover {{
        background-color: {p['accent2']};
    }}
    QPushButton#dangerButton {{
        background-color: {p['err']};
        color: #ffffff;
        border: none;
    }}
    QScrollBar:vertical {{
        background: {p['bg2']};
        width: 14px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {p['border']};
        min-height: 24px;
        border-radius: 6px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p['accent']};
    }}
    QScrollBar:horizontal {{
        background: {p['bg2']};
        height: 14px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: {p['border']};
        min-width: 24px;
        border-radius: 6px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {p['accent']};
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0px;
        height: 0px;
        border: none;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{
        background: none;
    }}
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        background-color: {p['surface']};
        color: {p['fg']};
        border: 1px solid {p['border']};
        border-radius: 4px;
        padding: 4px;
    }}
    /* スピンボックス上下ボタン/コンボのドロップダウン。本体(::up-button 等)を
       QSS で明示宣言してサブコントロール描画を QSS 側に握らせ、その上で矢印
       (::up-arrow 等)にテーマ色の画像を割り当てる。本体を宣言しないと矢印画像は
       無視され、Fusion がパレット非依存の暗色矢印(=黒いブロック)を描いてしまう。
       subcontrol-position を明示することで当たり判定も安定する(上ボタンが
       押せない不具合の対策)。 */
    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 18px;
        border-left: 1px solid {p['border']};
        border-top-right-radius: 4px;
        background: {p['bg2']};
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 18px;
        border-left: 1px solid {p['border']};
        border-bottom-right-radius: 4px;
        background: {p['bg2']};
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background: {p['toolbar_hover']};
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: url("{up_arrow}");
        width: 9px;
        height: 6px;
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow,
    QComboBox::down-arrow {{
        image: url("{down_arrow}");
        width: 9px;
        height: 6px;
    }}
    QComboBox::drop-down {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 20px;
        border-left: 1px solid {p['border']};
        border-top-right-radius: 4px;
        border-bottom-right-radius: 4px;
        background: {p['bg2']};
    }}
    QPlainTextEdit:focus, QTextEdit:focus {{
        border: 2px solid {p['accent']};
    }}
    QComboBox QAbstractItemView {{
        background-color: {p['surface']};
        color: {p['fg']};
        selection-background-color: {p['accent']};
        selection-color: {p['fg_bright']};
    }}
    QMenuBar {{
        background-color: {p['bg3']};
        color: {p['fg']};
    }}
    QMenuBar::item:selected {{
        background-color: {p['accent']};
        color: {p['fg_bright']};
    }}
    QMenu {{
        background-color: {p['bg3']};
        color: {p['fg']};
        border: 1px solid {p['border']};
    }}
    QMenu::item:selected {{
        background-color: {p['accent']};
        color: {p['fg_bright']};
    }}
    QStatusBar {{
        background-color: {p['bg3']};
        color: {p['fg_dim']};
    }}
    QTabWidget::pane {{
        border: 1px solid {p['border']};
        background: {p['bg']};
    }}
    QTabBar::tab {{
        background: {p['bg2']};
        color: {p['fg_dim']};
        padding: 6px 14px;
        border: 1px solid {p['border']};
        border-bottom: none;
    }}
    QTabBar::tab:selected {{
        background: {p['bg']};
        color: {p['fg']};
    }}
    QListWidget, QTextBrowser {{
        background-color: {p['surface']};
        color: {p['fg']};
        border: 1px solid {p['border']};
    }}
    QListWidget::item:selected {{
        background-color: {p['accent']};
        color: {p['fg_bright']};
    }}
    QCheckBox, QLabel {{
        color: {p['fg']};
    }}
    QGroupBox {{
        border: 1px solid {p['border']};
        border-radius: 4px;
        margin-top: 8px;
        padding-top: 8px;
        color: {p['fg']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 8px;
        color: {p['fg_dim']};
    }}
    QToolTip {{
        background-color: {p['bg3']};
        color: {p['fg']};
        border: 1px solid {p['border']};
        padding: 4px;
    }}
    QSplitter::handle {{
        background-color: {p['border']};
    }}
    """


# Bumped on every apply_theme. COLORS is mutated in place (so importers see
# live updates), which means anything caching values derived from it - QColor
# objects, pens, baked stylesheet strings - has no way to notice the change on
# its own. Such caches should compare against this and rebuild when it moves.
# Read it as `theme.GENERATION`, never `from theme import GENERATION` - the
# latter binds the int at import time and never sees a bump.
GENERATION = 0


def _build_qpalette(c: dict) -> QPalette:
    """テーマ色から QPalette を組む。Fusion が自前で描くサブコントロール
    (スピンボックスの矢印、チェックボックス、選択色、無効表示など)を
    ダーク/ライトの現行テーマに一致させるため。QSS が覆う面はそのまま QSS が
    優先されるので、ここは主にネイティブ描画部分に効く。"""
    def C(k):
        return QColor(c[k])
    pal = QPalette()
    pal.setColor(QPalette.Window, C("bg"))
    pal.setColor(QPalette.WindowText, C("fg"))
    pal.setColor(QPalette.Base, C("surface"))
    pal.setColor(QPalette.AlternateBase, C("bg2"))
    pal.setColor(QPalette.ToolTipBase, C("surface"))
    pal.setColor(QPalette.ToolTipText, C("fg"))
    pal.setColor(QPalette.PlaceholderText, C("fg_dim"))
    pal.setColor(QPalette.Text, C("fg"))
    pal.setColor(QPalette.Button, C("bg2"))
    pal.setColor(QPalette.ButtonText, C("fg"))
    pal.setColor(QPalette.BrightText, C("fg_bright"))
    pal.setColor(QPalette.Highlight, C("accent"))
    pal.setColor(QPalette.HighlightedText, C("fg_bright"))
    pal.setColor(QPalette.Link, C("accent"))
    dim = C("fg_dim")
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, dim)
    return pal


_FUSION_APPLIED = False


def apply_theme(app, name: str) -> None:
    global GENERATION, _FUSION_APPLIED
    # OS 既定スタイル(Windows では windowsvista)に QSS を被せると、QSpinBox /
    # QComboBox のように上下ボタンやドロップダウンを持つウィジェットで、
    # サブコントロールの当たり判定・矢印が壊れる(上ボタンが押せない等)。
    # Fusion は QSS を完全に尊重し、これらのサブコントロールも正しく描画・
    # クリックできるので Fusion へ切り替える。setStyleSheet を通すと style は
    # QStyleSheetStyle プロキシに包まれて種別判定できなくなるため、初回だけ
    # 設定するフラグで管理する(テーマ切替のたびに style を作り直さない)。
    if not _FUSION_APPLIED:
        try:
            app.setStyle("Fusion")
            _FUSION_APPLIED = True
        except Exception:
            pass
    palette = THEMES.get(name, THEMES["dark"])
    COLORS.update(palette)
    GENERATION += 1
    try:
        app.setPalette(_build_qpalette(COLORS))
    except Exception:
        pass
    app.setStyleSheet(build_qss(COLORS))
