from decimal import getcontext

APP_NAME = "NeoTJAEditor"
VERSION = "12.1.3"

getcontext().prec = 50

NEW_FILE_TEMPLATE = """\
TITLE:
SUBTITLE:--
BPM:
WAVE:
OFFSET:0.00
SONGVOL:100
SEVOL:100
DEMOSTART:0.00

COURSE:oni
LEVEL:
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
