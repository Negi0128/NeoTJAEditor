"""譜面解析を GUI スレッドの外で回すためのワーカー。

数万行の譜面では、構文ハイライトの計算・コース集計・カーソル索引作り・
プレビュー用タイムライン組み立てで合計 600ms 以上かかる。これを GUI
スレッドで直接やっていたあいだは、打鍵を止めるたびにウィンドウが完全に
無反応になっていた(描画も入力も止まる)。

ここでやるのは「計算だけ」で、ウィジェットには一切触らない。結果は
finished シグナルで GUI スレッドへ渡し、画面への反映は向こうで行う。
Qt のウィジェットは GUI スレッド以外から触ると即クラッシュするため、
この境界は厳守すること。

なお Python の GIL があるので、これは「解析が別 CPU で並列に走る」わけでは
ない。得られるのは *固まりが途切れる* ことで、インタプリタが 5ms ごとに
スレッドを切り替えるため、解析中でも GUI スレッドが定期的に動けるように
なる(入力もアニメーションも止まらない)。合計時間はむしろ僅かに増える。

呼び出し側との約束:
  - submit() は GUI スレッドから呼ぶ。保留中の依頼は常に「最新の1件」だけで、
    古いものは黙って捨てられる(打鍵のたびに依頼が積み上がるのを防ぐ)。
  - process() は必ずキュー接続でワーカースレッド上から呼ぶこと。
  - finished(job_id, result) の job_id が自分の投げた最新の番号でなければ、
    受け取り側は結果を捨てること(途中で内容が変わっている)。
"""

from PySide6.QtCore import QMutex, QMutexLocker, QObject, Signal, Slot

from neotja.highlighter import compute_highlight_data


class AnalysisWorker(QObject):
    """解析ジョブを1件ずつ処理する。ジョブは dict で、"kind" が

      "full"    … コース解析・構文ハイライト・カーソル索引・プレビュー一式
      "preview" … プレビュー/メトロノームだけ(内容は変わっておらず、
                  カーソルの居るコースや分岐だけが変わったとき)

    結果も dict で、計算できたものだけを入れて返す。例外は握り潰さず
    "error" に入れて返し、呼び出し側がステータスバーに出す。
    """

    finished = Signal(int, object)

    def __init__(self, analyzer, parent=None):
        super().__init__(parent)
        self._analyzer = analyzer
        self._mutex = QMutex()
        self._pending = None

    # -- GUI スレッドから ------------------------------------------------
    def submit(self, job_id, job):
        """次に処理するジョブを差し替える。まだ処理を始めていない古いジョブは
        捨てる。打鍵中は 600ms デバウンスでも依頼が連続するため、溜め込むと
        「もう誰も見ていない内容」の解析に時間を使ってしまう。"""
        with QMutexLocker(self._mutex):
            self._pending = (job_id, job)

    def drop_pending(self):
        """まだ始まっていないジョブを取り消す(_force_update が同期で全部
        やり直すときなど、走らせても無駄になる場合に使う)。"""
        with QMutexLocker(self._mutex):
            self._pending = None

    # -- ワーカースレッド上 ----------------------------------------------
    @Slot()
    def process(self):
        with QMutexLocker(self._mutex):
            item = self._pending
            self._pending = None
        if item is None:
            # submit() より process() の呼び出しが多いのは正常(1回の submit に
            # 対して複数回キューへ積まれうる)。何もせず戻る。
            return
        job_id, job = item
        try:
            result = self._compute(job)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            result = {"kind": job.get("kind"), "error": f"{type(e).__name__}: {e}"}
        self.finished.emit(job_id, result)

    def _compute(self, job):
        content = job["content"]
        res = {"kind": job["kind"], "cursor_line": job["cursor_line"]}

        if job["kind"] == "full":
            courses = self._analyzer.parse_courses(content)
            res["courses_info"] = courses
            res["highlight"] = compute_highlight_data(content, courses)
            res["cursor_index"] = self._analyzer.build_cursor_index(content)

        res["clicks"] = self._analyzer.build_metronome_clicks(
            content, job["cursor_line"], job["duration"],
        )
        res["preview"] = self._analyzer.build_preview_timeline(
            content, job["cursor_line"], job["course_override"],
            branch_level=job["branch_level"],
        )
        return res
