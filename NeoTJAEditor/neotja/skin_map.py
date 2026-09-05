# -*- coding: utf-8 -*-
"""skin/ の中身を、利用者が用意した TNDE の System/ フォルダから取り出すための対応表。

NeoTJAEditor はこれまで同梱の skin/ フォルダ(403ファイル)から絵・音・フォントを
読んでいたが、中身は TNDE(太鼓さん次郎の派生)の素材そのものなので再配布できない。
そこで「利用者の手元にある System/ フォルダのどのファイルを、どう処理すれば
skin/<相対パス> が得られるか」を1件ずつ書き出したのがこの表。

**copy の389件は手で書き換えるものではない。** 中身は次のように機械的に求めた。

  * copy(392件) … うち389件は skin/ の各ファイルと System/ 配下の全ファイルを
    中身の MD5 で総当たり照合し、完全一致したもの。ファイル名が違うだけの組
    (例: skin/Gauge.png = TNDE-R/Graphics/5_Game/7_Gauge/1P.png)が26件ある。
    実際に System からコピーし直して MD5 を突き合わせ、389/389 が一致することを
    確認済み。残る3件(Base.png / SENotes.png / Combo/Base.png)は後述の
    「TNDE の素材に揃える」で copy にしたもので、こちらは画素一致しない。
  * bundled(2件) … 作者の自作物。System には無いのでアプリに同梱する。
  * decode(3件) … 打音・風船音。System の ogg をデコードして wav にする。
    **PCM は一致しない**(下記)。
  * crop(2件) … System の原本から矩形を切り出す。
  * compose(4件) … System の原本を複数枚(または同じ原本の別の場所を)
    重ねて1枚に組み立てる。

skin/ のうち12件は「TNDE ではない別のソフトの素材」だった。作者が他所から
持ってきたもので、これも再配布できない。そこで**同じ役割の TNDE の素材へ
置き換える**方針にした。置き換えなので現物の skin とは絵も音も変わる
(exact は False)。何をどう選んだかは各項目の note に書いてある。
KIND_UNRESOLVED は 0 件になった。

音の3件は Sounds/Taiko/0(NeiroList.txt の先頭「太鼓」= 既定の音色)と
Sounds/Balloon.ogg を使う。ffmpeg でデコードした PCM と skin 側の wav の
正規化相互相関は 0.58 / 0.27 / 0.13 しかなく(System 内の音声228本すべてと
突き合わせた上での結論)、まったくの別録りだが、役割は同じなのでこれを使う。

各項目は dict で、キーは次のとおり:

  kind    … KIND_COPY / KIND_CROP / KIND_COMPOSE / KIND_DECODE /
            KIND_BUNDLED / KIND_UNRESOLVED
  source  … System/ からの相対パス(POSIX 区切り)。bundled と compose は None
            (compose の原本は layers 側が持つ)。
  rect    … KIND_CROP のときの切り出し矩形 (left, top, width, height)。
            それ以外は None。
  exact   … その手順で現物の skin と中身が一致することを確認できたかどうか。
            copy の389件と bundled だけが True。
  note    … 一致しないもの・注意が要るものの但し書き。

KIND_COMPOSE の項目だけ、さらに次の2つを持つ:

  size    … 出来上がりの大きさ (width, height)。透明で塗った画布から始める。
  layers  … 奥から手前へ重ねる順の list。1枚ぶんが dict で、

              source … System/ からの相対パス(POSIX 区切り)
              rect   … 原本から切り出す矩形 (left, top, width, height)。
                       None なら原本まるごと。
              pos    … 画布のどこへ置くか (x, y)
              tile_x … True なら pos から右へ、画布の幅を埋めるまで
                       幅ぶんずつ繰り返して置く(既定は False)

            重ね方はふつうのアルファ合成(SourceOver)。

キーの skin 相対パスは常に "/" 区切り(例 "Combo/Base.png")。lookup() は
"\\" 区切りで渡されても引けるようにしてある。
"""

KIND_COPY = "copy"
#: System のファイルをそのままコピーする。
KIND_CROP = "crop"
#: System の原本から rect の矩形を切り出す。
KIND_COMPOSE = "compose"
#: System の原本を layers の順に size の画布へ重ねて1枚に組み立てる。
KIND_DECODE = "decode"
#: System の ogg をデコードして wav にする。
KIND_BUNDLED = "bundled"
#: 作者の自作物。アプリに同梱する。
KIND_UNRESOLVED = "unresolved"
#: System から作り直す手順を特定できなかったもの。**現時点で該当する項目は無い。**

#: skin 相対パス -> 取り出し方。
SKIN_MAP = {
    "1_Chara/Balloon_Breaking/0.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Breaking/0.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Breaking/1.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Breaking/1.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Breaking/10.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Breaking/10.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Breaking/2.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Breaking/2.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Breaking/3.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Breaking/3.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Breaking/4.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Breaking/4.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Breaking/5.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Breaking/5.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Breaking/6.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Breaking/5.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Breaking/7.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Breaking/7.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Breaking/8.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Breaking/8.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Breaking/9.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Breaking/9.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/0.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/0.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/1.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/1.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/10.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/10.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/11.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/11.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/12.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/12.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/13.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/13.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/14.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/14.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/15.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/15.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/16.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/16.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/17.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/17.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/18.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/18.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/19.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/19.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/2.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/2.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/20.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/20.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/21.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/21.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/22.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/22.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/23.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/23.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/24.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/24.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/25.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/25.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/26.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/26.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/27.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/27.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/28.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/28.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/29.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/29.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/3.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/3.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/30.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/30.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/4.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/4.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/5.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/5.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/6.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/5.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/7.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/7.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/8.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/8.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Balloon_Broke/9.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Balloon_Broke/9.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/0.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/0.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/1.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/1.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/10.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/10.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/100.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/100.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/101.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/101.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/102.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/42.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/103.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/103.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/104.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/104.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/105.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/105.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/106.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/106.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/107.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/107.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/108.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/108.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/109.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/109.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/11.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/11.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/110.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/110.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/111.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/111.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/112.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/112.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/113.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/113.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/114.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/114.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/115.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/115.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/116.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/116.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/117.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/117.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/12.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/12.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/13.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/13.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/14.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/14.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/15.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/15.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/16.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/16.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/17.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/17.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/18.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/18.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/19.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/19.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/2.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/2.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/20.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/20.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/21.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/21.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/22.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/22.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/23.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/23.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/24.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/24.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/25.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/25.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/26.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/26.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/27.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/27.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/28.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/28.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/29.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/29.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/3.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/3.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/30.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/30.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/31.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/31.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/32.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/32.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/33.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/33.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/34.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/34.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/35.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/35.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/36.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/36.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/37.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/37.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/38.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/38.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/39.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/39.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/4.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/4.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/40.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/40.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/41.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/41.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/42.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/42.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/43.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/43.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/44.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/44.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/45.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/45.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/46.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/46.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/47.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/47.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/48.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/48.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/49.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/49.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/5.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/5.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/50.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/50.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/51.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/51.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/52.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/52.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/53.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/53.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/54.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/54.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/55.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/55.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/56.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/56.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/57.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/57.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/58.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/58.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/59.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/59.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/6.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/6.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/60.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/60.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/61.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/61.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/62.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/62.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/63.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/63.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/64.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/64.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/65.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/65.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/66.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/66.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/67.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/67.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/68.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/68.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/69.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/69.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/7.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/7.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/70.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/70.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/71.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/71.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/72.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/72.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/73.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/73.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/74.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/74.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/75.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/75.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/76.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/76.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/77.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/77.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/78.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/78.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/79.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/79.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/8.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/8.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/80.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/80.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/81.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/81.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/82.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/82.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/83.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/83.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/84.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/84.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/85.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/85.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/86.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/86.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/87.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/87.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/88.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/88.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/89.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/89.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/9.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/9.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/90.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/90.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/91.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/91.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/92.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/92.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/93.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/93.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/94.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/94.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/95.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/95.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/96.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/96.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/97.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/97.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/98.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/98.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGo/99.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGo/99.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/0.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/0.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/1.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/1.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/10.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/10.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/11.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/11.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/12.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/12.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/13.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/13.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/14.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/14.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/15.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/15.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/16.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/16.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/17.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/17.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/18.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/18.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/19.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/19.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/2.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/2.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/20.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/20.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/21.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/21.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/22.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/22.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/23.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/23.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/24.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/24.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/25.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/25.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/26.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/26.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/27.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/27.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/28.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/28.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/29.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/29.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/3.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/3.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/30.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/30.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/31.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/31.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/32.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/32.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/33.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/33.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/34.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/34.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/35.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/35.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/36.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/36.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/37.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/37.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/38.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/38.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/39.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/39.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/4.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/4.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/40.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/40.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/41.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/41.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/42.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/42.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/43.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/43.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/44.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/44.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/45.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/45.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/46.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/46.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/47.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/47.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/48.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/48.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/49.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/49.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/5.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/5.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/50.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/50.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/51.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/51.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/52.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/52.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/6.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/6.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/7.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/7.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/8.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/8.png", "rect": None, "exact": True, "note": None},
    "1_Chara/GoGoStart/9.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/GoGoStart/9.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/0.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/0.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/1.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/1.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/10.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/10.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/100.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/100.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/101.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/101.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/102.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/102.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/103.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/103.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/104.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/104.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/105.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/105.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/106.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/106.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/107.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/107.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/108.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/108.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/109.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/109.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/11.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/11.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/110.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/110.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/111.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/111.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/112.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/112.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/113.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/113.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/114.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/114.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/115.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/115.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/116.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/116.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/117.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/117.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/118.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/118.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/12.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/12.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/13.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/13.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/14.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/14.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/15.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/15.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/16.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/16.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/17.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/17.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/18.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/18.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/19.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/19.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/2.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/2.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/20.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/20.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/21.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/21.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/22.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/22.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/23.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/23.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/24.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/24.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/25.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/25.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/26.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/26.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/27.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/27.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/28.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/28.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/29.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/29.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/3.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/3.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/30.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/30.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/31.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/31.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/32.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/32.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/33.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/33.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/34.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/34.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/35.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/35.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/36.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/36.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/37.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/37.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/38.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/38.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/39.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/39.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/4.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/4.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/40.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/40.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/41.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/41.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/42.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/42.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/43.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/43.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/44.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/44.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/45.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/45.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/46.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/46.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/47.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/47.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/48.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/48.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/49.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/49.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/5.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/5.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/50.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/50.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/51.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/51.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/52.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/52.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/53.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/53.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/54.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/54.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/55.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/55.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/56.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/56.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/57.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/57.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/58.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/58.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/59.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/59.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/6.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/6.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/60.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/60.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/61.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/61.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/62.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/62.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/63.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/63.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/64.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/64.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/65.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/65.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/66.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/66.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/67.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/67.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/68.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/68.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/69.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/69.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/7.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/7.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/70.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/70.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/71.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/71.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/72.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/72.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/73.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/73.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/74.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/74.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/75.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/75.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/76.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/76.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/77.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/77.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/78.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/78.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/79.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/79.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/8.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/8.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/80.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/80.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/81.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/81.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/82.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/82.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/83.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/83.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/84.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/84.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/85.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/85.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/86.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/86.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/87.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/87.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/88.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/88.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/89.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/89.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/9.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/9.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/90.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/90.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/91.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/91.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/92.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/92.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/93.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/93.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/94.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/93.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/95.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/95.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/96.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/96.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/97.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/97.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/98.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/98.png", "rect": None, "exact": True, "note": None},
    "1_Chara/Normal/99.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/1_Chara/1P/Normal/99.png", "rect": None, "exact": True, "note": None},
    # --- 下背景の踊り子 -------------------------------------------------
    # 旧 skin/ には無かった素材で、これも「skin を再現する」ではなく**足す**
    # ためのもの(なので exact は False、下の KIND_COUNTS / TOTAL にも
    # 数えていない)。TNDE の 2_Dancer/Normal/ には 1〜3 の3組が入っていて、
    # そのうち作者の指定で **2 の犬の踊り子**を使う。16コマ(0..15)・全コマ
    # 213x306 の同寸で、中身は 0.png で (30,45)-(190,267)。置き場所と
    # コマ送りの速さは game_screen.py の DANCER_* に置いてある。
    "2_Dancer/Normal/0.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/0.png", "rect": None, "exact": False, "note": "下背景の踊り子(犬)16コマのうち 0。旧 skin には無い追加素材。"},
    "2_Dancer/Normal/1.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/1.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/10.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/10.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/11.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/11.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/12.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/12.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/13.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/13.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/14.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/14.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/15.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/15.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/2.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/2.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/3.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/3.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/4.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/4.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/5.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/5.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/6.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/6.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/7.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/7.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/8.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/8.png", "rect": None, "exact": False, "note": None},
    "2_Dancer/Normal/9.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/2_Dancer/Normal/2/9.png", "rect": None, "exact": False, "note": None},
    "Background.png":
        {"kind": KIND_COMPOSE, "source": None, "rect": None, "exact": False,
        "size": (1280, 316),
        "layers": [
            {"source": "TNDE-R/Graphics/5_Game/5_Background/Bg_up/3/Base.png",
             "rect": (0, 0, 335, 188), "pos": (0, 0), "tile_x": True},
            {"source": "TNDE-R/Graphics/5_Game/5_Background/Bg_up/3/Flower.png",
             "rect": (0, 0, 641, 184), "pos": (0, 0), "tile_x": True},
            {"source": "TNDE-R/Graphics/5_Game/5_Background/Bg_up/3/Chara.png",
             "rect": (0, 0, 656, 233), "pos": (0, 0), "tile_x": True},
        ],
        "note": "1280x316。skin 側はお祭りの町並みの1枚絵で、TNDE の素材ではなかった(Photoshop の XMP が残っている別ソフトの絵)。同じ役割 = 上背景を、TNDE の Bg_up/3 の3枚重ね(傘柄の地 Base + 花びら Flower + 隅の飾り Chara)を流れの位相 0 で焼いたもので置き換える。切り出す矩形と重ねる順は game_screen.py の BG_UP_BASE_CELL / BG_UP_FLOWER_ROW / BG_UP_CHARA_ROW と _bg_up_layers() に合わせてある(1P=赤の駒)。y=233 より下は透明のまま。上背景として見えるのは y<188 だけで、その下はレーン枠と左パネルが覆うので絵は要らない。"},
    "Balloon.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/11_Balloon/Balloon.png", "rect": None, "exact": True, "note": None},
    "Base.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/12_Lane/Base_Normal.png", "rect": None, "exact": False,
        "note": "947x130。同寸の Base_Normal.png(= Background_Main.png と同内容)とほぼ同じで、123110 画素のうち差があるのは 682 画素・最大差 28。画素一致しないので exact は False だが、TNDE の素材に揃える方針としてはそのままコピーでよい。なお現在のコードはこのファイルを読んでいない(レーンの地は Lane_Main.png などを使う)。"},
    "Bg_down.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/5_Background/Bg_down/1/0.png", "rect": None, "exact": True, "note": None},
    "Bg_down_Light.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/5_Background/Bg_down/1/1.png", "rect": None, "exact": True, "note": None},
    "Bg_down_Clear.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/5_Background/Bg_down/c/0/Clear.png", "rect": None, "exact": False,
        "note": "1280x3212。クリア(ノルマ到達)後の下背景。旧 skin/ には無かった素材で、これだけは「skin を再現する」ではなく**足す**ためのもの(なので exact は False、下の KIND_COUNTS / TOTAL にも数えていない)。1枚絵ではなく、透明な行で仕切られた層の縦置きアトラス。不透明な行のかたまりを拾うと 0..448 / 553..870 / 1190..1411 / 1584..1970 / 2188..2396 / 2672..2842 / 2865..3097 の7本で、それぞれ「市松の地+下辺の金雲」「笹+金雲+松桜」「松と桜」「大きな金雲」「独楽(赤)」「独楽(水色)」「独楽(水色・右)」。どの帯をどこへ何枚重ねるかは game_screen.py の BG_CLEAR_LAYERS に置いてある(作者が触るのはあちら)。Bg_down/c/1/ にも DownClear*.png というクリア背景が入っているが、あちらは桃色の青海波で、実機の映像(2026-08-26 12-41-48.mp4)に写るのは c/0 のほうだった。"},
    "Bg_up/Base.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/5_Background/Bg_up/3/Base.png", "rect": None, "exact": True, "note": None},
    "Bg_up/Chara.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/5_Background/Bg_up/3/Chara.png", "rect": None, "exact": True, "note": None},
    "Bg_up/Flower.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/5_Background/Bg_up/3/Flower.png", "rect": None, "exact": True, "note": None},
    "Breaking_0.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/11_Balloon/Breaking_0.png", "rect": None, "exact": True, "note": None},
    "Breaking_1.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/11_Balloon/Breaking_1.png", "rect": None, "exact": True, "note": None},
    "Breaking_2.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/11_Balloon/Breaking_2.png", "rect": None, "exact": True, "note": None},
    "Breaking_3.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/11_Balloon/Breaking_3.png", "rect": None, "exact": True, "note": None},
    "Breaking_4.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/11_Balloon/Breaking_4.png", "rect": None, "exact": True, "note": None},
    "Breaking_5.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/11_Balloon/Breaking_5.png", "rect": None, "exact": True, "note": None},
    "Combo/Base.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/6_Taiko/Base.png", "rect": None, "exact": False,
        "note": "120x133。左パネルの太鼓。同寸の 6_Taiko/Base.png とほぼ同じで、差があるのは 15960 画素中 1748 画素。画素一致しないので exact は False だが、同じ役割・同じ寸法なのでそのままコピーでよい。同じフォルダの Don.png / Ka.png(叩いた面・縁)は既に copy で一致しているので、揃えても違和感は出ない。"},
    "Combo/Digits.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/6_Taiko/Combo.png", "rect": None, "exact": True, "note": None},
    "Combo/DigitsGold.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/6_Taiko/Combo_Big.png", "rect": None, "exact": True, "note": None},
    "Combo/DigitsSilver.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/6_Taiko/Combo_Midium.png", "rect": None, "exact": True, "note": None},
    "Combo/Don.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/6_Taiko/Don.png", "rect": None, "exact": True, "note": None},
    "Combo/Ka.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/6_Taiko/Ka.png", "rect": None, "exact": True, "note": None},
    "Combo/Text.png":
        {"kind": KIND_COMPOSE, "source": None, "rect": None, "exact": False,
        "size": (100, 100),
        "layers": [
            {"source": "TNDE-R/Graphics/5_Game/6_Taiko/Combo_Text.png",
             "rect": None, "pos": (0, 0)},
            {"source": "TNDE-R/Graphics/5_Game/6_Taiko/Combo_Text.png",
             "rect": (0, 75, 100, 24), "pos": (0, 26)},
        ],
        "note": "100x100。skin 側は「コンボ」の帯が y=26(高さ23)と y=76(高さ23)の2段。読み込み側(game_screen.py の _measure_combo_text_bands)が不透明な帯を2つ数える作りなので、2段あることが前提になっている。TNDE の Combo_Text.png は同寸だが帯は y=75(高さ24)の1段だけなので、その帯を y=26 にもう一度置いて2段にする。skin の2段は色違い(通常/金)ではなく、実測すると 100x100 のうち差があるのは 3 画素だけの同じ絵だったので、同じ帯を複製するのが元の作りに忠実。"},
    "CourseSymbol/Easy.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/4_CourseSymbol/Easy.png", "rect": None, "exact": True, "note": None},
    "CourseSymbol/Edit.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/4_CourseSymbol/Edit.png", "rect": None, "exact": True, "note": None},
    "CourseSymbol/Hard.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/4_CourseSymbol/Hard.png", "rect": None, "exact": True, "note": None},
    "CourseSymbol/Normal.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/4_CourseSymbol/Normal.png", "rect": None, "exact": True, "note": None},
    "CourseSymbol/Oni.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/4_CourseSymbol/Oni.png", "rect": None, "exact": True, "note": None},
    "Footer.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/8_Footer/0.png", "rect": None, "exact": True, "note": None},
    # --- 難易度選択画面(Player)。本家の選曲後の画面をそのまま出すための素材。
    # Difficulty_Bar には「戻る/設定のボタン2つ + コース5枚」が1枚に並んでいる。
    # カードは 131x237 で x=176 から 143.25 間隔(かんたん/ふつう/むずかしい/
    # おに/うら)。アイコンも文字も星の帯もカードに含まれているので、切り出す
    # だけで本家と同じ絵になる。
    # おに⇄うら の切り替えに使う部品。1枚に「顔・金属の輪・巴マーク」が
    # 横に3つ、桃(おに)と紫(うら)の2段で入っている。本家はこれを使って
    # 「輪は止めたまま中の顔だけ入れ替える」動きをしている。
    # おに⇄うら をめくるボタンの絵。両向きの矢印(⇕)が角丸の枠に入っている。
    # 巴マークを使ってみたが「入れ替え」に見えず違和感が強かったので、
    # 意味がそのまま伝わるこちらにした(90度回して⇔として使う)。
    "Select_Swap.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/2_Config/Arrow.png", "rect": None, "exact": True, "note": None},
    "Select_OniUra_Parts.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/3_SongSelect/Difficulty_Select/Change_OniUra/UraOniChange.png", "rect": None, "exact": True, "note": None},
    # 選曲画面の BGM。譜面を開いていないあいだ流す(環境設定で切れる)。
    "SelectBgm.ogg":
        {"kind": KIND_COPY, "source": "TNDE-R/Sounds/BGM/SongSelect.ogg", "rect": None, "exact": True, "note": None},
    "Select_Cards.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/3_SongSelect/Difficulty_Select/Difficulty_Bar.png", "rect": None, "exact": True, "note": None},
    # 後ろの大きな角丸パネル。12色あるのはジャンル別だが、Player は
    # ジャンルを持たないので実機の見た目に合う teal(10番)を1枚だけ使う。
    "Select_Panel.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/3_SongSelect/Difficulty_Select/Difficulty_Back/Difficulty_Back_10.png", "rect": None, "exact": True, "note": None},
    # レベルの★(埋まっているぶん)と、★の右に出る数字(0-9 の並び)。
    "Select_Star.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/3_SongSelect/Difficulty_Select/Difficulty_Star.png", "rect": None, "exact": True, "note": None},
    "Select_Number.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/3_SongSelect/Difficulty_Select/Difficulty_Number.png", "rect": None, "exact": True, "note": None},
    "Gauge.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/1P.png", "rect": None, "exact": True, "note": None},
    "GaugeFire.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Fire.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/0.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/0.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/1.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/1.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/10.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/10.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/11.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/11.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/2.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/2.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/3.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/3.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/4.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/4.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/5.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/5.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/6.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/6.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/7.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/7.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/8.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/8.png", "rect": None, "exact": True, "note": None},
    "GaugeRainbow/9.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Rainbow/3/9.png", "rect": None, "exact": True, "note": None},
    "Gauge_Base.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/1P_Base.png", "rect": None, "exact": True, "note": None},
    "GoGoFire.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/10_Effects/Fire.png", "rect": None, "exact": True, "note": None},
    "GoGoSplash.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/10_Effects/GoGoSplash.png", "rect": None, "exact": True, "note": None},
    "HitExplosion.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/10_Effects/Hit/Explosion.png", "rect": None, "exact": True, "note": None},
    "Judge.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/Judge.png", "rect": None, "exact": True, "note": None},
    "Kanteiryu.otf":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/FOT-OedoKtr.otf", "rect": None, "exact": True, "note": None},
    "Lane_Base_Hard.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/12_Lane/Base_Expert.png", "rect": None, "exact": True, "note": None},
    "Lane_Base_Normal.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/12_Lane/Base_Normal.png", "rect": None, "exact": True, "note": None},
    "Lane_Base_Oni.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/12_Lane/Base_Master.png", "rect": None, "exact": True, "note": None},
    "Lane_GoGo.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/12_Lane/Background_GoGo.png", "rect": None, "exact": True, "note": None},
    "Lane_Main.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/12_Lane/Base_Normal.png", "rect": None, "exact": True, "note": None},
    "Lane_Sub.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/12_Lane/Background_Sub.png", "rect": None, "exact": True, "note": None},
    "NamePlate.png":
        {"kind": KIND_COMPOSE, "source": None, "rect": None, "exact": False,
        "size": (280, 79),
        "layers": [
            {"source": "TNDE-R/Graphics/NamePlate.png",
             "rect": (3, 165, 217, 50), "pos": (24, 13)},
            {"source": "TNDE-R/Graphics/NamePlate.png",
             "rect": (5, 4, 48, 48), "pos": (25, 14)},
        ],
        "note": "280x79。「1P どんちゃん」の名前板。System の Graphics/NamePlate.png は 220x1189 の縦長の部品シートで、アルファの帯を数えると上から 1P(赤丸) y=4 h=48 / 1P(青丸) y=58 h=48 / 2P y=112 h=48 / 白い板 y=165 h=50 と並び、その下は結果画面などで使う色帯・飾りが続く。ここで要るのは頭の2つ、白い板と 1P(赤丸)だけ。skin 側は板が x=24..238・y=13..61、赤丸が x=28..67・y=17..56(40x40)にあるので、板をその左上へ、丸を板の左端に重なる位置へ置く。**「どんちゃん」の文字は再現できない** — TNDE は板に名前をフォントで書き込む作りで、文字の絵は System に無い。よって出来上がりは板と 1P だけの空の名前板になる。"},
    "NamePlate_Parts.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/NamePlate.png",
         "rect": None, "exact": True,
         "note": "220x1189 の部品シートをそのまま持ってくる。NamePlate.png のほうは白い板と1Pだけを組み直した完成品だが、称号バーと段位バッジは JSON の内容で選ぶので、切り出しは描くときにやる。アルファの帯を数えた並び(y, 高さ)は 1P赤 4/48・1P青 58/48・2P青 112/48・白い板 165/50、称号バー 13種が 223 277 331 385 438 490 546 600 654 708 762 816 870 の各 22〜26px、段位の黒下地 945/23、段位バッジ 銀金虹が 1000 1054 1108 の各 22px、danGold の金線 1165/15。game_screen の NAMEPLATE_PART_* がこの座標を持つ。"},
    "Notes.png":
        {"kind": KIND_COMPOSE, "source": None, "rect": None, "exact": False,
        "size": (1690, 390),
        "layers": [
            {"source": "TNDE-R/Graphics/5_Game/Notes.png",
             "rect": (0, 0, 1690, 390), "pos": (0, 0)},
            {"source": "TNDE-R/Graphics/5_Game/Notes.png",
             "rect": (0, 0, 130, 130), "pos": (0, 130)},
            {"source": "TNDE-R/Graphics/5_Game/Notes.png",
             "rect": (0, 0, 130, 130), "pos": (0, 260)},
        ],
        "note": "1690x390。130px のセルのシート。System 側は 1690x520 = 4段で、上3段が音符の3コマ、4段目は同じ4種の顔を一回り大きく描いた別コマ(判定円・連打・風船は入っていない)。要るのは上3段なので (0,0,1690,390) を切る。ただし**判定円(左上の1コマ)は System では0段目にしか入っていない**のに対し、skin 側は0/1/2段のどの段にも同じ判定円が入っている。読み込み側(chart_preview_widget.py の _load_skin_sprites / _load_skin_roll / _load_skin_balloon)は真ん中の段(y=130..259)の不透明な列のかたまりを左から数えて [0]=判定円 [1]=ドン [2]=カッ [3]=ドン大 [4]=カッ大 [5]=連打頭 [6]=連打胴 [7]=連打大頭 [8]=連打大胴 [9]=風船 と決め打ちするので、判定円が無いと全部1つずつずれる。そこで0段目の判定円セル (0,0,130,130) を1段目・2段目の同じ位置へも置いて、skin と同じ並びに揃える。"},
    "Number_Roll.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/11_Balloon/Number_Roll.png", "rect": None, "exact": True, "note": None},
    "Panel.png":
        {"kind": KIND_CROP, "source": "TNDE-R/Graphics/5_Game/6_Taiko/1P_Background.png", "rect": (0, 0, 332, 176), "exact": False,
        "note": "332x176。左パネルの地。1P_Background.png(333x176)の左332px と絵柄は同じで、色が全体にずれているだけ(Photoshop で書き出し直されたもの。画素の絶対差の中央値 8)。同じ矩形を素直に切り出す。なお skin/Taiko_Background.png のほうは 1P_Background.png と MD5 完全一致で、現在のコードが読んでいるのはそちら。Panel.png はどこからも読まれていない。"},
    "Rainbow.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/10_Effects/Rainbow.png", "rect": None, "exact": True, "note": None},
    "Roll.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/11_Balloon/Roll.png", "rect": None, "exact": True, "note": None},
    "SENotes.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/SENotes.png", "rect": None, "exact": False,
        "note": "136x360。打音表記の12段(ドン/ド/コ/カッ/カ/ドン大/カッ大/連打/ー/ーっ!!/連打大/ふうせん)。同寸・同じ段構成で、違うのは書体と太さだけ(48960 画素中 11350 画素が異なる)。段の並びが同じなので、chart_preview_widget.py の SE_SPRITE_INDEX はそのまま使える。"},
    "Score.png":
        {"kind": KIND_CROP, "source": "TNDE-R/Graphics/5_Game/6_Taiko/Score.png", "rect": (0, 0, 293, 31), "exact": False,
        "note": "skin 側は 350x35 = 35x35 の白い数字が 0〜9 の横1列。TNDE の 6_Taiko/Score.png は 293x94 で、アルファの帯を測ると y=0(高さ31) 白 / y=32(高さ30) 橙 / y=63(高さ31) 水色 の3段 x 10桁。**白の段だけ**が skin/Score.png と同じ役割なので (0,0,293,31) を切る。**寸法が 350x35 -> 293x31 に変わる**(1桁 35x35 -> 29.3x31)。なお現在のコードはこのファイルを読んでいない。スコアの数字に使っているのは3段まるごとの skin/Score_Plate.png(= 同じ 6_Taiko/Score.png の copy)のほうで、そちらは cols=10 rows=3 で切って段を色として使い分けている。"},
    "Score_Plate.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/6_Taiko/Score.png", "rect": None, "exact": True, "note": None},
    "Soul.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/Soul.png", "rect": None, "exact": True, "note": None},
    "SoulExplosion.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/7_Gauge/1P_Explosion.png", "rect": None, "exact": True, "note": None},
    "Taiko_Background.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/6_Taiko/1P_Background.png", "rect": None, "exact": True, "note": None},
    "Taiko_Frame.png":
        {"kind": KIND_COPY, "source": "TNDE-R/Graphics/5_Game/6_Taiko/1P_Frame.png", "rect": None, "exact": True, "note": None},
    "balloon.wav":
        {"kind": KIND_DECODE, "source": "TNDE-R/Sounds/Balloon.ogg", "rect": None, "exact": False,
        "note": "風船を叩いたときの音。skin 側は TNDE の音ではない(System 内の全音声 228 本と突き合わせても一致するものは無く、最大相関 0.13)。役割が同じ TNDE-R/Sounds/Balloon.ogg をデコードして使う。**音は変わる**。"},
    "demo_silent.wav":
        {"kind": KIND_BUNDLED, "source": None, "rect": None, "exact": True,
        "note": "自作の無音 wav(sample_demo.tja の音源)。同じく同梱する。"},
    "don.wav":
        {"kind": KIND_DECODE, "source": "TNDE-R/Sounds/Taiko/0/dong.ogg", "rect": None, "exact": False,
        "note": "面(ドン)の打音。skin 側は TNDE の音ではない(最大正規化相互相関 0.58、時間伸縮を許しても上がらない)。Sounds/Taiko/0 は NeiroList.txt の先頭「太鼓」= 既定の音色なので、その dong.ogg をデコードして使う。**音は変わる**。"},
    "ka.wav":
        {"kind": KIND_DECODE, "source": "TNDE-R/Sounds/Taiko/0/ka.ogg", "rect": None, "exact": False,
        "note": "縁(カッ)の打音。skin 側は TNDE の音ではない(最大相関 0.27。長さも skin 側 0.501 秒に対し ogg は 0.404 秒)。don.wav と同じく既定の音色 Sounds/Taiko/0 の ka.ogg をデコードして使う。**音は変わる**。"},
    "sample_demo.tja":
        {"kind": KIND_BUNDLED, "source": None, "rect": None, "exact": True,
        "note": "自作のサンプル譜面。System には無いのでアプリに同梱する。"},
}

#: 種別ごとの件数(生成時点の実測値)。合計は skin/ の全ファイル数と一致する。
#: これは「旧 skin/ を System から作り直せるか」を数えたもので、あとから
#: **足した**素材(Bg_down_Clear.png / 2_Dancer/Normal/*.png)はここに数えない。旧 skin に無かった
#: ものを混ぜると、この数が「再現できた件数」を表さなくなるため。
KIND_COUNTS = {
    KIND_COPY: 392,
    KIND_CROP: 2,
    KIND_COMPOSE: 4,
    KIND_DECODE: 3,
    KIND_BUNDLED: 2,
    KIND_UNRESOLVED: 0,
}

#: skin/ の総ファイル数。
TOTAL = 403


def lookup(rel_path):
    """skin 相対パスから取り出し方を引く。区切りは "/" でも "\\" でもよい。

    表に無ければ None を返す。
    """
    key = str(rel_path).replace("\\", "/")
    while key.startswith("./"):
        key = key[2:]
    return SKIN_MAP.get(key)
