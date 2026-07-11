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


def build_qss(p: dict) -> str:
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


def apply_theme(app, name: str) -> None:
    palette = THEMES.get(name, THEMES["dark"])
    COLORS.update(palette)
    app.setStyleSheet(build_qss(COLORS))
