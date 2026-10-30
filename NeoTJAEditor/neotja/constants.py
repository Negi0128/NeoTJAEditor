import datetime
from decimal import getcontext

APP_NAME = "NeoTJAEditor"
VERSION = "14.5.1"

getcontext().prec = 50

NEW_FILE_TEMPLATE = """\
TITLE:
SUBTITLE:--
BPM:120
WAVE:
OFFSET:0.00
SONGVOL:100
SEVOL:100
DEMOSTART:0.00

COURSE:oni
LEVEL:10
BALLOON:
SCOREINIT:
SCOREDIFF:

#START
1111,
1111,
1111,
1111,
#END
"""

def new_file_text(title: str = "") -> str:
    """新しい譜面の本文。**1行目に作った日の印**を入れる
    (利用者の指定 2026-10-30)。

    ひな形そのもの(NEW_FILE_TEMPLATE)は日付を持てないので、
    作るたびにここで頭へ付ける。web 版(assets/mobile.html)の
    新規作成も同じ形の行を書く。"""
    stamp = datetime.date.today().strftime("%Y/%m/%d")
    text = f"// {APP_NAME} {stamp}\n" + NEW_FILE_TEMPLATE
    if title:
        text = text.replace("TITLE:\n", f"TITLE:{title}\n", 1)
    return text


VALID_MEASURE_COUNTS = {1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 48, 64, 96, 128, 192, 256}


# --- NeoTJAPlayer の遊び方 --------------------------------------------
#: 見るだけ。全部の音符が自動で「良」になる。これまでどおりの動き。
PLAY_MODE_WATCH = "watch"
#: 自分で叩く。判定・コンボ・スコアが入力で決まる。**Player だけ**の機能で、
#: Editor の譜面プレビューと録画は常に PLAY_MODE_WATCH。録画はフレームを
#: 飛び飛びに描くので、叩いた記録という状態とは両立しない。
PLAY_MODE_PLAY = "play"

#: 演奏モードの入力の種類。面(ドン)と縁(カツ)。
#: **ここに置くのは循環 import を避けるため** — 譜面プレビュー
#: (chart_preview_widget)も演奏の記録(player/play_state)も両方使う。
KIND_DON = "don"
KIND_KA = "ka"
