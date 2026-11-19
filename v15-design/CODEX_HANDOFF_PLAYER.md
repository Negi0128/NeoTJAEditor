## 2026-11-19 プレイリスト 第2段（Claude）

曲を入れる・並べる・外す・選んで遊ぶ。連続再生・録画はまだ。版上げ・ビルド・リリースはしていない。

- 保存（`playlists.PlaylistStore`）: `add_item` / `remove_item` / `move_item` / `set_item_course`（位置で指す。同じ曲の重複可。変えるたびに保存）。`default_course(courses)` = おにがあればおに、無ければいちばん難しいもの（裏だけなら裏）。道は normpath(abspath)。
- 曲を追加: 開いたプレイリストの道具の段の右に「＋ 曲を追加」（#flat）。押すと `begin_add_mode` → 右の上に帯 `playlist_view.AddModeBar`（＋・追加先の題・「完了」だけ）を出して `show_default`。あいだは `click_song`（いつもの行・Enter・「TJAを開く」のファイル選択も）が `add_song_to_playlist` になり、試聴・難易度選択はしない。入れた行は縁を 0.65 秒光らせる（`SongRow.flash`）。「完了」でそのプレイリストへ戻る。左の列でプレイリスト（一覧・開く）へ行っても終わる。
- 右クリック: いつもの曲の行 → 「プレイリストに追加」▸ プレイリストの題（無ければ押せない）。プレイリストの曲の行 → 「難易度を変更」/「外す」。メニューは `LibraryView.exec_menu(menu, pos)` を通す（試験で差し替え。PySide の QMenu.exec はクラスに差し替えても効かない）。
- コース選び `playlist_view.CoursePicker`: 縁なしの小さな窓に、譜面にあるコースだけ難易度選択の `CourseCard` を 0.62 倍で並べる（Select_Marks のマーク・勘亭流の名前・★）。最初に選んでおくのは default_course（変更のときは今のコース）。押したら決まり／←→と Enter／キャンセル。**コースが1つしか無い曲は聞かない**（判断）。差し替え口 `LibraryView.course_picker(題, courses, 今)`。
- プレイリストの曲の行 `PlaylistSongRow`（SongRow の子）: 左に何番目か、右（☆と試聴の印のあいだ）に保存したコースのマーク（`CourseMarkIcon` 34px）。見つからない曲は灰色・「ファイルなし」・押しても何もしない（再指定ボタンは出さない。外す・並べ替えはできる）。押して離すと選ぶ／押したまま動かすと QDrag（`PLAYLIST_MIME`）で並べ替え。`PlaylistRowList` が落とす位置に水色の線（`dropLine`）を出し、縁の近くでは送る。並びはプレイリストの順のまま（並べ替えの段なし）。
- 選んで遊ぶ: `click_playlist_item(index)` — 1回目は選んで試聴（印は押した行だけ。同じ曲の別の行は選ばない）、同じ行をもう一度で難易度選択。`LibraryView.chosen_course()` を `PlayerHost._on_song_chosen` が `show_difficulty(course=…)` に渡し、保存したコースを選んだ状態で開く。いつもの行を押すと保存したコースは使わない。並べ替え・外すで選んでいる行の番目を追う。
- 試験: `test_player_playlists` に足して 19 件（追加のあいだ・右クリック・番号/マーク/見つからない曲・2回押し・並べ替え（dropEvent も）・難易度を変更・外す・PlayerHost へコースを渡す）。`test_player_sidebar_genres` / `test_player_library_preview` / `test_player_library_thumbs` / `test_player_recent` / `test_player_difficulty` 通過。実設定は変更なし。
- 画像（1920×1009・widget.grab）: scratchpad の `playlist2/` に pl2_add_mode（帯と、入れた2行が光っているところ）/ pl2_course_picker / pl2_right_click_menu・pl2_right_click_submenu / pl2_playlist_rows（4行目を試聴中・5行目が見つからない曲・2と6が同じ曲の別コース）/ pl2_drag_line。

## 2026-11-19 プレイリスト 第1段（Claude）

仕様は `v15-design/PLAYLIST_SPEC.md`。第1段だけ（器・一覧・作成/編集/削除）。曲の追加・連続再生・録画はまだ。版上げ・ビルド・リリースはしていない。

- 利用者フォルダ `neotja/user_dirs.py`: exe（sys.frozen）なら sys.executable のフォルダ、ソースならアプリのフォルダ（NeoTJAEditor/NeoTJAEditor）の下の `ユーザーデータ/` に `録画` / `YouTube` / `プレイリスト` / `ジャケット`（使うときに初めて作る。読むだけでは作らない）。書けなければ `%LOCALAPPDATA%\NeoTJAEditor\ユーザーデータ` へ退避。試験は `NEOTJA_USER_DIR` か `user_dirs.base_override` で差し替え（差し替え先の直下に種類のフォルダ）。いま使っているのはプレイリストとそのジャケットだけ。録画・YouTube・曲のジャケットの保存先はまだ変えていない（次の段）。`.gitignore` に `NeoTJAEditor/ユーザーデータ/`。
- 保存 `neotja/player/playlists.py`: `プレイリスト/playlists.json` = `{"version":1,"playlists":[{id,title,description,jacket,items:[{path,course}]}]}`。一時ファイル→os.replace。壊れたファイルは `playlists.json.broken-日時` へ退かして空から。ジャケットは選んだ絵を `ジャケット/<id>_<日時>.<拡張子>` へ写し、json にはファイル名だけ（フォルダごと動かしても切れない）。替える・外す・削除で古い写しを消す。`PlaylistStore`（create/update/delete、変えたらすぐ保存）。
- 選曲: 左の列の「保存済み楽曲」の直下に「プレイリスト」（`nav_playlists`、kind "playlists"）。右に `page_playlists`（「＋ プレイリストを作成」= QSS `QPushButton#flat`、行 `playlist_view.PlaylistRow`: 88px のジャケット・題・説明・›）。ジャケットが無ければ1曲目のジャケット（`chart_art.existing_art`）、それも無ければ No Image の枠。右クリック 編集 / 削除（削除は `ConfirmDialog`、open() で待たない）。押すと `page_playlist`（パンくず「プレイリスト › 題」・検索欄・曲の一覧。今は空で「なし」）。`_list_key` に開いている id を入れたので、一覧⇔開く・別のプレイリストで `list_switched`（試聴を止める）。並び替えの段は出していない（プレイリストの順を保つため＝判断）。
- 作成/編集の窓 `playlist_view.PlaylistDialog`: 録画の背景の窓と同じ色・形（#101925・角 2〜3px・Yu Gothic UI）。タイトル / 説明（複数行）/ ジャケット（見本・「画像を選択…」・「外す」）、キャンセル / 作成（編集は「保存」）。題が空なら押せない。説明の字は足していない。
- 差し替え口（試験は窓を出さない）: `LibraryView.playlist_editor(pl or None)` → (題, 説明, 絵, 外すか) / None、`LibraryView.playlist_confirm(見出し, 実行の字, はいの呼び出し)`、`PlaylistDialog.pick_image`。
- 試験（新規）: `test_player_playlists`（12件）。`test_player_sidebar_genres` / `test_player_library_preview` / `test_player_library_thumbs` / `test_player_recent` も通過。実設定（settings.json）は変更なし。
- 画像（1920×1009・widget.grab）: scratchpad の `playlist1/` に pl1_sidebar_list / pl1_opened_empty / pl1_create_dialog / pl1_edit_dialog。

## 2026-11-19 Player 不具合まとめ対応 第5弾（Claude）

項目ごとにコミット・push 済み（789e321 / a407803 / b16594a / 6e8b2d9 / 9a2e389）。版上げ・ビルド・リリースはしていない。

- A ジャンルの開け閉め: 左の列のシミュレーターの見出しを `CategoryHead`（QLabel#category のまま、右端に ▸/▾ を QPainter で）にし、押すとその下の項目を隠す/出す。畳んだ名前は `player_collapsed`（名前の一覧）。作り直し・名前の変更でも畳んだまま。NeoTJAPlayer の見出しは今までどおり。
- B 一覧を替えたら試聴を止める: `LibraryView.list_switched`（show_section / open_dir / 登録フォルダの検索の結果が替わったとき。同じ一覧を押し直しただけでは出さない）→ `PlayerHost._on_list_switched` が試聴・背景動画・待っている試聴を止める。難易度選択から戻ったときは今までどおり止めない。
- C 再生中の印: 選んだ行の右端に `PlayMark`（流している=水色の棒が揺れる / 止めた=丸の中の ⏸）。場所はどの行でも取っておく（選んでも並びがずれない）。印を押すと止める ⇔ DEMOSTART から流し直す（`preview_toggled`。難易度選択へは進まない）。行の本体の押下は今までどおり。選曲へ戻るときは `core.demo_active_for` を見て印を合わせる。
- E 16:9: `wants_aspect_lock` は Player を出しているあいだずっと（詳細モードも＝判断）。保つ部分は `PlayerHost.aspect_widget()`＝Player 全体（上の段の有無で窓が変わらない）。Player に入ったとき・最大化/全画面から戻ったとき（戻った大きさが付いた次の resizeEvent、保険に 250ms 後）にも合わせる。Editor では何もしない。入るときに窓の高さが変わる（Editor へ戻っても元の大きさには戻さない）。
- D 移り目の音: 原因は GUI が GIL を長く持ち、Python の音声コールバック（出力の遅れ約 22ms・10ms ごと）が間に合わないこと。手当て: Player のあいだ `sys.setswitchinterval(0.001)`（`core.audio_friendly_gil`、出るとき戻す）／クロスフェードは新しい画面も1枚の絵にして重ねた絵だけで塗る（1コマ約 17ms → 約 3.5ms）／試聴が同じ音源を読んでいる最中は本編の先読みを2本目として始めない／どんちゃんのコマを 4ms ずつ QPixmap に（`chara.CharaSprites._absorb_some`）。測った値（1920×1009・SHEESH・音量 0・コールバックの間隔）: フェード中の最大 18〜27ms → 14〜16ms（20ms 超え 1〜3 回 → 0 回）、曲名表示中 15〜16ms のまま。測り方の注意: `QTest.qWait` は GIL を持ったまま待つので、待ちにこれを使うと音声が大きく遅れて見える（QEventLoop で待つこと）。
- 残り（他の担当のファイル）: 再生画面に入るとき `play_page.attach` の setParent が約 0.7 秒、最初の GL のコマが 0.4〜0.5 秒、再生直後に QOpenGLWidget の event（C++ 部分）で 90〜120ms GUI が止まる。どれも GIL を持ったまま。試聴を止めたあと／助走 3 秒の無音のうちなので聞こえないはずだが、助走の短い使い方をするなら要注意。
- 試験: 新規 `test_player_audio_transition`。`test_player_sidebar_genres` / `test_player_library_preview` / `test_player_window_aspect` に足した。`test_player_host` の PlayerHostTests は通しで流すと3件目で code 127（変更前の版でも同じ。1件ずつなら通る）。
- 画像（1920×1009・widget.grab）: scratchpad の `batch5/` に A_sidebar_collapsed / C_mark_c（流している）/ C_mark_cpause（止めた）/ C_mark_zoom。

## 2026-11-19 FPS/詳細モード数値（Claude）

項目ごとにコミット・push 済み（432cde4・77db2bd / b0f395c / 2倍 d8b5ee5 / 太字・右上・曲名は下の追記のコミット）。版上げ・ビルド・リリースはしていない。

- FPS 表示の比: Player の fps 表示（`set_view_fps`）の箱・字・左上の余白を、ゲーム画面（ScaledHost）の幅に比例させる（幅 1280 で字 24px・Meiryo UI の太字・余白 16,12。箱は見本「0000 fps」の幅と字の高さに余白 6,1 を足しただけの詰めた大きさ＝`view_fps_base_box`。`PreviewDock._fit_view_fps`、大きさが変わるたびに当て直す）。字は小数の pt で**自分の QSS に書く** — PlayerHost の QSS「QWidget { font-size: 13px }」（StartScreen._qss）が子へ効き、setFont では 13px のまま動いていなかった（箱だけ伸びていた）のが元の不具合。CPU・QOpenGLWidget・native GL の窓で同じ比を確認。外すと Editor と同じ作りへ戻る（Editor は呼ばない）。
- 詳細モードの数値: `value_readout.ValueReadout` を作り直し、作譜モードの命令パネルと同じ色・字（枠 rgba(20,24,34,220)・線 #33405c・見出し #8fa3c0 12px・欄 #10141d/#2e3a50・値 14px 白）を **2倍**（`ValueReadout.ZOOM = 2.0`。枠・欄・字・余白・間ぜんぶ）にして、ゲーム画面の右上の隅（余白 8×倍率、`VALUE_READOUT_MARGIN`）に置く。「BPM」「拍子」（欄2つと /）「SCROLL」「見た目BPM」の4つの独立した枠に。矢印・追加/変更ボタンは無し、値に合わせた幅（見本より長い値が来たら広げる）。値が変わった枠だけ縁を #7aa2f7 で光らせて 0.7 秒で戻す。表記は命令パネルと同じ桁（BPM 3桁・SCROLL 3桁・見た目BPM 1桁）。拍子は `_measure_times_of` から（命令パネルと同じ）。
- 曲名: Player の再生画面（`set_view_only` のあいだ。通常プレイ・詳細モードとも）は曲名を 1.1 倍（`GamePreviewWindow.VIEW_TITLE_SCALE` → 画面の `set_title_scale`、既定 1.0。Editor・録画は変わらない）。Yu Gothic UI の Bold は太字に見えないと言われたので FPS は Meiryo UI にした（判断）。
- 試験: `test_player_fps`（比の3件: CPU / GL widget / native、13px の QSS の親の中で）、`test_player_value_readout`（枠ごとの光り・4枠・倍率）。実設定（settings.json）は変更なし。
- 画像（1920×1009・CPU 描画）: scratchpad の `fpsval/` に fv_detail_readout / fv_detail_flash（と _crop）/ fv_detail_small_window（1100×640 の窓）。最新は `_v3`（fv_detail_readout_v3 / fv_detail_topright_crop_v3 / fv_detail_fps_crop_v3 / fv_detail_flash_v3）。

## 2026-11-19 Player 不具合まとめ対応 第4弾（Claude）

項目ごとにコミット・push 済み。版上げ・ビルド・リリースはしていない。

- 18 再生画面の背景・ジャケット: ゲーム画面に「下に敷くもの」`set_underlay`（既定 None・Editor と録画は使わない）を足し、`player/backdrop.ChartBackdrop` を通常プレイ・詳細モードで敷く。値は録画と同じ曲別の値（`player_background`: 開始位置・暗さ・レーンの濃さ・ジャケットの大きさ/場所/枠）。`player_show_bg` / `player_show_jacket` / `jacket_hidden` を守る。背景は上下を合わせて左右は黒、暗さは黒を重ねる、ジャケットは `record_bg_check._baked_art` を `recorder.art_xy` の場所へ（背景があれば画面の下、無ければ上 = 録画と同じ）。背景があるときは `set_background_passthrough(True, lane)`。詳細モードは画面が上半分なので同じ絵の上半分だけ（下背景が無いのでジャケットは出さない＝判断）。静止画は1回だけ縮めて覚える。
- 動画: QMediaPlayer の 1080p `toImage` が GUI で 1コマ約 7ms かかるため使わない。ffmpeg に縮めた大きさ（GPU 画面は 720 まで・CPU 画面は出す大きさ 1080 まで、30fps まで、`-hwaccel auto`、2スレッド、低い優先度、fps→scale の順）で生の画素を吐かせ、大きな入れ物のパイプ（3コマ）から**GUI のスレッドで待たずに**（PeekNamedPipe で1コマ揃ったときだけ）読む。時刻は「レーンの時刻 − 助走 + offset」（録画の -ss と同じ）。時計からコマを選ぶので速度の寄せは不要。戻る・3秒より先へ飛ぶ（詳細モードのシーク・やり直し）ときだけ ffmpeg をその位置から起こし直す（起こした直後は最初のコマまで5秒待つ）。止めている間は読まない（パイプが詰まって ffmpeg も止まる）。頭より前は黒、終わったら最後のコマ。離れる（`_end_play`）で止める。
- 20 YouTube の画質: `VIDEO_PLAYER_CLIENTS = [None, tv, web_safari, mweb, ios, android]`（音声は今までどおり）、`format_sort = res:1080, vcodec:h264, acodec:m4a`。落としたあと高さ（info の height、無ければ ffmpeg -i）が 720 未満なら次のクライアントで取り直し、一番良いものを返す。それでも低ければ録画の背景の窓のボタンの横に「360p」だけ出す。
- fps（1920×1009 の窓・通常プレイ・自動演奏中、paint_screen の回数。GTX970 / 4コア8スレッド。揺れが大きいので2回ずつ）: GL 背景なし 375〜405 / 画像 340〜397 / 360p30 の MV 360〜372（約 −6%）/ 1080p30（YouTube 相当 5Mbps）326〜334（約 −16%）/ 1080p60 の録画 254〜290（約 −25%）。CPU 描画 背景なし 54 / 画像 50 / 360p30 44〜48 / 1080p30 42 / 1080p60 40。重さの大半は ffmpeg が復号すること自体（別の ffmpeg を走らせるだけで GL が 347 → 240〜270 に落ちた）。
- 試験（新規）: `test_player_backdrop`（11件）、`test_ytdlp_video_quality`（14件）。関係する player 系・録画系の試験を流して確認。実設定（settings.json）は変更なし。
- 画像（1920×1009・CPU 描画）: scratchpad の `batch4/` に batch4_play_image_jacket / batch4_play_video / batch4_detail_image / batch4_detail_video。
- 残り: 20 は実際の URL で確かめてほしい（ネットに出ていない）。GPU の native 窓で背景動画の見え方・音とのずれを実機で見てほしい。歌詞ごとのジャケット切り替え・録画のフェード／棒は Player の画面には出していない。

## 2026-11-18 Player 不具合まとめ対応 第3弾（Claude）

項目ごとにコミット・push 済み。版上げ・ビルド・リリースはしていない。

- 3 再生画面へのフェード: ゲーム画面に黒の濃さ `_fade_dim`（既定 0・`set_fade_dim`）を持たせ、`paint_screen` の最後で塗る（GL の窓・QOpenGLWidget・CPU どれでも同じ）。`FadeStack` は `supports_fade_dim()` を持つ画面（`PlayPage`）なら、入るときは真っ黒(1)で出してから 0 へ、出るとき（結果・選曲へ）は画面自身を 0→1 にしてから切り替える。曲は**明け切ってから**流す（利用者の指定）。録画は別に作る CPU 版の画面なので 0 のまま。
- 25 左右の黒い帯: 通常の窓（最大化・全画面でない）で通常プレイの再生画面のあいだだけ、窓の縁を引きずっても 16:9 を保ち（`MainWindow.nativeEvent` の WM_SIZING → `player/window_aspect.sizing_rect`）、再生画面へ入るときに1回 16:9 に合わせる（`snap_client_size`、画面に入らなければ幅を縮める）。詳細モードは下にペインがあるので保たない（判断）。アプリ全体の native フィルタには WM_SIZING が来ないため窓の nativeEvent を使う。Player を出している間だけ見る。
- 5 選曲の行のジャケット: 行の左に 48px・角丸の絵。無い曲・表示しない曲は空の枠（題名の位置をそろえる）。ワーカーで QImage まで縮め、QPixmap は GUI で作る。(道, 更新日時) を鍵に 300 枚まで（`player/thumbs.py`）。背景・ジャケットを指定したらその曲の行だけ出し直す。
- 7 左の列のジャンル: 登録フォルダの直下の box.def のジャンルを、カテゴリの下に直接並べる（名前は題、左に色の丸）。直下にほかの曲・中身のあるフォルダもあれば最後に「その他」（登録フォルダそのもの。その一段ではジャンルの行を出さない）。ジャンルの無いフォルダは今までどおり1つ。右クリックの外す・カテゴリを変更は登録フォルダへ（`folder_action`）。並びは覚えておき、足す・外すで読み直す（`refresh_sidebar` もある）。パンくずはジャンルから。
- 13 詳細モードの数字: 右上（作譜モードの命令パネルの場所）に BPM・HS・見た目BPM（`neotja/value_readout.py`、`PreviewDock.set_value_readout`、既定 OFF）。値は命令パネルと同じカーソル(=再生位置)の所の値を 50ms ごとに見て、変わったら縁を `#7aa2f7` で 0.7 秒光らせて戻す。下のペインは変えていない。
- 21 ジャケットを表示しない: Player から開く「背景・ジャケットを指定」のジャケットの行に「表示しない」。曲別の `player_chart_art` に `jacket_hidden`（道は残す）。`chart_art.existing_art` がジャケットを "" で返すので、難易度選択・曲名表示・結果・選曲の行は No Image / 空の枠。**録画にも入れない**（`RecordDialog._bg_used`。Editor から同じ曲を録画しても同じ）。録画の窓の欄には道がそのまま出る。
- 24 TJAを開いたあと: ファイルを選ぶ・落とすと、その曲の画面（`player/opened.py`）へクロスフェード。ジャケット・曲名・副題・コースの札、「保存済みに追加」（押すと「✓ 保存済み」で押せない・この画面にとどまる）と「すぐに再生」（難易度選択へ・試聴つき）、左上の戻る・Esc で選曲へ。ボタンは横一列（`actions`）なので「プレイリストに追加」を足せる。選曲の行を押したとき・`library.choose` は今までどおり直接難易度選択。演奏中などに落とされたら鳴っているものを止める。
- 試験（新規）: `test_player_fade_dim`、`test_player_window_aspect`（WM_SIZING を実際に送る1件を含む）、`test_player_jacket_hidden`、`test_player_library_thumbs`、`test_player_sidebar_genres`、`test_player_value_readout`、`test_player_opened`。仕様変更に合わせて `test_player_host`（ジャンルの行の試験は登録フォルダの一段を直接開く）と `test_player_recent`（落とすと開いた曲の画面）を直した。関係する player 系の試験を流して確認。実設定（settings.json）は変更なし。
- 画像（1920×1009・CPU 描画）: scratchpad の `batch3/` に batch3_5_library_thumbs / 7_sidebar_genres / 7_sidebar_other / 13_detail_readout / 13_detail_flash / 21_art_dialog / 24_opened_before / 24_opened_after / 3_play_midfade。3 の途中の絵は撮るためにフェードを3秒に延ばして撮った（実際は 350ms）。
- 残り: 25 は実機の窓で縁を引きずって確かめてほしい（試験は WM_SIZING を直接送ったもの）。GPU の native 窓での 3 の明け方も実機で見てほしい。`test_player_host` はまれに code 127 で落ちる（以前からと同じ。再実行で通る）。

## 2026-11-18 Player 不具合まとめ対応 第2弾（Claude）

項目ごとにコミット・push 済み。版上げ・ビルド・リリースはしていない。

- 10 音量: 選曲の上の段の右にスピーカー（押すと小さな板）、難易度設定と途中メニュー（4項目の下）に「全体／曲／効果音」の3段（`player/volume.py`）。キーは Editor と同じ `master_volume` / `preview_volume` / `sfx_volume`。変えたら `PlayerHost.set_audio_level` が設定へ書き、Player と Editor の音（`editor_audio_cb` → `MainWindow._apply_volumes_from_config`。Editor の欄も動く）へすぐ当て、保存は400ms後にまとめて1回。Player を出るときに保存を済ませる。`PlayerCore.apply_audio_settings` が音量・打音WAV・ワイヤレス調整・出力デバイスを当て直す（試聴開始・本編の前・音量変更時）。デバイスの開き直しは rescan しない（同じプロセスの Editor の出力まで無効になるため）。途中メニューで動かしても止めたまま。
- 16 背景動画のずれ: 実測で、1080p・キーフレーム間隔250の H264 は試聴の音より常に約+45ms 先（0.25秒のシーク閾値では直らない）。ずれを「届いたフレームの時刻 − 音源の時計（出力遅延とワイヤレス調整を引いた位置）」で測り、20ms 以上は再生速度を ±3% まで変えて寄せ、8ms 未満で1倍へ。0.4秒超え・折り返し・始めだけシーク。対策後は平均+10ms 前後（1フレーム未満）。
- 8 試聴の出だし: DEMOSTART から10秒だけ先に読んで（ffmpeg -ss/-t）、曲の長さぶんの無音の入れ物に置いて鳴らし、全体が読めたら位置を保ったまま差し替える（ミキサーの `song_swap`、`PreviewDock.start_audition_segment`）。読み終わった曲は最大3曲・300MB まで持つ（`audio_engine.DecodeCache`。Player の PreviewDock だけ）。試聴・曲名表示の先読み・本編で読み直さない。本編は持っている PCM に助走・余韻を足して渡す。Player を出たら手放す。実測（3.0MB・3分50秒の ogg）: 初めての曲 1282ms → 約170ms、戻った曲 1270ms → 約14ms。
- 6 選曲で試聴: 曲の行を1回押すと選ぶだけ（水色の縁）で、150ms 後にその曲の DEMOSTART から試聴（続けて押したら最後の曲だけ）。選んでいる曲をもう一度押す（Enter も同じ）と難易度選択へ、試聴は続けたまま（`restart=False`）。選んだ直後150ms の2回目は無視。難易度選択から戻っても試聴は止めない（背景動画は止める）。Player を出たら止める。`PlayerCore` は選曲が出たときに裏で作る。TJAを開く・落としたときは今までどおり直接難易度選択へ。
- 試験（新規）: `test_player_volume`、`test_player_fast_preview`、`test_player_library_preview`、`test_player_demo_video` に追従の2件。「戻る」で試聴を止めない仕様に合わせて `test_player_preview` / `test_player_audition` / `test_player_difficulty` を直した。実設定（settings.json）は変更なし。
- 残り: 背景動画は画面の更新ぶん、実際はさらに少し遅れて見える。耳と目での確認をしてほしい。試験の後始末で、読み込み中に PreviewDock を消すと波形の「C++ object already deleted」が出ることがある（試験だけ。アプリでは PreviewDock を途中で消さない）。

## 2026-11-18 Player 不具合まとめ対応 第1弾（Claude）

項目ごとにコミット・push 済み。版上げ・ビルド・リリースはしていない。

- 1 試聴に風船の破裂音が混ざる: 試聴中（`set_audition`）は `_on_position_changed` がレーンを走らせない（`playing=False`）。ゲーム画面の波形と作譜ペインも動かさない。
- 2 開き直すと試聴が途中から: 新しいセッションでは再現できなかった。見つけた原因は、`load_wave_only` が道筋だけ見て読み直しを省いていたこと。本編を読んだあとの助走つき PCM や、別の曲の本編を、試聴にそのまま使っていた。ミキサーが持つ中身（`_mixer_holds`）で判断するよう直した。あわせて、読み込み失敗や先読みの取り消しで `_decoding_path` が残る問題、追い越された読み込みの結果を受け取る問題も直した。`start_preview(path, restart=True)` は必ず DEMOSTART から始める。`restart=False` と `PlayerCore.continue_demo` は試聴を続ける口で、次の弾の選曲で試聴する件に使う。
- 4 結果画面が一瞬崩れる: `FadeStack` が切り替えや絵を撮る前に、大きさと配置を確定させる（`presize`）。試験では崩れを再現できなかった（GL 環境だけで起きる可能性あり）。
- 9 最近開いた楽曲: 選んだだけでは足さない。Play と、録画を実際に始めたとき（`RecordDialog.recording_started`。Editor では使わない）に足す。選曲へ戻ったら一覧を作り直す。
- 11 最近開いた楽曲の道具の段に「履歴を消す」を追加。確認の窓は `ConfirmDialog`（`CategoryDialog` と同じ見た目）で、`open()` を使うので閉じるのを待たない。
- 12 TJA を引きずっているあいだ（選曲画面のときだけ）は「TJAを開く」を出し、板の縁を光らせる。離れたら元の画面へ戻す。落としたときの動きは今までどおり。
- 14 FPS 表示: 詳細モードでは常に出す。通常プレイでは難易度設定の「FPS表示」（`player_show_fps`、既定 OFF）で切り替える。左上に緑の太字で出す（`PreviewDock.set_view_fps` / `GamePreviewWindow.set_view_extra`。Editor は使わない）。
- 15 難易度設定に「連打秒速」を追加（1〜100 打/s の増減ボタン）。Editor と同じ `roll_speed` を使い、次に譜面を読むときから効く。Player を出ると Editor の欄にも同じ値が入る。`PLAYER_KEYS` に `roll_speed` と `player_show_fps` を追加。
- 17 曲名表示: 難易度選択の背景動画を止めずに引き継ぐ（`DemoVideo.set_targets`）。5 秒目に試聴が止まったら、動画は無音のまま自分の時計でループする（`free_run`）。本編へ入るときに止める。
- 19 結果画面: 背景が動画なら、無音・ループで流す（`start(..., free=True)`）。「もう一度」では曲名表示へ引き継ぎ、選曲へ戻るときに止めて手放す。
- 22 Player から開く「背景・ジャケットを指定」では、フェードと「この画面をPNGで保存」を隠す（`RecordBackgroundDialog(context="player")`。既定は `"record"`）。隠した値はそのまま返す。開始位置・暗さ・レーンの濃さ・ジャケットの配置は、今の Player の画面には効かない。ただし同じ曲を Editor で録画するときに使う曲別の値で、窓の見本にも出るので残した。
- 試験（新規）: `test_player_audition`、`test_player_transitions`、`test_player_recent`、`test_player_fps`、`test_player_roll_speed`。既存の player 系の試験も対象のものを確認した。実設定（settings.json）は変更なし。
- 残り: 2 の元の症状は、実機で同じ手順を試して確かめてほしい。`test_player_host` が終了時などにまれに code 127 で落ちる（再実行すると通る。以前からある shiboken の落ち方と思われる）。

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
