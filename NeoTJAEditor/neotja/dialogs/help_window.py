from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QListWidget, QSplitter, QTextBrowser, QVBoxLayout

from neotja.constants import VERSION

#: 困ったときの連絡先(利用者の指定 2026-10-31)。
DISCORD_URL = "https://discord.gg/skWXHD33g9"
from neotja.theme import COLORS

# このヘルプ本文が「どのバージョンの仕様に合わせて書かれたか」。
# アプリを更新して仕様を変えたら、ヘルプを追記・修正したうえでこの値も上げること
# (各ページの下部と「このヘルプについて」に表示される)。
HELP_FOR_VERSION = VERSION

CHAPTERS = {
    "はじめに": (
        "NeoTJAEditor は太鼓の達人の譜面(TJA)を作るアプリ。\n\n"
        "作り方は2とおりある。\n"
        "  ・作譜モード … 譜面を見ながらキーで音符を置く(こちらが基本)\n"
        "  ・テキスト   … TJA の数字を直接書く(「TJAを直接書く」の章)\n\n"
        "このヘルプは ① 曲を用意する → ② 基本 → ③ 応用 → ④ 道具 の順に\n"
        "読めば1曲作れるように並べてある。"
    ),
    "① 曲を用意する": (
        "■ 作る\n"
        "  「新規」→ 音源を選ぶ。TITLE: と WAVE: が入った TJA ができる。\n"
        "  YouTube から音源を落として作ることもできる(新規の画面で切替)。\n\n"
        "■ 音源の置き場所\n"
        "  音源は TJA と同じフォルダに置く。WAVE: にはファイル名だけ書く。\n"
        "  音源が無くても譜面は作れる(無音で再生される)。\n\n"
        "■ OFFSET を合わせる\n"
        "  譜面と音源のズレ(秒)。下の「OFFSET: +0.01 / -0.01」で動かす。\n"
        "  音より譜面が早ければ マイナス、遅ければ プラス。\n"
        "  波形の〔OFFSET調整〕を ON にして、波形を直接ドラッグしてもよい。\n\n"
        "■ BPM\n"
        "  〔タップ〕を曲に合わせて叩くと出る。手で BPM: に書いてもよい。"
    ),
    "② 作譜モードの基本": (
        "下のパネルを「作譜」にすると、譜面の上に黄色いカーソルが出る。\n"
        "キーを押すとカーソルの所に音符が置かれる。キーは PeepoDrumKit と同じ。\n\n"
        "■ 音符を置く\n"
        "  F / J   ドン      Alt を足すと 大ドン\n"
        "  D / K   カッ      Alt を足すと 大カッ\n"
        "  R / U   連打      押したまま ←→ で長さ、離した所で置く\n"
        "  E / I   風船      同じ。離すとき Alt で くす玉\n"
        "  G       ゴーゴー  今いる所で 開始 / 終了 が決まる\n\n"
        "  ・置いてもカーソルは進まない。\n"
        "  ・同じ色の上でもう一度押すと消える。違う色なら置き換わる。\n"
        "  ・風船の打数は BALLOON: に自動で書かれる。\n\n"
        "■ 動かす・選ぶ\n"
        "  ← →         カーソルを1つ動かす\n"
        "  ↑ ↓         音符の間隔(1/4〜1/48)を変える\n"
        "  Home / End  曲の頭 / 終わり\n"
        "  クリック     音符を1つ選ぶ。Ctrl+クリックで足し引き\n"
        "  右ドラッグ   四角で囲って選ぶ\n"
        "  右クリック   選んだ音符に使う道具が出る\n"
        "  Delete      選んだものを消す\n"
        "  Ctrl+Z / Y  元に戻す / やり直す\n\n"
        "■ 聴く\n"
        "  F または J  少し前から助走をつけて再生\n"
        "  Space       その場から再生 / 一時停止"
    ),
    "③ 作譜モードの応用": (
        "■ 命令を置く(BPM・スクロール・拍子・小節線・ゴーゴー)\n"
        "  右上の命令パネルから置く。入るのはカーソルの位置。\n"
        "  ・BPM と スクロールは、欄の数字を変えた時点で入る(ボタンは無い)。\n"
        "  ・拍子は値を決めて〔追加〕。その小節の頭に入る。\n"
        "  ・同じ位置に同じ命令があれば、増やさずその行を書き換える。\n\n"
        "■ 置いた命令を直す\n"
        "  行に出ている札をクリックすると、パネルが「変更」に変わる。\n"
        "  値を直すとその行が書き換わる。\n\n"
        "■ スクロールは見た目BPMでも入れられる\n"
        "  見た目BPM = いまのBPM × 倍率。どちらを変えても もう一方が付いてくる。\n"
        "  BPM200 の所で見た目を 400 にすれば #SCROLL 2 が入る。\n\n"
        "■ 拍子を変えると後ろが組み直される\n"
        "  音符は動かない。動くのは小節線だけ。消したときも同じ。\n"
        "  分岐のある譜面では組み直さない(行を置くだけ)。\n\n"
        "■ ゴーゴー・小節線の帯\n"
        "  開始と終了をそれぞれ置く。置いたあとは端をドラッグで動かせる。\n"
        "  音符を選んで〔開始〕を押すと、その範囲をゴーゴーで覆う。"
    ),
    "④ 作譜モードの道具": (
        "音符を選んでから、右クリックまたは命令パネルの〔！〕で使う。\n\n"
        "■ ハイスピ変換\n"
        "  選んだ音符それぞれに #SCROLL を入れて、だんだん速く/遅くする。\n"
        "  「終わりの値」は\n"
        "  　最後の音符に合わせる … 最後の音符が終わりの値になる\n"
        "  　範囲の次に合わせる   … 範囲の次から終わりの値が効く\n\n"
        "■ 小節線割り(拍子)\n"
        "  選んだ音符それぞれに小節線が付くよう、拍子を割り直す。\n\n"
        "■ あべこべ反転 (Ctrl+M)\n"
        "  選んだ範囲のドンとカッを入れ替える。\n\n"
        "■ ノーツ間隔リサイズ\n"
        "  選んだ小節を、指定した分割数で書き直す。\n\n"
        "■ 小節編集\n"
        "  Ctrl+D 複製 / Ctrl+Shift+D 削除 / Ctrl+Shift+N 挿入\n"
        "  Ctrl+Shift+↑↓ 上下へ移動"
    ),
    "⑤ 聴いて確かめる": (
        "■ 再生\n"
        "  F / J       助走つきで再生 / 一時停止\n"
        "  Space       その場から再生 / 一時停止\n"
        "  ← / →       前 / 次の小節へ\n"
        "  Home / End  曲頭 / 最終小節へ\n"
        "  Z / C       再生速度を 遅く / 速く(×0.25〜×1.00)\n"
        "  Tab         等速モード(HS1固定で見る)\n"
        "  P           チェックポイント。P + 小節移動で最寄りへ飛ぶ\n\n"
        "■ 重いとき\n"
        "  レーン左上のモードボタンで「軽量」にする。\n"
        "  表示倍率ボタン(100%→75%→50%→25%)でも軽くなる。\n\n"
        "■ 打音・メトロノーム\n"
        "  下の〔打音〕〔メトロノーム〕で切り替える。音量も下のつまみ。"
    ),
    "⑥ 書き出す": (
        "  Ctrl+S            上書き保存\n"
        "  ファイル → ZIP     TJA と音源をまとめて書き出す\n"
        "  譜面画像生成       譜面を1枚の画像にする\n"
        "  ツール → 動画      譜面を流した動画を書き出す"
    ),
    "TJAを直接書く": (
        "上のテキスト欄に直接書いてもよい。作譜モードと中身は同じ。\n\n"
        "■ 音符\n"
        "  0 休符   1 ドン   2 カッ   3 大ドン   4 大カッ\n"
        "  5 連打   6 大連打  7 風船   8 連打/風船の終わり   9 くす玉\n"
        "  カンマ ( , ) が1小節の終わり。1小節は文字数で等分される。\n\n"
        "■ ヘッダ\n"
        "  TITLE: 曲名   SUBTITLE: サブタイトル   BPM: テンポ\n"
        "  WAVE: 音源名  OFFSET: ズレ(秒)         DEMOSTART: 試聴開始(秒)\n"
        "  COURSE: 難易度(Easy/Normal/Hard/Oni/Edit)  LEVEL: ★の数\n"
        "  BALLOON: 風船の打数(出てくる順にカンマ区切り)\n\n"
        "■ 命令\n"
        "  #START / #END          譜面の始まりと終わり\n"
        "  #BPMCHANGE 180         BPM を変える\n"
        "  #SCROLL 1.5            流れる速さの倍率\n"
        "  #MEASURE 3/4           拍子\n"
        "  #GOGOSTART / #GOGOEND  ゴーゴー\n"
        "  #BARLINEOFF / #BARLINEON  小節線の表示\n"
        "  #DELAY 0.5             そこだけ遅らせる\n"
        "  #BRANCHSTART … #N #E #M … #BRANCHEND   譜面分岐"
    ),
    "ショートカット": (
        "■ エディタ\n"
        "  Ctrl+S 保存   Ctrl+Z 元に戻す   Ctrl+Y やり直す\n"
        "  Ctrl+F 検索   Ctrl+H 置換      Ctrl+M あべこべ反転\n"
        "  Ctrl+D 小節を複製   Ctrl+Shift+D 削除   Ctrl+Shift+N 挿入\n"
        "  Ctrl+Shift+↑↓ 小節を移動   Ctrl+ホイール 文字の大きさ\n"
        "  Alt+B #BPMCHANGE   Alt+S #SCROLL   Alt+U #MEASURE\n"
        "  Alt+G #GOGOSTART   Alt+O #GOGOEND  Alt+R #BRANCHSTART\n\n"
        "■ 作譜・プレビュー\n"
        "  F1 プレビュー起動 / 打音の ON-OFF\n"
        "  F・J ドン(助走再生)  D・K カッ  R・U 連打  E・I 風船  G ゴーゴー\n"
        "  Space 再生 / 一時停止   ←→ 小節移動   ↑↓ 音符の間隔\n"
        "  Home / End 曲頭 / 最終小節   P チェックポイント   Tab 等速モード\n"
        "  Z / C 再生速度"
    ),
    "困ったら": "__link__",
    "このヘルプについて": "__about__",
}


class HelpWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"NeoTJAEditor ヘルプ（v{HELP_FOR_VERSION} 対応）")
        self.resize(720, 520)

        splitter = QSplitter()
        self.list = QListWidget()
        for ch in CHAPTERS:
            self.list.addItem(f"  {ch}")
        splitter.addWidget(self.list)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(lambda url: QDesktopServices.openUrl(url))
        splitter.addWidget(self.browser)
        splitter.setSizes([190, 530])

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
                "<p>うまくいかないとき・不具合・要望は Discord のサーバーで"
                "聞いてください。</p>"
                f'<p><a href="{DISCORD_URL}" style="color:{COLORS["accent"]};">'
                "Discord サーバーを開く</a></p>"
                "<p>開発者への DM でも受け付けています。</p>"
                f'<p><a href="https://x.com/n_enu_taiko" style="color:{COLORS["accent"]};">@n_enu_taiko を開く</a></p>'
            )
        elif body == "__about__":
            html += (
                f"<p>このヘルプは <b>v{HELP_FOR_VERSION}</b> に合わせて書かれています。<br>"
                f"お使いのバージョンは <b>v{VERSION}</b> です。</p>"
            )
            if VERSION != HELP_FOR_VERSION:
                html += (
                    f'<p style="color:{COLORS["accent"]};">'
                    "※ ヘルプとアプリのバージョンが違います。"
                    "新しい機能が載っていないことがあります。</p>"
                )
        else:
            html += "<p>" + body.replace("\n", "<br>") + "</p>"

        # どのページを見ていても、ヘルプの対応バージョンが分かるようにする。
        # 落ちた理由の記録(neotja/crashlog.py)がこの環境で働いているか。
        # 「落ちたのにログが無い」と言われたときに、推測ではなくその場で
        # 確かめられるようにここへ出す。実際、書けていないのに気づけずに
        # 何度も取り逃した。
        from neotja import crashlog as _cl
        if _cl.LOG_PATH is not None:
            rec = f"記録の書き出し先: {_cl.LOG_PATH}"
        elif _cl.INSTALL_ERROR:
            rec = f"記録は無効です（{_cl.INSTALL_ERROR}）"
        else:
            rec = "記録は無効です"
        html += (
            f'<hr><p style="color:{COLORS["fg_dim"]}; font-size:9pt;">'
            f"ヘルプ対応バージョン: v{HELP_FOR_VERSION} ／ 実行中: v{VERSION}"
            f"<br>{rec}</p>"
        )
        self.browser.setHtml(html)
