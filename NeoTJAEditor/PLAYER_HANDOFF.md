## 2026-11-18 GPU途中メニュー修正完了

- native GL停止画像とQt詳細ペインを合成し、メニュー背面の下段が黒く消える問題を修正。
- リサイズ時に現在の配置で再合成。実GPUで矩形一致と下段表示を確認。
- 回帰テスト1件、Escメニュー関連5件が成功。実設定は変更なし。
- 下記の過去記録にあるGPU修正承認待ちは解消済み。TJA直接起動は追加しない、代替フォントは現状採用、王冠表記はユーザー指定で確定済み。
- 残作業: 終了時異常の切り分け、実音声と録画書き出しを含む公開前確認。リリースは未承認。

## 2026-11-18 表示・起動方針の確定

- 王冠横は虹「ドンだフルコンボ」、金「フルコンボ」、銀「クリア」。
- TJAダブルクリックでEditorを直接開く対応は追加しない（ユーザー指定）。
- 勘亭流なしでは現在のYu Gothic UI Bold→Meiryo Bold→Qt既定を採用（ユーザー一任）。

## 2026-11-18 難易度選択の背景動画（Codex）

- ユーザー確定: 音源か動画の短い方の終端で、両方DEMOSTARTへ戻す。動画音声は常にミュート/音量0。
- `player/demo_video.py` のQt動画デコードを難易度画面へ表示し、既存PlayerCoreの音声時計へ追従。動画尺が先/後に判明する両方に対応。DEMOSTARTが短い方の尺以上なら両方0秒へ補正。
- 背景OFF、曲変更、録画/素材設定、選曲復帰、曲名表示、終了で動画停止・解放。動画の読込失敗は無地背景へ戻し音源試聴を継続。古い動画通知は世代IDで破棄。
- 対象テスト: test_player_preview 14件、test_player_demo_video 2件、test_player_intro 6件、test_player_chart_art 2件を確認。実H264動画とQVideoSinkでフレーム表示/ループ/無音を確認（音源時計はテスト用）。実音声出力の聴取検証は含まない。
- 今回は難易度選択の試聴のみ。本編・結果画面の動画描画の追加ではない。GPU途中メニューの実機確認は別途残る。

## 2026-11-17 Player F11対応（Codex）

- Player内のF11はMainWindow全体の全画面を切り替える。通常サイズ/最大化状態を復元し、Playerを離れる際も復元。Escの途中メニューは変更しない。ダイアログ中とEditorでは介入しない。
- `test_player_fullscreen.py` の2件で復元とキーの単回処理を確認。旧本文の「F11未実装」は解消済み。

## 2026-11-17 曲別背景・ジャケット共有（Codex、ユーザー承認済み）

- ユーザー確定B: 曲ごとにEditorとPlayerで共有し、全曲共通の既定は変更しない。
- 難易度設定の表示ON/OFFの下へ「背景・ジャケットを指定」を追加。既存の背景調整画面を開き、OKのみ保存、取消は保存しない。試聴は閉じた後に再開。
- 共通処理は `neotja/chart_art.py`。基本ジャケットは `.tja.jacket.json` のbase、背景と調整値は `player_chart_art` の曲別entryに保持。歌詞別画像・未知のsidecar項目・Editorの未保存LYRICを保持。明示した「なし」と未指定を区別。
- Playerからの画面では全曲の「既定にする／消す」を隠して無効化。Editor/Playerの録画で曲別調整値を読み書きし、保存済み曲の録画開始で `rec_bg_*` を上書きしない。
- 共有データ5件、設定入口2件、難易度7件を検証。録画4件のassertもOK。録画テストのQt音声フォールバックで一時音源を解放する後始末を追加。既存の終了code1は残る。今回の検証は無音で、実音声出力の確認は含まない。
- 実設定と指定TJA原本を変更せず、1920×1009の設定パネル画像を確認。背景動画のループ再生は別工程で未実装。

> **旧Playerの記録です。** 新Playerの実装は [PLAYER_IMPLEMENTATION_PLAN.md](../v15-design/PLAYER_IMPLEMENTATION_PLAN.md) を優先してください。本文に残る別exe、旧起動メニュー、プレイリスト先行等は過去の前提です。現行コードと最新計画を確認してください。

## 新 Player (v15.5 開発中)

ランチャーの「Player を起動」で、Editor と**同じ窓・同じプロセス**の中央へ差し込む1ページ(PlayerHost)。別 exe ではない。計画の基準は `v15-design/PLAYER_IMPLEMENTATION_PLAN.md`。

### 流れ(できているもの)
選曲 → 難易度選択(DEMOSTART から試聴ループ・設定パネル・録画) → 曲名表示(5 秒・飛ばせない・試聴は 2 秒後から 3 秒でフェード) → 通常プレイ(常時オート。停止・巻き戻し・シーク不可) → Esc の途中メニュー(続ける / 初めからやり直す / 詳細モード⇔通常演奏 / 選曲画面にもどる) → 譜面の終わりで結果画面(もう一度プレイする / 選曲画面に戻る・Esc)。
詳細モードは作譜ペインを読むだけで出し、停止・シーク・速度は可。終わりまで行くとそこで止まり、結果へは進まない。
難易度選択の「録画」は Editor と同じ RecordDialog を開く(曲ごとのジャケット・背景を最初の値に。.jacket.json は書かない。Editor と窓の置き場 `MainWindow._record_dialog` を共有して2つ開かない。閉じたら試聴を再開)。

### ファイル(`neotja/player/`)
- `host.py` … PlayerHost。ページの流れ・試聴・曲名表示・再生・途中メニュー・結果・録画の段取り
- `library_view.py` / `song_lists.py` … 選曲(最近 / 保存済み / 登録フォルダ、box.def ジャンル、並び替え、検索、曲ごとの画像)
- `difficulty.py` … 難易度選択。平らなガラスの描き方(paint_glass)・書体(勘亭流 / UI)もここ
- `intro.py` … 曲名表示と IntroSequence
- `play_page.py` / `pause_menu.py` … 再生画面(GamePreviewWindow を埋め込む)と Esc メニュー
- `results.py` … 結果画面(王冠は skin の Result_Crown.png の左列、無ければ色の丸)
- `record.py` … 録画の窓に渡す入れ物
- `transitions.py` … FadeStack(cross / black)
- `ui_sound.py` … 「ピ」(合成)と「ドン」。Editor の音声エンジンの `ui_now` で鳴らす(F1 に関係なく鳴る)
- `core.py` … PlayerCore(PreviewDock を1つ抱える。読むだけ・Player 専用)

### 共通部品に足した口(どれも既定は今までどおり = Editor は変わらない)
- `GamePreviewWindow.set_controls_locked(on, key_cb)` / レーンの `controls_locked` … 通常プレイで操作を締める(キー・ホイール・Tab・クリック・窓の F11/Esc)。Esc は key_cb へ
- `PreviewDock.set_read_only` / `ChartEditWaveform.set_read_only` / `WaveformWidget.set_read_only` … 作譜ペインの編集経路を全部ふさぐ
- `GamePreviewWindow.set_view_only(on, with_bottom)` / `fit_view_only` … 絵だけ(+ 下の段)で置く
- `PreviewDock.predecode_wave` … 試聴を止めずに本編の音源を先読み
- ミキサーの `ui_now` … 操作音
- `game_screen.result_values()` / `gauge_strip()`、`ScoreTimeline.roll_hits_at`、`ChartPreviewWidget.combo_at` … 結果は HUD と同じ計算を譜面の終わりで引く
- `PreviewDock.set_speed_row_visible` / `set_pane_follow_scale`、`RecordDialog(bg_path=)`
- ゲーム画面の `set_underlay(u)`(既定 None)… 再生画面の背景・ジャケット(`player/backdrop.ChartBackdrop`)を下地の下(背景なしのジャケットは上)へ描く。録画は使わない

### 設定のキー(config_data を Editor と共有)
`player_folders` `player_recent` `player_saved` `player_sort` `player_last_file` `player_chart_art`(曲ごとのジャケット・背景) `player_show_jacket` `player_show_bg` `player_show_lyric` `player_select_bgm` `player_select_bgm_volume`。skin キャッシュに `Select_Marks.png` `Result_Crown.png` を追加(TNDE の素材から。同梱しない)。

### 試験(アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/<file>.py`)
`test_player_host` `test_player_difficulty` `test_player_preview` `test_player_intro` `test_player_pause` `test_player_detail` `test_player_results` `test_player_record`。

### 残っていること
- GPU(native GL)のときの途中メニューの見た目は未確認(止めた画面を敷く作り)
- 録画の「背景...」の窓は左右分割・濃紺フラットへ変更済み（2026-11-17、利用者の実画面承認済み）。左プレビュー/シーク/PNG、右背景/ジャケット/フェード。保存仕様は維持。隔離した1920×1009画面と既存PNGテスト2件のassertを確認。終了code1は変更前の同テストでも再現し未解決。
- 計画書の B(曲別と既定の関係)/ C(TJA 直接起動)/ D(代替フォント)は未決定
- プレイリストは今回の範囲外。F11 の全画面は未実装
- 終了時のクラッシュ(shiboken の PepException_GetArgs 系)は未解決


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
- **F11** 再生画面を全画面にする/戻す(戻すのは Esc でも可)。
  2026-10-05 までは「ボタン類を隠す/出す」だったが、F11 は全画面という
  期待のほうが強いので差し替えた。隠す機能は全画面が兼ねるので廃止

### まだ確かめていないこと

- **まとめて録画を実データで**。試したのは同じ譜面を3つ複製したものだけで、
  30譜面・長い曲・音声波形レイアウトでは通していない
- **曲一覧(library.py)** は作ってあるが窓から外してある(単発ファイル中心と
  いう使い方に合わないため)。ファイルは残っている

### 残っている作業

- ~~再生中の 437ms の停止~~ → 直した(虹の帯の列ごとの中心を pixelColor の
  二重ループで測っていた。numpy にして 447ms -> 6.8ms)。**いまは 4476コマで
  中央値 3.10ms・最悪 9.7ms・8ms 超えは1回だけ。**
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

## 配布ビルド確認（2026-11-10 / v14.9.0）

- Editor・Player双方のspecで、外部ツールのPATHから混入するicuuc.dll／icuin.dll／icudt*を除外。
  PopplerのICUがWindows標準ICUを隠し、QtGuiの起動時に関数不足となることを配布前に検出した。
- 修正後のPlayer exeが起動することを、隔離した設定・TEMP・LOCALAPPDATAで確認。
- Editorの実差し替え処理で両exeを14.7.0へ変更し、14.9.0へ戻した後のハッシュ一致も確認。
- ユーザー設定は変更していない。実exeの画面操作はツール許可の制限で未実施。

## v14.9.0 以降の風船・くす玉の確認

- BPM 240、必要 8 打、秒速 45 で、`7080000000000000,` は 5 打、`7008000000000000,` は 7 打＋破裂音。再生と録画は同じ打音生成処理を使う。
- `tests/test_short_balloon_ticks.py` で打音の間隔、累計表示、通常連打の短連打補正を確認できる。

