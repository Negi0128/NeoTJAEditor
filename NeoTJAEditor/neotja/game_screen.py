"""本家(太鼓の達人 / TNDE)の画面構成を再現する合成ウィジェット。

譜面レーンそのものは既存の ChartPreviewWidget が描く。ここはその周りを組み
立てる係で、背景・左パネル・フッターを敷き、レーンを実測位置へ置くだけ。
レーンの描画ロジックには一切触っていない(寸法だけ set_lane_geometry で
本家に合わせる)。

座標は TNDE のゲーム内キャプチャ(1280x720 ネイティブ)から実測した:

    y=  0..188   上部背景(どんちゃん・曲名・魂ゲージ)
    y=188..364   レーン一式   左パネル 333x176 / レーン x=333
    y=192..322   └ レーン本体 947x130   (333+947 = 1280 ちょうど)
    y=322..348   └ 打音表記帯 947x26
    y=360..720   下部背景(踊り子) 1280x360
    y=676..720   フッター 1280x44
    判定円の中心 x=414, y=257   (レーン左端から 81px)

FULL(録画用)は 1280x720 全部、COMPACT(再生モード)は上部背景とレーン一式
だけの 1280x360。どちらも同じ座標系なので、切り替えても位置がずれない。
"""

import math
import os

from PySide6.QtCore import Qt, QPointF, QRect, QRectF, QTimer
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QImage,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QWidget

from neotja import chara as chara_mod
from neotja import gauge as gauge_mod
from neotja import settings as settings_mod
from neotja.chart_preview_widget import (ChartPreviewWidget, blit_fitted,
                                         blit_sprite, dev_info)

SCREEN_W = 1280
SCREEN_H_FULL = 720
SCREEN_H_COMPACT = 360
# 軽量モードの画面の高さ。上半分(compact)と同じ 1280x360 にしてある。
#
# 上の帯(y=0..188)には魂ゲージしか無いので、一度は 100px 切り落として
# 1280x260 にしていた。だが軽量に残すものが増えて、そこを使う要素が
# 戻ってきた:
#   ・連打/風船の打数の読み出し(金の扇は y≒22..202、風船の吹き出しも同じ辺り)
#   ・判定文字「良」の板 (OVERLAY_RECT の上端が y=88)
# どちらも切り取り範囲に丸ごと入るので、切ると上が欠ける。「戻した要素が
# 全部ちゃんと見えていること」を優先して、切り取りはやめて compact と同じ
# 高さに戻した。左右は元から一切触っていないので、判定円の x・音符の
# 横位置・流れる速さは通常再生と完全に同一のまま。
# 軽量モードの画面の高さ。通常再生と**同じ 16:9** にして、背景を出さない
# ぶんは黒で埋める。以前は上半分だけの 1280x360 にしていたが、モードを
# 行き来するたびに窓の縦横比が変わって落ち着かないという指摘を受けた。
# 黒く塗る部分は静的キャッシュに焼くので、面積が増えても毎フレームの費用は
# 増えない(貼るのは元から画面1枚ぶんの単純コピー)。
SCREEN_H_LITE = SCREEN_H_FULL

# --- 実測した配置 --------------------------------------------------------
BG_TOP_H = 188            # 上部背景の高さ
PANEL_X, PANEL_Y = 0, 188      # 左パネル(スコア/コンボ/太鼓/銘板)
PANEL_W, PANEL_H = 333, 176
# レーン本体の左上。Y は本家に合わせて 192 から 2px 下げた(要望)。判定枠・
# 火花・判定文字「良」・打音表記・連打数・レーン枠・譜面ウィジェット本体は
# すべてこの LANE_Y から導出しているので、ここを変えれば一緒に動く。
LANE_X, LANE_Y = 333, 196
LANE_W, LANE_H = 947, 130
# 打音表記帯(レーン本体の直下)。枠素材(Taiko_Frame.png)では
#   上辺 48..55 (8px) / 窓 56..185 (130px) / 帯 186..214 (29px) / 下辺 215..223 (9px)
# = 合計 176px で、これは左パネル(銘板ブロック 333x176)の高さと一致する。
# 帯を 26px にしていたせいでレーンの箱だけ 3px 低く、パネルと下端が揃って
# いなかった。素材どおりの 29px にすると 188..363 でぴったり重なる。
SE_STRIP_H = 29
JUDGE_X_IN_LANE = 81           # レーン左端から判定円の中心まで
BG_DOWN_Y, BG_DOWN_H = 360, 360
FOOTER_Y, FOOTER_H = 676, 44

# --- どんちゃん (skin/1_Chara/) ------------------------------------------
# 素材は 360x184 で、この画面と同じ 1280x720 基準なので等倍で置く。
# 本家(TNDE)の画面ではどんちゃんは**上段の左**にいて、足元がレーンの左に
# ある左パネルの上端あたりに来る。屋台(下段背景)の前ではない。
#
# 置き場所は素材の左上ではなく「中身の左下」で決める。素材は左に 76px の
# 透明な余白があり(中身は x76..359 / y46..183)、左上を基準にすると絵が
# 右へ寄ってしまうため。本家のスクショに合わせると中身の左端が x=21、
# 足元が y=193 なので、素材の左上はその逆算になる。
CHARA_CONTENT_LEFT = 76      # 素材の中で絵が始まる x
CHARA_CONTENT_BOTTOM = 183   # 同 足元の y
#: 画面上での「中身の左端」と「足元」。ここだけ触れば位置が動く。
CHARA_ANCHOR = (76, 188)
CHARA_POS = (CHARA_ANCHOR[0] - CHARA_CONTENT_LEFT,
             CHARA_ANCHOR[1] - CHARA_CONTENT_BOTTOM)
# 連番を1周するのにかける拍数。BPM120・119コマなら 2.0 秒 = 約60fps。
CHARA_BEATS_PER_LOOP = 4.0
# 風船の状態(Balloon_Breaking / Balloon_Broke)だけは、中身で合わせるのを
# やめて画布(648x345)の左上をふだんの絵と同じ位置に置き、そこからずらす。
# 中身で合わせると立ち姿(高さ195)の頭が画面の上へ出るため。
CHARA_BALLOON_CANVAS_OFF = (41, 10)

# --- 左パネルの中身(本家スクショから採った位置) ----------------------
# 数字シートの1文字は 29.3x31.3。本家のスコアは高さ25px前後だったので
# 0.8 倍で置く(1.6 倍にしたら6桁がパネルからはみ出した)。
SCORE_RIGHT, SCORE_Y = 178, 198      # スコアは右詰め
SCORE_SCALE = 1.02
# 数字シートは1文字ぶんの枠(29.3px)に余白を含むので、そのまま送ると字間が
# 空きすぎる。本家は字が詰まっているので送り幅を枠の 76% にする。
SCORE_ADVANCE = 0.73
# Score_Plate.png は文字ごとに上下がそろっていない。実測すると 5 と 6 だけ
# セル内で 1px 上に寄っている(他は y=1..30、5/6 は y=0..29)。数字ごとに
# 下げ量を持たせて揃える。
SCORE_DIGIT_Y_OFF = {5: 1, 6: 1}
COURSE_SYM_POS = (26, 237)           # コース記号(おに 等)
DRUM_POS = (208, 209)                # 太鼓 120x133
# コンボの数字と「コンボ」文字は太鼓の上に載る。太鼓を動かしたら数字も
# 一緒に動いてほしいので、基準は太鼓からの相対で持つ(以前は独立した座標に
# していたが、太鼓だけずらしたときに数字が置いていかれた)。
COMBO_OFFSET = (-3, -3)              # 太鼓の左上から見たコンボの基準
COMBO_ANCHOR = (DRUM_POS[0] + COMBO_OFFSET[0], DRUM_POS[1] + COMBO_OFFSET[1])
NAMEPLATE_POS = (-25, 291)           # 1P 銘板(素材の左余白ぶん外へ出す)
# コンボ数字は太鼓の中心にそろえると本家よりわずかに右へ寄って見えるので、
# 少し左へずらす。
COMBO_X_OFF = 0
COMBO_Y_OFF = 19                     # 太鼓の上端からコンボ数字までの距離
COMBO_TEXT_Y_OFF = 65                # 同 「コンボ」文字まで
COMBO_TEXT_X_OFF = 3                 # 「コンボ」文字の左右微調整
COMBO_SCALE = 0.954                  # 本家に合わせて 1.06 から 0.9 倍
COMBO_ADVANCE = 0.80
# Combo/Text.png (100x100) には「コンボ」が縦に2つ入っている(通常色と金色)。
# 実測では y=26..48 と y=76..98 で、間に空きがある。中身の範囲を単純に
# 2等分すると金色側だけ十数px下へずれるので、素材から不透明な帯を実際に
# 測って使う(_measure_combo_text_bands)。測れなかったときの保険がこれ。
COMBO_TEXT_BAND_FALLBACK = ((26, 23), (76, 23))   # ((y, 高さ), ...)
# コンボ数字の色: 0-49 白 / 50-99 銀 / 100- 金
COMBO_SILVER_AT, COMBO_GOLD_AT = 50, 100
# 太鼓が光る時間(叩いた瞬間からの秒数)。本家もごく短い。
DRUM_GLOW_SEC = 0.09

# --- 魂ゲージ ------------------------------------------------------------
# Gauge.png / Gauge_Base.png は 700x68 だが、ゲージ本体は上の 44px だけ。
# 残り(y=44..67)は使い回しの「クリア」文字が左下に詰め込まれているだけなので
# 切り捨てる。本体の形は「y=0..17 は x=547 から(クリア圏の背の高い部分)、
# y=18..43 は x=1 から(通常圏)」という段付き。
GAUGE_BAR_H = 44
# 黒枠(Taiko_Frame)の上帯も同じ段付きで、背の高い側が枠の x=715 から始まる。
# 段の位置を合わせると 枠左端331 + 715 - 547 = 499 がゲージの左端になる。
GAUGE_POS = (499, 146)   # レーンと揃えて 144 から 2px 下げた(要望)
# クリア(ノルマ)の位置。本家は内部10000点中8000点＝80%。素材の段差
# (背の高いクリア圏の始まり)は実測 547/697 = 78.5% とわずかに手前だが、
# 魂が光るかどうかはゲームの規則どおり 80% で判定する。
GAUGE_CLEAR_RATIO = gauge_mod.CLEAR_RATIO
# 魂の文字 (Soul.png 80x160 = 80x80 が2段。上段=通常 / 下段=クリア)。
# ゲージ右端(499+697=1196)と枠の右端(331+950=1281)の間、85px の窓に収める。
SOUL_CELL = 80
SOUL_POS = (1198, 126)
# ゲージは1本ずつ増える。内部10000点で200点ごとに1本 = 50本。素材の縞も
# 50等分で入っているので、幅を1本ぶんの倍数に丸めると縞と揃う。
GAUGE_BLOCKS = gauge_mod.GAUGE_MAX // gauge_mod.GAUGE_STEP
# 「クリア」の文字。Gauge.png / Gauge_Base.png の y=44.. に 45x22 が2つ
# 入っている(左=明るい灰 / 右=暗い灰)。実機ではクリア圏(段が高くなる所)の
# 左上に出る。地の金色が暗いうちは明るい方、満ちて明るくなったら暗い方に
# 替えて読めるようにする(実機のキャプチャで明るい方が出ているのを確認)。
#   1つ目 = 未クリアのとき / 2つ目 = クリアしたとき。
#   素材の左(x=3)は塗りが白、右(x=61)は塗りが灰色。ノルマに届く前は灰色で
#   沈ませ、届いたら白く光らせる。最初これが逆で「明るすぎる」状態だった。
GAUGE_CLEAR_GLYPH = ((61, 44), (3, 44), 45, 22)
GAUGE_CLEAR_STEP_X = 547      # 素材の中でクリア圏(背の高い側)が始まる x
GAUGE_CLEAR_TEXT_OFF = (3, 1)     # 段の始まりからの微調整
# 叩いた音符が判定円から魂ゲージへ飛ぶ演出。レーンの外まで出るので画面側で描く。
SOUL_FLY_SEC = 0.42
# 弧のてっぺん(道のりの半分の地点)が通る点。y=0 なら音符の中心が画面の
# 上端に来る = 半分だけ画面の外へ出る。x は左右の膨らみの偏り — 判定円
# (414)と魂(1238)のちょうど中間は 826 だが、そこだと右へ寄って見えるので
# 左へ寄せて、判定円を出たあとすぐ立ち上がる形にする。
# 制御点はこの2つから逆算する。
SOUL_FLY_APEX_X = 700.0
SOUL_FLY_APEX_Y = 0.0
# OpenTaiko の FlyingNotes.cs は NotesManager.DisplayNote() を素のまま呼ぶ
# だけで、拡大率も不透明度もいじっていない。飛んでいる間は等倍のまま。
SOUL_FLY_SCALE_END = 1.0
# 着弾したあと、音符は魂の上にしばらく残る。OpenTaiko の
# CActImplChipEffects.cs は ctChipEffect = CCounter(0, 24, 17ms) を回して
# 「CurrentValue < 13 のあいだ終点に音符を描く」= 13 * 17ms ≒ 0.22 秒。
# 本家はそのまま消えるが、こちらは白へ寄せながら薄くして消す。
SOUL_LAND_SEC = 0.22

# --- 風船が割れたときの虹 (skin/Rainbow.png = TNDE の 10_Effects/Rainbow.png)
# OpenTaiko の Rainbow.cs をそのまま移す。カウンタ 0..164 を Timer(ms) 刻みで
# 進めて、
#   前半 (0..81)  : 左から順に「描き足していく」
#                   切り出し (0, 0, W*c/85, H) を (X, Y) に置く
#   後半 (82..164): 左から順に「消していく」
#                   nx = W*(c-82)/85 として (nx, 0, W-nx, H) を (X+nx, Y) に置く
# 数値は OpenTaiko の既定値(CSkin.cs):
#   Game_Effect_Rainbow_X = 360 / _Y = -100 / _Timer = 8ms
# 全体で 164*8 = 1.31 秒。
SHOW_BALLOON_RAINBOW = True
RAINBOW_POS = (360, -100)
RAINBOW_TICK_SEC = 0.008
RAINBOW_TICKS = 164
RAINBOW_HALF = 82
RAINBOW_WIPE_DEN = 85
# 描き足していく間、その先端に大ドンの顔を乗せる。素材どおりに切ると
# 先端が縦にスパッと切れて見えるので、顔で隠して「顔が虹を引いている」
# 見え方にする。Notes.png は 130px のセルが 13列x3行で、(3, 0) が
# 大ドンの叩いた顔。虹の帯の真ん中に乗るよう、その列の不透明な範囲の
# 中心に置く。
RAINBOW_HEAD_CELL = 130
RAINBOW_HEAD_INDEX = (3, 0)      # (列, 行)
RAINBOW_HEAD_SCALE = 1.0
# レーンより手前に描くものを載せる板。ここに入るのは
#   * 判定円(y=261)から魂(y=166)へ、画面の上端をかすめる弧を描く音符
#   * 風船中のどんちゃん(画布 648x345 を CHARA_BALLOON_CANVAS_OFF に置く)
# どちらも丸ごと入るだけの範囲を取る。上端 0 は、弧のてっぺんで画面の外へ
# 出る分をここで切るため。
SOUL_FLY_RECT = (0, 0, SCREEN_W, 520)

# レーンと魂ゲージを囲む黒枠(1P_Frame.png 951x224)。アルファを測ったところ
# 中の透明な窓が y=56..185 = 高さ130 で、レーン本体とぴったり同じだった。
# よって窓をレーン(y=192)に合わせると frame_y = 192-56 = 136。横は 951 が
# レーン947 + 左右2pxの縁なので frame_x = 333-2 = 331。
# 上端(y=0..55)がゲージの載る黒帯、下端(y=220..)がレーン下の縁になる。
LANE_FRAME_POS = (LANE_X - 2, LANE_Y - 56)
# 枠のうち「レーンを囲う部分」(y=56 以降)は上の位置で固定。上の黒帯だけは
# 独立して上下できるようにする — 帯とゲージが重なって潰れるのを避けるため。
# 帯だけ下げてもレーンの窓は動かないので、レーンとの1px合わせは崩れない。
# 素材の y=48..55 はレーン箱の上辺(黒枠)なので、ここは動かしてはいけない。
# 動かしてよいのは魂ゲージを囲う箱(y=0..47)だけ。
FRAME_TOP_BAND = 48          # 素材のうち「ゲージの箱」はここまで
# 素材の y=190..214 の x=0 に、打音表記帯と同じ灰色 (140,140,142) の列が
# 1px だけ入っている。本家は帯が枠の内側ぎりぎりまで来るので繋がるが、
# こちらは帯がレーンと同じ x=333 から始まるため、この1pxだけが黒の中に
# 浮いて「何かが重なっている」ように見える。その列は描かない。
FRAME_SLIVER_Y0, FRAME_SLIVER_Y1 = 190, 215
FRAME_TOP_X_OFF = 5          # ゲージの箱だけ右へずらす量
FRAME_TOP_Y_OFF = 1          # ゲージの箱だけ下へずらす量
# 枠の下辺(素材 y=215..)だけ独立して上下できるようにしておく。帯の高さを
# 素材どおり(29px)にすれば隙間は出ないので、既定は 0。
FRAME_BOTTOM_Y_OFF = 0       # 枠の下辺だけ上へずらす量

# 背景の絵(下段の屋台 / フッター)を出すか。素材が無ければどのみち描かれない。
SHOW_BACKGROUND = True
# 上段(レーンより上)の風景は別扱い。ここを False にすると真っ黒で塗る。
# 上段は曲名・魂ゲージ・どんちゃんが乗る帯で、絵を敷くとそれらが読みにくい。
SHOW_BACKGROUND_TOP = True

# --- 上背景の作り ---------------------------------------------------------
# "flat"    : Background.png を1枚貼るだけ(絵は綺麗だが動かせない)
# "layered" : TNDE の 5_Background/Bg_up/3 と同じ3枚重ね。傘柄のタイル地
#             (Base) + 落ちてくる花びら(Flower) + 隅の飾り(Chara)。
#             タイル地と花びらが動く。
BG_TOP_STYLE = "layered"
# 素材は色違いが1枚に詰まっている。行/列で 1P(赤) の駒を取り出す。
#   Base.png   1005x188 = 335x188 が3色ぶん横並び
#   Chara.png   656x699 = 328x233 が [変化2種] x [色3種]
#   Flower.png  641x552 = 320x184 が [空, 花びら] x [色3種]
#
# Chara と Flower は「1色ぶんの行を丸ごと」敷き詰める。行の中に飾りが
# 2種類(だんご三兄弟 / ひよこ)並んでいるので、行ごと繰り返せば本家と
# 同じ「交互に出てくる」並びになる。Flower の行は左半分が空で、これが
# 花びらの間隔そのもの。素材の詰め方がそのまま配置になっている。
BG_UP_COLOR_ROW = 0          # 0=赤(1P) 1=青 2=橙
BG_UP_BASE_CELL = (335, 188)     # 地は1色ぶんが1駒
BG_UP_CHARA_ROW = (656, 233)     # 飾りは1色ぶんの行(飾り2種ぶん)
BG_UP_FLOWER_ROW = (641, 184)    # 花びらも行ごと(左半分は空)
# 流れる速さ。実機キャプチャ(1920x1080 を 1280 に落として計測)から、
# 1秒違いの2コマを総当たりで突き合わせて実測した値。
#   上/中/下どの帯でも 横 -67px, 縦 0px で一致 (3か所 x 2組で同じ)
# つまり地も飾りも花びらも同じ速さで左へ流れ、縦には動かない。
BG_UP_SCROLL_VX = -67.0

# --- 下背景の光レイヤー ---------------------------------------------------
# TNDE の背景は 5_Background/Bg_down/<1..5>/ に「土台 0.png + 重ねる絵」の
# 形で入っていて(本家はこの5セットから毎回ランダムに選ぶ)、こちらは
# セット1 に固定して使っている。土台(Bg_down.png)だけを敷いていたが、
# 同じセットには提灯の光だけを描いた 1.png があり、これが未使用だった。
# 加算合成で重ねると屋台の提灯が灯る。
#
# ゆらぎの速さと明るさは本家(コンパイル済み)から読み取れないので、
# OpenTaiko の背景スクリプトが時間ベースの固定周期でループしている
# (Normal/Up/0 は3秒周期の sin)のに倣った、こちらの決め打ち。
SHOW_BACKGROUND_LIGHT = True
BG_LIGHT_PERIOD = 2.4        # ゆらぎの周期(秒)
BG_LIGHT_MIN, BG_LIGHT_MAX = 0.55, 1.0   # 不透明度の下限/上限
BACKGROUND_TOP_COLOR = "#000000"

# 連打数と判定文字「良」はレーンより上(黒枠の帯の上)に出す。レーンの
# ウィジェットは帯ぴったりの高さしか無く、上へはみ出して描けないので、
# この2つは画面側(親)が描く。判定円の真上、魂ゲージの左に収まる位置。
# --- ゴーゴー / 魂MAX の演出 --------------------------------------------
# GoGoSplash.png 6900x460 = 230x460 が30コマ。下から吹き上がる金色の火花で、
# ゴーゴーが始まった瞬間に一度だけ流れる(ループしない)。下端中央アンカー。
# 判定枠まわりがうるさくなるので既定では出さない。True にすれば戻る。
SHOW_GOGO_SPLASH = False
GOGO_SPLASH_CELL = (230, 460)
GOGO_SPLASH_FRAMES = 30
GOGO_SPLASH_FRAME_SEC = 1.0 / 30.0
GOGO_SPLASH_SCALE = 0.825        # 0.55 の 1.5 倍
GOGO_SPLASH_BOTTOM = LANE_Y + LANE_H   # 火花の足元をレーン下端に置く

# 魂ゲージが満タン(入魂)のあいだ、ゲージが虹色に変わる。
# Rainbow/<コース>/0..11.png 696x44。マスクがゲージ本体と dx=0,dy=0 で一致
# するので、ゲージと同じ位置にそのまま重ねるだけでよい。
GAUGE_RAINBOW_FRAMES = 12
GAUGE_RAINBOW_FRAME_SEC = 1.0 / 20.0

# 1P_Explosion.png 3240x180 = 180x180 が18コマ。先頭2コマは完全に透明。
# 12個の丸が輪になって広がるワンショット。中心アンカー。
# クリア(ノルマ)に到達した瞬間、魂の文字の後ろで開く。
SOUL_BURST_CELL = 180
SOUL_BURST_FRAMES = 18
SOUL_BURST_FIRST = 2
SOUL_BURST_FRAME_SEC = 1.0 / 30.0
SOUL_BURST_SCALE = 1.0

# --- 連打数(金の扇) ------------------------------------------------------
# 11_Balloon/Roll.png 1670x204 = 334x204 が5コマ。閉じた状態から開いていき、
# 最後のコマだけ「連打!!」の札が付く。数字は専用シート
# 11_Balloon/Number_Roll.png 630x75 = 63x75 が10文字(0..9)。
# 本家キャプチャ(1333px幅)で扇はおよそ x345..470 / y25..150 に見えたので、
# 1280 換算で幅 120px 前後。素材の絵は 231px 幅なので約 0.52 倍で置く。
ROLL_FAN_CELL = (334, 204)
ROLL_FAN_FRAMES = 5
ROLL_FAN_SCALE = 0.884           # 0.52 の 1.7 倍
ROLL_FAN_CENTER_X = 413          # 扇の中心 x
ROLL_FAN_BOTTOM = 202            # 扇の下端 y
ROLL_NUM_CELL = (63, 75)
ROLL_NUM_SCALE = 0.81            # 1.5倍(0.90)からさらに 90%
ROLL_NUM_ADVANCE = 0.86          # 字送り(セル幅に対する割合)
# 数字は扇のセル(334x204)の中に位置を持つ。こうしておくと扇を拡大・移動
# しても数字が置いていかれない。微調整は ROLL_NUM_OFF で。
ROLL_NUM_ANCHOR = (167, 96)
ROLL_NUM_OFF = (-7, -7)          # (右, 下)

# --- 風船・くす玉(白い吹き出し) ------------------------------------------
# 11_Balloon/Balloon.png 200x160。中身は x13..186 / y3..154 で、左下に尻尾。
# 数字は連打と同じ Number_Roll.png を使う。どんちゃんは描かない。
BALLOON_SCALE = 0.93             # 0.62 の 1.5 倍
BALLOON_CENTER_X = 438           # 吹き出しの中心 x
BALLOON_BOTTOM = 195             # 吹き出し(尻尾の先)の下端 y
BALLOON_NUM_SCALE = 0.72         # 0.60 の 1.2 倍
# 数字は吹き出しの楕円の中心に置く。素材(200x160)のうち下の尖りは尻尾なので
# 中心には含めず、楕円だけの中心 (100, 64) を基準にする。こうしておくと
# 吹き出しを拡大・移動しても数字が置いていかれない。
BALLOON_NUM_ANCHOR = (100, 64)
BALLOON_NUM_OFF = (-5, 5)        # そこからの微調整 (右, 下)

TAP_COUNT_BOTTOM = LANE_Y - 4    # (素材が無いときの文字表示)連打数の下端
JUDGE_BOTTOM = LANE_Y + 21       # 「良」の下端。レーンに 21px かぶる
JUDGE_SCALE = 1.05               # 「良」の拡大率
JUDGE_POP_RISE = 13.0            # 「良」が昇る高さ
JUDGE_POP_SEC = 0.34             # ポップの持続
# 出た瞬間から薄くしていくと、ずっと半透明の文字が漂って見える。本家は
# しばらくはっきり出てから消えるので、ここまでは不透明のままにする。
JUDGE_POP_FADE_FROM = 0.55       # 0..1 のうちどこからフェードを始めるか

# --- 曲名(録画のときだけ、画面の右上) ------------------------------------
# 勘亭流。DFP勘亭流(DynaFont)はこの環境に無かったので、TNDE が同梱している
# FOT-大江戸勘亭流(Fontworks)を使う。skin/Kanteiryu.otf があればそれを読み、
# 無ければ入っていそうな家族名を順に当たり、それも駄目なら既定のフォント。
TITLE_FONT_FILE = "Kanteiryu.otf"
TITLE_FONT_FALLBACKS = ("FOT-大江戸勘亭流 Std E", "FOT-OedoKtr Std E",
                        "DFPKanteiryu-XB", "DFP勘亭流")
TITLE_RECT = (603, 31, 640, 52)   # 右詰めの基準枠 (x, y, w, h)
TITLE_SIZE = 41                   # 大きさは曲名の長さによらず一定(34の1.2倍)
# 長い曲名は縮めず、右端を揃えたまま左へはみ出させる。左はここまで。
TITLE_LEFT_LIMIT = 8
TITLE_COLOR = "#ffffff"
# 上背景を敷いてから白1色だと柄に埋もれるので、黒で縁取る。落ち影ではなく
# 文字の輪郭をなぞる線なので、下が何色でも読める。太さは線の幅で、外へ
# 出るのはその半分。
TITLE_OUTLINE = "#000000"
TITLE_OUTLINE_W = 9.0

# --- スコアの加算表示 ----------------------------------------------------
# 音符を叩くたびに、入った点をスコアの上へ浮かべて消す。数字は Score_Plate の
# 2段目(橙)を使う — 本家も加算分だけ色が違う。
SCORE_GAIN_SEC = 0.5             # 出てから消えるまで
SCORE_GAIN_RISE = 16.0           # 昇る高さ
SCORE_GAIN_SCALE = 0.902
SCORE_GAIN_ROW = 1               # Score_Plate.png の段(0=白 1=橙 2=水)
SCORE_GAIN_Y_OFF = 4             # スコアの上端からさらに上へ(正=下)
# 「良」を描くオーバーレイの大きさ(判定円の中心を基準にした矩形)。
# レーンより手前に重ねる必要があるので、レーンの兄弟ウィジェットにする。
OVERLAY_RECT = ((-130, 0, 260, 338) if SHOW_GOGO_SPLASH
                else (-130, 88, 260, 250))   # (dx, y, w, h) dx は判定円中心からの左端


class _FlightOverlay(QWidget):
    """叩いた音符が魂ゲージへ飛んでいくところだけを描く板。

    飛び始めは判定円の上、つまりレーンの中にいる。親(画面)は子(レーン)より
    先に描かれるので親に描くとその間だけレーンに隠れて、途中から湧いて出た
    ように見える。「良」と同じくレーンの兄弟として重ねて手前に出す。"""

    def __init__(self, screen):
        super().__init__(screen)
        self._screen = screen
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        # どんちゃん → 風船 → 飛んでいく音符 の順。後のものほど手前。
        self._screen.draw_chara_front(p, self.x(), self.y())
        self._screen.draw_balloon_front(p, self.x(), self.y())
        self._screen.draw_soul_flights(p, self.x(), self.y())
        p.end()


class _JudgeOverlay(QWidget):
    """判定文字「良」だけを描く、レーンより手前の板。

    「良」はレーンに少しかぶる位置に出したいが、親(画面)は子(レーン)より
    先に描かれるので、親に描くとレーンに隠れてしまう。レーンの兄弟として
    重ねることで手前に出す。背景は塗らない(下のレーン・黒枠が透ける)。
    """

    def __init__(self, screen):
        super().__init__(screen)
        self._screen = screen
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self._screen.draw_gogo_splash(p, self.x(), self.y())
        recent = self._screen.judge_pop()
        if recent is None:
            p.end()
            return
        elapsed = recent[0]
        if not (0.0 <= elapsed < JUDGE_POP_SEC):
            p.end()
            return
        spr = self._screen.chart_preview.judge_sprite()
        jp = elapsed / JUDGE_POP_SEC
        rise = JUDGE_POP_RISE * (1.0 - (1.0 - jp) ** 2)
        if jp <= JUDGE_POP_FADE_FROM:
            p.setOpacity(1.0)
        else:
            p.setOpacity(max(0.0, 1.0 - (jp - JUDGE_POP_FADE_FROM)
                             / (1.0 - JUDGE_POP_FADE_FROM)))
        cx = self.width() // 2
        bottom = JUDGE_BOTTOM - self.y() - rise
        if spr is not None:
            w = int(spr.width() * JUDGE_SCALE)
            h = int(spr.height() * JUDGE_SCALE)
            p.drawPixmap(QRect(int(cx - w / 2), int(bottom - h), w, h), spr)
        else:
            p.setPen(QColor("#f5c518"))
            f = p.font()
            f.setPointSize(int(20 * JUDGE_SCALE))
            f.setBold(True)
            p.setFont(f)
            p.drawText(int(cx - 40), int(bottom - 26), 80, 26, Qt.AlignCenter, "良")
        p.end()


class GameScreenWidget(QWidget):
    """1280x720(または上半分だけの 1280x360)の画面を組み立てる。

    compact=True は「通常再生モード」用で、下部背景と踊り子を描かないぶん
    軽く、窓も小さい。録画は compact=False で全部描く。
    """

    def __init__(self, chart_preview: ChartPreviewWidget, compact=False, parent=None):
        super().__init__(parent)
        self.chart_preview = chart_preview
        self._compact = bool(compact)
        # 軽量モード(下部パネルの「軽量」)。低スペック機でも譜面が止まらずに
        # 流れることを狙って、**譜面と手応えに関係しない絵だけ**を止める。
        #   止めるもの: どんちゃん / 上下の背景(重ね式の流れる背景・提灯の光・
        #               Background.png・Bg_down.png・フッター) / 魂ゲージ・
        #               「クリア」・虹・叩いた音符が魂へ飛ぶ演出 /
        #               スコア加算の浮き上がり
        #   残すもの:   レーンと音符(連打・風船込み)・小節線・判定円・
        #               コンボ・スコア・打音(音声)・ゴーゴー(炎と帯)・
        #               打音表記の帯・判定の火花・「良」・連打の金の扇・
        #               太鼓の光り
        # つまり「叩いている感じ」はそのままで、背景と装飾の絵だけが消える。
        # レイアウトは通常再生と同じ 1280x720(SCREEN_H_LITE 参照)。背景の
        # あった場所は黒く塗るだけで、レーンも左パネルも位置は動かさない。
        # 左右も一切動かさないので、音符の横位置も流れる速さも通常再生と同一。
        self._lite = False
        self._skin = {}
        self._score_timeline = None
        self._gauge = None
        self._clear_time = None
        self._course_key = None
        self._course_sym = None
        self._load_skin()

        chart_preview.setParent(self)
        # レーンの寸法を本家に合わせる。上下の余白は 0 にして、レーン本体と
        # 打音表記帯だけの高さ(130+26)にする — 余白ぶんの情報(連打カウント等)
        # は画面側の余白に描くほうが本家に近い。
        chart_preview.set_lane_geometry(LANE_W, LANE_H, JUDGE_X_IN_LANE,
                                        top_margin=0, bottom_margin=0,
                                        se_height=SE_STRIP_H)
        # コンボはこちらが左パネルの太鼓の上に描くので、レーン内には出さない。
        chart_preview._hide_lane_combo = True
        # 叩いた音符の飛び去りは、こちらが魂ゲージまで一続きに描く。
        chart_preview._hide_hit_fly = True
        # 判定枠の風船も、どんちゃんより手前に出すためこちらで描く。
        chart_preview._hide_balloon_sprite = True
        chart_preview.move(LANE_X, LANE_Y)

        self.setFixedSize(SCREEN_W, SCREEN_H_COMPACT if compact else SCREEN_H_FULL)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        # 静的な下地(背景・左パネル・黒枠 等)を焼いたキャッシュ。None なら
        # 次の paintEvent で作り直す。compact 切替やスキン再読込で捨てること。
        self._static_layer = None
        # 静的キャッシュを焼いたときの devicePixelRatio。録画(1080p)は 1.5 で
        # 描くので、DPR 1 で焼いた1枚を貼ると毎コマ 1.5 倍へ拡大される
        # (1コマ 3.2ms、1080p の描画時間の 3割)。DPR ごとに焼き直して
        # 等倍ブリットにする。
        self._static_layer_dpr = 1.0
        # いま描いている面の devicePixelRatio。paintEvent が毎コマ更新する。
        # 子の板(_FlightOverlay など)は親と同じ面へ描かれるのでこれを見る。
        self._dpr = 1.0
        # 上背景シートから切り出した駒((key,col,row) -> QPixmap)。
        self._bg_up_cache = {}
        # 上背景3層を焼いた帯(画面幅+余白)と、それを焼いたときの位相。
        # 位相が同じだけ進んでいる間は、この帯から窓を切って貼るだけで済む。
        self._bg_strip = None
        self._bg_strip_keys = None
        # 白く染めた音符(着弾の白飛ばし用)。文字 -> QPixmap
        self._white_note_cache = {}
        # 虹の先端に乗せる顔と、虹の帯の中心の高さ(列ごと)。どちらも1回だけ。
        self._rainbow_head_pm = None
        self._rainbow_centers = None
        # 数字シートの縮小済みグリフ((sheet,cols,rows,row,scale) -> [0..9])
        self._digit_cache = {}
        # 判定ポップが前フレームに出ていたか(消し込みを1回だけ行うため)
        self._judge_was_active = False
        # 飛んでいる音符が前フレームに居たか(同じく消し込みを1回だけ行う)
        self._flight_was_active = False

        # レーンが update() しても、Qt が塗り直すのはレーンの矩形だけ。
        # スコア・コンボ・太鼓・魂ゲージ・「良」はどれもレーンの外にあるので、
        # 放っておくと一度描かれたきり止まって見える。ここで毎フレーム
        # 塗り直す。レーンに重ならない2つの矩形だけを指定して、レーンを
        # 二重に描かせない。
        # 「良」はレーンにかぶるので、レーンより手前の板に描く。
        # 飛んでいく音符も、判定円(レーンの中)から出るのでレーンより手前。
        # 「良」より奥にしたいので先に作って先に raise する。
        self._flight_overlay = _FlightOverlay(self)
        self._flight_overlay.setGeometry(*SOUL_FLY_RECT)
        self._flight_overlay.raise_()

        ox, oy, ow, oh = OVERLAY_RECT
        self._judge_overlay = _JudgeOverlay(self)
        self._judge_overlay.setGeometry(LANE_X + JUDGE_X_IN_LANE + ox, oy, ow, oh)
        self._judge_overlay.raise_()

        self._hud_timer = QTimer(self)
        self._hud_timer.setInterval(max(1, chart_preview._timer.interval()))
        self._hud_timer.timeout.connect(self._tick_hud)
        # 動かすのは表示されているあいだだけ。録画用の画面はずっと非表示の
        # まま render() されるだけなので、そこでタイマーを回す意味がない。

    def showEvent(self, event):
        super().showEvent(event)
        self._hud_timer.start()

    def hideEvent(self, event):
        self._hud_timer.stop()
        super().hideEvent(event)

    # --- 録画(オフライン描画)用の受け渡し ---------------------------------
    # 録画側は「1つのウィジェットに時刻を渡して render() する」だけの作りに
    # してある。画面ごと録るときも同じ扱いにできるよう、時刻の出し入れは
    # レーンへそのまま流す。HUD はレーンの現在値から描くので、これだけで
    # スコアもコンボも魂ゲージも録画に乗る。
    def begin_offline_render(self):
        self.chart_preview.begin_offline_render()

    def set_render_time(self, seconds):
        self.chart_preview.set_render_time(seconds)

    def end_offline_render(self):
        self.chart_preview.end_offline_render()

    def draw_gogo_splash(self, p, ox, oy):
        """ゴーゴーが始まった瞬間の金色の火花。オーバーレイ(レーンより手前)
        から呼ぶ。ox/oy はそのオーバーレイの左上。"""
        sheet = self._skin.get("gogo_splash") if SHOW_GOGO_SPLASH else None
        if sheet is None:
            return
        try:
            now = self.chart_preview.game_state()[0]
            regions = self.chart_preview.gogo_regions()
        except Exception:  # noqa: BLE001
            return
        span = GOGO_SPLASH_FRAMES * GOGO_SPLASH_FRAME_SEC
        f = None
        for g0, _g1 in regions:
            el = now - g0
            if 0.0 <= el < span:
                f = int(el / GOGO_SPLASH_FRAME_SEC)
                break
        if f is None:
            return
        cw, ch = GOGO_SPLASH_CELL
        k = GOGO_SPLASH_SCALE
        dw, dh = cw * k, ch * k
        cx = LANE_X + JUDGE_X_IN_LANE
        p.drawPixmap(QRect(int(cx - dw / 2 - ox), int(GOGO_SPLASH_BOTTOM - dh - oy),
                           int(dw), int(dh)),
                     sheet, QRect(min(f, GOGO_SPLASH_FRAMES - 1) * cw, 0, cw, ch))

    def judge_pop(self):
        """直近ヒット (経過秒, 音符の文字, コンボ番号)。オーバーレイ用。"""
        try:
            return self.chart_preview.game_state()[2]
        except Exception:  # noqa: BLE001
            return None

    def _tick_hud(self):
        if not self.isVisible():
            return
        # 上の帯: 魂ゲージ・魂・黒枠・連打数
        self.update(0, 0, SCREEN_W, LANE_Y)
        # 左パネル: スコア・コース記号・太鼓・コンボ・銘板
        self.update(PANEL_X, PANEL_Y, PANEL_W, PANEL_H)
        # 「良」の板(レーンの手前)。判定ポップが出ていない間は中身が空なので、
        # 毎フレーム更新する必要がない(半透明の子ウィジェットの再描画は親の
        # 巻き込み再描画も呼ぶ)。消え際を残さないよう、「前フレームは出ていた」
        # 場合だけもう1回だけ更新して消し込む。
        recent = self.judge_pop()
        active = bool(recent is not None and 0.0 <= recent[0] < JUDGE_POP_SEC)
        if active or self._judge_was_active:
            self._judge_overlay.update()
        self._judge_was_active = active
        # レーンより手前の板(飛んでいく音符・風船中のどんちゃん)。同じ理由で、
        # 中身がある間と、その次の1回だけ塗り直す。
        try:
            now = self.chart_preview.game_state()[0]
            flying = bool(self.chart_preview.recent_hits(
                now, SOUL_FLY_SEC + SOUL_LAND_SEC))
            if not flying and self._chara is not None:
                flying = self._chara.state() in chara_mod.TIME_BASED_STATES
            if not flying and self._lite:
                # 軽量ではどんちゃんを描かない = anim.update() を回さないので、
                # 上の「風船中のどんちゃん」判定が効かない。板の中身は風船の絵
                # だけになるので、風船が判定枠に居るかを直接見る。これが無いと
                # 風船の絵が最初のコマで固まる(板が塗り直されないため)。
                flying = self.chart_preview.balloon_sprite_frame(now) is not None
        except Exception:  # noqa: BLE001
            flying = False
        if flying or self._flight_was_active:
            self._flight_overlay.update()
        self._flight_was_active = flying

    # ------------------------------------------------------------------
    def _load_skin(self):
        """skin/ から使う絵を読む。無ければ None のままで、描画側が黙って飛ばす
        (スキンは同梱しない外部パックなので、無くても動くのが前提)。"""
        base = str(settings_mod.skin_dir())
        for key, rel in (
            ("bg_top", "Background.png"),
            ("bg_down", "Bg_down.png"),
            ("bg_down_light", "Bg_down_Light.png"),
            ("bg_up_base", os.path.join("Bg_up", "Base.png")),
            ("bg_up_chara", os.path.join("Bg_up", "Chara.png")),
            ("bg_up_flower", os.path.join("Bg_up", "Flower.png")),
            ("rainbow", "Rainbow.png"),
            ("notes", "Notes.png"),
            ("footer", "Footer.png"),
            ("panel", "Taiko_Background.png"),
            ("lane_frame", "Taiko_Frame.png"),
            ("drum", os.path.join("Combo", "Base.png")),
            ("drum_don", os.path.join("Combo", "Don.png")),
            ("drum_ka", os.path.join("Combo", "Ka.png")),
            ("combo_white", os.path.join("Combo", "Digits.png")),
            ("combo_silver", os.path.join("Combo", "DigitsSilver.png")),
            ("combo_gold", os.path.join("Combo", "DigitsGold.png")),
            ("combo_text", os.path.join("Combo", "Text.png")),
            ("score_digits", "Score_Plate.png"),
            ("nameplate", "NamePlate.png"),
            ("gauge", "Gauge.png"),
            ("gauge_base", "Gauge_Base.png"),
            ("soul", "Soul.png"),
            ("roll_fan", "Roll.png"),
            ("roll_num", "Number_Roll.png"),
            ("balloon", "Balloon.png"),
            ("gogo_splash", "GoGoSplash.png"),
            ("soul_burst", "SoulExplosion.png"),
        ):
            path = os.path.join(base, rel)
            if os.path.exists(path):
                pm = QPixmap(path)
                if not pm.isNull():
                    self._skin[key] = pm
        self._combo_text_bands = self._measure_combo_text_bands()
        self._gauge_rainbow = self._load_gauge_rainbow()
        # どんちゃん。連番はコマ単位で遅延読みするので、ここでは枚数を
        # 数えるだけ(素材が無ければ available() が False になる)。
        self._chara = chara_mod.CharaAnimator()
        self._chara.beats_per_loop = CHARA_BEATS_PER_LOOP
        self._title = ""
        self._title_family = self._load_title_font()

    def draw_chara_front(self, p, ox=0, oy=0):
        """風船中のどんちゃんだけを、レーンより手前に描く。

        風船の絵は立ち姿で背が高く、置き場所もレーンに重なる。親(画面)は
        子(レーン)より先に描かれるので、親に描くと下半分がレーンに隠れる。
        「良」や飛んでいく音符と同じく、レーンの兄弟の板に描いて手前に出す。
        ふだん/ゴーゴーの絵はレーンに重ならないので、これまでどおり親が描く。"""
        anim = self._chara
        if anim is None or self._compact or self._lite or not anim.sprites.available():
            return
        state = anim.state()
        if state not in chara_mod.TIME_BASED_STATES:
            return
        idx = anim.frame_index()
        if idx is None:
            return
        pm = anim.sprites.frame(state, idx)
        if pm is None:
            return
        bx, by = CHARA_BALLOON_CANVAS_OFF
        p.drawPixmap(CHARA_POS[0] + bx - ox, CHARA_POS[1] + by - oy, pm)

    def draw_balloon_front(self, p, ox=0, oy=0):
        """判定枠の風船を、どんちゃんより手前に描く。

        レーン側の描画は _hide_balloon_sprite で止めてあるので、ここが
        唯一の描き手。並びは レーン < どんちゃん < 風船。"""
        cp = self.chart_preview
        try:
            now = cp.game_state()[0]
            f = cp.balloon_sprite_frame(now)
        except Exception:  # noqa: BLE001
            return
        if f is None:
            return
        cx = LANE_X + JUDGE_X_IN_LANE - ox
        cy = LANE_Y + LANE_H // 2 - oy
        cp.draw_balloon_sprite_at(p, cx, cy, f)

    def _draw_chara(self, p, now):
        """どんちゃんを1コマ描く。素材が無ければ何もしない。

        コマは拍で進める(時間で進めると BPM が変わったとき曲と合わない)。
        ゴーゴーの出入りは chart_preview に問い合わせる — レーンが持って
        いる権威データをそのまま使うので、HUD 側で数え直す必要がない。"""
        anim = self._chara
        if anim is None or not anim.sprites.available():
            return
        try:
            bpm = self.chart_preview.bpm_at(now)
            gogo = self.chart_preview.is_gogo(now)
            balloon = self.chart_preview.balloon_state(now)
        except Exception:  # noqa: BLE001
            bpm, gogo, balloon = 0.0, False, None
        state, idx = anim.update(now, bpm, gogo, balloon)
        if idx is None:
            return
        pm = anim.sprites.frame(state, idx)
        if pm is None:
            return
        if state in chara_mod.TIME_BASED_STATES:
            # 風船の絵はレーンより手前に出したいので、ここでは描かない
            # (draw_chara_front が板の側で描く)。状態を進めるのが目的。
            return
        # ふだん/ゴーゴーは絵の中身で合わせる。状態ごとに画布もキャラの
        # 立ち位置も違うので、画布の左上を固定すると切り替わった瞬間に飛ぶ。
        box = anim.sprites.content_box(state)
        if box is None:
            p.drawPixmap(CHARA_POS[0], CHARA_POS[1], pm)
            return
        p.drawPixmap(CHARA_ANCHOR[0] - box[0], CHARA_ANCHOR[1] - box[3], pm)

    def _load_title_font(self):
        """曲名用の勘亭流を読む。skin/ の .otf を優先し、無ければ入って
        いそうな家族名を当たる。どれも無ければ None(既定のフォントで描く)。"""
        path = os.path.join(str(settings_mod.skin_dir()), TITLE_FONT_FILE)
        if os.path.exists(path):
            fid = QFontDatabase.addApplicationFont(path)
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                return fams[0]
        have = set(QFontDatabase.families())
        for fam in TITLE_FONT_FALLBACKS:
            if fam in have:
                return fam
        return None

    def _draw_title(self, p):
        """曲名を画面の右上に右詰めで描く。ジャンルは出さない。
        大きさは一定。長い曲名は縮めず、右端を揃えたまま左へ伸ばす。"""
        if not self._title:
            return
        x, y, w, h = TITLE_RECT
        f = QFont(self._title_family) if self._title_family else QFont()
        f.setPixelSize(TITLE_SIZE)
        p.setFont(f)
        # 右端は固定。左へはみ出せるよう、枠の左端を画面左まで広げておく
        # (右詰めなので、収まる曲名の見た目は変わらない)。
        left = TITLE_LEFT_LIMIT
        # 縁取りは文字の輪郭を線でなぞる。drawText では出せないので、
        # 文字を図形(パス)にしてから「線=黒 / 塗り=白」で1回で描く。
        # 右詰めなので、文字幅を測って右端から逆算して置く。
        fm = QFontMetricsF(f)
        tw = fm.horizontalAdvance(self._title)
        bx = max(float(left), x + w - tw)
        by = y + (h + fm.ascent() - fm.descent()) / 2.0
        path = QPainterPath()
        path.addText(QPointF(bx, by), f, self._title)
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        # 先に黒でなぞってから、白で塗り潰す。塗りと線を同時に出すと線が
        # 内側へも太って、勘亭流の細い所が黒く埋まってしまう。
        p.strokePath(path, QPen(QColor(TITLE_OUTLINE), TITLE_OUTLINE_W,
                                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.fillPath(path, QColor(TITLE_COLOR))
        p.restore()

    def _load_gauge_rainbow(self):
        """skin/GaugeRainbow/0..11.png を読む。1枚でも欠けたら None。"""
        base = os.path.join(str(settings_mod.skin_dir()), "GaugeRainbow")
        out = []
        for i in range(GAUGE_RAINBOW_FRAMES):
            path = os.path.join(base, "%d.png" % i)
            if not os.path.exists(path):
                return None
            pm = QPixmap(path)
            if pm.isNull():
                return None
            out.append(pm)
        return out

    def _measure_combo_text_bands(self):
        """Combo/Text.png の「コンボ」2つ(通常色/金色)の縦位置を測る。

        戻り値は ((y, 高さ), (y, 高さ))。素材の上下に余白があり、2つの間にも
        空きがあるので、範囲を等分するのではなく不透明な行のかたまりを
        そのまま拾う。拾えなければ実測値の定数へ落とす。"""
        ct = self._skin.get("combo_text")
        if ct is None:
            return COMBO_TEXT_BAND_FALLBACK
        try:
            import numpy as np
            img = ct.toImage().convertToFormat(QImage.Format_RGBA8888)
            w, h = img.width(), img.height()
            a = np.frombuffer(memoryview(img.constBits()), dtype=np.uint8)
            a = a.reshape(h, img.bytesPerLine() // 4, 4)[:, :w, 3]
            rows = a.max(axis=1) > 16
            runs, start = [], None
            for y in range(h):
                if rows[y] and start is None:
                    start = y
                elif not rows[y] and start is not None:
                    runs.append((start, y - start))
                    start = None
            if start is not None:
                runs.append((start, h - start))
            if len(runs) >= 2:
                return (runs[0], runs[1])
        except Exception:  # noqa: BLE001
            pass
        return COMBO_TEXT_BAND_FALLBACK

    def set_chart(self, preview_data: dict, course_key=None):
        """譜面が変わったときに呼ぶ。スコアの配点をここで決めておく
        (毎フレーム計算しないで済むよう、時刻→点数の表を先に作る)。"""
        from neotja.score import ScoreTimeline

        self._score_timeline = ScoreTimeline(preview_data or {})
        # 魂ゲージの伸び方(おに基準)。譜面が決まればランクが決まる。
        self._gauge = gauge_mod.GaugeModel(preview_data or {})
        self._title = (preview_data or {}).get("title") or ""
        # クリア(ノルマ)に届く音符の時刻。魂の弾けは着弾ごとに出すので
        # ここでは使っていないが、クリアの瞬間そのものを使う演出を足すとき
        # のために求めておく。
        try:
            self._clear_time = self.chart_preview.note_time(self._gauge.notes_to_clear)
        except Exception:  # noqa: BLE001
            self._clear_time = None
        self._course_key = course_key or (preview_data or {}).get("course_key")
        self._course_sym = None
        if self._course_key:
            # コース記号は Easy/Normal/Hard/Oni/Edit の5種。
            name = {"easy": "Easy", "normal": "Normal", "hard": "Hard",
                    "oni": "Oni", "edit": "Edit", "tower": "Oni",
                    "ura": "Oni"}.get(str(self._course_key).lower())
            if name:
                path = os.path.join(str(settings_mod.skin_dir()), "CourseSymbol", name + ".png")
                if os.path.exists(path):
                    pm = QPixmap(path)
                    self._course_sym = pm if not pm.isNull() else None
        # 曲名は静的キャッシュに焼いてある。ここで捨てないと、譜面を切り替えても
        # 前の曲名が出たままになる(update() だけではキャッシュを貼り直すだけ)。
        self._static_layer = None
        self.update()

    # ------------------------------------------------------------------
    def _draw_digits(self, p, sheet, value, *, cols=10, rows=1, row=0,
                     right=None, left=None, y=0, scale=1.0, advance=1.0,
                     y_offsets=None):
        """0-9 が横に並んだシートから数字を描く。right 指定で右詰め。

        advance は「次の字までどれだけ送るか」を1文字枠に対する割合で指定する。
        シートの1枠には字の左右に余白が入っているため、1.0 のまま送ると
        本家より字間が空いて間延びして見える。"""
        if sheet is None:
            return
        cw = sheet.width() / cols
        step = cw * scale * advance
        s = str(int(value))
        x = (right - step * len(s)) if right is not None else (left or 0)
        # 0-9 を「切り出し済み・指定倍率へ縮小済み」でキャッシュしておく。倍率は
        # どの呼び出しでも定数なので毎フレーム変倍する必要がない(スコア+コンボ+
        # 連打数で1フレーム十数回の変倍 blit になっていた)。
        glyphs = self._digit_glyphs(sheet, cols, rows, row, scale)
        for c in s:
            i = int(c)
            dy = (y_offsets or {}).get(i, 0)
            p.drawPixmap(int(x), int(y) + dy, glyphs[i])
            x += step

    def _digit_glyphs(self, sheet, cols, rows, row, scale):
        """数字シートの1行を、指定倍率で縮小済みの 0-9 のリストにして返す。

        描画サイズは従来の QRect(int(w)+1, int(h)+1) と同一にしてあるので
        見た目は変わらない。素材もレイアウトも実行中に変わらないため使い回せる。"""
        # QPixmap.cacheKey() は「その中身」に対する Qt の一意キー。id() だと
        # 素材を読み直したときに同じアドレスが再利用されて、別のシートの
        # キャッシュを引き当てる恐れがある。
        # 倍率だけでなく devicePixelRatio もキーに含める。録画(DPR 1.5)で
        # DPR 1 のまま作った字を貼ると、毎コマ 1.5 倍へ拡大されてしまう
        # (スコア+コンボ+連打数で1コマ十数回)。実寸を dpr 倍で作って
        # setDevicePixelRatio を立てれば、論理サイズは同じまま等倍で貼れる。
        dpr = max(1.0, float(getattr(self, "_dpr", 1.0)))
        key = (sheet.cacheKey(), cols, rows, row, round(float(scale), 4),
               round(dpr, 4))
        got = self._digit_cache.get(key)
        if got is not None:
            return got
        cw, ch = sheet.width() / cols, sheet.height() / rows
        # 論理サイズは従来どおり int(cw*scale)+1。実寸だけ dpr 倍にする。
        dw, dh = int(cw * scale) + 1, int(ch * scale) + 1
        out = []
        for i in range(10):
            cell = sheet.copy(QRect(int(i * cw), int(row * ch), int(cw), int(ch)))
            g = cell.scaled(max(1, int(round(dw * dpr))), max(1, int(round(dh * dpr))),
                            Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            g.setDevicePixelRatio(dpr)
            out.append(g)
        self._digit_cache[key] = out
        return out

    def _draw_left_panel(self, p, combo, score, recent, now):
        """左パネル: スコア / コース記号 / 太鼓 + コンボ / 銘板。"""
        # --- スコア(右詰め) ---
        self._draw_digits(p, self._skin.get("score_digits"), score,
                          cols=10, rows=3, row=0, advance=SCORE_ADVANCE,
                          right=SCORE_RIGHT, y=SCORE_Y, scale=SCORE_SCALE,
                          y_offsets=SCORE_DIGIT_Y_OFF)

        # --- スコアの加算分(スコアの上へ浮かんで消える) ---
        # 軽量では出さない。要るのは「いま何点か」であって、加算の演出は
        # スコアそのものを見れば分かる情報の重ね描きでしかない。
        if self._score_timeline is not None and not self._lite:
            ev = self._score_timeline.last_event(now)
            if ev is not None:
                et, gain = ev
                el = now - et
                if 0.0 <= el < SCORE_GAIN_SEC and gain > 0:
                    q = el / SCORE_GAIN_SEC
                    rise = SCORE_GAIN_RISE * (1.0 - (1.0 - q) ** 2)
                    sheet = self._skin.get("score_digits")
                    if sheet is not None:
                        gh = sheet.height() / 3 * SCORE_GAIN_SCALE
                        p.setOpacity(max(0.0, 1.0 - q))
                        self._draw_digits(p, sheet, gain, cols=10, rows=3,
                                          row=SCORE_GAIN_ROW, advance=SCORE_ADVANCE,
                                          right=SCORE_RIGHT,
                                          y=int(SCORE_Y - gh + SCORE_GAIN_Y_OFF - rise),
                                          scale=SCORE_GAIN_SCALE,
                                          y_offsets=SCORE_DIGIT_Y_OFF)
                        p.setOpacity(1.0)

        # --- コース記号 ---
        if self._course_sym is not None:
            blit_sprite(p, COURSE_SYM_POS[0], COURSE_SYM_POS[1],
                        self._course_sym, self._dpr)

        # --- 太鼓 ---
        drum = self._skin.get("drum")
        dx, dy = DRUM_POS
        if drum is not None:
            blit_sprite(p, dx, dy, drum, self._dpr)
            # 連打・風船を叩いている間は、そちらの打に合わせて光らせる。
            # 打の時刻は打音と同じ決め方なので、音と手が合う。連打は面だけ
            # (打音も面だけにしてある)。
            hit = None
            try:
                tick = self.chart_preview.roll_tick(now)
            except Exception:  # noqa: BLE001
                tick = None
            if tick is not None:
                hit = (tick[0], "1", tick[1])
            elif recent is not None:
                hit = recent
            # 叩いた瞬間だけ、その音符の色で光らせる(面=赤 / 縁=水色)。
            if hit is not None:
                elapsed, char, n = hit
                if 0.0 <= elapsed < DRUM_GLOW_SEC:
                    glow = self._skin.get("drum_don" if char in "13" else "drum_ka")
                    if glow is not None:
                        # 本家は両面同時ではなく片面ずつ。音符ごとに
                        # 左→右→左…と交互に光らせる。
                        gw, gh = glow.width(), glow.height()
                        half = gw // 2
                        sx = 0 if (n % 2 == 0) else half
                        # 叩いた直後がいちばん明るく、すぐ消える。
                        p.setOpacity(max(0.0, 1.0 - elapsed / DRUM_GLOW_SEC))
                        p.drawPixmap(dx + sx, dy, glow, sx, 0, gw - half, gh)
                        p.setOpacity(1.0)

        # --- コンボ(太鼓の上に重ねる) ---
        # 位置の基準は太鼓ではなく COMBO_ANCHOR。太鼓だけを動かしても
        # コンボが一緒に動かないようにするため。
        cx0, cy0 = COMBO_ANCHOR
        dw = drum.width() if drum is not None else 120
        if combo > 0:
            key = ("combo_gold" if combo >= COMBO_GOLD_AT else
                   "combo_silver" if combo >= COMBO_SILVER_AT else "combo_white")
            sheet = self._skin.get(key)
            if sheet is not None:
                step = sheet.width() / 10 * COMBO_SCALE * COMBO_ADVANCE
                # 基準の中心に対して左右対称に置く。
                right = cx0 + dw // 2 + int(step * len(str(combo))) // 2 + COMBO_X_OFF
                self._draw_digits(p, sheet, combo, right=right, advance=COMBO_ADVANCE,
                                  y=cy0 + COMBO_Y_OFF, scale=COMBO_SCALE)
            ct = self._skin.get("combo_text")
            if ct is not None:
                # 素材には「コンボ」が縦に2つ入っている。上段=通常色 /
                # 下段=金 とみなし、数字の色に合わせて片方だけを切り出す。
                # 位置は実測した帯を使うので、どちらの色でも同じ場所に出る。
                bands = getattr(self, "_combo_text_bands", COMBO_TEXT_BAND_FALLBACK)
                src_y, band = bands[1] if combo >= COMBO_GOLD_AT else bands[0]
                p.drawPixmap(QRect(cx0 + dw // 2 - ct.width() // 2 + COMBO_TEXT_X_OFF,
                                   cy0 + COMBO_TEXT_Y_OFF, ct.width(), band),
                             ct, QRect(0, src_y, ct.width(), band))

        # --- 銘板 ---
        np_ = self._skin.get("nameplate")
        if np_ is not None:
            blit_sprite(p, NAMEPLATE_POS[0], NAMEPLATE_POS[1], np_, self._dpr)

    def _draw_gauge(self, p, ratio, now):
        """魂ゲージ。全良前提なので「叩いた数 / 総数」で満ちていく。"""
        base = self._skin.get("gauge_base")
        fill = self._skin.get("gauge")
        gx, gy = GAUGE_POS
        ratio = max(0.0, min(1.0, ratio))
        if base is not None:
            p.drawPixmap(gx, gy, base, 0, 0, base.width(), GAUGE_BAR_H)
        # 1本ぶんの幅に切り下げる。中途半端に伸びず、素材の縞と揃って
        # 「カチッ、カチッ」と1本ずつ増える。
        step = fill.width() / float(GAUGE_BLOCKS) if fill is not None else 0.0
        blocks = int(ratio * GAUGE_BLOCKS + 1e-9)
        wpx = 0
        if fill is not None:
            wpx = int(round(blocks * step))
            if wpx > 0:
                p.drawPixmap(gx, gy, fill, 0, 0, wpx, GAUGE_BAR_H)
        # 入魂(満タン)のあいだはゲージが虹色になる。素材のマスクがゲージ本体と
        # 一致しているので、同じ位置に重ねるだけで色だけ入れ替わる。
        if ratio >= 1.0 and self._gauge_rainbow:
            i = int(now / GAUGE_RAINBOW_FRAME_SEC) % len(self._gauge_rainbow)
            blit_sprite(p, gx, gy, self._gauge_rainbow[i], self._dpr)
        # 「クリア」の文字。クリア圏の左上に置く。虹色のときも読めるよう、
        # ゲージの色を差し替えたあとに描く(先に描くと虹に塗り潰される)。
        src = fill if fill is not None else base
        if src is not None:
            (lx, ly), (dx_, dy_), gw, gh = GAUGE_CLEAR_GLYPH
            lit = wpx > GAUGE_CLEAR_STEP_X if fill is not None else False
            sx, sy = (dx_, dy_) if lit else (lx, ly)
            p.drawPixmap(gx + GAUGE_CLEAR_STEP_X + GAUGE_CLEAR_TEXT_OFF[0],
                         gy + GAUGE_CLEAR_TEXT_OFF[1], src, sx, sy, gw, gh)

        # 飛んできた音符が魂に当たった瞬間の弾け。以前は「クリアに届いた
        # 瞬間の演出」だと思って1回だけ出していたが、実機のキャプチャを見ると
        # 音符が着弾するたびに出ている(叩き続けているあいだ出っぱなしになる)。
        # 着弾の時刻は「叩いた時刻 + 飛行時間」なので、それだけで描ける。
        burst = self._skin.get("soul_burst")
        if burst is not None:
            n = SOUL_BURST_FRAMES - SOUL_BURST_FIRST
            span = n * SOUL_BURST_FRAME_SEC
            el = None
            try:
                hits = self.chart_preview.recent_hits(now, SOUL_FLY_SEC + span)
            except Exception:  # noqa: BLE001
                hits = []
            for elapsed, _c in hits:      # 新しい順。最初に着弾済みのものを採る
                e = elapsed - SOUL_FLY_SEC
                if e >= 0.0:
                    el = e
                    break
            if el is not None and el < span:
                f = SOUL_BURST_FIRST + int(el / SOUL_BURST_FRAME_SEC)
                c = SOUL_BURST_CELL
                d = c * SOUL_BURST_SCALE
                cx = SOUL_POS[0] + SOUL_CELL / 2.0
                cy = SOUL_POS[1] + SOUL_CELL / 2.0
                blit_fitted(p, int(cx - d / 2), int(cy - d / 2), int(d), int(d),
                            burst, self._dpr, src=QRect(f * c, 0, c, c))

        # 魂の文字。ゲージの右端に置き、クリア圏まで溜まったら光る段に変える。
        soul = self._skin.get("soul")
        if soul is not None:
            row = 1 if ratio >= GAUGE_CLEAR_RATIO else 0
            blit_fitted(p, SOUL_POS[0], SOUL_POS[1], SOUL_CELL, SOUL_CELL,
                        soul, self._dpr,
                        src=QRect(0, row * SOUL_CELL, SOUL_CELL, SOUL_CELL))

    def _white_note(self, char, sprite):
        """音符の絵を真っ白に染めたもの(形はそのまま)。着弾の白飛ばし用。"""
        pm = self._white_note_cache.get(char)
        if pm is None or pm.size() != sprite.size():
            pm = QPixmap(sprite.size())
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.drawPixmap(0, 0, sprite)
            # 元の絵の形(アルファ)だけ残して、中身を白で置き換える。
            q.setCompositionMode(QPainter.CompositionMode_SourceIn)
            q.fillRect(pm.rect(), QColor("#ffffff"))
            q.end()
            self._white_note_cache[char] = pm
        return pm

    def draw_soul_flights(self, p, ox=0, oy=0):
        """叩いた音符を判定円から魂ゲージへ飛ばす。

        レーンの外(黒枠やゲージの上)まで出るので、レーン側ではなく画面側が
        描く。ox/oy は描き先の板の左上。持ち回る状態は無く、その時刻に
        飛んでいる音符を毎回引き直すだけなので、シークしても止めても
        矛盾しない。"""
        # 軽量では出さない。行き先の魂ゲージごと描いていないので、飛んだ先に
        # 何も無い(音符が画面の右上へ吸い込まれて消えるだけの絵になる)。
        if self._lite:
            return
        cp = self.chart_preview
        try:
            now = cp.game_state()[0]
            hits = cp.recent_hits(now, SOUL_FLY_SEC + SOUL_LAND_SEC)
        except Exception:  # noqa: BLE001
            return
        if not hits:
            return
        dpr, dev_off = dev_info(p)
        sx, sy = self.judge_center()
        ex = SOUL_POS[0] + SOUL_CELL / 2.0
        ey = SOUL_POS[1] + SOUL_CELL / 2.0
        # 上へ膨らむ二次ベジェ。q=0.5 の点は (始点 + 2*制御点 + 終点)/4 なので、
        # てっぺんを通したい点から制御点はこう逆算できる。
        cx = (4.0 * SOUL_FLY_APEX_X - sx - ex) / 2.0
        cy = (4.0 * SOUL_FLY_APEX_Y - sy - ey) / 2.0
        for elapsed, char in hits:
            sprite, _big = cp.note_sprite(char)
            if sprite is None or sprite.width() <= 0:
                continue
            w = sprite.width() * SOUL_FLY_SCALE_END
            h = sprite.height() * SOUL_FLY_SCALE_END
            if elapsed < SOUL_FLY_SEC:
                # --- 飛行中。大きさも濃さも変えない(OpenTaiko と同じ) ---
                q = max(0.0, elapsed / SOUL_FLY_SEC)
                u = 1.0 - q
                x = u * u * sx + 2 * u * q * cx + q * q * ex
                y = u * u * sy + 2 * u * q * cy + q * q * ey
                p.setOpacity(1.0)
                blit_fitted(p, x - w / 2.0 - ox, y - h / 2.0 - oy, w, h,
                            sprite, dpr, dev_off)
                continue
            # --- 着弾後。魂の上に残しつつ、白へ寄せながら消す ---
            t = (elapsed - SOUL_FLY_SEC) / SOUL_LAND_SEC
            if t >= 1.0:
                continue
            rx, ry = ex - w / 2.0 - ox, ey - h / 2.0 - oy
            # 着いた瞬間は素の音符、そこから白へ寄せながら全体を薄くする。
            alpha = 1.0 - t
            p.setOpacity(alpha * (1.0 - t))
            blit_fitted(p, rx, ry, w, h, sprite, dpr, dev_off)
            p.setOpacity(alpha * t)
            blit_fitted(p, rx, ry, w, h, self._white_note(char, sprite),
                        dpr, dev_off)
        p.setOpacity(1.0)

    def _draw_lane_readouts(self, p, now, recent):
        """連打・風船の打数を、本家と同じ金の扇で出す。
        (「良」はレーンにかぶるので _JudgeOverlay が手前に描く)"""
        try:
            count, kind, alpha = self.chart_preview.live_tap_state(now)
        except Exception:  # noqa: BLE001
            count, kind, alpha = None, None, 0.0
        if count is None or alpha <= 0.0:
            return
        # 消え際だけ薄くする。扇と数字をまとめて薄くしたいので、
        # この関数の描画すべてに掛ける。
        p.save()
        p.setOpacity(alpha)
        try:
            self._draw_tap_readout(p, count, kind)
        finally:
            p.restore()

    def _draw_tap_readout(self, p, count, kind):
        """連打の扇 / 風船の吹き出しを描く(濃さは呼び出し側が設定済み)。"""

        num = self._skin.get("roll_num")
        # --- 風船・くす玉: 白い吹き出しに残り打数 ---
        if kind == "balloon":
            bl = self._skin.get("balloon")
            if bl is not None and num is not None:
                bw, bh = bl.width() * BALLOON_SCALE, bl.height() * BALLOON_SCALE
                p.drawPixmap(QRect(int(BALLOON_CENTER_X - bw / 2),
                                   int(BALLOON_BOTTOM - bh), int(bw), int(bh)), bl)
                bx0 = BALLOON_CENTER_X - bw / 2.0
                by0 = BALLOON_BOTTOM - bh
                ncx = bx0 + BALLOON_NUM_ANCHOR[0] * BALLOON_SCALE + BALLOON_NUM_OFF[0]
                ncy = by0 + BALLOON_NUM_ANCHOR[1] * BALLOON_SCALE + BALLOON_NUM_OFF[1]
                nw, nh = ROLL_NUM_CELL
                gw, gh = nw * BALLOON_NUM_SCALE, nh * BALLOON_NUM_SCALE
                step = gw * ROLL_NUM_ADVANCE
                text = str(int(count))
                x = ncx - step * len(text) / 2.0
                y = ncy - gh / 2.0
                for c in text:
                    p.drawPixmap(QRect(int(x), int(y), int(gw) + 1, int(gh) + 1),
                                 num, QRect(int(c) * nw, 0, nw, nh))
                    x += step
                return
            # 素材が無ければ下の文字表示へ落とす。

        fan = self._skin.get("roll_fan")
        if kind == "balloon" or fan is None or num is None:
            # 素材が無い環境では従来どおり文字で出す。
            jx = LANE_X + JUDGE_X_IN_LANE
            p.setPen(QColor("#ffd24a"))
            f = p.font()
            f.setPointSize(22)
            f.setBold(True)
            p.setFont(f)
            p.drawText(jx - 100, TAP_COUNT_BOTTOM - 34, 200, 34,
                       Qt.AlignHCenter | Qt.AlignBottom, str(count))
            return

        # 「連打!!」の札は最後のコマにしか入っていない。1打でも入ったら
        # 数字と一緒に出したいので、開く途中のコマは使わず一気に開く。
        #
        # ここに来る時点で kind は必ず "roll"(風船は上の分岐で return 済み)、
        # つまり連打区間の真っ最中。以前は「1打でも入ったら開く」つもりで
        # count>=1 を見ていたが、count は _live_top_count() が
        # int(hits * 経過割合) で補間した値なので、1小節に満たない極短の連打
        # (16分1つぶんなど)では区間が終わるまで count が 0 のまま=扇が
        # 閉じたコマで止まって見えるバグになっていた。連打区間に入っている
        # こと自体が「開いている」の条件なので、count を見ずに開き切ったコマ
        # を出す。
        cw, ch = ROLL_FAN_CELL
        frame = ROLL_FAN_FRAMES - 1
        dw, dh = cw * ROLL_FAN_SCALE, ch * ROLL_FAN_SCALE
        p.drawPixmap(QRect(int(ROLL_FAN_CENTER_X - dw / 2),
                           int(ROLL_FAN_BOTTOM - dh), int(dw), int(dh)),
                     fan, QRect(frame * cw, 0, cw, ch))

        # 打数。専用の数字シート(0..9 が横に10個)を中央そろえで。
        # 0 のときは数字を出さない(開き始めた扇だけ見せる)。
        if int(count) <= 0:
            return
        fx0 = ROLL_FAN_CENTER_X - dw / 2.0
        fy0 = ROLL_FAN_BOTTOM - dh
        ncx = fx0 + ROLL_NUM_ANCHOR[0] * ROLL_FAN_SCALE + ROLL_NUM_OFF[0]
        ncy = fy0 + ROLL_NUM_ANCHOR[1] * ROLL_FAN_SCALE + ROLL_NUM_OFF[1]
        nw, nh = ROLL_NUM_CELL
        gw, gh = nw * ROLL_NUM_SCALE, nh * ROLL_NUM_SCALE
        step = gw * ROLL_NUM_ADVANCE
        text = str(int(count))
        x = ncx - step * len(text) / 2.0
        y = ncy - gh / 2.0
        for c in text:
            p.drawPixmap(QRect(int(x), int(y), int(gw) + 1, int(gh) + 1),
                         num, QRect(int(c) * nw, 0, nw, nh))
            x += step

    def set_compact(self, compact: bool):
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self._apply_geometry()         # 高さが変わるので焼き直す
        self.update()

    def is_compact(self) -> bool:
        return self._compact

    def set_lite(self, lite: bool):
        """軽量モードの入り切り。

        レーン(ChartPreviewWidget)側には何も伝えない。軽量で止めるのは
        「画面側が描いている背景と装飾」だけで、レーンの中(音符・ゴーゴー・
        判定の火花・打音表記の帯)は通常再生と同じものをそのまま描くため。
        「良」と風船を描く板(_JudgeOverlay / _FlightOverlay)も出したままに
        して、その中で軽量に要らないもの(どんちゃん・魂の飛翔)だけを
        描き手側で落とす(draw_chara_front / draw_soul_flights)。"""
        lite = bool(lite)
        if lite == self._lite:
            return
        self._lite = lite
        self._apply_geometry()      # 高さと静的キャッシュの面倒はここで見る
        self.update()

    def is_lite(self) -> bool:
        return self._lite

    def _screen_height(self) -> int:
        if self._lite:
            return SCREEN_H_LITE
        return SCREEN_H_COMPACT if self._compact else SCREEN_H_FULL

    def _apply_geometry(self):
        """画面の高さを、いまの compact/lite に合わせ直す。"""
        self.setFixedSize(SCREEN_W, self._screen_height())
        self._static_layer = None      # 大きさ/中身が変わるので焼き直す

    # ------------------------------------------------------------------
    def _build_static_layer(self, dpr=1.0):
        """毎フレーム同じ絵になる部分(下地・上下背景・フッター・曲名・左パネル・
        黒枠)を1枚のピクスマップに焼く。

        これらは再生中いっさい変化しないのに、以前は毎フレーム 6〜7 回の
        drawPixmap(うち 951x224 のアルファ付き黒枠を4分割)で描き直していた。
        1回だけ焼いて以降は不透明な blit 1回にする(波形ウィジェットで音符を
        ピクスマップ化したのと同じ手口)。compact 切替やスキン再読込のときは
        _static_layer = None にして作り直させること。"""
        # **不透明(RGB32)** の QImage に焼いてから QPixmap にする。QPixmap に
        # 直接描くとアルファ付き(ARGB32_Premultiplied)になり、毎フレームの
        # 貼り付けが SourceOver 合成になって、元の「必要な部分だけ数回 blit」
        # より遅くなってしまう。不透明なら単純コピーで済む。
        # 重ね式の上背景は毎フレーム動くので、静的キャッシュ側は上の帯を
        # 透明にしておき、paintEvent で「動く背景 → キャッシュ」の順に貼る。
        # このときだけアルファ付きになる(貼るのが単純コピーでなくなる)。
        # `dpr` は貼り付け先(録画用 QImage)の devicePixelRatio。実寸を dpr 倍で
        # 取り、QImage 側にも同じ dpr を立てておくと、_paint_static は論理座標の
        # ままで中身が dpr 倍の細かさで焼き直される。貼るときは DPR が一致する
        # ので拡大なしの単純ブリットになる。
        layered = self._layered_top()
        fmt = (QImage.Format_ARGB32_Premultiplied if layered
               else QImage.Format_RGB32)
        dpr = max(1.0, float(dpr))
        img = QImage(int(round(self.width() * dpr)),
                     int(round(self.height() * dpr)), fmt)
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.transparent if layered else QColor("#0d1117"))
        p = QPainter(img)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self._paint_static(p)
        p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        return pm

    def _layered_top(self):
        """上背景を重ね式で描くか(素材が揃っているときだけ)。"""
        # 軽量では重ね式を使わない。重ね式は毎フレーム「3層を帯へ焼く/位相が
        # 進んだら焼き直す + 画面幅ぶんの窓を切って貼る」が要るうえ、静的
        # キャッシュ側をアルファ付きにするので、貼り付けまで SourceOver 合成に
        # なってしまう。軽量は上背景を出さない = キャッシュを不透明(単純コピー)
        # に戻せる、という二重の効き方をする。
        return (not self._lite and SHOW_BACKGROUND_TOP and BG_TOP_STYLE == "layered"
                and self._skin.get("bg_up_base") is not None)

    def _paint_static(self, p):
        """静的部分の描画本体(キャッシュ作成時に1回だけ呼ばれる)。"""

        # --- 上部背景 (0..188 が見える範囲) ---
        # 重ね式のときはここでは何も敷かない(paintEvent が毎フレーム描く)。
        # 絵を出さないときは黒で塗る。下地(#0d1117)のままだと少し青みが
        # 残って「消し忘れ」に見えるので、はっきり黒にする。
        if self._layered_top():
            bg = None
        elif self._lite:
            # 軽量: 背景の絵は上も下も出さない。画面ぜんぶを黒で塗っておく
            # (ここはキャッシュなので1回きり)。下地(#0d1117)のままだと青みが
            # 残って「消し忘れ」に見えるので、はっきり黒で埋める。
            p.fillRect(QRect(0, 0, SCREEN_W, self.height()), QColor(BACKGROUND_TOP_COLOR))
            bg = None
        else:
            if not SHOW_BACKGROUND_TOP:
                p.fillRect(QRect(0, 0, SCREEN_W, BG_TOP_H),
                           QColor(BACKGROUND_TOP_COLOR))
            bg = self._skin.get("bg_top") if SHOW_BACKGROUND_TOP else None
        if bg is not None:
            # 素材(1280x316)を 188px で切ると絵が途中で断ち切られるので、
            # 丸ごと描いて下端(188 以降)はレーン一式で隠す。地面の線が
            # レーンの上に来るので、本家と同じ「奥行きのある」見え方になる。
            p.drawPixmap(0, 0, bg)

        if not self._compact and not self._lite and SHOW_BACKGROUND:
            # --- 下部背景 (360..720) ---
            bd = self._skin.get("bg_down")
            if bd is not None:
                p.drawPixmap(0, BG_DOWN_Y, bd)
            # --- フッター (676..720) ---
            ft = self._skin.get("footer")
            if ft is not None:
                p.drawPixmap(0, FOOTER_Y, ft)

        # --- 曲名(録画の 1280x720 のときだけ。通常再生の窓では出さない) ---
        if not self._compact:
            self._draw_title(p)

        # --- 左パネル (0,188 - 333x176) ---
        panel = self._skin.get("panel")
        if panel is not None:
            p.drawPixmap(PANEL_X, PANEL_Y, panel)
        else:
            p.fillRect(QRect(PANEL_X, PANEL_Y, PANEL_W, PANEL_H), QColor("#8c1d1d"))

        # --- レーンと魂ゲージを囲む黒枠 ---
        # 素材そのもの。中の窓は透明なのでレーン(子ウィジェット)がそのまま見える。
        frame = self._skin.get("lane_frame")
        if frame is not None:
            fx, fy = LANE_FRAME_POS
            # 魂ゲージを囲う箱(素材の y=0..FRAME_TOP_BAND)だけ独立して動かす。
            # 軽量ではゲージそのものを出さないので、この箱も描かない。中身の
            # 無い枠だけが宙に浮いて残ると「消し忘れ」に見えるため。
            if not self._lite:
                p.drawPixmap(fx + FRAME_TOP_X_OFF, fy + FRAME_TOP_Y_OFF, frame,
                             0, 0, frame.width(), FRAME_TOP_BAND)
            # レーンを囲う部分(上辺・左右・下辺)は動かさない。透明窓がレーンと
            # 1px 合わせなので、ここを動かすとレーンとずれる。
            # ただし y=190..214 の左端1pxだけは打音表記帯の色が入っていて、
            # こちらの帯とは繋がらず浮いて見えるので、その帯だけ x=1 から描く。
            s0, s1 = FRAME_SLIVER_Y0, FRAME_SLIVER_Y1
            fw = frame.width()
            p.drawPixmap(fx, fy + FRAME_TOP_BAND, frame,
                         0, FRAME_TOP_BAND, fw, s0 - FRAME_TOP_BAND)
            p.drawPixmap(fx + 1, fy + s0, frame, 1, s0, fw - 1, s1 - s0)
            # 下辺だけ 3px 上げて、帯の下端(352)と枠の下辺(355)の隙間を潰す。
            p.drawPixmap(fx, fy + s1 + FRAME_BOTTOM_Y_OFF, frame,
                         0, s1, fw, frame.height() - s1)
        else:
            p.fillRect(QRect(LANE_X, LANE_Y - 2, LANE_W, 2), QColor(0, 0, 0, 220))
            p.fillRect(QRect(LANE_X, LANE_Y + LANE_H + SE_STRIP_H, LANE_W, 2),
                       QColor(0, 0, 0, 220))

    def _bg_up_cell(self, key, cell, col, row):
        """色違いが詰まったシートから 1 駒だけ切り出す(結果は使い回す)。"""
        sheet = self._skin.get(key)
        if sheet is None:
            return None
        ck = (key, col, row)
        pm = self._bg_up_cache.get(ck)
        if pm is None:
            cw, ch = cell
            pm = sheet.copy(QRect(col * cw, row * ch, cw, ch))
            self._bg_up_cache[ck] = pm
        return pm

    def _tile_row(self, p, pm, phase, width):
        """1枚を横に敷き詰めて、左へ phase 画素ずらして描く。

        置き方は元のまま(float で持って int() で切り捨て)。ここを変えると
        1px 単位で絵が変わるので、式には一切手を触れていない。違うのは
        「どこへ何 px ぶん敷くか」を引数で受けるようになったことだけ。"""
        if pm is None:
            return
        w = pm.width()
        x = -phase
        while x < width:
            p.drawPixmap(int(x), 0, pm)
            x += w

    #: 焼いた帯に持たせる余分な幅(画素)。これを使い切るまで焼き直さない。
    #: 67px/秒 で流れるので 384px ≒ 5.7 秒ぶん。
    BG_STRIP_MARGIN = 384

    def _bg_up_layers(self):
        """上背景の3層(奥→手前)。素材が無ければ None を含む。"""
        row = BG_UP_COLOR_ROW
        return (self._bg_up_cell("bg_up_base", BG_UP_BASE_CELL, row, 0),
                self._bg_up_cell("bg_up_flower", BG_UP_FLOWER_ROW, 0, row),
                self._bg_up_cell("bg_up_chara", BG_UP_CHARA_ROW, 0, row))

    def _bg_up_phases(self, layers, now):
        """各層の位相 f = (now*67) % 幅。そのまま _tile_row へ渡す。"""
        return tuple(0.0 if pm is None
                     else (now * -BG_UP_SCROLL_VX) % pm.width()
                     for pm in layers)

    @staticmethod
    def _bg_up_keys(layers, phases):
        """位相から「絵が決まる整数」を作る。

        _tile_row の置き場所は int(-f) と int(k*w - f)、つまり
        floor(f) と ceil(f) だけで決まる(f の小数部そのものは効かない)。
        この2つが全層で同じだけ進んでいれば、絵は帯を平行移動したものに
        等しい — 帯を焼き直さずに窓をずらすだけで済む。

        素材が欠けている層は絵に効かないので数に入れない(入れると位相が
        いつも 0 のまま = 常に「ずれた」判定になり、毎フレーム焼き直して
        しまう)。"""
        return tuple((math.floor(f), math.ceil(f))
                     for pm, f in zip(layers, phases) if pm is not None)

    def _draw_bg_top_layers(self, p, now):
        """上背景を3枚重ねで描く。3枚とも同じ速さで左へ流れる。

        3枚を毎フレーム敷き詰めると 11 回の drawPixmap(うち2枚はアルファ付き
        の全面)になり、1フレーム 1.1ms を食っていた。3枚とも同じ速さで流れる
        ので、重ねた結果は「1枚の帯が左へ流れているだけ」。画面幅+余白ぶんの
        帯に焼いておいて、毎フレームはそこから窓を1回貼るだけにする。焼き
        直すのは、どれかの層が一周して並びがずれたとき(いちばん短い地で
        5 秒に1回)か、余白を使い切ったとき(5.7 秒)だけ。

        帯はアルファ付きのまま持ち、貼るのも SourceOver のまま。地の素材にも
        わずかにアルファ 253/254 の画素があり(全体の 0.03%)、下が透ける作りに
        なっているため — 不透明に潰すとそこだけ色が変わる。SourceOver は
        結合則が成り立つので、3枚を先に重ねてから1回で貼っても、1枚ずつ
        貼ったのと同じ絵になる。"""
        layers = self._bg_up_layers()
        if layers[0] is None:
            return
        phases = self._bg_up_phases(layers, now)
        keys = self._bg_up_keys(layers, phases)
        d = None
        if (self._bg_strip is not None and self._bg_strip_keys is not None
                and len(self._bg_strip_keys) == len(keys)):
            # 全層の floor/ceil が同じだけ進んでいれば平行移動と同じ。
            ds = set()
            for (a, b), (a0, b0) in zip(keys, self._bg_strip_keys):
                ds.add(a - a0)
                ds.add(b - b0)
            if len(ds) == 1:
                dd = ds.pop()
                if 0 <= dd <= self.BG_STRIP_MARGIN:
                    d = dd
        if d is None:
            self._bake_bg_strip(layers, phases, keys)
            d = 0
        p.drawPixmap(0, 0, self._bg_strip, d, 0, SCREEN_W, BG_TOP_H)

    def _bake_bg_strip(self, layers, phases, keys):
        """上背景3層を、画面幅+余白の帯1枚に焼く。"""
        sw = SCREEN_W + self.BG_STRIP_MARGIN
        img = QImage(sw, BG_TOP_H, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        q = QPainter(img)
        for pm, f in zip(layers, phases):
            self._tile_row(q, pm, f, sw)
        q.end()
        self._bg_strip = QPixmap.fromImage(img)
        self._bg_strip_keys = keys

    def _rainbow_head(self):
        """虹の先端に乗せる大ドンの顔(Notes.png から1枚切って覚える)。"""
        pm = self._rainbow_head_pm
        if pm is None:
            sheet = self._skin.get("notes")
            if sheet is None:
                return None
            c = RAINBOW_HEAD_CELL
            col, row = RAINBOW_HEAD_INDEX
            if sheet.width() < (col + 1) * c or sheet.height() < (row + 1) * c:
                return None
            pm = sheet.copy(QRect(col * c, row * c, c, c))
            self._rainbow_head_pm = pm
        return pm

    def _rainbow_band_center(self, col):
        """虹の絵の col 列目で、帯の中心が上から何pxか。無ければ None。

        虹は弧なので、先端の高さは列ごとに違う。素材のアルファから列ごとに
        1回だけ測って覚えておく(毎フレーム測ると重い)。"""
        if self._rainbow_centers is None:
            pm = self._skin.get("rainbow")
            if pm is None:
                return None
            img = pm.toImage()
            w, h = img.width(), img.height()
            centers = []
            for x in range(w):
                top, bottom = -1, -1
                for y in range(h):
                    if img.pixelColor(x, y).alpha() > 8:
                        if top < 0:
                            top = y
                        bottom = y
                centers.append(None if top < 0 else (top + bottom) / 2.0)
            self._rainbow_centers = centers
        if 0 <= col < len(self._rainbow_centers):
            return self._rainbow_centers[col]
        return None

    def _draw_rainbow(self, p, now):
        """風船が割れたあとにかかる虹。上背景の上・レーンより奥に描く。"""
        if not SHOW_BALLOON_RAINBOW:
            return
        pm = self._skin.get("rainbow")
        if pm is None:
            return
        span = RAINBOW_TICKS * RAINBOW_TICK_SEC
        try:
            el = self.chart_preview.balloon_pop_elapsed(now, span)
        except Exception:  # noqa: BLE001
            return
        if el is None:
            return
        c = int(el / RAINBOW_TICK_SEC)
        w, h = pm.width(), pm.height()
        x, y = RAINBOW_POS
        if c < RAINBOW_HALF:
            nx = w * c // RAINBOW_WIPE_DEN
            if nx > 0:
                p.drawPixmap(x, y, pm, 0, 0, nx, h)
                head = self._rainbow_head()
                cy = self._rainbow_band_center(min(nx, w - 1))
                if head is not None and cy is not None:
                    k = RAINBOW_HEAD_SCALE
                    hw, hh = head.width() * k, head.height() * k
                    p.drawPixmap(QRectF(x + nx - hw / 2.0,
                                        y + cy - hh / 2.0, hw, hh),
                                 head, QRectF(0, 0, head.width(), head.height()))
        else:
            nx = w * (c - RAINBOW_HALF) // RAINBOW_WIPE_DEN
            if nx < w:
                p.drawPixmap(x + nx, y, pm, nx, 0, w - nx, h)

    def _draw_bg_light(self, p, now):
        """下背景の提灯の光を、加算合成でゆっくり明滅させながら重ねる。"""
        if not (SHOW_BACKGROUND and SHOW_BACKGROUND_LIGHT):
            return
        lit = self._skin.get("bg_down_light")
        if lit is None:
            return
        phase = (now % BG_LIGHT_PERIOD) / BG_LIGHT_PERIOD
        # 0.5+0.5cos なので 1.0(いちばん明るい)から始めてひと呼吸する。
        k = 0.5 + 0.5 * math.cos(2.0 * math.pi * phase)
        p.save()
        # フッター(676..)は光より手前に出るので、そこは塗らない。
        p.setClipRect(QRect(0, BG_DOWN_Y, SCREEN_W, FOOTER_Y - BG_DOWN_Y))
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setOpacity(BG_LIGHT_MIN + (BG_LIGHT_MAX - BG_LIGHT_MIN) * k)
        blit_sprite(p, 0, BG_DOWN_Y, lit, self._dpr)
        p.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        # この1コマの実効 devicePixelRatio。画面は 1.0、録画(1080p)は 1.5。
        # ウィジェットの devicePixelRatio() ではなく deviceTransform を見るのは、
        # 前者が「今ぶら下がっている画面の拡大率」を返すため(画面外へ render する
        # 録画では実際の描画先と一致しない)。deviceTransform は論理座標から
        # 実画素への変換そのものなので、描画先が何であれ正しい。
        self._dpr = float(p.deviceTransform().m11() or 1.0)
        # 静的な下地は1枚のキャッシュを貼るだけ(初回のみ作る)。
        if (self._static_layer is None
                or self._static_layer.deviceIndependentSize().toSize() != self.size()
                or abs(self._static_layer_dpr - self._dpr) > 1e-4):
            self._static_layer = self._build_static_layer(self._dpr)
            self._static_layer_dpr = self._dpr
        # 重ね式の上背景はキャッシュに焼けないので、先に敷いてから被せる。
        if self._layered_top():
            try:
                bg_now = self.chart_preview.game_state()[0]
            except Exception:  # noqa: BLE001
                bg_now = 0.0
            self._draw_bg_top_layers(p, bg_now)
            # 虹は上背景の上、レーン一式より奥。キャッシュを被せる前に描く
            # (1枚絵の上背景のときはキャッシュが不透明なので出ない)。
            self._draw_rainbow(p, bg_now)
        p.drawPixmap(0, 0, self._static_layer)

        # --- HUD(スコア・コンボ・太鼓・ゲージ) ---
        # ゲージは黒枠の上端に載るので、枠を描いたあとに描く。
        # 現在値はレーン側が持っているものをそのまま使う。HUD 用に別のカウントを
        # 持たないので、シークしても再生を止めてもズレようがない。
        try:
            now, combo, recent = self.chart_preview.game_state()
        except Exception:  # noqa: BLE001
            now, combo, recent = 0.0, 0, None
        score = self._score_timeline.at(now) if self._score_timeline else 0
        # 魂ゲージは「叩いた数 × ランク / 10000」。音符数で決まるランクが
        # 1個あたりの点なので、譜面の7割半ばで入魂して以降は満タンのまま
        # — 最後の音符でちょうど満タンになる線形の伸び方とは違う。
        # どんちゃんは下部背景の高さがある表示(録画レイアウト)でだけ出す。
        # compact はレーンの下に余白が無いので置き場所がない。
        # 軽量も出さない — 背景と装飾を落とすのが軽量の目的で、提灯の光は
        # 毎フレームの加算合成、どんちゃんは毎フレーム別画像への差し替えと、
        # どちらも「譜面を読む」のに要らないわりに重い。素材(1_Chara /
        # Bg_down_Light.png)が入っている環境では、ここが軽量の効きの大半。
        if not self._compact and not self._lite:
            self._draw_bg_light(p, now)
            self._draw_chara(p, now)
        self._draw_left_panel(p, combo, score, recent, now)
        # 軽量では魂ゲージ(+「クリア」+ 虹)を出さない。ゲージは 400px 超の
        # 帯とブロックを毎フレーム重ね描きするうえ、このプレビューは全ノーツ
        # 自動「良」なので「叩いた数」以上の情報を持たない = 譜面を読むのに
        # 要らない。連打・風船の金の扇(_draw_lane_readouts)は残す — あれは
        # 「今この連打を何回叩いたか」という譜面そのものの情報。
        if not self._lite:
            self._draw_gauge(p, self._gauge.ratio(combo) if self._gauge else 0.0, now)
        self._draw_lane_readouts(p, now, recent)

        p.end()
        # レーン本体は子ウィジェット(ChartPreviewWidget)が自分で描く。

    # ------------------------------------------------------------------
    def lane_rect(self) -> QRect:
        """レーン本体の矩形(照合・デバッグ用)。"""
        return QRect(LANE_X, LANE_Y, LANE_W, LANE_H)

    def judge_center(self):
        """判定円の中心(画面座標)。実測値と突き合わせるのに使う。"""
        return (LANE_X + JUDGE_X_IN_LANE, LANE_Y + LANE_H // 2)
