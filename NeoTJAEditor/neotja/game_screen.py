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

import bisect
import math
import os

from PySide6.QtCore import Qt, QPointF, QRect, QRectF, QTimer
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QImage,
                           QPainter, QPainterPath, QPen, QPixmap, QTransform)
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
#   ・レーンより手前の板 _LaneOverlay (良・飛ぶ音符・どんちゃん・風船)
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
# 風船の絵だけは本家より大きく見えるので縮める。ふだん/ゴーゴーの絵は素材
# どおりなので触らない。
#
# **口を支点に縮める。** 風船はレーンの判定円に結び目を合わせて描かれるので、
# どんちゃんの口もそこに来ていないと「風船の先と口がずれている」ことになる。
# 左上や足元を支点にすると、縮めたぶんだけ口が judge から離れてしまい、その
# ずれを毎回手で足し直すことになる。支点を口そのものにしておけば、倍率を
# いくつにしても口と風船の先はそろったまま。
CHARA_BALLOON_SCALE = 0.80
#: 口の位置(画布の中の座標)。素材は口が判定円に来るように置かれているので、
#: 判定円の中心から画布の左上を引いた値がそのまま口の座標になる。
CHARA_BALLOON_MOUTH = (LANE_X + JUDGE_X_IN_LANE - (CHARA_POS[0] + 41),
                       LANE_Y + LANE_H // 2 - (CHARA_POS[1] + 10))
CHARA_BALLOON_OFF = (0, 0)       # そこからの微調整 (右, 上が負)

# --- 左パネルの中身(本家スクショから採った位置) ----------------------
# 数字シートの1文字は 29.3x31.3。本家のスコアは高さ25px前後だったので
# 0.8 倍で置く(1.6 倍にしたら6桁がパネルからはみ出した)。
# スコアの位置は実機のキャプチャから測って合わせた(720p 換算)。
#   合計スコア  右端 176.7  字の上端 198.0
#   加算文字    右端 176.7(合計と**同じ**)  字の上端 160.0
# 右端は両方とも同じなので、加算も SCORE_RIGHT を使う。
SCORE_RIGHT, SCORE_Y = 174, 199      # スコアは右詰め
# ふだんの大きさ。点が入った瞬間だけ上へ伸びて戻る。
SCORE_SCALE = 0.98
# 伸び方は実機の映像(1920x1080 / 60fps)を1コマずつ測って合わせた。
# 字の高さ(下端は固定、幅も不変):
#     0ms 36 / 16ms 40(頂点) / 33ms 39 / 50ms 38 / 66ms 37 / 83ms 36 / 116ms 34
# 落ち着いた高さ 34 に対して頂点 40 = 1.18 倍。**1コマで伸びきって、そこから
# 直線的に戻る。** 以前は 1.02/0.98 = 1.04 倍しか伸ばしておらず、しかも
# 山なりだったので、実機の弾む感じが出ていなかった。
#: ふだんの何倍まで伸びるか。
SCORE_POP_RATIO = 1.20
#: 伸びきるまで(実測 1コマ)。
SCORE_POP_ATTACK = 0.016
#: 戻りきるまで(頂点からではなく、点が入った瞬間からの合計)。
#: 実機の実測は 0.116 秒だが、要望で長くしてある。
SCORE_POP_SEC = 0.175
#: 伸縮の刻み。数字の絵は倍率ごとに切り出してキャッシュしているので、連続値
#: のままだとキャッシュが際限なく増える。0.01 刻みなら多くても数個で収まる。
SCORE_POP_STEP = 0.01
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
# 銘板に書き込むプレイヤー名。TNDE の銘板素材は「空の白い板」で、名前は
# ゲーム側がフォントで書く作りなので、こちらでも板の上に書く(旧 skin の
# NamePlate.png は「どんちゃん」が絵として焼き込まれていた)。将来ここを
# 差し替えられるよう定数にしてあるが、設定項目にはしていない。
NAMEPLATE_NAME = "どんちゃん"
# 以下は旧素材 skin/NamePlate.png(280x79)の「どんちゃん」を実測した値。
# 縁取りまで含めた文字の外形が x=95..194 / y=27..49(100x23)だったので、
# その中心と大きさに合わせる。座標は **銘板素材の左上から見た相対** に
# しておくと、NAMEPLATE_POS を動かしても文字が置いていかれない。
NAMEPLATE_NAME_CENTER = (144.5, 38.0)
# 勘亭流で「どんちゃん」を組むと、字の外形は だいたい 4.86*px × 0.945*px。
# 縁取りの太さ(4)を足して 100x23 になるのが px=20。
NAMEPLATE_NAME_SIZE = 20
NAMEPLATE_NAME_COLOR = "#ffffff"
NAMEPLATE_NAME_OUTLINE = "#000000"
NAMEPLATE_NAME_OUTLINE_W = 4.0
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
DRUM_GLOW_SEC = 0.07
# 光が消えるときの余韻。ここまでは明るさそのまま、そこから DRUM_GLOW_FADE_SEC
# かけて消える。以前は最初の瞬間から線形に暗くしていたので、光った瞬間から
# もう暗く、消え際も「ぷつっ」と切れて見えた。
DRUM_GLOW_FADE_SEC = 0.05

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
# Gauge.png は 700px あるが、絵が入っている(不透明な)のは x=1..695 の 695px
# だけで、右端の 4px は余白。以前はここを 700 とみなして 50 等分していたので
# 1本 14.0px となり、満タンで本家より 5px 長く伸びていた。実測に合わせる。
GAUGE_FILL_X0 = 1             # 塗りが始まる x(左の余白)
GAUGE_FILL_W = 695            # 50本ぶんの実寸(1本 13.9px)
GAUGE_CLEAR_TEXT_OFF = (3, 1)     # 段の始まりからの微調整
# --- ノルマに届いたあとの脈打ち ------------------------------------------
# 実機キャプチャ (1920x1080 / 60fps / G:\Captures\...\2026-08-26 12-41-48.mp4)
# をコマ送りで実測した。ゲージは 1920 換算で左端 x=738、1本 21px = 素材の
# 14px の 1.5 倍で、素材と 1:1 で対応する。
#   * 素材の段差(金色・背の高い側)は 50本中 39本目から始まる(素材 x=546 /
#     695 = 78.6%)。ノルマは 40本目(80%)なので、**段差より1本あと**。
#     実測でも、39本のコマ(#169〜#286)は「クリア」の字が灰のままで金色も
#     1本も出ず、40本目が入ったコマ(#287)で
#       - 金色のブロックが1本ぶん(素材 10px)顔を出し
#       - 「クリア」の字が灰 → 白に変わり
#       - 同じコマから下の脈打ちが始まる。
#     いまの実装の「wpx > 547 なら点灯」がちょうどこの境目なので、脈打ちも
#     同じ条件で出す。
#   * 脈打ちは「通常圏(赤)のブロック全体が一斉に金色へ染まって戻る」。
#     地(未達の部分)とクリア圏の金色のブロックはまったく変わらない。
#     x=60 でも x=840 でも同じコマで同じ色 = 波ではなく全体が同時に染まる。
#   * 山は 33コマ = 0.550秒ごとにきっちり繰り返す(#293 から #1217 まで
#     29回、間隔はすべて 33コマ)。1回の山は 12コマ = 0.200秒。
#   * 混ざり具合を緑成分から逆算(赤 #f83606 の G=54、金 #faf805 の G=248 を
#     0 と 1 に取って 29周期を平均)すると、山の頂を 0 コマとして
#       -6:0  -5:.17  -4:.41  -3:.43  -2:.68  -1:.85  0:1.0
#       +1:.85  +2:.61  +3:.59  +4:.34  +5:.17  +6:0
#     直線 1-|k|/6 (0, .17, .33, .50, .67, .83, 1.0) とほぼ重なる。
#     ところどころ同じ値が2コマ続くのは本家側が 30fps 刻みで色を更新して
#     いるためで、そこまで真似る意味は無いので三角波で足りる。
#   * 頂はクリアのコマ(#287)の 6コマ後(#293)。位相の原点をクリアの時刻に
#     取り、その半分ぶんあとを頂にすると実機と揃う。
GAUGE_PULSE_PERIOD_SEC = 33.0 / 60.0
GAUGE_PULSE_HALF_SEC = 6.0 / 60.0
# 金色版を焼くときに敷き直す種。Gauge.png のクリア圏 10本ぶん (x=546..686,
# 上から 22px) をそのまま通常圏へ並べる。縞の区切りは素材の中で 14px 間隔
# (実測: 中心が 12.5 + 14k)なので、140px ずつ左へずらすだけで縞が揃う
# (端で 0.5px ずれるだけ)。上から 22px を取るのは、クリア圏の上端
# ハイライト(白っぽい 4px)を通常圏の上端ハイライトへ重ねるため。
GAUGE_GOLD_SRC = (546, 0, 140, 22)
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
# 位置は OpenTaiko の既定値(CSkin.cs): Game_Effect_Rainbow_X = 360 / _Y = -100。
#
# 長さは本家の実測に合わせてある(60fps の実機映像を1コマずつ数えた)。
#   引く   : f16 に出て f34 で魂まで届く =  18コマ = 0.30 秒
#   止まる : f34 から f47 まで動かない   =  13コマ = 0.22 秒
#   消す   : f47 から f58 で消えきる     =  11コマ = 0.18 秒
# OpenTaiko の既定値は引き 0.656 秒・タメ無しで、本家の倍以上遅かった。
SHOW_BALLOON_RAINBOW = True
RAINBOW_POS = (360, -100)
RAINBOW_TICK_SEC = 0.008
RAINBOW_DRAW_TICKS = 38          # 0.30 秒
RAINBOW_HOLD_TICKS = 28          # 0.22 秒
RAINBOW_ERASE_TICKS = 23         # 0.18 秒
#: 引き終わりのコマ番号。ここから止まる。
RAINBOW_HALF = RAINBOW_DRAW_TICKS
#: 消し始めるコマ番号。
RAINBOW_ERASE_FROM = RAINBOW_DRAW_TICKS + RAINBOW_HOLD_TICKS
RAINBOW_TICKS = RAINBOW_ERASE_FROM + RAINBOW_ERASE_TICKS
#: 帯を W*c/DEN で切り出す。引き終わり(c = 37)で素材の 94.9% = 894px まで出て、
#: 先端がちょうど魂の手前に来る。OpenTaiko の 82/85 = 898px と同じ位置。
RAINBOW_WIPE_DEN = 39
#: 消すほうは最後のコマ(c = 88)でちょうど全部消えるように。
RAINBOW_ERASE_DEN = 22
# 描き足していく間、その先端に大ドンの顔を乗せる。素材どおりに切ると
# 先端が縦にスパッと切れて見えるので、顔で隠して「顔が虹を引いている」
# 見え方にする。Notes.png は 130px のセルが 13列x3行で、(3, 0) が
# 大ドンの叩いた顔。虹の帯の真ん中に乗るよう、その列の不透明な範囲の
# 中心に置く。
RAINBOW_HEAD_CELL = 130
RAINBOW_HEAD_INDEX = (3, 0)      # (列, 行)
RAINBOW_HEAD_SCALE = 1.0
#: 引き終わりから、顔が帯の先端から魂の中心まで**滑らかに移る**のにかける
#: 時間。ここを 0 にすると 1コマで 65px 飛んで、目に見えてブレる(実測: 帯の
#: 先端 (1258,104) から魂の中心 (1238,166) へ一気に動いていた)。
#: 加減速は smoothstep。引き終わりの帯は縦にほとんど動いていない(実測 +0.0
#: px/コマ)ので、出だしが速いカーブ(1-(1-u)^2)だと「1コマ止まって次に 14px
#: 落ちる」段差になる。出だしを 0 から立ち上げて、その段差を無くしてある。
RAINBOW_LAND_MOVE_SEC = 0.10
#: 虹を引き終わったあと、先端の顔が魂の上に残って消えるまでの時間。
#: 引き終わり(RAINBOW_HALF コマ = 0.656 秒)にちょうど魂へ着く動きなので、
#: そこで消してしまうと「どこかへ行った」ように見える。音符が魂へ着弾する
#: ときと同じ長さ・同じ消え方(白へ寄せながら薄く)にそろえてある。
RAINBOW_LAND_SEC = 0.22
RAINBOW_HEAD_OFF = (0, 0)        # 虹の先端からのずれ (右, 上が負)
# 本家は先端に顔が無く、代わりに白〜水色の光と 4本角の星が散っている
# (実機映像 f18〜f29 で確認)。こちらは顔を残したまま、その星だけを足す。
# 星は「そのコマの先端の位置」に生まれて、あとは動かずに縮みながら消える。
# 先端が進むので、結果として尾を引いたように残る。
RAINBOW_SPARK_LIFE = 11          # 1粒が消えるまでのコマ数
RAINBOW_SPARK_PER_TICK = 3       # 1コマに生まれる数
RAINBOW_SPARK_SPREAD = 52.0      # 帯を横切る向きのばらつき (px)
RAINBOW_SPARK_ALONG = 26.0       # 帯に沿う向きのばらつき (px)
RAINBOW_SPARK_MIN = 5.0          # 星の半径 (px)
RAINBOW_SPARK_MAX = 15.0
RAINBOW_SPARK_WAIST = 0.16       # 星のくびれ。小さいほど鋭い
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

# --- 下背景の踊り子 (skin/2_Dancer/Normal/) -------------------------------
# 素材は TNDE の 5_Game/2_Dancer/Normal/2(犬の踊り子)。16コマ 0..15、全コマ
# 213x306 の同寸で、中身は 0.png で (30,45)-(190,267)。
#
# 実機キャプチャ (G:\Captures\CBK譜面ジャーのお部屋\超人\2026-08-26 12-41-48.mp4、
# 1920x1080 / 60fps。曲は「永遠なる絆と想いのキセキ」、クリアは #288) を
# コマ送りで実測して決めた。以下すべて **1280 換算** の値。
#
#  * 横位置: クリア前(4体)の区間で、下背景の帯の時間差分を取ると動く列が
#    4か所 — 中心 215 / 417 / 628 / 844。クリア後(5体)のコマを目で拾うと
#    右端がもう1体 増えて 1053。間隔はおよそ 210px で、真ん中の3体
#    (417/628/844)の中心が 628 ≒ 画面の中央。
#  * 大きさ: 動く行は y=441..675。足元は 675 = フッターの上端(676)ぴったり。
#    素材の立ち姿(14.png)は中身が y=50..267 の 217px で、足元を 676 に
#    置くと頭が 459 に来る — 実機の 456〜460 と一致するので **等倍**。
#  * コマ送り: 左端の1体を 288 コマぶん分類すると、7つの姿勢を
#    「12コマかけて往き → 12コマ止める → 12コマかけて還り → 12コマ止める」
#    の 48コマ周期で繰り返していた(振り付けが変わっても 12コマの止めは
#    変わらない)。レーンを流れる音符の間隔 236px と流れる速さ 1152px/秒 から
#    音符の間隔は 0.205 秒 = 8分。つまり 12コマ = 0.200 秒 = 8分で、
#    **踊りは拍に同期している**(48コマ = 2拍)。よって 16コマを 2拍で1周する。
#  * 出てくるとき: 5体目はクリアの次のコマ(#289)に画面の下から顔を出し、
#    #296 で立ち位置より 32px ほど高く跳ね上がり、#300 頃に落ち着く。
#    = 下から跳び出して一度行き過ぎてから収まる、約 12コマ(0.2秒 = 8分)。
#    実機は跳ねながら一回転するが、その絵は Normal/ の16コマには無いので
#    (TNDE には別に 2_Dancer/In/2 の31コマがある)、こちらは上下の跳ねだけで
#    真似る。
# 踊り子は既定で出さない。動きが本家と違うという作者の判断で一旦しまった。
# 実装(素材の読み込み・立ち位置・出てくる演出・コマ送りの重み)と、実機
# キャプチャから測った値はすべて下に残してあるので、True にすれば戻る。
SHOW_DANCERS = False
DANCER_FRAMES = 16
DANCER_CELL_W, DANCER_CELL_H = 213, 306
#: 素材の中の「体の中心 x」と「足元 y」。ここを画面の立ち位置に合わせる。
DANCER_ANCHOR_IN_CELL = (106, 267)
#: 立ち位置(中心 x)。左から順。
DANCER_SLOT_X = (215, 417, 628, 844, 1053)
#: 最初から居る3体 / 33% で増える1体 / クリアで増える1体。
DANCER_SLOTS_INITIAL = (1, 2, 3)
DANCER_SLOT_AT_RATIO = 0
DANCER_SLOT_AT_CLEAR = 4
#: 4体目が出る魂ゲージの割合(作者の指定)。
DANCER_JOIN_RATIO = 0.33
#: 足元を置く y。フッターの上端に合わせる(実機もここで脚が切れる)。
DANCER_FEET_Y = FOOTER_Y
DANCER_SCALE = 1.0
#: 16コマを何拍で1周するか。実機の 48コマ = 2拍 に合わせる。
DANCER_BEATS_PER_LOOP = 4.0
#: BPM が読めないときのコマ送り(秒/1周)。BPM150 の 2拍 = 0.8 秒。
DANCER_FALLBACK_LOOP_SEC = 0.8
# コマ送りは**等間隔ではない**。実機キャプチャ
# (G:\Captures\2025-10-26 00-17-07.mp4、1920x1080/60fps、この犬の踊り子が
# 写っている)から測った:
#  * 1周は自己相関で **121コマ = 2.017秒**(lag 121 が突出して最良。前後の
#    lag より差が 1.5 倍以上小さい)。
#  * 背景を中央値で作って引き、踊り子だけのシルエットにして1周ぶんを
#    数えると、姿勢は 14種類ほど。多くは **3〜5コマずつ**で送られるのに対し、
#    **2つの姿勢だけ 18〜25コマ止まる**。「動いて、止めて、動いて、止めて」
#    という振り付けで、等間隔で回すと別物に見える。
#  * 素材の16コマは 3と12・4と11・8と9 が同じ絵。0→15 がすでに往復
#    (ピンポン)になっていて、**折り返しの両端＝0 と 8 が「止め」**と読むのが
#    素直で、実測の「1周に長い止めが2回」と数も合う。
# そこで各コマに「重み」を持たせ、0 と 8 だけ長く出す。合計の重みで1周を
# 割るので、下の DANCER_LOOP_SEC を変えれば全体の速さだけが変わる。
DANCER_HOLD_FRAMES = (0, 8)      # 長く止まるコマ
DANCER_HOLD_WEIGHT = 4.5         # 止めは通常の何倍の長さか(実測 19/4.3 ≒ 4.4)
DANCER_LOOP_SEC = 121.0 / 60.0   # 1周(秒)。実測 2.017 秒
DANCER_USE_BEATS = False         # True にすると拍に同期させる(下の拍数で1周)
#: 出てくるときの跳ね。かかる時間 / 出はじめの沈み / 行き過ぎる高さ /
#: 頂点が来る位置(0..1)。
DANCER_IN_SEC = 0.20
DANCER_IN_DROP = 230.0
DANCER_IN_RISE = 32.0
DANCER_IN_PEAK = 0.55

# --- クリア(ノルマ到達)後の背景 -----------------------------------------
# 実機キャプチャ(G:\Captures\CBK譜面ジャーのお部屋\超人\2026-08-26 12-41-48.mp4、
# 1920x1080 / 60fps)をコマごとに見て決めた。ゲージが40本目に届くのは #288 で、
# その前後で起きるのは次の3つ。
#
#  1. 下背景が夜の縁日(あの映像は Bg_down/5 セット)から金色のクリア背景へ
#     **クロスフェード**で入れ替わる。幕も群衆も光の演出も挟まらない。
#     下背景の平均色(緑成分)を測ると 75 -> 192 へ #288..#296 の 8コマで
#     ほぼ直線に上がりきる = 0.133 秒。
#  2. 上背景も赤 -> 金へ変わる。こちらは同じ測り方で #288..#308 の 20コマ
#     = 0.333 秒と、下背景よりゆっくり。
#  3. 入れ替わったあとのクリア背景は**コマ送りではなく、左へ流れる**。
#     踊り子のいない行(映像 y=560..640)だけで総当たりに突き合わせると
#     基準コマ #326 から +10/+20/+30/+60/+90 コマで -16/-32/-48/-96/-144 画素
#     (縦は 0)。1920 幅の値なので 1280 換算で -64px/秒。クリア前の下背景は
#     同じ測り方で 0 画素 = 止まっているので、「クリアすると背景が動き出す」
#     のもこの演出の一部。
# ゴーゴーとは混ざらない(映像はゴーゴー中にクリアしているが、下背景の絵は
# クリアのほうで完全に置き換わる)。ゴーゴーの火花はもともと別の層なので、
# こちらは何も足さない。
SHOW_BACKGROUND_CLEAR = True
BG_CLEAR_FADE_SEC = 0.133        # 下背景が入れ替わるのにかける時間
BG_CLEAR_TOP_FADE_SEC = 0.333    # 上背景の色が変わるのにかける時間
BG_CLEAR_SCROLL_VX = -64.0       # クリア後の下背景が流れる速さ (px/秒)
# クリア背景を描き始める y。レーン枠の下端 = SE帯の下端(355) + 枠の下辺 9px。
# BG_DOWN_Y(360) より 4px 下なので、ここを守らないと黒帯が痩せて見える。
BG_CLEAR_TOP_Y = LANE_Y + LANE_H + SE_STRIP_H + 9
# 素材は画面と同じ 1280 幅なので、流すには横に繰り返すしかない。継ぎ目
# (市松の位相が飛ぶ縦線)は隠しようがないが、**実機にも同じものが出る**。
# 映像の踊り子がかからない帯(1280換算 y=380..420)で列ごとの段差を拾うと、
# #990 で x=823、以降 15コマごとに 16px ずつ左へ移る縦線が最後まで残る
# (= 64px/秒。上で測った流れる速さとも一致する)。20秒に1度画面を横切る。
# クリア後の上背景の色。Bg_up/3 のシートは 1駒 = 1色で3色ぶん入っていて、
# 0=赤(1P) 1=青(2P) 2=金。金だけがふだん使われないまま余っていた —
# Bg_up/1 が Normal_*/Clear_*、Bg_up/2 が *_Clear* とクリア用を別ファイルで
# 持っているのに対し、セット3はクリア用のファイルが無い。3色目がその
# 代わりだと読むのが素直で、実機の「赤 -> 金」とも向きが合う。
BG_CLEAR_UP_COLOR_ROW = 2

# Bg_down_Clear.png (= TNDE の Bg_down/c/0/Clear.png、1280x3212) は1枚絵では
# なく、透明な行で仕切られた層の縦置きアトラス。不透明な行のかたまりを拾うと
# 7本あり(skin_map.py の同名の項目に一覧)、それを重ねて 1280x360 の背景を
# 組み立てる。
#: 地の市松だけ。素材の 0..449 のうち下辺 385..449 は金雲なので、そこは外す。
BG_CLEAR_BASE_BAND = (0, 385)
#: その金雲の帯。実機では画面の**上**から下向きにぶら下がっているので y=0 に置く。
BG_CLEAR_CLOUD_BAND = (385, 449)
#: その金雲を上下反転して置くか。解析の覚書は「素材のままだと雲の膨らみが
#: 上を向くので反転する」としていたが、素材を見ると逆だった — 帯 385..449 は
#: 上辺が平ら(素材 0..449 の一枚絵を途中で切った断面)で、**下辺が雲の
#: ふくらみ**(そこから下は透明)。つまり素材のままで「上からぶら下がる」形に
#: なっている。反転すると平らな断面が下に来て、帯の下端に横一直線の切れ目が
#: 出る。実機 #430 と並べると、雲のふくらみの下端は 帯の y≒55..60 で、
#: 反転しない側だけが一致した(反転させると y=64 に直線が残り、実機には
#: そんな線は無い)。戻したくなったらここを True にする。
BG_CLEAR_CLOUD_FLIP = False
#: 奥から手前へ。((素材の上端, 下端), 画面の帯の中での y)。
#: y は実機の #430 コマと並べて目で合わせた値。金雲の帯はこの全部より手前
#: (いちばん最後)に描く。
BG_CLEAR_LAYERS = (
    ((1584, 1971), 55),    # 大きな金雲
    ((1190, 1412), 10),    # 松と桜
    ((553, 871), 215),     # 笹(+ 金雲と松桜。下半分はフッターに隠れる)
    ((2865, 3098), 110),   # 独楽(水色・右)
    ((2188, 2397), 140),   # 独楽(赤)
    ((2672, 2843), 170),   # 独楽(水色)
)

# 連打数と判定文字「良」はレーンより上(黒枠の帯の上)に出す。レーンの
# ウィジェットは帯ぴったりの高さしか無く、上へはみ出して描けないので、
# この2つは画面側(親)が描く。判定円の真上、魂ゲージの左に収まる位置。
# --- ゴーゴー / 魂MAX の演出 --------------------------------------------
# GoGoSplash.png 6900x460 = 230x460 が30コマ。下から吹き上がる金色の火花で、
# ゴーゴーが始まった瞬間に一度だけ流れる(ループしない)。下端中央アンカー。
# 本家と同じく出す。うるさく感じるかもしれないと一度切っていたが、
# 「ゴーゴーに入るときに下から火花が欲しい」という要望があったので戻した。
SHOW_GOGO_SPLASH = True
GOGO_SPLASH_CELL = (230, 460)
GOGO_SPLASH_FRAMES = 30
GOGO_SPLASH_FRAME_SEC = 1.0 / 30.0
GOGO_SPLASH_SCALE = 0.825        # 0.55 の 1.5 倍
# 本家は判定枠の1本ではなく、画面の下端から横に並んで吹き上がる。
# 幅を GOGO_SPLASH_COUNT 等分して、その真ん中それぞれに立てる。
GOGO_SPLASH_COUNT = 6
GOGO_SPLASH_BOTTOM = SCREEN_H_FULL     # 火花の足元は画面の下端

# 魂ゲージが満タン(入魂)のあいだ、ゲージが虹色に変わる。
# Rainbow/<コース>/0..11.png 696x44。マスクがゲージ本体と dx=0,dy=0 で一致
# するので、ゲージと同じ位置にそのまま重ねるだけでよい。
GAUGE_RAINBOW_FRAMES = 12
# 速さは実機キャプチャ(2026-08-26 12-41-48.mp4、虹は #1233 から)で測った。
# ゲージの1点(x=200 付近)の色の自己相関を取ると 42/84/126 コマで揃うので、
# 12枚で一巡 = 42コマ = 0.700秒。1枚あたり 3.5コマ。
# (以前は 1/20 秒 = 一巡 0.600秒 で、実機より 17% 速かった)
GAUGE_RAINBOW_FRAME_SEC = 42.0 / 60.0 / GAUGE_RAINBOW_FRAMES

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
ROLL_FAN_SCALE = 1.021           # 0.928 の 1.1 倍
ROLL_FAN_CENTER_X = 413          # 扇の中心 x
ROLL_FAN_BOTTOM = 202            # 扇の下端 y
ROLL_NUM_CELL = (63, 75)
ROLL_NUM_SCALE = 0.936           # 0.851 の 1.1 倍(扇と同じだけ大きくする)
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
JUDGE_BOTTOM = LANE_Y + 18       # 「良」の下端。レーンに 18px かぶる
JUDGE_SCALE = 1.05               # 「良」の拡大率
# 「良」の動き。**本家の実機映像(1920x1080 / 60fps)を1コマずつ測って合わせた。**
#
#   出た瞬間は落ち着く場所より上に居て、そこへ**落ちてくる**。
#   コマ  0   16  33  50  66ms
#   ずれ  19  15  10   5   0 px (1080p) -> 720p では 12.7 0 まで
#   落ちきったあとは動かない。0.233 秒まで不透明のまま、そこから
#   0.05 秒で消える(全体 0.283 秒)。
#
# 以前は逆に「下から昇る」うえに、昇りきるのに 0.34 秒かけ、0.187 秒から
# 薄くしていた。つまり**ずっと半透明の文字が上へ漂う**見え方で、本家の
# 「ぱっと出て静止し、最後に消える」とは別物になっていた。
JUDGE_POP_DROP = 13.0            # 出た瞬間、静止位置より上にいる量
JUDGE_POP_DROP_SEC = 0.066       # 落ちきるまで(実測 4コマ)。等速。
JUDGE_POP_SEC = 0.283            # 出てから消えるまで
JUDGE_POP_FADE_FROM = 0.82       # 0..1 のうちどこからフェードを始めるか

# --- 曲名(録画のときだけ、画面の右上) ------------------------------------
# 勘亭流。DFP勘亭流(DynaFont)はこの環境に無かったので、TNDE が同梱している
# FOT-大江戸勘亭流(Fontworks)を使う。skin/Kanteiryu.otf があればそれを読み、
# 無ければ入っていそうな家族名を順に当たり、それも駄目なら既定のフォント。
TITLE_FONT_FILE = "Kanteiryu.otf"
TITLE_FONT_FALLBACKS = ("FOT-大江戸勘亭流 Std E", "FOT-OedoKtr Std E",
                        "DFPKanteiryu-XB", "DFP勘亭流")
TITLE_RECT = (603, 31, 640, 52)   # 右詰めの基準枠 (x, y, w, h)
TITLE_SIZE = 39                   # 大きさは曲名の長さによらず一定(34の1.2倍を5%詰めた)
# 長い曲名は縮めず、右端を揃えたまま左へはみ出させる。左はここまで。
TITLE_LEFT_LIMIT = 8
TITLE_COLOR = "#ffffff"
# 上背景を敷いてから白1色だと柄に埋もれるので、黒で縁取る。落ち影ではなく
# 文字の輪郭をなぞる線なので、下が何色でも読める。太さは線の幅で、外へ
# 出るのはその半分。
TITLE_OUTLINE = "#000000"
TITLE_OUTLINE_W = 10.0

# --- スコアの加算表示 ----------------------------------------------------
# 音符を叩くたびに、入った点をスコアの上へ浮かべて消す。
#
# 動きは実機のキャプチャ(1920x1080 / 60fps)を、背景を引いてから字の形で
# 1コマずつ追って測った。以下は 720p 換算(実測値の 1.5 分の1)。
#
#   +16ms  薄く、定位置より 39px 右に出る
#   +33ms  +24px   濃くなりながら左へ
#   +50ms  +10px
#   +66ms   -4px   行き過ぎる。ここでほぼ濃い
#   +116ms   0px   定位置に落ち着く
#   〜250ms        静止・不透明
#   +266〜350ms    8px 上へ("落ちる"前の溜め)
#   +366ms〜       下がりながら消える(実測でも -12 -> -10 -> -9 と戻っている)
#
# 以前は叩いた瞬間から 16px 昇りながら、出た瞬間から薄くしていた。つまり
# **半透明の数字がスコアの上を昇り続ける**見え方で、実機とは別物だった。
SHOW_SCORE_GAIN = True
SCORE_GAIN_DELAY = 0.016         # 叩いてから出るまで
SCORE_GAIN_SEC = 0.40            # 出てから消えるまで
SCORE_GAIN_IN_SEC = 0.10         # 右から滑り込むのにかける時間
SCORE_GAIN_IN_X = 39.0           # 出はじめの右へのずれ
SCORE_GAIN_IN_OVERSHOOT = 4.0    # 定位置を通り過ぎる量
SCORE_GAIN_FADEIN_SEC = 0.05     # 濃くなりきるまで
SCORE_GAIN_HOP_FROM = 0.62       # ここから跳ねはじめる(0..1)
SCORE_GAIN_HOP_UP = 8.0          # 跳ねる高さ
SCORE_GAIN_FADE_FROM = 0.875     # ここから消えはじめる(0..1)
SCORE_GAIN_SCALE = 0.902
#: 合計スコアの字送りを、**加算文字の字送りから何 px 広げるか。**
#:
#: 送り幅は「1枠の幅 x 倍率 x 割合」で決まる。合計と加算は字の大きさが違う
#: (1.02 対 0.902)ので、割合をそのまま揃えても送り幅は揃わない。1枠の幅は
#: 素材によって変わるため、割合は素材を読んだあとに出す
#: (_score_total_advance)。ここは「加算とちょうど同じ」を 0 とした px 単位の
#: ずらし量。
SCORE_TOTAL_ADVANCE_PX = 1.0
SCORE_GAIN_ROW = 1               # Score_Plate.png の段(0=白 1=橙 2=水)
#: スコアの上端からさらに上へ(正=下)。実機の加算文字の上端 160.0 に合う値。
#: SCORE_Y を動かすと加算も一緒に動くので、ここを変えるときは注意。
SCORE_GAIN_Y_OFF = -11


class _LaneOverlay(QWidget):
    """レーンより手前に出すものを、**1枚の板にまとめて**描く。

    レーンは画面(親)の子ウィジェットで、親は子より先に描かれる。だから
    「飛んでいく音符」「判定文字 良」「風船中のどんちゃんと風船」のように
    レーンに重なるものは、親に描くとレーンの下に潜ってしまう。レーンの
    兄弟として重ねた板に描くことで手前に出している。

    **なぜ1枚なのか(以前は3枚だった)**
    半透明の子ウィジェットは、塗り直すたびに Qt が下の親ぶんも巻き込んで
    合成する。実測で、この板だけで **1コマ 1.77ms**(1コマ 6.84ms のうち)を
    使っていた。画面本体の描画(1.18ms)より重い。3枚のうち2枚は 1280x520 の
    全幅で、密度の高い譜面ではどれも毎コマ塗り直しになる。

    前後関係は板の重ね順でしか作れないと思って分けていたが、1枚の中でも
    **描く順番**でそのまま作れる。飛んでいく音符 -> 良 -> どんちゃん -> 風船
    の順に描けば、以前の3枚と1枚も違わない絵になる。合成は3回から1回へ。
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
        ox, oy = self.x(), self.y()
        # 後のものほど手前。
        self._screen.draw_soul_front(p, ox, oy)
        self._screen.draw_rainbow_sparks(p, ox, oy)
        self._screen.draw_rainbow_head_front(p, ox, oy)
        self._screen.draw_soul_flights(p, ox, oy)
        self._screen.draw_judge_pop(p, ox, oy)
        self._screen.draw_chara_front(p, ox, oy)
        self._screen.draw_balloon_front(p, ox, oy)
        p.end()


#: 中心線をならす幅(列)。素材の都合でできる小さな段差を消すため。
#: 61 列。虹の素材は先端が三日月形で、下の角が切れる列 810〜815 で中心が
#: 一段とぶ。9列だと先端付近で 9.1px 逆走(顔が一度上へ戻る)が残り、61列で
#: 0.7px まで落ちる。いずれも実測。
_BAND_SMOOTH = 61


def _column_centers(img, smooth=_BAND_SMOOTH):
    """画像の列ごとに「帯の中心が上から何pxか」を返す。不透明な画素が
    無い列は None。

    中心はアルファの重心。**上端と下端の中点ではない**。虹の素材では
      * 列 810〜814 に、帯の下へちぎれた小さな塊がある。中点だと、その塊が
        切れる列 815 で中心が 21px 飛ぶ。
      * 先端は 1px まで細くなって消える。中点そのものは最後まで滑らかに
        続くので、細い列を捨てる必要は無い(以前は厚み12px未満を捨てていて、
        最後の 16列ぶん中心が 204 に貼り付き、顔が 13px 上へ取り残されて
        いた)。
    重心にすると段差は 21.5px → 4.7px になり、さらに 9列で移動平均を
    かけると 2.1px まで落ちる。いずれも実測。

    アルファだけ見れば済むので numpy で一度に処理する。pixelColor の
    二重ループで書くと、虹(943x400)で 377,200 回の呼び出しになり、
    その1コマだけ 500ms 止まる。

    numpy が使えない環境でも動くよう、素直な実装も残してある。"""
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return []
    try:
        import numpy as np

        conv = img.convertToFormat(QImage.Format_RGBA8888)
        stride = conv.bytesPerLine()
        arr = np.frombuffer(conv.constBits(), dtype=np.uint8, count=stride * h)
        alpha = arr.reshape(h, stride // 4, 4)[:, :w, 3].astype(np.float64)
        any_col = (alpha > 8).any(axis=0)
        ys = np.arange(h)[:, None]
        weight = alpha.sum(axis=0)
        cent = (alpha * ys).sum(axis=0) / np.where(weight > 0, weight, 1.0)
        cols = np.nonzero(any_col)[0]
        if len(cols) >= 2 and smooth > 1:
            # 帯のある列だけ並べてならす。端は端の値で埋める。
            pad = np.pad(cent[cols], (smooth // 2, smooth // 2), mode="edge")
            sm = np.convolve(pad, np.ones(smooth) / smooth, mode="valid")
            cent = cent.copy()
            cent[cols] = sm
        return [float(cent[x]) if any_col[x] else None for x in range(w)]
    except Exception:  # noqa: BLE001
        pass
    out = []
    for x in range(w):
        tot = 0.0
        acc = 0.0
        for y in range(h):
            a = img.pixelColor(x, y).alpha()
            if a > 8:
                tot += a
                acc += a * y
        out.append(None if tot <= 0 else acc / tot)
    return out


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
        self._last_gauge_ratio = 0.0
        self._gauge = None
        self._gauge_pulse = None
        self._clear_time = None
        # 4体目の踊り子が出る時刻(魂ゲージが DANCER_JOIN_RATIO に届く音符の
        # 時刻)と、時刻→累計拍の対応表。どちらも set_chart が作る。
        self._dancer_join_time = None
        self._beat_marks = None
        self._course_key = None
        self._course_sym = None
        # 素材は **ここでは読まない**。読むのは _ensure_skin()(説明はそちら)。
        # ここに入れておくのは「素材が1枚も無かったとき」と同じ値で、
        # 描画側はどれも None を見て自前の絵へ落ちるようにできている。
        self._skin_ready = False
        self._combo_text_bands = None
        self._gauge_rainbow = None
        self._dancer = None
        self._dancer_cum = None
        self._chara = None
        self._title = ""
        self._title_family = None
        self._nameplate_path = None
        # 焼いた文字の置き場(_baked_text)。倍率ごとに1枚。
        self._text_cache = {}

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
        # 子の板(_LaneOverlay)は親と同じ面へ描かれるのでこれを見る。
        self._dpr = 1.0
        # 上背景シートから切り出した駒((key,col,row) -> QPixmap)。
        self._bg_up_cache = {}
        # 上背景3層を焼いた帯(画面幅+余白)と、それを焼いたときの位相。
        # 位相が同じだけ進んでいる間は、この帯から窓を切って貼るだけで済む。
        # クリアで色が赤 -> 金へ変わるので、シートの色(行)ごとに1本ずつ持つ。
        # 中身は 色 -> (帯, 焼いたときの位相の鍵)。
        self._bg_strips = {}
        # クリア後の下背景を組み立てて焼いた帯(画面2枚ぶん)。作るのは1回だけ。
        self._bg_clear_strip = None
        # 白く染めた音符(着弾の白飛ばし用)。文字 -> QPixmap
        self._white_note_cache = {}
        # 虹の先端に乗せる顔と、虹の帯の中心の高さ(列ごと)。どちらも1回だけ。
        self._rainbow_head_pm = None
        self._rainbow_centers = None
        # 数字シートの縮小済みグリフ((sheet,cols,rows,row,scale) -> [0..9])
        self._digit_cache = {}
        # 判定ポップが前フレームに出ていたか(消し込みを1回だけ行うため)
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
        self._overlay = _LaneOverlay(self)
        self._overlay.setGeometry(*SOUL_FLY_RECT)
        self._overlay.raise_()

        self._hud_timer = QTimer(self)
        self._hud_timer.setInterval(max(1, chart_preview._timer.interval()))
        self._hud_timer.timeout.connect(self._tick_hud)
        # 動かすのは表示されているあいだだけ。録画用の画面はずっと非表示の
        # まま render() されるだけなので、そこでタイマーを回す意味がない。

    def showEvent(self, event):
        # 見える直前に素材を揃える(_ensure_skin の説明を参照)。ふつうは
        # 起動直後の手すきに済んでいるので、ここは真偽値を1つ見るだけ。
        self._ensure_skin()
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

    def draw_gogo_splash(self, p):
        """ゴーゴーが始まった瞬間の金色の火花。画面の下端から横並びで
        吹き上がる(本家と同じで、判定枠の1本ではない)。"""
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
        src = QRect(min(f, GOGO_SPLASH_FRAMES - 1) * cw, 0, cw, ch)
        y = int(GOGO_SPLASH_BOTTOM - dh)
        n = GOGO_SPLASH_COUNT
        for i in range(n):
            cx = SCREEN_W * (i + 0.5) / n
            p.drawPixmap(QRect(int(cx - dw / 2), y, int(dw), int(dh)), sheet, src)

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
        # 下背景の帯: クリア後の金色へのクロスフェード・左流れ・提灯の光の
        # ゆらぎ・(出すなら)踊り子。ここは今まで塗り直していなかったので、
        # 等倍(ScaledHost が素通しになる 100%)では下半分が最初のコマで
        # 止まっていた。踊り子の有無で条件を付けてはいけない
        # (SHOW_DANCERS = False の今は _dancer が必ず None なので、
        # 付けるとこの行が一度も実行されず、クリア演出が画面でだけ動かない
        # = 録画や 75%/50% と絵が食い違う)。縮小表示のときは ScaledHost が
        # 画面ごと描き直しているので、これは重ならない(同じ矩形を2度描く
        # ことにはならない)。
        if not self._compact and not self._lite:
            self.update(0, BG_DOWN_Y, SCREEN_W, self.height() - BG_DOWN_Y)
        # 「良」の板(レーンの手前)。判定ポップが出ていない間は中身が空なので、
        # 毎フレーム更新する必要がない(半透明の子ウィジェットの再描画は親の
        # 巻き込み再描画も呼ぶ)。消え際を残さないよう、「前フレームは出ていた」
        # 場合だけもう1回だけ更新して消し込む。
        recent = self.judge_pop()
        judge_active = bool(recent is not None and 0.0 <= recent[0] < JUDGE_POP_SEC)
        # レーンより手前の板(飛んでいく音符・風船中のどんちゃん)。同じ理由で、
        # 中身がある間と、その次の1回だけ塗り直す。
        try:
            now = self.chart_preview.game_state()[0]
            # 魂の弾けは飛行より長く続く(飛行 0.42 + 弾け 0.53 = 0.95 秒 に
            # 対して、飛んでいる音符は 0.42 + 0.22 = 0.64 秒)。長いほうで
            # 見ないと、弾けの途中で板の塗り直しが止まる。
            _win = max(SOUL_FLY_SEC + SOUL_LAND_SEC,
                       SOUL_FLY_SEC + (SOUL_BURST_FRAMES - SOUL_BURST_FIRST)
                       * SOUL_BURST_FRAME_SEC)
            flying = bool(self.chart_preview.recent_hits(now, _win)
                          or self.chart_preview.recent_roll_hits(now, _win))
            if not flying and SHOW_BALLOON_RAINBOW:
                # 虹の先端の顔も板が描くので、虹が出ている間は塗り直す。
                flying = self.chart_preview.balloon_pop_elapsed(
                    now, RAINBOW_TICKS * RAINBOW_TICK_SEC) is not None
            if not flying and self._chara is not None:
                flying = self._chara.state() in chara_mod.TIME_BASED_STATES
            if not flying:
                # どんちゃんを描かない = anim.update() を回さない場面では、
                # 上の「風船中のどんちゃん」判定が効かない(state() が永久に
                # 通常のまま)。軽量モードだけでなく、1_Chara を持たない
                # スキンでも同じことが起きる。風船が判定枠に居るかを直接
                # 見ておく。これが無いと風船の絵が最初のコマで固まる
                # (風船中は判定線を通る音符も無いので、板が塗り直されない)。
                flying = self.chart_preview.balloon_sprite_frame(now) is not None
        except Exception:  # noqa: BLE001
            flying = False
        # 「良」も飛んでいく音符もどんちゃんも同じ板なので、どれかに中身が
        # あれば塗り直す。消え際を残さないよう、前のコマで中身があった場合も
        # もう1回だけ塗って消し込む。
        live = flying or judge_active
        if live or self._flight_was_active:
            self._overlay.update()
        self._flight_was_active = live

    # ------------------------------------------------------------------
    def _ensure_skin(self):
        """画面が使う絵を読む。2回目以降は何もしない。

        **なぜ __init__ から追い出したのか**
        ここで読むのは 30枚ほどの PNG と、どんちゃんの連番の枚数調べで、
        実測 316ms(chara の枚数調べを直したあとで 210ms)かかっていた。
        だがこの画面が入っている GamePreviewWindow は、利用者が「ゲーム風
        プレビュー」を開くまで**一度も表示されない**。起動時に払う理由が無い。

        呼ぶのは showEvent と paintEvent の頭(= 実際に見える直前)と、
        メインウィンドウが最初に描き終わったあとの手すき
        (MainWindow.paintEvent 参照)。前者があるので「読む前に描かれる」ことは
        起こらず、後者があるので利用者が開いたときには既に読み終わっている。
        録画(recorder.py)は窓を出さずに render() するが、それも paintEvent を
        通るのでここで揃う。"""
        if self._skin_ready:
            return
        self._skin_ready = True
        self._load_skin()

    def _load_skin(self):
        """skin/ から使う絵を読む。無ければ None のままで、描画側が黙って飛ばす
        (スキンは同梱しない外部パックなので、無くても動くのが前提)。"""
        base = str(settings_mod.skin_dir())
        for key, rel in (
            ("bg_top", "Background.png"),
            ("bg_down", "Bg_down.png"),
            ("bg_down_light", "Bg_down_Light.png"),
            ("bg_down_clear", "Bg_down_Clear.png"),
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
        # 下背景の踊り子。16コマを中身の大きさまで刈り込んで持つ。
        self._dancer = self._load_dancer()
        # スキンを読み直したら引き当て表も作り直す。
        self._dancer_cum = None
        # クリア後の脈打ちに使う「金色に染めたゲージ」。素材が読めたここで
        # 1枚だけ焼いておく(毎コマ作ると当然重い)。
        self._gauge_pulse = self._build_gauge_pulse()
        # どんちゃん。連番はここで**全部**先読みする(裏スレッドで復号 ->
        # GUI スレッドで QPixmap 化)。コマ単位の遅延読みだと、BPM120 で
        # 毎秒 60枚の新しいコマが再生中に読まれることになり、1枚 7.5ms が
        # そのまま締切落ちになって実効 fps が半分に落ちる
        # (CharaSprites.preload の説明を参照)。
        self._chara = chara_mod.CharaAnimator()
        self._chara.beats_per_loop = CHARA_BEATS_PER_LOOP
        self._chara.sprites.preload()
        # **曲名はここで消さない。** 曲名は譜面の情報であって素材ではない。
        # ここで空にしていたせいで、素材の読み込みが set_chart より後になる
        # 経路(NeoTJAPlayer は初回の描画で素材を読む)では曲名が消えていた。
        # Editor は起動時に先読みするので、たまたま表に出ていなかっただけ。
        self._title_family = self._load_title_font()
        # 銘板の名前は毎フレーム描くので、字形(パス)だけは1回作って使い回す。
        # フォントが差し替わるここで捨てて、次の描画で組み直させる。
        self._nameplate_path = None

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
        k = CHARA_BALLOON_SCALE
        dx, dy = CHARA_BALLOON_OFF
        mx, my = CHARA_BALLOON_MOUTH
        w, h = pm.width() * k, pm.height() * k
        # 口(mx, my)を動かさずに縮める。画布の点 u は左上 + k*u へ移るので、
        # 左上を (1-k)*口 だけずらせば、口だけがその場に残る。
        x = CHARA_POS[0] + bx - ox + (1.0 - k) * mx + dx
        y = CHARA_POS[1] + by - oy + (1.0 - k) * my + dy
        p.drawPixmap(QRectF(x, y, w, h), pm, QRectF(0, 0, pm.width(), pm.height()))

    def draw_judge_pop(self, p, ox=0, oy=0):
        """判定文字「良」。叩いた直後に判定円の上へ出て、昇りながら消える。

        レーンに少しかぶる位置なので、レーンより手前の板(_LaneOverlay)から
        呼ぶ。ox/oy はその板の左上。"""
        recent = self.judge_pop()
        if recent is None:
            return
        elapsed = recent[0]
        if not (0.0 <= elapsed < JUDGE_POP_SEC):
            return
        spr = self.chart_preview.judge_sprite()
        jp = elapsed / JUDGE_POP_SEC
        # 上から落ちてくる。落ちきったら 0 で、あとは動かない。
        rise = JUDGE_POP_DROP * max(0.0, 1.0 - elapsed / JUDGE_POP_DROP_SEC)
        if jp <= JUDGE_POP_FADE_FROM:
            p.setOpacity(1.0)
        else:
            p.setOpacity(max(0.0, 1.0 - (jp - JUDGE_POP_FADE_FROM)
                             / (1.0 - JUDGE_POP_FADE_FROM)))
        cx = LANE_X + JUDGE_X_IN_LANE - ox
        bottom = JUDGE_BOTTOM - oy - rise
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
        p.setOpacity(1.0)

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

    def _baked_text(self, p, key, path, outline_color, outline_w, fill_color):
        """縁取りした文字を1枚の絵に焼いて、次からはそれを貼るだけにする。

        **毎コマ縁取りしていたのを見直した。** 文字の輪郭を線でなぞる
        strokePath は 1回 0.9ms かかり、曲名と銘板で毎フレーム2回呼んで
        いた(実測: 1491コマで strokePath だけ 1.33 秒)。中身は変わらない
        のだから、1回焼いて貼れば済む。

        焼く倍率は painter の現在の倍率に合わせる。表示倍率を下げたときや
        録画(1.5倍)でも輪郭が甘くならないようにするため。倍率ごとに別の
        1枚を持つ(静的レイヤーと同じ考え方)。

        戻り値は (絵, 貼る位置)。焼けなければ None。
        """
        try:
            scale = float(p.transform().m11()) or 1.0
        except Exception:  # noqa: BLE001
            scale = 1.0
        scale = max(0.25, min(4.0, scale))
        ck = (key, round(scale, 3))
        hit = self._text_cache.get(ck)
        if hit is not None:
            return hit
        r = path.boundingRect().adjusted(-outline_w, -outline_w,
                                         outline_w, outline_w)
        w = max(1, int(math.ceil(r.width() * scale)))
        h = max(1, int(math.ceil(r.height() * scale)))
        if w > 4096 or h > 4096:
            return None
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.Antialiasing, True)
        q.scale(scale, scale)
        q.translate(-r.x(), -r.y())
        q.strokePath(path, QPen(QColor(outline_color), outline_w,
                                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        q.fillPath(path, QColor(fill_color))
        q.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(scale)
        hit = (pm, QPointF(r.x(), r.y()))
        self._text_cache[ck] = hit
        return hit

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
        # 先に黒でなぞってから白で塗る絵を1枚焼いて、次からはそれを貼る
        # (塗りと線を同時に出すと線が内側へも太って、勘亭流の細い所が
        # 黒く埋まってしまうので、なぞってから塗る順は変えない)。
        baked = self._baked_text(p, ("title", self._title, bx, by),
                                 path, TITLE_OUTLINE, TITLE_OUTLINE_W,
                                 TITLE_COLOR)
        if baked is None:
            p.save()
            p.setRenderHint(QPainter.Antialiasing, True)
            p.strokePath(path, QPen(QColor(TITLE_OUTLINE), TITLE_OUTLINE_W,
                                    Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.fillPath(path, QColor(TITLE_COLOR))
            p.restore()
            return
        pm, at = baked
        p.drawPixmap(at, pm)

    def _draw_nameplate_name(self, p):
        """銘板の白い板に NAMEPLATE_NAME を書く。

        字形は曲名と同じ勘亭流。旧素材に焼かれていた「どんちゃん」も同じ
        書体だったので、板だけ差し替わっても見た目が揃う。"""
        if not NAMEPLATE_NAME:
            return
        path = self._nameplate_path
        if path is None:
            f = QFont(self._title_family) if self._title_family else QFont()
            f.setPixelSize(NAMEPLATE_NAME_SIZE)
            path = QPainterPath()
            path.addText(QPointF(0.0, 0.0), f, NAMEPLATE_NAME)
            # 原点は文字送りの基準(ベースライン左端)なので、そのままだと
            # 上下も左右もずれる。**実際に描かれる外形**の中心を測って、
            # それが NAMEPLATE_NAME_CENTER に来るよう平行移動しておく。
            # 縁取りは外形の周りに均等に付くので、中心はこれで合う。
            r = path.boundingRect()
            path.translate(NAMEPLATE_NAME_CENTER[0] - r.center().x(),
                           NAMEPLATE_NAME_CENTER[1] - r.center().y())
            self._nameplate_path = path
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        # 座標は銘板素材の左上を原点にしてある。板の位置を動かせば文字も
        # 一緒に動く。
        p.translate(NAMEPLATE_POS[0], NAMEPLATE_POS[1])
        # 曲名と同じく、黒でなぞってから白で塗った絵を1枚焼いて貼る。
        baked = self._baked_text(p, ("nameplate", NAMEPLATE_NAME), path,
                                 NAMEPLATE_NAME_OUTLINE,
                                 NAMEPLATE_NAME_OUTLINE_W,
                                 NAMEPLATE_NAME_COLOR)
        if baked is None:
            p.strokePath(path, QPen(QColor(NAMEPLATE_NAME_OUTLINE),
                                    NAMEPLATE_NAME_OUTLINE_W,
                                    Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.fillPath(path, QColor(NAMEPLATE_NAME_COLOR))
        else:
            pm, at = baked
            p.drawPixmap(at, pm)
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

    # --- 下背景の踊り子 -------------------------------------------------
    def _load_dancer(self):
        """skin/2_Dancer/Normal/0..15.png を読む。1枚でも欠けたら None。

        素材(213x306)は中身の周りに透明な余白が広く、そのまま貼ると1体
        あたり 65000 画素ぶんのアルファ合成になる。5体×毎フレームで効いて
        くるので、読んだところで中身の矩形まで刈り込んで、貼る位置のずれを
        添えて持つ。刈り込んだ結果は 160x230 前後 = 面積で 4割ほど減る。
        戻り値は [(絵, 画布の中でその絵が始まる x, 同 y), ...]。"""
        if not SHOW_DANCERS:
            return None
        base = os.path.join(str(settings_mod.skin_dir()), "2_Dancer", "Normal")
        out = []
        for i in range(DANCER_FRAMES):
            path = os.path.join(base, "%d.png" % i)
            if not os.path.exists(path):
                return None
            pm = QPixmap(path)
            if pm.isNull():
                return None
            box = self._opaque_box(pm)
            if box is not None:
                x0, y0, x1, y1 = box
                pm = pm.copy(QRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1))
            else:
                x0, y0 = 0, 0
            if abs(DANCER_SCALE - 1.0) > 1e-6:
                pm = pm.scaled(max(1, int(round(pm.width() * DANCER_SCALE))),
                               max(1, int(round(pm.height() * DANCER_SCALE))),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                x0 = int(round(x0 * DANCER_SCALE))
                y0 = int(round(y0 * DANCER_SCALE))
            out.append((pm, x0, y0))
        return out

    @staticmethod
    def _opaque_box(pm):
        """絵の中で不透明な画素が入っている矩形 (x0, y0, x1, y1)。測れなければ None。"""
        try:
            import numpy as np
            img = pm.toImage().convertToFormat(QImage.Format_RGBA8888)
            w, h = img.width(), img.height()
            a = np.frombuffer(memoryview(img.constBits()), dtype=np.uint8)
            a = a.reshape(h, img.bytesPerLine() // 4, 4)[:, :w, 3] > 0
            cols = np.flatnonzero(a.any(axis=0))
            rows = np.flatnonzero(a.any(axis=1))
            if not cols.size or not rows.size:
                return None
            return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _build_beat_marks(preview_data):
        """時刻 -> 累計拍 を引くための表を作る。

        踊りは拍で送るので「その時刻が曲の頭から何拍目か」が要る。BPM が
        途中で変わる譜面でも位相が飛ばないよう、BPM 変化の各点で累計拍を
        先に足しておき、あとは直近の点からの直線で求める。どんちゃん
        (chara.py)のように前のコマからの差分を貯め込む作りにしないのは、
        シークしても録画(オフライン描画)でも同じ絵が出るようにするため。
        戻り値は (時刻のリスト, [(時刻, その時点の累計拍, BPM), ...])。"""
        changes = sorted((preview_data or {}).get("bpm_changes") or [])
        if not changes:
            return None
        times, marks, beats = [], [], 0.0
        prev_t, prev_bpm = None, None
        for t, bpm in changes:
            try:
                t, bpm = float(t), float(bpm)
            except (TypeError, ValueError):
                continue
            if prev_t is not None:
                beats += (t - prev_t) * prev_bpm / 60.0
            times.append(t)
            marks.append((t, beats, bpm))
            prev_t, prev_bpm = t, bpm
        return (times, marks) if marks else None

    def _beats_at(self, now):
        """曲の頭から何拍目か。BPM が読めなければ None。"""
        table = self._beat_marks
        if not table:
            return None
        times, marks = table
        i = max(0, bisect.bisect_right(times, now) - 1)
        t0, b0, bpm = marks[i]
        return b0 + (now - t0) * bpm / 60.0

    @staticmethod
    def _dancer_rise(el):
        """出てくるときの跳ねで、立ち位置からどれだけ縦にずれるか(正=下)。

        実機は画面の下から跳び出して、立ち位置より少し上まで行ってから
        落ちてくる。出はじめはフッターに隠れる高さ(DANCER_IN_DROP)から
        一気に上がるので、行きは強めに緩ませる。"""
        if el is None or el >= DANCER_IN_SEC:
            return 0.0
        u = max(0.0, el) / DANCER_IN_SEC
        if u < DANCER_IN_PEAK:
            q = u / DANCER_IN_PEAK
            return DANCER_IN_DROP + (-DANCER_IN_RISE - DANCER_IN_DROP) * (
                1.0 - (1.0 - q) ** 3)
        q = (u - DANCER_IN_PEAK) / max(1e-6, 1.0 - DANCER_IN_PEAK)
        return -DANCER_IN_RISE * (1.0 - q * q)

    def _draw_dancers(self, p, now, ratio):
        """下背景の踊り子を5体まで描く。

        コマ送りは等間隔ではない(DANCER_HOLD_FRAMES の覚書を参照)。

        クリアすると下背景は金色のクリア背景に変わって左へ流れるが、踊り子は
        流れに乗らずその場で踊る。**呼ぶのはクリア背景を貼ったあと**
        (_draw_bg_clear より手前)。足元はフッターの上端に置くので、跳ねて
        出てくる途中の脚はフッターに隠す = 帯の中だけに切って描く。"""
        frames = self._dancer
        if frames is None:
            return
        self._ensure_dancer_table()
        beats = self._beats_at(now)
        if DANCER_USE_BEATS and beats is not None:
            phase = beats / max(1e-6, DANCER_BEATS_PER_LOOP)
        else:
            phase = now / max(1e-6, DANCER_LOOP_SEC)
        pm, sx, sy = frames[self._dancer_frame(phase)]
        ax, ay = DANCER_ANCHOR_IN_CELL
        # 立ち位置(中心x, 足元y)から、刈り込んだ絵の左上へ。
        bx = sx - int(round(ax * DANCER_SCALE))
        by = sy - int(round(ay * DANCER_SCALE))
        p.save()
        p.setClipRect(QRect(0, BG_DOWN_Y, SCREEN_W, FOOTER_Y - BG_DOWN_Y))
        for slot in range(len(DANCER_SLOT_X)):
            if slot == DANCER_SLOT_AT_RATIO:
                if ratio < DANCER_JOIN_RATIO:
                    continue
                t0 = self._dancer_join_time
            elif slot == DANCER_SLOT_AT_CLEAR:
                if ratio < GAUGE_CLEAR_RATIO:
                    continue
                t0 = self._clear_time
            elif slot not in DANCER_SLOTS_INITIAL:
                continue
            else:
                t0 = None
            dy = self._dancer_rise(None if t0 is None else now - t0)
            blit_sprite(p, DANCER_SLOT_X[slot] + bx, DANCER_FEET_Y + by + dy,
                        pm, self._dpr)
        p.restore()

    def _build_gauge_pulse(self):
        """通常圏(赤)をクリア圏(金)の色で塗り替えたゲージを1枚だけ焼く。

        クリア後の脈打ちは「赤の上に金色版を不透明度αで重ねる」で作る
        (合成の結果は素材どうしの線形補間になり、実機で測った色の動き
        — 赤 #f83606 と金 #faf805 の間を行き来する — とそのまま一致する)。
        色を計算で作るのではなく素材のクリア圏をそのまま敷き直すので、
        縞・区切りの暗い列・上端のハイライトまで本家と同じ形で揃う。
        素材が無い/短いときは None。"""
        fill = self._skin.get("gauge")
        sx, sy, sw, sh = GAUGE_GOLD_SRC
        if fill is None or fill.width() < sx + sw or fill.height() < GAUGE_BAR_H:
            return None
        pm = QPixmap(fill.size())
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.drawPixmap(0, 0, fill)
        # 金の段より左を、クリア圏 10本(140px)ずつ左へ敷き詰めて埋める。
        # 縦は下揃え(GAUGE_BAR_H - sh = 22)。通常圏は下 22px しか無いので、
        # そこへクリア圏の上 22px を重ねるとハイライトの位置が合う。
        x = sx - sw
        while x > -sw:
            q.drawPixmap(x, GAUGE_BAR_H - sh, fill, sx, sy, sw, sh)
            x -= sw
        # 敷き足したぶんが素材の外形からはみ出さないよう、元の形で抜く
        # (左端の丸みや上下の縁が四角く出てしまうのを防ぐ)。
        q.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        q.drawPixmap(0, 0, fill)
        q.end()
        return pm

    def _gauge_pulse_alpha(self, now):
        """クリア後の脈打ちで、金色をどれだけ重ねるか(0..1)。

        位相の原点はクリアに届いた時刻。実機ではその 6コマ後(0.100秒)が頂
        で、前後 6コマで 0 に戻る三角。山と山の間は 0 のまま。

        クリアの時刻が採れなかった(note_time() が範囲外で None)ときは
        0 を返して脈打ちを出さない。now をそのまま位相に使うと、クリア到達
        とまるで関係ない曲頭基準の位相で光ってしまう。"""
        t0 = self._clear_time
        if t0 is None:
            return 0.0
        ph = now - t0
        ph = math.fmod(ph, GAUGE_PULSE_PERIOD_SEC)
        if ph < 0.0:
            ph += GAUGE_PULSE_PERIOD_SEC
        d = abs(ph - GAUGE_PULSE_HALF_SEC)
        if d >= GAUGE_PULSE_HALF_SEC:
            return 0.0
        return 1.0 - d / GAUGE_PULSE_HALF_SEC

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

        # 譜面が決まった = そう遠くないうちに再生される。どんちゃんの連番が
        # まだなら、ここでも先読みを蹴っておく(素材の読み込みより先に譜面が
        # 来る順序でも、再生中の遅延読みにならないように)。済んでいれば
        # 何もしない。
        if self._chara is not None:
            self._chara.sprites.preload()
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
        # 4体目の踊り子が出る時刻。出す/出さないの判定そのものは
        # _draw_gauge と同じ ratio(= GaugeModel.ratio(叩いた数))で行うので、
        # ここで求めるのは跳ねて出てくる演出の起点だけ。同じ GaugeModel から
        # 「割合が DANCER_JOIN_RATIO に届く音符の番号」を逆算して、その音符の
        # 時刻を採る(クリアの時刻 _clear_time とまったく同じ求め方)。
        self._dancer_join_time = None
        try:
            need = -(-int(gauge_mod.GAUGE_MAX * DANCER_JOIN_RATIO)
                     // max(1, self._gauge.rank))
            self._dancer_join_time = self.chart_preview.note_time(need)
        except Exception:  # noqa: BLE001
            self._dancer_join_time = None
        # 踊りは拍で送るので、時刻→累計拍の表を先に作っておく。
        self._beat_marks = self._build_beat_marks(preview_data)
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
                     right=None, left=None, y=0, scale=1.0, scale_y=None,
                     advance=1.0, y_offsets=None):
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
        glyphs = self._digit_glyphs(sheet, cols, rows, row, scale, scale_y)
        for c in s:
            i = int(c)
            dy = (y_offsets or {}).get(i, 0)
            p.drawPixmap(int(x), int(y) + dy, glyphs[i])
            x += step

    def _digit_glyphs(self, sheet, cols, rows, row, scale, scale_y=None):
        """数字シートの1行を、指定倍率で縮小済みの 0-9 のリストにして返す。

        scale_y を渡すと縦だけ別倍率にできる(合計スコアが上へ伸びるとき、
        横幅は変えたくないため。実機も幅は変わらない)。

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
        sy = float(scale if scale_y is None else scale_y)
        key = (sheet.cacheKey(), cols, rows, row, round(float(scale), 4),
               round(sy, 4), round(dpr, 4))
        got = self._digit_cache.get(key)
        if got is not None:
            return got
        cw, ch = sheet.width() / cols, sheet.height() / rows
        # 論理サイズは従来どおり int(cw*scale)+1。実寸だけ dpr 倍にする。
        dw, dh = int(cw * scale) + 1, int(ch * sy) + 1
        out = []
        for i in range(10):
            cell = sheet.copy(QRect(int(i * cw), int(row * ch), int(cw), int(ch)))
            g = cell.scaled(max(1, int(round(dw * dpr))), max(1, int(round(dh * dpr))),
                            Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            g.setDevicePixelRatio(dpr)
            out.append(g)
        self._digit_cache[key] = out
        return out

    def _score_pop(self, now):
        """合計スコアの伸び具合(1.0 = ふだん)。

        点が入った**その瞬間**から SCORE_POP_SEC かけて、SCORE_POP_PEAK まで
        上へ伸びて戻る。

        **倍率は刻む。** 数字の絵は倍率ごとに切り出してキャッシュしている
        ので、毎コマ違う倍率を渡すとキャッシュが際限なく増える。"""
        if self._score_timeline is None or self._lite:
            return 1.0
        try:
            ev = self._score_timeline.last_event(now)
        except Exception:  # noqa: BLE001
            return 1.0
        if ev is None:
            return 1.0
        el = now - ev[0]
        if not (0.0 <= el < SCORE_POP_SEC):
            return 1.0
        up = SCORE_POP_RATIO - 1.0
        if el < SCORE_POP_ATTACK:
            k = 1.0 + up * (el / SCORE_POP_ATTACK)          # 1コマで伸びきる
        else:
            # そこから直線的に戻る(実測どおり)。
            k = 1.0 + up * (1.0 - (el - SCORE_POP_ATTACK)
                            / (SCORE_POP_SEC - SCORE_POP_ATTACK))
        return round(k / SCORE_POP_STEP) * SCORE_POP_STEP

    def _score_total_advance(self):
        """合計スコアの字送り(1枠の幅に対する割合)。

        加算文字と同じ送り幅に SCORE_TOTAL_ADVANCE_PX を足したものになる
        よう、素材の1枠の幅から逆算する。素材が読めていなければ、加算と
        同じ割合を倍率の比で割った値(px の足しぶんは無視)を返す。"""
        sheet = self._skin.get("score_digits")
        base = SCORE_ADVANCE * SCORE_GAIN_SCALE / SCORE_SCALE
        if sheet is None or sheet.width() <= 0:
            return base
        cw = sheet.width() / 10.0
        return base + SCORE_TOTAL_ADVANCE_PX / (cw * SCORE_SCALE)

    def _draw_left_panel(self, p, combo, score, recent, now):
        """左パネル: スコア / コース記号 / 太鼓 + コンボ / 銘板。"""
        # --- スコア(右詰め) ---
        # 点が入った瞬間、上へ伸びて戻る。**伸ばすのは縦だけ。** 実機も幅は
        # 変わらない(実測: 高さ 34->40 のあいだ、幅は 110 のまま)。下端を
        # そろえたまま縦の倍率を上げるので、伸びるのは上だけになる。
        sy = SCORE_SCALE * self._score_pop(now)
        sheet = self._skin.get("score_digits")
        dy = 0.0
        if sheet is not None and sy != SCORE_SCALE:
            dy = (sheet.height() / 3.0) * (SCORE_SCALE - sy)
        self._draw_digits(p, sheet, score,
                          cols=10, rows=3, row=0,
                          advance=self._score_total_advance(),
                          right=SCORE_RIGHT, y=int(SCORE_Y + dy),
                          scale=SCORE_SCALE, scale_y=sy,
                          y_offsets=SCORE_DIGIT_Y_OFF)

        # --- スコアの加算分(スコアの上へ浮かんで消える) ---
        # 軽量では出さない。要るのは「いま何点か」であって、加算の演出は
        # スコアそのものを見れば分かる情報の重ね描きでしかない。
        if SHOW_SCORE_GAIN and self._score_timeline is not None and not self._lite:
            # **重なるぶんは重ねる。** 古い順に描くので、新しいものほど手前。
            # ふつうの密度の譜面は音符が 0.1 秒おきに来るし、連打を叩いて
            # いる間はもっと詰まるので、1枚しか出さないと点滅して見える。
            for et, gain in self._score_timeline.events_in(
                    now - SCORE_GAIN_DELAY - SCORE_GAIN_SEC,
                    now - SCORE_GAIN_DELAY):
                el = now - et - SCORE_GAIN_DELAY
                if 0.0 <= el < SCORE_GAIN_SEC and gain > 0:
                    q = el / SCORE_GAIN_SEC
                    # 横: 右から滑り込んで、少し行き過ぎてから戻る。実測は
                    # 前半も後半も**直線**だったので、そのまま2本の直線で書く
                    # (39 -> -4 が 0.05秒、-4 -> 0 がもう 0.05秒)。
                    half = SCORE_GAIN_IN_SEC / 2.0
                    if el < half:
                        t = el / half
                        dx = SCORE_GAIN_IN_X + (-SCORE_GAIN_IN_OVERSHOOT
                                                - SCORE_GAIN_IN_X) * t
                    elif el < SCORE_GAIN_IN_SEC:
                        t = (el - half) / half
                        dx = -SCORE_GAIN_IN_OVERSHOOT * (1.0 - t)
                    else:
                        dx = 0.0
                    # 縦: 定位置に着いてしばらく動かず、最後に少し上がってから
                    # 落ちながら消える。上がって落ちるのは投げ上げと同じ形。
                    if q <= SCORE_GAIN_HOP_FROM:
                        rise = 0.0
                    else:
                        u = (q - SCORE_GAIN_HOP_FROM) / (1.0 - SCORE_GAIN_HOP_FROM)
                        # u=0.45 で SCORE_GAIN_HOP_UP、u=0.9 で 0 に戻り、
                        # そのあとは定位置より下へ落ちていく。
                        g = SCORE_GAIN_HOP_UP / 0.2025
                        rise = g * (0.9 * u - u * u)
                    sheet = self._skin.get("score_digits")
                    if sheet is not None:
                        gh = sheet.height() / 3 * SCORE_GAIN_SCALE
                        # 出はじめに濃くなり、消え際に薄くなる。あいだは不透明。
                        a = 1.0
                        if el < SCORE_GAIN_FADEIN_SEC:
                            a = el / SCORE_GAIN_FADEIN_SEC
                        elif q > SCORE_GAIN_FADE_FROM:
                            a = 1.0 - (q - SCORE_GAIN_FADE_FROM) / (1.0 - SCORE_GAIN_FADE_FROM)
                        p.setOpacity(max(0.0, min(1.0, a)))
                        self._draw_digits(p, sheet, gain, cols=10, rows=3,
                                          row=SCORE_GAIN_ROW, advance=SCORE_ADVANCE,
                                          right=SCORE_RIGHT + dx,
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
                if 0.0 <= elapsed < DRUM_GLOW_SEC + DRUM_GLOW_FADE_SEC:
                    glow = self._skin.get("drum_don" if char in "13" else "drum_ka")
                    if glow is not None:
                        # 本家は両面同時ではなく片面ずつ。音符ごとに
                        # 左→右→左…と交互に光らせる。
                        gw, gh = glow.width(), glow.height()
                        half = gw // 2
                        sx = 0 if (n % 2 == 0) else half
                        # 光っているあいだは明るさそのまま。消えるときだけ
                        # 短くフェードさせる。
                        over = elapsed - DRUM_GLOW_SEC
                        a = 1.0 if over <= 0.0 else 1.0 - over / DRUM_GLOW_FADE_SEC
                        p.setOpacity(max(0.0, min(1.0, a)))
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
            # 板は空なので、名前はここで書く。板と同じ「毎フレーム描く側」に
            # 置くのがみそで、静的キャッシュ(_static_layer)へ描くと板だけが
            # 焼き直されて文字が取り残される事故が起きる(曲名で一度やった)。
            self._draw_nameplate_name(p)

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
        step = GAUGE_FILL_W / float(GAUGE_BLOCKS) if fill is not None else 0.0
        blocks = int(ratio * GAUGE_BLOCKS + 1e-9)
        wpx = 0
        if fill is not None and blocks > 0:
            # 左の余白ぶんを足してから切り出す(0本のときは何も描かない)。
            wpx = GAUGE_FILL_X0 + int(round(blocks * step))
            if wpx > 0:
                p.drawPixmap(gx, gy, fill, 0, 0, wpx, GAUGE_BAR_H)
        # ノルマに届いたかどうか。実機では「金色のブロックが1本でも出たら」
        # = 塗った幅が段差を越えたらで、「クリア」の字の点灯と脈打ちが
        # 同じコマで始まる。下の文字もこの判定を使い回す。
        lit = wpx > GAUGE_CLEAR_STEP_X if fill is not None else False
        maxed = ratio >= 1.0 and bool(self._gauge_rainbow)
        # クリアしたあとは、通常圏の赤が周期的に金色へ染まって戻る。
        # 金色版を不透明度αで重ねるだけなので、増えるのは1コマにつき
        # drawPixmap 1回だけ(しかも山の間の 21/33 コマは α=0 で何も描かない)。
        # 入魂して虹になっているあいだは虹が上から全部塗り替えるので描かない。
        if lit and not maxed and self._gauge_pulse is not None:
            a = self._gauge_pulse_alpha(now)
            if a > 0.0:
                p.setOpacity(a)
                p.drawPixmap(gx, gy, self._gauge_pulse, 0, 0, wpx, GAUGE_BAR_H)
                p.setOpacity(1.0)
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
            sx, sy = (dx_, dy_) if lit else (lx, ly)
            p.drawPixmap(gx + GAUGE_CLEAR_STEP_X + GAUGE_CLEAR_TEXT_OFF[0],
                         gy + GAUGE_CLEAR_TEXT_OFF[1], src, sx, sy, gw, gh)


    def draw_soul_front(self, p, ox=0, oy=0):
        """魂の弾けと「魂」の文字を、**レーンより手前**に描く。

        どちらも魂ゲージのそばにあり、レーンの上端(y=196)に少しかぶる。
        画面(親)はレーン(子)より先に描かれるので、_draw_gauge の中で描くと
        かぶったぶんがレーンの下に潜っていた。_LaneOverlay から呼ぶ。

        並びは元のまま「弾け → 魂の文字」。弾けのほうが後ろになる。
        ox/oy は板の左上。"""
        try:
            now = self.chart_preview.game_state()[0]
        except Exception:  # noqa: BLE001
            return
        ratio = self._last_gauge_ratio
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
            # 虹の先端が魂へ着弾したときも、音符と同じ弾けを出す。
            rb = self._rainbow_phase(now)
            if rb is not None and rb[0] >= RAINBOW_HALF:
                e = (rb[0] - RAINBOW_HALF) * RAINBOW_TICK_SEC
                if 0.0 <= e < span and (el is None or e < el):
                    el = e
            if el is not None and el < span:
                f = SOUL_BURST_FIRST + int(el / SOUL_BURST_FRAME_SEC)
                c = SOUL_BURST_CELL
                d = c * SOUL_BURST_SCALE
                cx = SOUL_POS[0] + SOUL_CELL / 2.0
                cy = SOUL_POS[1] + SOUL_CELL / 2.0
                blit_fitted(p, int(cx - d / 2 - ox), int(cy - d / 2 - oy), int(d), int(d),
                            burst, self._dpr, src=QRect(f * c, 0, c, c))

        # 魂の文字。ゲージの右端に置き、クリア圏まで溜まったら光る段に変える。
        soul = self._skin.get("soul")
        if soul is not None:
            row = 1 if ratio >= GAUGE_CLEAR_RATIO else 0
            blit_fitted(p, SOUL_POS[0] - ox, SOUL_POS[1] - oy, SOUL_CELL, SOUL_CELL,
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
            span = SOUL_FLY_SEC + SOUL_LAND_SEC
            hits = cp.recent_hits(now, span)
            # 連打を叩いている間も、その打に合わせて飛ばす。打の時刻は打音・
            # 太鼓の光と同じ決め方なので、手と絵が合う。
            hits = hits + cp.recent_roll_hits(now, span)
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
        (「良」はレーンにかぶるので _LaneOverlay が手前に描く)"""
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

    def _ensure_dancer_table(self):
        """コマの重みから「位相 -> コマ番号」の引き当て表を1回だけ作る。

        毎フレーム重みを足し直すのは無駄なので、累計を持っておいて bisect で
        引く。表は 16 個しかないので探索も一瞬。"""
        if getattr(self, "_dancer_cum", None) is not None:
            return
        w = [DANCER_HOLD_WEIGHT if i in DANCER_HOLD_FRAMES else 1.0
             for i in range(DANCER_FRAMES)]
        total = sum(w)
        cum, acc = [], 0.0
        for v in w:
            acc += v / total
            cum.append(acc)
        cum[-1] = 1.0
        self._dancer_cum = cum

    def _dancer_frame(self, phase):
        """位相(0..1 で1周)から、いま出すコマ番号を返す。"""
        f = phase - math.floor(phase)
        cum = self._dancer_cum
        return min(bisect.bisect_right(cum, f), DANCER_FRAMES - 1)

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
        「良」と風船を描く板(_LaneOverlay)も出したままに
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
        # compact が最優先。軽量は「通常再生と縦横比を揃えたい」という理由で
        # 720 のままにしてあるが、音声波形/情報モードは下にペインを置くので、
        # そこで 720 を通すとレーンの下の黒い余白のぶんだけ窓がむやみに高く
        # なる(実測 748 -> 1048)。あの黒い場所はペインに譲る。
        if self._compact:
            return SCREEN_H_COMPACT
        return SCREEN_H_LITE if self._lite else SCREEN_H_FULL

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

    def _bg_up_layers(self, row=BG_UP_COLOR_ROW):
        """上背景の3層(奥→手前)。素材が無ければ None を含む。

        `row` はシートの色。ふだんは 0(赤=1P)で、クリア後は
        BG_CLEAR_UP_COLOR_ROW(金)。切り出しは同じ式のままで、駒の場所だけが
        変わる。"""
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

    def _draw_bg_top_layers(self, p, now, row=BG_UP_COLOR_ROW):
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
        貼ったのと同じ絵になる。

        帯は色(`row`)ごとに別に持つ。クリアのクロスフェード中は赤と金の
        2本を重ねて貼るので、1本を共有すると毎コマ焼き直しになってしまう。"""
        layers = self._bg_up_layers(row)
        if layers[0] is None:
            return
        phases = self._bg_up_phases(layers, now)
        keys = self._bg_up_keys(layers, phases)
        strip, strip_keys = self._bg_strips.get(row, (None, None))
        d = None
        if (strip is not None and strip_keys is not None
                and len(strip_keys) == len(keys)):
            # 全層の floor/ceil が同じだけ進んでいれば平行移動と同じ。
            ds = set()
            for (a, b), (a0, b0) in zip(keys, strip_keys):
                ds.add(a - a0)
                ds.add(b - b0)
            if len(ds) == 1:
                dd = ds.pop()
                if 0 <= dd <= self.BG_STRIP_MARGIN:
                    d = dd
        if d is None:
            strip = self._bake_bg_strip(row, layers, phases, keys)
            d = 0
        p.drawPixmap(0, 0, strip, d, 0, SCREEN_W, BG_TOP_H)

    def _bake_bg_strip(self, row, layers, phases, keys):
        """上背景3層を、画面幅+余白の帯1枚に焼く。"""
        sw = SCREEN_W + self.BG_STRIP_MARGIN
        img = QImage(sw, BG_TOP_H, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        q = QPainter(img)
        for pm, f in zip(layers, phases):
            self._tile_row(q, pm, f, sw)
        q.end()
        strip = QPixmap.fromImage(img)
        self._bg_strips[row] = (strip, keys)
        return strip

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
        1回だけ測って覚えておく(毎フレーム測ると重い)。

        測るのは風船が初めて割れたコマ。以前はここを pixelColor の二重ループで
        回しており、377,200 回の呼び出しで **1コマだけ 500ms 前後止まって**
        いた(実測。プロファイラで tottime の1位)。どんちゃんの外接計算と
        まったく同じ書き方が残っていた。"""
        if self._rainbow_centers is None:
            pm = self._skin.get("rainbow")
            if pm is None:
                return None
            self._rainbow_centers = _column_centers(pm.toImage())
        centers = self._rainbow_centers
        if not centers:
            return None
        col = max(0, min(int(col), len(centers) - 1))
        # 素材の左右には帯の無い余白がある(実測: 帯は 11..898 列)。そこを
        # 指されたら、いちばん近い帯のある列まで左へ戻る。
        for x in range(col, max(-1, col - 120), -1):
            if centers[x] is not None:
                return centers[x]
        return None

    def _rainbow_phase(self, now):
        """虹の進み具合 (コマ番号, 絵, 幅, 高さ)。出ていなければ None。"""
        if not SHOW_BALLOON_RAINBOW:
            return None
        pm = self._skin.get("rainbow")
        if pm is None:
            return None
        span = RAINBOW_TICKS * RAINBOW_TICK_SEC
        try:
            el = self.chart_preview.balloon_pop_elapsed(now, span)
        except Exception:  # noqa: BLE001
            return None
        if el is None:
            return None
        return int(el / RAINBOW_TICK_SEC), pm, pm.width(), pm.height()

    @staticmethod
    def _rainbow_max_nx(w):
        """帯を出す右端。引き終わりのコマと同じ幅。"""
        return w * (RAINBOW_DRAW_TICKS - 1) // RAINBOW_WIPE_DEN

    def _draw_rainbow(self, p, now):
        """風船が割れたあとにかかる虹の**帯**。上背景の上・レーンより奥。

        先端の顔はここでは描かない。魂より手前に出したいので、レーンより
        手前の板から draw_rainbow_head_front() で描く。"""
        got = self._rainbow_phase(now)
        if got is None:
            return
        c, pm, w, h = got
        x, y = RAINBOW_POS
        if c < RAINBOW_HALF:
            # 左から描き足していく。
            nx = w * c // RAINBOW_WIPE_DEN
            if nx > 0:
                p.drawPixmap(x, y, pm, 0, 0, nx, h)
        elif c < RAINBOW_ERASE_FROM:
            # 引き終わり。本家はここで 0.22 秒そのまま止まる。素材の残り
            # (魂より右)は出さない。出すと引き終わった瞬間に右端が 894→943 と
            # 飛んで、帯が一段伸びたように見える。
            p.drawPixmap(x, y, pm, 0, 0, self._rainbow_max_nx(w), h)
        else:
            # 左から順に消していく。
            mx = self._rainbow_max_nx(w)
            nx = w * (c - RAINBOW_ERASE_FROM) // RAINBOW_ERASE_DEN
            if nx < mx:
                p.drawPixmap(x + nx, y, pm, nx, 0, mx - nx, h)

    @staticmethod
    def _spark_path(r):
        """4本角の星。上下左右に尖って、斜めがくびれている。"""
        path = QPainterPath()
        s = r * RAINBOW_SPARK_WAIST
        d = s * 0.7071
        path.moveTo(0.0, -r)
        path.quadTo(d, -d, r, 0.0)
        path.quadTo(d, d, 0.0, r)
        path.quadTo(-d, d, -r, 0.0)
        path.quadTo(-d, -d, 0.0, -r)
        return path

    def draw_rainbow_sparks(self, p, ox=0, oy=0):
        """虹の先端に散る星。**魂より手前**に出すので板から呼ぶ。

        コマ c の先端に RAINBOW_SPARK_PER_TICK 個生まれて、その場で縮みながら
        消える。先端が右へ進むぶん、後ろへ尾を引いたように見える。"""
        try:
            now = self.chart_preview.game_state()[0]
        except Exception:  # noqa: BLE001
            return
        got = self._rainbow_phase(now)
        if got is None:
            return
        c, _pm, w, _h = got
        x, y = RAINBOW_POS
        last = min(c, RAINBOW_DRAW_TICKS - 1)     # 引き終わったら新しく生まれない
        first = max(1, c - RAINBOW_SPARK_LIFE + 1)
        if last < first:
            return
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        for b in range(first, last + 1):
            nx = w * b // RAINBOW_WIPE_DEN
            cy = self._rainbow_band_center(min(nx, w - 1))
            if cy is None:
                continue
            bx = x + nx - ox
            by = y + cy - oy
            age = (c - b) / float(RAINBOW_SPARK_LIFE)
            for k in range(RAINBOW_SPARK_PER_TICK):
                # コマ番号と粒番号から決まる値なので、毎フレーム散らばらない。
                hsh = (b * 6364136223846793005 + k * 1442695040888963407) & 0xFFFFFFFF
                u0 = ((hsh >> 4) & 0xFF) / 255.0
                u1 = ((hsh >> 12) & 0xFF) / 255.0
                u2 = ((hsh >> 20) & 0xFF) / 255.0
                r0 = RAINBOW_SPARK_MIN + (RAINBOW_SPARK_MAX - RAINBOW_SPARK_MIN) * u2
                # 生まれてすぐ一番大きく、そのあと縮んで消える。
                r = r0 * (1.0 - age) ** 0.7
                if r < 1.0:
                    continue
                p.save()
                p.translate(bx + (u0 - 0.5) * 2.0 * RAINBOW_SPARK_ALONG,
                            by + (u1 - 0.5) * 2.0 * RAINBOW_SPARK_SPREAD)
                p.setOpacity(min(1.0, (1.0 - age) * 1.6))
                p.drawPath(self._spark_path(r))
                p.restore()
        p.restore()

    def draw_rainbow_head_front(self, p, ox=0, oy=0):
        """虹の先端に乗る顔。**魂より手前**に出すので板から呼ぶ。

        帯そのものは背景の一部として奥に描いてある(_draw_rainbow)。顔だけを
        手前へ出すと、虹が魂ゲージまで届いたときに顔が魂の後ろへ隠れない。"""
        try:
            now = self.chart_preview.game_state()[0]
        except Exception:  # noqa: BLE001
            return
        got = self._rainbow_phase(now)
        if got is None:
            return
        c, pm, w, h = got
        head = self._rainbow_head()
        if head is None:
            return
        k = RAINBOW_HEAD_SCALE
        hw, hh = head.width() * k, head.height() * k
        x, y = RAINBOW_POS
        dx, dy = RAINBOW_HEAD_OFF

        if c >= RAINBOW_HALF:
            # 引き終わり。顔は魂の上に着弾して、そこで消える。
            el_land = (c - RAINBOW_HALF) * RAINBOW_TICK_SEC
            if el_land >= RAINBOW_LAND_SEC:
                return
            t = el_land / RAINBOW_LAND_SEC
            cx = SOUL_POS[0] + SOUL_CELL / 2.0 - ox
            cy2 = SOUL_POS[1] + SOUL_CELL / 2.0 - oy
            # 引き終わりの瞬間は、まだ帯の先端に居る。そこから魂の中心へ
            # 短い時間で寄せる(1コマで飛ばすとブレて見える)。
            if el_land < RAINBOW_LAND_MOVE_SEC:
                last_nx = w * (RAINBOW_HALF - 1) // RAINBOW_WIPE_DEN
                last_cy = self._rainbow_band_center(min(last_nx, w - 1))
                if last_cy is not None:
                    fx = x + last_nx + dx - ox
                    fy = y + last_cy + dy - oy
                    # 引き終わりのコマと着地 0 コマ目は帯の先端で同じ位置に
                    # なる。そのまま lerp すると 1コマ分その場で止まるので、
                    # 1コマ進めたところから始める。
                    u = (el_land + RAINBOW_TICK_SEC) / RAINBOW_LAND_MOVE_SEC
                    u = min(1.0, u)
                    u = u * u * (3.0 - 2.0 * u)       # 加速してから減速する
                    cx = fx + (cx - fx) * u
                    cy2 = fy + (cy2 - fy) * u
            r = QRectF(cx - hw / 2.0, cy2 - hh / 2.0, hw, hh)
            src = QRectF(0, 0, head.width(), head.height())
            # 音符の着弾と同じ消し方: 素の絵を薄くしながら、白へ寄せる。
            p.setOpacity((1.0 - t) * (1.0 - t))
            p.drawPixmap(r, head, src)
            p.setOpacity((1.0 - t) * t)
            p.drawPixmap(r, self._white_note("R", head), src)
            p.setOpacity(1.0)
            return

        nx = w * c // RAINBOW_WIPE_DEN
        if nx <= 0:
            return
        cy = self._rainbow_band_center(min(nx, w - 1))
        if cy is None:
            return
        p.drawPixmap(QRectF(x + nx - hw / 2.0 + dx - ox,
                            y + cy - hh / 2.0 + dy - oy, hw, hh),
                     head, QRectF(0, 0, head.width(), head.height()))

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

    # --- クリア(ノルマ到達)後の背景 -----------------------------------------
    def _clear_elapsed(self, now):
        """クリアに届いてから何秒たったか。まだ届いていなければ None。

        軽量では背景そのものを描かないので、ここで断って以降を全部止める
        (新しい演出も軽量には出さない、という決め事)。"""
        if not SHOW_BACKGROUND_CLEAR or self._lite:
            return None
        t0 = self._clear_time
        if t0 is None:
            return None
        el = now - t0
        return el if el >= 0.0 else None

    @staticmethod
    def _clear_fade(el, sec):
        """経過 el 秒を、sec 秒かけたクロスフェードの割合(0..1)に直す。"""
        if el is None:
            return 0.0
        if sec <= 0.0:
            return 1.0
        return min(1.0, max(0.0, el / sec))

    def _bg_clear_strip_pm(self):
        """クリア背景を 1280x360 に組み立て、横に2枚並べた帯へ焼く(1回だけ)。

        素材(Bg_down_Clear.png)は透明な行で仕切られた縦置きアトラスなので、
        毎コマ帯を切り出して7枚重ねるとそれだけで背景の意味がなくなる。
        組み上がりは動かない絵なので1枚に焼いてしまい、流れる動きは
        「焼いた帯のどこを切って貼るか」だけで作る。2枚ぶん並べておけば
        どの位相でも画面幅ぶんが足りるので、貼り付けは常に1回で済む。

        焼くのは実寸(DPR 1)。上背景の帯(_bake_bg_strip)と同じ扱いで、
        録画(DPR 1.5)では貼るときに拡大される。"""
        pm = self._bg_clear_strip
        if pm is not None:
            return pm
        sheet = self._skin.get("bg_down_clear")
        if sheet is None:
            return None
        w = SCREEN_W
        one = QImage(w, BG_DOWN_H, QImage.Format_ARGB32_Premultiplied)
        one.fill(Qt.transparent)
        q = QPainter(one)
        b0, b1 = BG_CLEAR_BASE_BAND          # 地の市松(下辺の金雲は外してある)
        q.drawPixmap(0, 0, sheet, 0, b0, w, b1 - b0)
        for (s0, s1), y in BG_CLEAR_LAYERS:  # 奥から手前へ
            q.drawPixmap(0, y, sheet, 0, s0, w, s1 - s0)
        # 金雲だけは画面の**上**からぶら下がるので y=0 に置き、全部より手前
        # (いちばん最後)に描く。上下反転は BG_CLEAR_CLOUD_FLIP 参照。
        c0, c1 = BG_CLEAR_CLOUD_BAND
        cloud = sheet.copy(QRect(0, c0, w, c1 - c0))
        if BG_CLEAR_CLOUD_FLIP:
            cloud = cloud.transformed(QTransform().scale(1.0, -1.0))
        q.drawPixmap(0, 0, cloud)
        q.end()
        strip = QImage(w * 2, BG_DOWN_H, QImage.Format_ARGB32_Premultiplied)
        strip.fill(Qt.transparent)
        q = QPainter(strip)
        q.drawImage(0, 0, one)
        q.drawImage(w, 0, one)
        q.end()
        pm = QPixmap.fromImage(strip)
        self._bg_clear_strip = pm
        return pm

    def _draw_bg_clear(self, p, el):
        """クリア後の下背景。焼いた帯から窓を1回切って貼るだけ。

        クリア前の屋台の上へ不透明な絵を重ねる形なので、割合を上げていけば
        そのままクロスフェードになる(提灯の光もいっしょに隠れて消える)。"""
        if not (SHOW_BACKGROUND and SHOW_BACKGROUND_CLEAR):
            return
        a = self._clear_fade(el, BG_CLEAR_FADE_SEC)
        if a <= 0.0:
            return
        pm = self._bg_clear_strip_pm()
        if pm is None:
            return
        # 左へ流れる。絵は画面幅で一巡するので、2枚ぶんの帯の中で窓を位相ぶん
        # 右へずらせば、繋ぎ目なく流れて見える。
        x = int((el * -BG_CLEAR_SCROLL_VX) % SCREEN_W)
        p.save()
        # クリア前の下背景はレーン枠の**下**に敷かれるが、こちらは静的キャッシュ
        # の上から貼るので、そのままだと枠の下辺(y=360..363)を塗り潰して黒帯が
        # 9px から 5px へ痩せる。枠の下端から下だけに限る。
        p.setClipRect(QRect(0, BG_CLEAR_TOP_Y, SCREEN_W,
                            BG_DOWN_Y + BG_DOWN_H - BG_CLEAR_TOP_Y))
        if a < 1.0:
            p.setOpacity(a)
        p.drawPixmap(0, BG_DOWN_Y, pm, x, 0, SCREEN_W, BG_DOWN_H)
        if a < 1.0:
            p.setOpacity(1.0)
        p.restore()
        # フッターは静的キャッシュに焼いてあるので、いま上から塗り潰した。
        # 貼り直して手前に戻す(実機でも笹の下半分はフッターに隠れる)。
        ft = self._skin.get("footer")
        if ft is not None:
            p.drawPixmap(0, FOOTER_Y, ft)

    def paintEvent(self, event):
        # 録画は窓を出さずに render() するので、showEvent を通らない経路がある。
        # ここでも揃えておけば、どちらから来ても同じ絵になる。
        self._ensure_skin()
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
            # クリアすると上背景も赤 -> 金へ変わる。下背景よりゆっくりで、
            # 途中は2色を重ねたクロスフェード。変わりきったら金だけを貼る。
            ta = self._clear_fade(self._clear_elapsed(bg_now),
                                  BG_CLEAR_TOP_FADE_SEC)
            if ta < 1.0:
                self._draw_bg_top_layers(p, bg_now)
            if ta > 0.0:
                if ta < 1.0:
                    p.setOpacity(ta)
                self._draw_bg_top_layers(p, bg_now, BG_CLEAR_UP_COLOR_ROW)
                if ta < 1.0:
                    p.setOpacity(1.0)
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
        # 魂ゲージの満ち具合。ゲージ本体と踊り子(4体目=33% / 5体目=クリア)が
        # 同じ値を見るよう、ここで1回だけ出して両方へ渡す。
        ratio = self._gauge.ratio(combo) if self._gauge else 0.0
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
            # クリア背景が完全に被さったら、その下の提灯の光は1画素も見えない。
            # 加算合成の全面貼りなので、見えないぶんはまるごと省く。
            el = self._clear_elapsed(now)
            if self._clear_fade(el, BG_CLEAR_FADE_SEC) < 1.0:
                self._draw_bg_light(p, now)
            self._draw_bg_clear(p, el)
            # 踊り子はクリア背景より**手前**。流れる背景に乗らず、その場で
            # 踊る(実機のキャプチャでもそう見える)。
            self._draw_dancers(p, now, ratio)
            self._draw_chara(p, now)
        # ゴーゴー突入の火花は画面の下端から。踊り子より手前、左パネルより奥。
        if not self._compact:
            self.draw_gogo_splash(p)
        self._draw_left_panel(p, combo, score, recent, now)
        # 軽量では魂ゲージ(+「クリア」+ 虹)を出さない。ゲージは 400px 超の
        # 帯とブロックを毎フレーム重ね描きするうえ、このプレビューは全ノーツ
        # 自動「良」なので「叩いた数」以上の情報を持たない = 譜面を読むのに
        # 要らない。連打・風船の金の扇(_draw_lane_readouts)は残す — あれは
        # 「今この連打を何回叩いたか」という譜面そのものの情報。
        if not self._lite:
            # 魂の弾けと「魂」の文字は _LaneOverlay が手前に描くので、
            # そちらが使う割合をここで渡しておく。
            self._last_gauge_ratio = ratio
            self._draw_gauge(p, ratio, now)
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
