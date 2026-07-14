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
    "えぬいーさん次郎（プレビュー）": (
        "えぬいーさん次郎は、譜面を確認するための独立したプレビュープレイヤーです。\n\n"
        "■ まず覚える3つの用語\n"
        "  0小節目     曲の先頭（音源の開始位置）。プレビューを開くと最初はここに位置する。\n"
        "  カレント小節  いま選んでいる小節線。PgUp/PgDn やホイールで移動する。\n"
        "  アンカー     Space で再生を始めた小節。Q キーで戻れる基準点。\n\n"
        "■ キー操作（プレビューのレーンにフォーカスしている時）\n"
        "  Space\n"
        "    停止中・一時停止中に押すと、カレント小節から再生する。\n"
        "    （最初に再生した小節が、以後 Q で戻るアンカーとして記憶される）\n"
        "    再生中に押すと、その場で一時停止する。\n"
        "    一時停止中にもう一度押すと、今いる小節の頭から再生を再開する。\n\n"
        "  Q\n"
        "    アンカー小節へ戻って一時停止する。\n"
        "    （プレビューを開いた直後、まだ一度も再生していない場合は 0小節目 へ戻る）\n\n"
        "  PgUp / PgDn（またはマウスホイール）\n"
        "    カレント小節を次/前へ移動する（画面がスクロールして表示が動く）。\n"
        "    ※再生中は移動できない。一時停止中のみ有効。\n"
        "    一時停止中に移動すると、アンカーもその移動先の小節に更新される。\n\n"
        "  Tab\n"
        "    下部パネルの表示モードを「情報 → 作譜 → 非表示」の順に切り替える。\n\n"
        "  [ / ]\n"
        "    作譜モードの再生速度を下げる/上げる（0.25〜1.00倍）。\n\n"
        "■ 補足\n"
        "  曲の最後まで再生すると、末尾で自動的に停止する。\n"
        "  以前あった「Qでカーソル（編集中の位置）へ同期」する機能は廃止された。\n"
        "  現在の Q は、上記のとおり「アンカー小節へ戻る」動作になっている。"
    ),
    "下部パネルのモード": (
        "プレビュー画面下部のパネルには3つの表示モードがある。\n"
        "Tabキー、または下部/レーン隅のモード切替ボタンで順に切り替えられる。\n\n"
        "■ 情報モード\n"
        "  BPM・SCROLL・現在の小節番号・コンボ数・連打数などの情報を表示する。\n\n"
        "■ 作譜モード\n"
        "  情報表示のかわりに波形を表示する。\n"
        "  再生速度スライダー（0.25〜1.00倍）を操作できる（[ / ] キーでも調整可能）。\n"
        "  ※速度を落とすと、それに合わせて音程も下がる（仕様）。\n\n"
        "■ 非表示モード\n"
        "  情報も波形も表示せず、レーンだけを表示する。"
    ),
    "F1キー": (
        "F1キーはアプリ全体（エディタ画面）で有効なショートカット。\n\n"
        "  プレビュー未起動のとき  ： えぬいーさん次郎（内蔵プレビュー）を起動する。\n"
        "  プレビュー起動中のとき  ： 打音（ドン/カツ）のON/OFFを切り替える。\n\n"
        "※外部シミュレータの起動は F2 / F3 キーに割り当てられている（F1とは別機能）。"
    ),
    "打音の音源設定": (
        "■ 既定の音\n"
        "  何も設定していない場合、内蔵の合成ドン/カツ音が鳴る（追加の準備は不要）。\n\n"
        "■ 自分の音源に差し替える\n"
        "  「設定 → 環境設定」の「エディタ・ツール」タブに「ドン音源」「カツ音源」の\n"
        "  設定項目がある。ここに任意のWAVファイルを指定すると、その音が使われる。\n"
        "  片方だけ指定した場合、未指定の側は内蔵音のまま鳴る。\n\n"
        "■ 注意\n"
        "  設定はファイルパスとして保存される。別のPCで使う場合は、そのPCでも\n"
        "  改めてパスを指定し直す必要がある（パスは自動で共有されない）。"
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
        "Alt+R  #BRANCHSTART\n\n"
        "■ プレビュー（えぬいーさん次郎）\n"
        "F1            プレビュー起動 / 打音ON-OFF切替\n"
        "F2 / F3        外部シミュレータ起動\n"
        "Space         プレビュー再生 / 一時停止\n"
        "Q            アンカー小節へ戻る\n"
        "PgUp/PgDn      カレント小節を移動（一時停止中のみ）\n"
        "Tab           下部パネルのモード切替（情報→作譜→非表示）\n"
        "[ / ]         作譜モードの再生速度を変更"
    ),
    "ツール": (
        "メニュー「ツール」から利用できる。\n\n"
        "■ ハイスピ変換 / リサイズ\n"
        "  選択範囲のノーツ間隔やスクロール速度を変換する。\n\n"
        "■ ストロボ生成\n"
        "  カーソル位置に指定FPS・BPMで静止するギミックを生成する。\n\n"
        "■ あべこべ反転 (Ctrl+M)\n"
        "  ドン(1,3)とカッ(2,4)を入れ替える。\n\n"
        "■ 譜面画像生成\n"
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
