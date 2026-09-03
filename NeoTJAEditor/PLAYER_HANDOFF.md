# NeoTJAPlayer 開発の引き継ぎメモ

作業が中断しても、このファイルだけ読めば続きから再開できるようにしてある。
**工程を1つ終えるたびに、ここを更新してからコミットすること。**

## これは何を作っているか

再生と録画に特化した別 exe「NeoTJAPlayer」。用途は2つ。

- 鑑賞会で流す（全画面）
- 30譜面などをまとめて録画する

譜面を編集する機能は載せない。

## 決まっていること（利用者の判断済み）

| 項目 | 決定 |
|---|---|
| まとめて録画の入口 | Player の画面にキュー（コマンドラインではなく GUI） |
| 鑑賞会で要るもの | 全画面表示 / 曲を選ぶ一覧画面（連続再生は今回入れない） |
| 「えぬいーさん次郎」の改名 | 画面に出る所ぜんぶ（ヘルプ本文9箇所を含む全26箇所） |
| 設定 | Editor と**共有**。ただし保存は読み直して自分のキーだけ書き戻す |

## 設計の要（ここを外すと作り直しになる）

**再生機能は作り直さない。** `PreviewDock` は `MainWindow` を1箇所も参照して
おらず、結合は全部コールバック（必須は `apply_offset_cb` ひとつだけ）。
Player は `PreviewDock` を1つ作り、その `game_preview_window` を見せるだけで、
4モード・速度・表示倍率・コース切替・録画・音声波形がそのまま手に入る。

**録画は画面外の専用ウィジェットを1コマずつ描く方式。** 画面を録るのではない
ので、書き出し中に他の操作をしても出来上がりに影響しない。

## 工程と進み具合

### 出来ていて、実機で確かめたもの

- **単独起動と再生** 4モード(通常再生/軽量/音声波形/情報)・速度・表示倍率・
  コース切替・録画が、Editor と同じものがそのまま動く
- **難易度選択画面** 本家の座標を実測して再現。おに/うら は2枚出して選ぶ。
  無いコースは薄暗く ★-。押せる所でカーソルが変わり、押すとドンが鳴る
- **音** 未選択時は SongSelect.ogg、譜面を選ぶと DEMOSTART から試聴してループ
- **まとめて録画** 3譜面を通しで書き出して3件とも成功(5.6秒)
- **Editor 連携** 実行メニューから、譜面・コース・再生位置を渡して起動
- **改名** えぬいーさん次郎 → NeoTJAPlayer を27箇所
- **F11** 再生画面のボタン類を隠す/出す

### まだ確かめていないこと

- **まとめて録画を実データで**。試したのは同じ譜面を3つ複製したものだけで、
  30譜面・長い曲・音声波形レイアウトでは通していない
- **曲一覧(library.py)** は作ってあるが窓から外してある(単発ファイル中心と
  いう使い方に合わないため)。ファイルは残っている

### 残っている作業

- **再生中の 437ms の停止が1回**。4644コマ中これ1つだけで、ほかは中央値
  3.07ms・最悪 5ms。**再生開始 30.3 秒＝クリアに届いた瞬間**に起きているので、
  クリア演出の背景を作り直しているところが濃厚。譜面を読んだ時点で焼いて
  おけば消えるはず。
- **リリース**(利用者の承認待ち)。exe は古い(選曲画面より前のもの)

## いまのファイル構成

```
neotja/player/
  __init__.py    なぜ別アプリなのかの説明
  __main__.py    引数（譜面 / --course / --at）と素材の用意
  core.py        PlayerCore: PreviewDock を抱えて譜面を読む。Qt の窓は作らない
                 save_shared_settings(): 読み直して PLAYER_KEYS だけ書き戻す
  window.py      PlayerWindow: タブ2枚（譜面を見る / まとめて録画）
  select_screen.py  難易度選択画面。座標は実機のスクショと素材から実測
  library.py     LibraryPage: フォルダを覚えて譜面を並べる。走査は別スレッド
  batch.py       BatchPage: 待ち行列に積んで順番に書き出す
```

## 使う API（録画まわり）

すべて `neotja/recorder.py`。

- `prepare_recording(...)` … Qt に触らないのでワーカースレッドで走らせる。
  `want_mips=True` で音声波形モード用の波形も作る。`cancel=CancelToken()`。
- `make_offline_widget(preview_data, offset, se_text_enabled)` … 本家レイアウト
- `make_offline_wave_widget(preview_data, offset, mips, se_text_enabled)` … 音声波形
- `VideoRecording(widget, out_path, plan=, canvas=, supersample=, preset=)`
  … QTimer で少しずつ回す。GUI から使うときはこちら。
- `record_chart_video(...)` … 最後まで回す同期版（GUI なしの用途）

譜面の終わり（＝最終小節が終わる時刻）は `recorder.chart_end_seconds(
preview_data, offset)`。`bar_times` は小節の**開始**しか持たないので、最後の
小節線の BPM と `#MEASURE` から `240/BPM × 拍子` を足して求める。

## 動かしかた

**ふだんはソースから動かす。exe は作り直さない。**

```
cd <このリポジトリの NeoTJAEditor フォルダ>
.venv\Scripts\python.exe -m neotja.player [譜面.tja] [--course Oni] [--at 12.3]
```

ビルドは1回 80〜110 秒かかるうえ、**開いたままの exe がファイルを掴んで
いると失敗する**(実際に起きた)。手を入れて確かめる往復にはまったく
向かない。exe を作るのは配るときだけ:

```
.venv\Scripts\python.exe -m PyInstaller NeoTJAPlayer.spec --noconfirm
.venv\Scripts\python.exe -m PyInstaller NeoTJAEditor.spec --noconfirm
```

## 注意

- `settings.json` は**絶対にコミットしない**（`git add` は明示的なファイル列挙で）
- 太鼓さん次郎 / TNDE の素材は同梱・再配布しない
- リリース（push・ビルド・`gh release`）は利用者の承認を得てから
