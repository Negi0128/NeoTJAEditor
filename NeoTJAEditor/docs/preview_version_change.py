"""バージョン選択の画面案。通信・実行ファイル操作・設定の読み書きは行わない。

Run: .venv\\Scripts\\python.exe docs/preview_version_change.py --releases JSON --capture PATH
現在の版はサンプル。変更内容は指定した公開リリース一覧JSONから表示する。
"""
import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QLineEdit, QTextBrowser)


class VersionChangePreview(QWidget):
    def __init__(self, releases):
        super().__init__()
        self.releases = releases
        self.setWindowTitle('バージョン変更（画面案）')
        self.resize(940, 600)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        title = QLabel('バージョン変更')
        title.setObjectName('heading')
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel('現在  v14.9.0'))
        root.addLayout(header)
        body = QHBoxLayout()
        body.setSpacing(20)
        left = QVBoxLayout()
        search = QLineEdit()
        search.setPlaceholderText('バージョンを検索')
        search.setFixedWidth(240)
        left.addWidget(search)
        self.versions = QListWidget()
        self.versions.setFixedWidth(240)
        for i, release in enumerate(releases):
            text = release['tag_name'] + ('   最新' if i == 0 else '')
            item = QListWidgetItem(text)
            item.setSizeHint(QSize(190, 44))
            self.versions.addItem(item)
        left.addWidget(self.versions)
        left.addWidget(QPushButton('さらに表示'))
        body.addLayout(left)
        right = QVBoxLayout()
        self.selected = QLabel()
        self.selected.setObjectName('heading')
        right.addWidget(self.selected)
        right.addWidget(QLabel('変更内容'))
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(False)
        right.addWidget(self.notes, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)
        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton('閉じる')
        close.clicked.connect(self.close)
        footer.addWidget(close)
        self.change = QPushButton()
        self.change.setObjectName('primary')
        footer.addWidget(self.change)
        root.addLayout(footer)
        self.versions.currentRowChanged.connect(self.select_version)
        search.textChanged.connect(self.filter_versions)
        self.versions.setCurrentRow(0)

    def select_version(self, index):
        if index < 0:
            return
        release = self.releases[index]
        self.selected.setText(release['tag_name'])
        self.notes.setMarkdown(release.get('body', '').replace('\n', '  \n'))
        self.change.setText(release['tag_name'] + ' に変更')

    def filter_versions(self, query):
        for i, release in enumerate(self.releases):
            self.versions.item(i).setHidden(query.lower() not in release['tag_name'].lower())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--releases', type=Path, required=True)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    app.setFont(QFont('Yu Gothic UI', 10))
    app.setStyleSheet("""
        QWidget { background: #0f1117; color: #cdd6f4; }
        QLabel#title { font-size: 27px; font-weight: 600; color: #ffffff; }
        QLabel#heading { font-size: 23px; font-weight: 600; color: #ffffff; }
        QListWidget, QTextBrowser, QLineEdit { background: #161b27; border: 1px solid #2e3a50; border-radius: 6px; padding: 8px; }
        QListWidget::item { padding: 8px; border-radius: 5px; }
        QListWidget::item:selected { background: #2a3f6f; color: white; }
        QPushButton { background: #1e2535; border: 1px solid #3e4c66; border-radius: 5px; padding: 10px 14px; }
        QPushButton:hover { background: #2e3a50; }
        QPushButton#primary { background: #326dc4; border-color: #4f9cf9; color: white; }
    """)
    releases = json.loads(args.releases.read_text(encoding='utf-8'))
    win = VersionChangePreview(releases)
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
