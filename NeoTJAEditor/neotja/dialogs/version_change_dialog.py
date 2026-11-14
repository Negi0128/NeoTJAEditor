"""必要なときだけ開く公開バージョン一覧。"""
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtWidgets import (QDialog, QLabel, QPushButton, QLineEdit,
    QListWidget, QListWidgetItem, QTextBrowser, QHBoxLayout, QVBoxLayout)

from neotja.constants import VERSION
from neotja.updater import ASSET_NAME, PLAYER_ASSET_NAME, asset_url, fetch_json, scrub
from neotja.worker_util import detach_worker

API = 'https://api.github.com/repos/Negi0128/NeoTJAEditor/releases'
PAGE_SIZE = 30


class ReleaseListWorker(QThread):
    loaded = Signal(list, bool)
    failed = Signal(str)

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page

    def run(self):
        try:
            # 鍵が積まれていれば使う(非公開リポジトリでも一覧が取れる)。
            releases = fetch_json(API + '?per_page=%d&page=%d' % (PAGE_SIZE, self.page))
            if not isinstance(releases, list):
                raise ValueError('一覧を取得できませんでした')
            self.loaded.emit([r for r in releases if not r.get('draft')],
                             len(releases) == PAGE_SIZE)
        except Exception as exc:
            self.failed.emit(scrub(exc))


class VersionChangeDialog(QDialog):
    def __init__(self, parent=None, auto_load=True):
        super().__init__(parent)
        self.setWindowTitle('バージョン変更')
        self.resize(940, 600)
        self._worker = None
        self._page = 1
        self._more = True
        self._releases = []
        self.selected_release = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        title = QLabel('バージョン変更')
        font = title.font()
        font.setPointSize(17)
        font.setBold(True)
        title.setFont(font)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel('現在  v' + VERSION))
        root.addLayout(header)
        body = QHBoxLayout()
        body.setSpacing(20)
        left = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('バージョンを検索')
        self.search.setFixedWidth(240)
        left.addWidget(self.search)
        self.versions = QListWidget()
        self.versions.setFixedWidth(240)
        left.addWidget(self.versions)
        self.more = QPushButton('さらに表示')
        self.more.clicked.connect(self.load_more)
        left.addWidget(self.more)
        body.addLayout(left)
        right = QVBoxLayout()
        self.selected = QLabel('—')
        self.selected.setFont(font)
        right.addWidget(self.selected)
        right.addWidget(QLabel('変更内容'))
        self.notes = QTextBrowser()
        self.notes.setOpenLinks(False)
        right.addWidget(self.notes, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        root.addWidget(self.status)
        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton('閉じる')
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        self.change = QPushButton('変更')
        self.change.setEnabled(False)
        self.change.clicked.connect(self.accept)
        footer.addWidget(self.change)
        root.addLayout(footer)
        self.versions.currentRowChanged.connect(self._select)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self.load_more)
        # Return in the search box must not accidentally accept a downgrade.
        for button in (close, self.more, self.change):
            button.setAutoDefault(False)
        if auto_load:
            self.load_more()

    def load_more(self):
        if self._worker is not None or not self._more:
            return
        self.more.setEnabled(False)
        self.more.setText('読み込み中…')
        self.status.hide()
        worker = ReleaseListWorker(self._page, self)
        worker.loaded.connect(self._loaded)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self._worker = worker
        worker.start()

    def _loaded(self, releases, more):
        known = {r['tag_name'] for r in self._releases}
        for release in releases:
            tag = release.get('tag_name')
            if not tag or tag in known:
                continue
            known.add(tag)
            self._releases.append(release)
            label = tag
            if tag.lstrip('vV') == VERSION:
                label += '   現在'
            if release.get('prerelease'):
                label += '   試験版'
            item = QListWidgetItem(label)
            item.setSizeHint(QSize(190, 44))
            self.versions.addItem(item)
        self._more = more
        self._page += 1
        self._filter()

    def _filter(self, *_args):
        query = self.search.text().strip().lower()
        first = -1
        for index, release in enumerate(self._releases):
            hidden = query not in release['tag_name'].lower()
            self.versions.item(index).setHidden(hidden)
            if not hidden and first < 0:
                first = index
        current = self.versions.currentRow()
        if current < 0 or self.versions.item(current).isHidden():
            self.versions.setCurrentRow(first)
        # 検索は取得済みの範囲に限定しない。未取得ページも順に確認する。
        if query and self._worker is None and self._more:
            self.load_more()

    def _select(self, index):
        self.selected_release = self._releases[index] if index >= 0 else None
        release = self.selected_release
        if release is None:
            self.selected.setText('—')
            self.notes.clear()
            self.change.setEnabled(False)
            return
        tag = release['tag_name']
        self.selected.setText(tag)
        self.notes.setMarkdown(release.get('body', '').replace('\n', '  \n'))
        names = {a.get('name') for a in release.get('assets', []) if asset_url(a)}
        complete = {ASSET_NAME, PLAYER_ASSET_NAME}.issubset(names)
        self.change.setEnabled(complete and tag.lstrip('vV') != VERSION)
        self.change.setText(tag + ' に変更')
        self.change.setToolTip('' if complete else 'Editor・Playerが揃っていない版です')

    def _failed(self, message):
        self.status.setText('取得失敗：' + message)
        self.status.show()

    def _finished(self):
        self._worker = None
        self.more.setEnabled(self._more)
        self.more.setText('さらに表示' if self._more else 'すべて表示済み')
        # 失敗時には自動再試行しない。
        if self.search.text().strip() and self._more and self.status.isHidden():
            self.load_more()

    def done(self, result):
        worker, self._worker = self._worker, None
        if worker is not None:
            for signal, slot in ((worker.loaded, self._loaded),
                                 (worker.failed, self._failed),
                                 (worker.finished, self._finished)):
                try:
                    signal.disconnect(slot)
                except RuntimeError:
                    pass
            detach_worker(worker)
        super().done(result)
