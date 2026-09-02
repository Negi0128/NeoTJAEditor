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

- [x] **工程1** 骨組み。単独起動して1曲再生（`2640f95`）
- [x] **工程2** 全画面。F11 / Esc（`63bedf9`）
- [x] **工程3** まとめて録画（`neotja/player/batch.py`）。3譜面を実際に書き出して確認
- [x] **工程4** 難易度選択画面（`select_screen.py`）。本家の座標を実測して再現
      曲一覧（`library.py`）も作ったが、単発ファイル中心という使い方に合わず
      いまは窓から外してある（ファイルは残っている）
- [x] **工程5** Editor 連携（実行メニュー → NeoTJAPlayer を別ウィンドウで開く）と改名27箇所
- [x] **工程6** `NeoTJAPlayer.spec` でビルド。exe 109.5MB、実際に起動して確認
- [x] **追加** 難易度選択画面・2枚から選ぶ動き・設定ボタン。exe 再ビルド済み
- [ ] **残り** リリース（利用者の承認待ち）

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

```
cd C:\Users\admin\Desktop\プログラミング\NeoTJAEditor\NeoTJAEditor\NeoTJAEditor
.venv\Scripts\python.exe -m neotja.player [譜面.tja] [--course Oni] [--at 12.3]
```

## 注意

- `settings.json` は**絶対にコミットしない**（`git add` は明示的なファイル列挙で）
- 太鼓さん次郎 / TNDE の素材は同梱・再配布しない
- リリース（push・ビルド・`gh release`）は利用者の承認を得てから
