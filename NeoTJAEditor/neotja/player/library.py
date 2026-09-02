"""Player の曲一覧。「どれを再生するか」を選ぶだけの画面。

覚えているフォルダを再帰的に走査して .tja を集め、表に並べる。選ばれたら
chartChosen で知らせるところまでが仕事で、再生そのものには関わらない
(再生は PreviewDock の再生ウィンドウが持っている)。

走査は必ずワーカースレッドで行う。数百譜面のフォルダだと、列挙だけなら
一瞬でも「1譜面ずつ読んで解析する」ところで数秒かかる。GUI スレッドで
やると、その間ウィンドウが完全に無反応になる(描画も入力も止まる)。
AnalysisWorker と同じく、ここでも計算だけをスレッドでやり、ウィジェットに
触るのは GUI スレッド側だけ、という境界を厳守する。
"""

import os
import time

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from neotja.tja_analyzer import TJACourseAnalyzer
from neotja.worker_util import detach_worker

#: 1回のシグナルでまとめて送る件数。1譜面ごとに emit すると、数百譜面では
#: キュー接続の往復だけで GUI スレッドが忙しくなり、かえって固まる。
_BATCH = 20

#: バッチが埋まらなくても、これだけ経ったら送る。譜面数が少ないときに
#: 「走査は終わっているのに表が空のまま」にならないように。
_BATCH_SEC = 0.2


def _read_text(path):
    """TJA を読む。cp932 の譜面が多いので、UTF-8 で読めなければそちらへ。

    player/core.py にも同じものがあるが、あちらを import すると PreviewDock
    (= 音声エンジン一式)まで芋づるで読み込まれる。一覧を出すだけの画面に
    音声デバイスを開かせたくないので、ここでは独立に持つ。
    """
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # 全滅。壊れた譜面でも一覧には出したいので、読めない文字は潰して返す。
    return raw.decode("utf-8", errors="replace")


def _header_value(content, key):
    """ヘッダ(例 TITLE:)の最初の値を返す。無ければ空文字。"""
    prefix = key.upper() + ":"
    for line in content.splitlines():
        t = line.strip()
        if t.upper().startswith(prefix):
            return t[len(prefix):].strip()
    return ""


def _levels_by_course(content, analyzer):
    """コースキー -> LEVEL の対応を作る。

    レベルだけは parse_courses が返してくれない(実際に流して確認した戻り値の
    キーは key/label/color/data/notes/don_count/ka_count/measures/time/
    rolls_info/balloons_info で、level は無い)。level を持っているのは
    build_preview_timeline だが、あれはコース1つ分のタイムラインを丸ごと
    組み立てるので、一覧のために全譜面×全コースで回す代物ではない。
    そこでヘッダ行だけを拾う。COURSE: の解釈は analyzer.DIFF をそのまま
    使い、表記ゆれ(0/Easy など)の扱いが解析器とずれないようにしている。

    同じコースが2回出てくる譜面では最初のものを採る(後段の分岐用ダミー等で
    上書きされないように)。
    """
    levels = {}
    cur = "Oni"
    for raw in content.splitlines():
        s = raw.split("//")[0].strip()
        if s.upper().startswith("COURSE:"):
            cur = analyzer.DIFF.get(s[7:].strip(), "Oni")
        elif s.upper().startswith("LEVEL:"):
            try:
                levels.setdefault(cur, int(s[6:].strip()))
            except ValueError:
                # LEVEL が空、あるいは「9★」のような自由記述。レベル表示が
                # 空欄になるだけなので黙って捨てる。
                pass
    return levels


def _scan_one(path, analyzer):
    """譜面1つ分の一覧エントリを作る。読めない譜面は None。"""
    try:
        content = _read_text(path)
    except OSError:
        # 消えた・権限が無い。一覧から抜けるだけで、他の譜面には影響しない。
        return None

    subtitle = _header_value(content, "SUBTITLE")
    # SUBTITLE の先頭 "--" は「字幕として出すな」の印で、中身ではない。
    if subtitle.startswith("--"):
        subtitle = subtitle[2:].strip()

    try:
        courses = analyzer.parse_courses(content)
    except Exception:  # noqa: BLE001
        # 壊れた譜面で解析器が転んでも、曲名だけは一覧に出したい。
        courses = []
    levels = _levels_by_course(content, analyzer)

    # 表示順は解析器と同じランク順(裏 > おに > むずかしい > ふつう > かんたん)。
    courses = sorted(courses,
                     key=lambda c: analyzer.DIFF_RANK.get(c.get("key"), -1),
                     reverse=True)
    parts = []
    for c in courses:
        lv = levels.get(c.get("key"))
        label = c.get("label") or c.get("key") or ""
        parts.append("%s(%s)" % (label, lv) if lv is not None else label)

    return {
        "path": path,
        "title": _header_value(content, "TITLE") or os.path.basename(path),
        "subtitle": subtitle,
        "courses": " / ".join(parts),
        # ダブルクリック時に渡すコース。一番上(通常はおに)を既定にする。
        "course_key": courses[0].get("key", "") if courses else "",
        "filename": os.path.basename(path),
    }


class _ScanWorker(QThread):
    """フォルダを歩いて .tja を集め、1つずつ解析して送り返すワーカー。

    ウィジェットには一切触らない。結果は found シグナルで GUI スレッドへ渡す。
    """

    found = Signal(object)   # エントリ dict のリスト
    done = Signal(int)       # 走査できた譜面数

    def __init__(self, folders, config_data, parent=None):
        super().__init__(parent)
        self._folders = list(folders)
        # 解析器は設定を読むだけで状態を持たないので、スレッド内で作って良い。
        self._analyzer = TJACourseAnalyzer(config_data)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        paths = self._collect()
        batch = []
        last = time.monotonic()
        count = 0
        for p in paths:
            if self._cancelled:
                return
            entry = _scan_one(p, self._analyzer)
            if entry is not None:
                batch.append(entry)
                count += 1
            now = time.monotonic()
            if len(batch) >= _BATCH or (batch and now - last >= _BATCH_SEC):
                self.found.emit(batch)
                batch = []
                last = now
        if batch and not self._cancelled:
            self.found.emit(batch)
        if not self._cancelled:
            self.done.emit(count)

    def _collect(self):
        """フォルダを再帰的に歩いて .tja のパスを集める(重複は除く)。"""
        seen = set()
        out = []
        for folder in self._folders:
            if self._cancelled:
                return out
            for root, _dirs, files in os.walk(folder):
                if self._cancelled:
                    return out
                for name in sorted(files):
                    if not name.lower().endswith(".tja"):
                        continue
                    full = os.path.normpath(os.path.join(root, name))
                    key = full.lower()
                    if key in seen:
                        # 同じフォルダを親子で登録された場合。
                        continue
                    seen.add(key)
                    out.append(full)
        return out


class LibraryPage(QWidget):
    """覚えたフォルダの譜面を並べて、再生するものを選ばせる画面。"""

    chartChosen = Signal(str, str)   # (tja のパス, コースキー or "")

    COLUMNS = ("曲名", "サブタイトル", "コース(レベル)", "ファイル名")

    def __init__(self, config_data, save_cb, parent=None):
        super().__init__(parent)
        self.cfg = config_data
        # 設定ファイルの書き方(どのキーだけ書き戻すか)は呼び出し側の都合。
        # ここは「保存して」と頼むだけにして、settings には触らない。
        self._save_cb = save_cb
        self._entries = []       # 走査で得た全エントリ(絞り込み前)
        self._rows = []          # 現在表を占めているエントリ(行番号と対応)
        self._worker = None

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("曲名・サブタイトル・ファイル名で絞り込み")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search, 1)
        b_add = QPushButton("フォルダを追加")
        b_add.clicked.connect(self.add_folder)
        top.addWidget(b_add)
        self.b_remove = QPushButton("フォルダを外す")
        self.b_remove.clicked.connect(self.remove_folder)
        top.addWidget(self.b_remove)
        b_reload = QPushButton("再読み込み")
        b_reload.clicked.connect(self.refresh)
        top.addWidget(b_reload)
        v.addLayout(top)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        # 一覧は選ぶためのもので、編集させない(誤ってヘッダを書き換えたように
        # 見えると混乱するだけ。譜面ファイルには何も書き戻さない)。
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        # 並べ替えの向きを明示しておく。指定しないと Qt が持っている既定の
        # ソート指標(列0・降順)で勝手に並び、走査順とも五十音順とも違う
        # 「なぜこの順?」という並びで最初の一覧が出てしまう。
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.itemDoubleClicked.connect(lambda _item: self.play_selected())
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        head.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        v.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        bottom.addWidget(self.status, 1)
        self.b_play = QPushButton("再生")
        self.b_play.clicked.connect(self.play_selected)
        bottom.addWidget(self.b_play)
        v.addLayout(bottom)

        self.setAcceptDrops(True)
        self.refresh()

    # ------------------------------------------------------------------
    # フォルダの出し入れ
    def folders(self):
        """覚えているフォルダ。設定が壊れていても落ちないように正規化する。"""
        raw = self.cfg.get("player_folders")
        if not isinstance(raw, list):
            return []
        return [p for p in raw if isinstance(p, str) and p]

    def _set_folders(self, folders):
        self.cfg["player_folders"] = list(folders)
        try:
            self._save_cb()
        except Exception:  # noqa: BLE001
            # 保存に失敗しても、このセッションの一覧は使える。落とさない。
            pass

    def add_folder(self):
        start = ""
        cur = self.folders()
        if cur:
            start = cur[-1]
        path = QFileDialog.getExistingDirectory(self, "譜面フォルダを追加", start)
        if path:
            self.add_folders([path])

    def add_folders(self, paths):
        """フォルダを追加して走査し直す。既にあるものは無視。"""
        folders = self.folders()
        lower = {p.lower() for p in folders}
        added = False
        for p in paths:
            p = os.path.normpath(p)
            if not os.path.isdir(p) or p.lower() in lower:
                continue
            folders.append(p)
            lower.add(p.lower())
            added = True
        if added:
            self._set_folders(folders)
            self.refresh()

    def remove_folder(self):
        """選択中の譜面が属するフォルダを一覧から外す。

        フォルダの一覧を別ウィジェットで見せるほどの物ではないので、
        「今選んでいる曲の入っているフォルダ」を外す形にしている。
        親と子を両方登録していた場合は両方外れる — 「もうこの曲を出すな」が
        利用者の意図なので、片方だけ残して曲が消えないほうが驚かれる。
        """
        entry = self._selected_entry()
        if entry is None:
            return
        path = entry["path"].lower()
        folders = self.folders()
        keep = [f for f in folders
                if not path.startswith(os.path.normpath(f).lower() + os.sep)]
        if len(keep) != len(folders):
            self._set_folders(keep)
            self.refresh()

    # ------------------------------------------------------------------
    # 走査
    def refresh(self):
        """フォルダを走査し直す。走査中に呼ばれたら古い方は捨てる。"""
        # 走っているワーカーの参照をここで上書きすると
        # "QThread: Destroyed while thread is still running" で落ちる。
        # 待機所へ預けて、自分で終わるまで生かしておく。
        detach_worker(self._worker)
        self._worker = None

        self._entries = []
        self._fill([])
        folders = self.folders()
        if not folders:
            self.status.setText("フォルダが登録されていません。"
                                "「フォルダを追加」か、ここへドロップしてください。")
            return
        self.status.setText("走査中...")
        w = _ScanWorker(folders, self.cfg, parent=self)
        w.found.connect(self._on_found)
        w.done.connect(self._on_done)
        self._worker = w
        w.start()

    def _on_found(self, batch):
        self._entries.extend(batch)
        self.status.setText("走査中... %d件" % len(self._entries))
        self._apply_filter()

    def _on_done(self, count):
        self._worker = None
        self.status.setText("%d件" % count)
        self._apply_filter()

    # ------------------------------------------------------------------
    # 表示
    def _apply_filter(self):
        q = self.search.text().strip().lower()
        if not q:
            rows = list(self._entries)
        else:
            rows = [e for e in self._entries
                    if q in e["title"].lower()
                    or q in e["subtitle"].lower()
                    or q in e["filename"].lower()]
        self._fill(rows)

    def _fill(self, rows):
        """表を作り直す。行番号 -> エントリの対応は self._rows で持つ。

        並べ替えを有効にしたまま setItem すると、挿入の途中で行が動いて
        中身が混ざる。埋め終わるまで切っておく。
        """
        keep = self._selected_path()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self._rows = list(rows)
        self.table.setRowCount(len(rows))
        for r, e in enumerate(rows):
            for c, text in enumerate((e["title"], e["subtitle"],
                                      e["courses"], e["filename"])):
                item = QTableWidgetItem(text)
                item.setToolTip(e["path"])
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        if keep:
            self._select_path(keep)

    def _selected_path(self):
        e = self._selected_entry()
        return e["path"] if e else ""

    def _select_path(self, path):
        """走査の続きが流れ込んでも選択を見失わないように選び直す。"""
        for r in range(self.table.rowCount()):
            e = self._entry_at(r)
            if e and e["path"] == path:
                self.table.selectRow(r)
                return

    def _entry_at(self, row):
        """表示上の行 -> エントリ。並べ替え後は行番号がずれるので、
        1列目のアイテムに紐づけた元の並び順で引き直す。"""
        item = self.table.item(row, 0)
        if item is None:
            return None
        # 並べ替えでアイテムごと動くので、パス(ツールチップ)で引く。
        path = item.toolTip()
        for e in self._rows:
            if e["path"] == path:
                return e
        return None

    def _selected_entry(self):
        rows = self.table.selectionModel().selectedRows() \
            if self.table.selectionModel() else []
        if not rows:
            return None
        return self._entry_at(rows[0].row())

    def play_selected(self):
        e = self._selected_entry()
        if e is not None:
            self.chartChosen.emit(e["path"], e.get("course_key", ""))

    # ------------------------------------------------------------------
    # ドラッグ&ドロップ
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if not p:
                continue
            if os.path.isdir(p):
                paths.append(p)
            elif p.lower().endswith(".tja"):
                # 譜面そのものを落とされたら、その入れ物を覚える。
                # 覚えるのはあくまでフォルダ(player_folders)なので。
                paths.append(os.path.dirname(p))
        if paths:
            self.add_folders(paths)
            event.acceptProposedAction()

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        # 走査中に画面を閉じられても落ちないよう、ワーカーを切り離す。
        detach_worker(self._worker)
        self._worker = None
        super().closeEvent(event)
