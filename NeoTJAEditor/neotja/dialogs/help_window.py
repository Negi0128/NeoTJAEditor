from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QSplitter, QTextBrowser, QVBoxLayout

from neotja.theme import COLORS

CHAPTERS = {
    "譜面の仕組み": (
        "TJA形式の譜面は 0〜9 の数字とカンマで構成される。\n\n"
        "0 : 休符    1 : ドン    2 : カッ\n"
        "3 : 大ドン  4 : 大カッ  5 : 連打開始\n"
        "6 : 大連打  7 : 風船    8 : 連打/風船終了\n"
        "9 : 芋音符\n\n"
        "カンマ ( , ) が 1 小節の終わりを表す。"
    ),
    "ショートカット": (
        "Ctrl+S        上書き保存\n"
        "Ctrl+A        全選択\n"
        "Ctrl+T        切り取り\n"
        "Ctrl+Z        元に戻す\n"
        "Ctrl+Y        やり直す\n\n"
        "Alt+P         チェックポイント設定/解除\n"
        "Ctrl+↑/↓      前後のチェックポイントへ移動\n"
        "Ctrl+ホイール  フォントサイズ変更\n"
        "Ctrl+M        あべこべ反転\n\n"
        "Alt+0〜9      カスタムショートカット挿入\n"
        "Alt+B  #BPMCHANGE    Alt+S  #SCROLL\n"
        "Alt+D  #DELAY        Alt+U  #MEASURE\n"
        "Alt+G  #GOGOSTART    Alt+O  #GOGOEND\n"
        "Alt+L  #BARLINEON    Alt+I  #BARLINEOFF\n"
        "Alt+R  #BRANCHSTART"
    ),
    "ツール": (
        "メニュー「ツール」から利用できる。\n\n"
        "■ ハイスピ変換 / リサイズ\n"
        "  選択範囲のノーツ間隔やスクロール速度を変換する。\n\n"
        "■ ストロボ生成\n"
        "  カーソル位置に指定FPS・BPMで静止するギミックを生成する。\n\n"
        "■ あべこべ反転 (Ctrl+M)\n"
        "  ドン(1,3)とカッ(2,4)を入れ替える。"
        "■ 譜面画像生成(試験的)\n"
        "  譜面画像を自動で生成する。"
    ),
    "エラー診断": (
        "■ 行番号の「!」マーク\n"
        "  1小節の文字数が不正な行を警告する。\n\n"
        "■ 背景の黄色ハイライト\n"
        "  #SCROLL が空行やノーツを挟まずに連続して重複している箇所をハイライト。\n\n"
        "■ ステータスバー（画面下部）\n"
        "  風船数やSTART/END、GOGOSTART/ENDの不一致を警告する。"
    ),
    "バグ報告": "__link__",
}


class HelpWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NeoTJAEditor ヘルプ")
        self.resize(680, 480)

        splitter = QSplitter()
        self.list = QListWidget()
        for ch in CHAPTERS:
            self.list.addItem(f"  {ch}")
        splitter.addWidget(self.list)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(lambda url: QDesktopServices.openUrl(url))
        splitter.addWidget(self.browser)
        splitter.setSizes([180, 500])

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self.list.currentRowChanged.connect(self._select)
        self.list.setCurrentRow(0)

    def _select(self, row):
        if row < 0:
            return
        key = list(CHAPTERS.keys())[row]
        body = CHAPTERS[key]

        html = f'<p style="color:{COLORS["accent"]}; font-size:14pt; font-weight:bold;">{key}</p>'
        if body == "__link__":
            html += (
                "<p>NeoTJAEditor をご利用いただきありがとうございます。<br>"
                "バグ報告・要望は開発者NegiのDMにお願いします。</p>"
                f'<p><a href="https://x.com/n_enu_taiko" style="color:{COLORS["accent"]};">@n_enu_taiko を開く</a></p>'
            )
        else:
            html += "<p>" + body.replace("\n", "<br>") + "</p>"
        self.browser.setHtml(html)
