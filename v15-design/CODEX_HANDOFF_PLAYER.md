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

# 新 Player 開発の引き継ぎ（Claude → Codex）

作成: 2026-11-17 / 最新コミット: `94abfda`（origin/master に push 済み）

この文書だけで続きを始められるようにまとめた。詳しい仕様は下の「読む順番」の資料にある。

## 読む順番

1. `AGENTS.md`（リポジトリ直下）: 起動方法・テストの隔離・コミットの決まり
2. `v15-design/PLAYER_IMPLEMENTATION_PLAN.md`: 実装の基準。旧 `PLAYER_REDESIGN.md` / `PLAYER_SAMPLE.md` と食い違うときはこちらを優先する
3. `NeoTJAEditor/PLAYER_HANDOFF.md` の先頭の章「新 Player (v15.5 開発中)」: 作った部品と共有部品のフラグの一覧（その下の本文は旧 Player の記録）
4. `v15-design/player-approved/*.png`: 承認済みの画面案。配置と配色の参考で、生成画像の数値・素材はそのまま使わない

## 決まっていること

- **Player は Editor と同じ exe・同じプロセスに入れる。** ランチャー（起動画面）の「Player を起動」から、同じ窓の中で切り替える。別 exe にはせず、アップデーターとリリース手順も変えない。
- **再生・描画・音・時刻は Editor のプレビュー部品をそのまま使う。** Player 用に同じ機能を作り直さない。Player 固有の制限は、共有部品のフラグで扱う。フラグの既定は OFF なので、Editor の動きは変わらない。
- **オートは常に ON。** プレイリスト、手動演奏、自己ベストの保存は今回の対象外。
- **画面に説明の文字を足さない。** 「譜面をつくる」「選曲→再生→結果」のような文字は禁止（利用者の指定）。見出し・ボタン・必要な値だけで作る。
- **ボタンの見た目は利用者のデザイン案に合わせる。** 平たい作りで、暗い半透明の面に細い水色の線で縁取り、角はほぼ立てる。ツヤ・グラデーション・強い影は使わない。文字は太め（Yu Gothic UI の Semibold/Bold）。
- **最大化した窓で作る。** 確認用の画像は 1920×1009（最大化したときの窓の中身）で撮る。

## 画面の流れ（実装済み）

ランチャー → 選曲（`library_view`）→ 難易度選択（`difficulty`）→ 曲名表示5秒（`intro`）→ 演奏（`play_page`、オート）→ 結果（`results`）

- **選曲**
  - 左の一覧: TJAを開く／最近開いた楽曲／保存済み楽曲、その下にシミュレーター別のカテゴリ（名前は利用者が入力、名前順）。
  - `box.def` があるフォルダはジャンルとして出し、`#TITLE` と `#BGCOLOR` を使う。
  - 並び替え: フォルダ名順・難易度順（おに→裏→最高レベル）・BPM順と、↑↓で逆順。最近開いた楽曲だけ「最近順」もある。BPM は表示しない。
  - 検索: 部分一致。NFKC と大文字小文字の無視で比べ、フォルダの下の階層まで探す。
- **難易度選択**
  - おに／裏は「⇔」で切り替える。マークは背景なしの `Difficulty_Mark.png`。
  - 設定パネル（オート再生 ON 固定、ジャケット・背景・LYRIC の ON/OFF）。
  - 試聴は DEMOSTART から終わりまで流してループする。DEMOSTART が無い・おかしい・長すぎるときは0秒から。
  - 音: Play と「もう一度プレイする」は「ドン」、ほかは合成した「ピ」。どちらも F1 の打音の ON/OFF に関係なく鳴る。
- **曲名表示:** 5秒で、飛ばせない。試聴は最初の2秒そのまま鳴らし、残り3秒でフェードアウトする。この間に音源を先に読み込む（`predecode_wave`）。
- **演奏:** 止める・シーク・速さ変更は効かない（`controls_locked`）。Esc でメニュー（続ける／やり直す／詳細モード／選曲へ）。
- **詳細モード:** 作譜ペインを読み取り専用（`read_only`）で出す。止める・シーク・表示切替はできる。最後まで行ったら一時停止し、その画面にとどまる。
- **結果:** 値は `game_screen.result_values()`（演奏画面と同じ計算を、譜面が終わった時点で読む）。王冠は `6_Result/Crown.png` の左の列で、無いときは色の丸で代わりに出す。
- **録画:** 難易度選択の録画から、今ある `RecordDialog` を開く。録画の窓は Editor と1つの枠を共有する。

## 主なファイル（アプリのフォルダは `NeoTJAEditor/`）

| ファイル | 役割 |
|---|---|
| `neotja/start_screen.py` | ランチャー（左 Editor、右 Player の正方形の板） |
| `neotja/main_window.py` | `_show_player` / `_leave_player` / `is_player_visible`。Player 表示中は Editor のキー（Ctrl+S、Undo、F1〜F3 など）を止める |
| `neotja/player/host.py` | `PlayerHost`。画面の切り替え、試聴、Esc メニュー、結果、録画への入口 |
| `neotja/player/library_view.py`, `song_lists.py` | 選曲画面と、その元になるデータ（最近・保存済み・フォルダ・box.def・並び替え・検索） |
| `neotja/player/difficulty.py` | 難易度選択（`paint_glass` が平たい見た目の共通の描き方） |
| `neotja/player/intro.py`, `play_page.py`, `pause_menu.py`, `results.py`, `record.py` | それぞれの画面 |
| `neotja/player/transitions.py` | `FadeStack`（クロスフェードと、黒を挟むフェード） |
| `neotja/player/ui_sound.py` | 「ピ」の合成と、`ui_now` での再生 |
| `neotja/player/core.py` | `PlayerCore`（Player 専用の、隠した PreviewDock を持つ） |

**共有部品に足したもの**（どれも既定は OFF・未使用で、Editor の動きは変わらない）

- `controls_locked`
- `read_only`
- `set_view_only`
- `predecode_wave`
- `ui_now`（mixer）
- `result_values`
- `combo_at`
- `roll_hits_at`
- `RecordDialog(bg_path=)`

**設定のキー**

- `player_recent`
- `player_saved`
- `player_folders`（`{"simulator", "path"}` の配列）
- `player_sort`
- `player_show_jacket` / `player_show_bg` / `player_show_lyric`
- `player_chart_art`（TJA の絶対パスごとに jacket と background を持つ。表示用）

`skin_map.py` に `Select_Marks.png` と `Result_Crown.png` を足した。素材そのものは利用者の TNDE System フォルダから読むもので、リポジトリには入れない。

## テスト

アプリのフォルダから、ファイルごとに直接実行する（pytest は入っていない）。

```bash
cd NeoTJAEditor
PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_host.py
```

**Player のテスト**（8ファイル。今はすべて通る）

- `test_player_host`
- `test_player_difficulty`
- `test_player_preview`
- `test_player_intro`
- `test_player_pause`
- `test_player_detail`
- `test_player_results`
- `test_player_record`

**守ること**

- 本物の設定に触らない。neotja を import する前に、`neotja.settings.settings_path`、`_primary_settings_path`、`_fallback_settings_path` を一時ファイルに向ける。
- テスト用の設定は `master_volume=0.0` にする。
- 画面全体を撮らない。撮るときは `widget.grab()` を使う。
- テストでダイアログやエクスプローラーを開かない。
- 利用者の譜面には触らず、コピーを使う。
- 全テストは、リリースの直前か頼まれたときだけ流す。
- GPU（GL）の画面は `grab()` で正しく撮れない。確かめるときは CPU 描画で撮る。

## 作業の決まり（利用者の方針）

- **ファイルの改行:** 多くのファイルは CRLF。`io.open(newline="")` で読み書きし、`sed -i` は使わない（CRLF が崩れる）。
- **コミット:** `settings.json` は絶対にコミットしない。ステージするときはパスを1つずつ指定する。
- **見せてから作る:** 見た目を変えたら、毎回画像を見せる。
- **コミットの単位:** 1件ずつコミットして push する。版上げ・ビルド・リリース・告知は、利用者の承認が出てから。
- **開いてはいけないもの:** ルートの `NeoTJAEditor.py`（旧 tkinter 版）は起動しない。
- **素材:** TNDE/Taiko の素材はコミットも配布もしない。

## 未決・残っている作業（2026-11-18 現行コード監査）

**完了**
- 録画背景画面のデザイン、難易度設定からの背景・ジャケット指定。
- 曲別にEditor/Player共有。曲別保存で全曲既定を変更しない。
- 難易度選択の背景動画を音源と短い方の終端で同期ループ。
- Player F11全画面と元のウィンドウ状態復元。

**残作業・確認**
- GPU native実物で2点再現: Escメニュー中のサイズ変更で停止画像矩形が旧サイズのまま、詳細モードの作譜ペインがメニュー背面で黒く消える。配置更新と作譜ペイン取り込みによる修正を提案、ユーザー承認待ち。grabFramebufferは有効画像、実設定不変を確認。
- 終了時異常終了の切り分け。テスト終了code1と過去のshiboken/exit127を同一原因と断定しない。
- TJA起動引数は現Editorで未処理。ダブルクリック時Editor直接起動の方針を確認して実装する（OS関連付け自体は未確認）。
- 王冠横のクリア/フルコンボ/全良の文字を残すか、代替フォント（現状Yu Gothic UI Bold→Meiryo Bold→Qt既定）を採用するか、ユーザー回答待ち。
- 詳細モード上部の黒は共通MODE_EDIT→set_lite(True)の現仕様。Player固有の不具合とは扱わず、表示を変えるなら別途合意する。
- 公開前の実音声・録画書き出しを含む確認とリリース承認。未承認の版上げ・ビルド・公開は行わない。

**今回の対象外**

- プレイリスト（連続再生）
- 手動演奏
- 自己ベストの保存
- 曲名表示を録画に入れること
