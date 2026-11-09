"""バージョン選択の画面案。通信・実行ファイル操作・設定の読み書きは行わない。

Run: .venv\\Scripts\\python.exe docs/preview_version_launcher.py --capture PATH
取得状況・変更内容は見た目確認用のサンプル。
"""
import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem)


class LauncherPreview(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('NeoTJA Launcher — バージョン選択（画面案）')
        self.resize(720, 420)
        self.setMinimumSize(680, 400)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(20)
        title = QLabel('NeoTJA Launcher')
        title.setObjectName('title')
        root.addWidget(title)
        body = QHBoxLayout()
        body.setSpacing(24)
        left = QVBoxLayout()
        left.setSpacing(12)
        self.versions = QListWidget()
        self.versions.setFixedWidth(260)
        for text in ('v14.8.0   最新版・取得済み', 'v14.7.0   取得済み', 'v14.6.5   未取得'):
            item = QListWidgetItem(text)
            item.setSizeHint(QSize(210, 58))
            self.versions.addItem(item)
        left.addWidget(self.versions)
        refresh = QPushButton('一覧を更新')
        left.addWidget(refresh)
        body.addLayout(left)
        right = QVBoxLayout()
        right.setSpacing(12)
        self.selected = QLabel()
        self.selected.setObjectName('heading')
        right.addWidget(self.selected)
        history = QPushButton('変更履歴')
        right.addWidget(history)
        right.addStretch()
        back = QPushButton('前の版へ戻る  (v14.7.0)')
        back.clicked.connect(lambda: self.versions.setCurrentRow(1))
        right.addWidget(back)
        buttons = QVBoxLayout()
        self.editor = QPushButton('Editorを起動')
        self.editor.setObjectName('primary')
        self.player = QPushButton('Playerを起動')
        for button in (self.editor, self.player):
            button.setMinimumHeight(44)
            buttons.addWidget(button)
        right.addLayout(buttons)
        body.addLayout(right, 1)
        root.addLayout(body, 1)
        self.versions.currentRowChanged.connect(self.select_version)
        self.versions.setCurrentRow(1)

    def select_version(self, index):
        version = ('14.8.0', '14.7.0', '14.6.5')[index]
        self.selected.setText('v' + version)
        downloaded = index != 2
        self.editor.setText('Editorを起動' if downloaded else '取得してEditorを起動')
        self.player.setText('Playerを起動' if downloaded else '取得してPlayerを起動')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', type=Path)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    app.setFont(QFont('Yu Gothic UI', 10))
    app.setStyleSheet("""
        QWidget { background: #0f1117; color: #cdd6f4; }
        QLabel#title { font-size: 27px; font-weight: 600; color: #ffffff; }
        QLabel#heading { font-size: 23px; font-weight: 600; color: #ffffff; }
        QListWidget { background: #161b27; border: 1px solid #2e3a50; border-radius: 6px; padding: 8px; }
        QListWidget::item { padding: 8px; border-radius: 5px; }
        QListWidget::item:selected { background: #2a3f6f; color: white; }
        QPushButton { background: #1e2535; border: 1px solid #3e4c66; border-radius: 5px; padding: 10px 14px; }
        QPushButton:hover { background: #2e3a50; }
        QPushButton#primary { background: #326dc4; border-color: #4f9cf9; color: white; }
    """)
    win = LauncherPreview()
    win.show()
    if args.capture:
        def capture():
            args.capture.parent.mkdir(parents=True, exist_ok=True)
            if not win.grab().save(str(args.capture)):
                raise RuntimeError('画像を保存できませんでした')
            print(str(args.capture), flush=True)
            app.quit()
        QTimer.singleShot(500, capture)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
