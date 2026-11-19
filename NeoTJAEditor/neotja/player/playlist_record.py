"""プレイリストの録画(v15-design/PLAYLIST_SPEC.md「全部まとめた録画 ＋ 楽曲別個別」)。

選んだ範囲の曲を順に、今の録画と同じ道(画面外の画面を1コマずつ描いて ffmpeg へ
流す。recorder.prepare_recording / make_offline_widget / VideoRecording)で
書き出す。実時間の再生は要らない。

- 曲ごと: 1曲1本。背景・ジャケットは曲別の値(chart_art.record_background)、
  「ジャケットを表示しない」の曲には入れない、歌詞ごとのジャケットの切り替えも
  Player の「背景・ジャケット」と同じ。
- まとめて: 曲の前に曲名表示(普段の曲名表示の絵。ボタンや数えは出さない)を
  CARD_SEC 秒はさみ、全部を1本につなぐ。つなぐのは ffmpeg の concat
  (まず -c copy、だめなら作り直す)。
- 両方: 上の2つ。まとめてだけのときは、曲ごとの書き出しは作業用のフォルダに
  作ってつないだあとに消す。

見つからない曲・書き出せなかった曲は飛ばして(failed に入れて)続ける。
"""

import os
import shutil
import subprocess
import tempfile
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from neotja import recorder
from neotja import settings as settings_mod

#: まとめて書き出すときの曲名表示の長さ(秒)。
CARD_SEC = 3.0
#: 書き出し方。
MODES = ("combined", "each", "both")
MODE_LABELS = {"combined": "まとめて", "each": "曲ごと", "both": "両方"}


def playlist_out_dir(title):
    """プレイリストの録画の置き場所: ユーザーデータ/録画/<題>(使える名前にして)。"""
    from neotja import user_dirs
    return os.path.join(str(user_dirs.user_dir("recordings")),
                        recorder.safe_file_stem(title, "playlist"))


def unique_path(path):
    """同じ名前があれば _2, _3 … を付ける(上書きしない)。"""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists("%s_%d%s" % (stem, i, ext)):
        i += 1
    return "%s_%d%s" % (stem, i, ext)


def song_background(cfg, path, preview, offset):
    """曲別の背景・ジャケット(Player の「背景・ジャケット」と同じ決まり)。"""
    from neotja.chart_art import jacket_hidden, record_background
    from neotja.lyric_jacket import LyricJackets, jacket_segments
    bg = record_background(cfg, path, recorder.bg_with_defaults(
        recorder.RecordBackground(), cfg.get("rec_bg_default")))
    if jacket_hidden(cfg, path):
        bg.art = ""
        bg.art_track = []
        return bg
    try:
        jk = LyricJackets.load(path)
    except Exception:  # noqa: BLE001
        jk = None
    lyrics = preview.get("lyrics") or []
    if jk is not None and jk.lyrics and lyrics and bg.art:
        use = LyricJackets(bg.art, {k: v for k, v in jk.lyrics.items() if os.path.exists(v)})
        if use.lyrics:
            segs = jacket_segments([(float(t) - offset, txt) for t, txt in lyrics], use)
            bg.art_track = segs if len(segs) > 1 else []
    return bg


def render_card(cfg, path, course, level, size, out_png):
    """曲名表示(普段の見た目)を1枚の絵にする。GUI スレッドで。"""
    from PySide6.QtGui import QImage
    from neotja.player.intro import IntroPage
    page = IntroPage(cfg)
    try:
        page.resize(1280, 720)
        page.set_song(path, course, level)
        img = QImage(1280, 720, QImage.Format_RGB32)
        img.fill(0)
        page.render(img)
        if size and tuple(size) != (1280, 720):
            img = img.scaled(int(size[0]), int(size[1]), Qt.IgnoreAspectRatio,
                             Qt.SmoothTransformation)
        img.save(out_png)
    finally:
        page.deleteLater()
    return out_png


def encode_card(png, out_mp4, seconds, fps, preset="veryfast", crf=18):
    """止まった絵＋無音の短い動画(曲の書き出しと同じ形式: H.264 yuv420p・
    AAC 48kHz ステレオ・同じ fps)。"""
    cmd = [recorder._ffmpeg_exe(), "-v", "error", "-y", "-nostdin",
           "-loop", "1", "-framerate", str(int(fps)), "-t", "%.3f" % seconds, "-i", png,
           "-f", "lavfi", "-t", "%.3f" % seconds,
           "-i", "anullsrc=r=%d:cl=stereo" % recorder.SAMPLE_RATE,
           "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-r", str(int(fps)),
           "-c:a", "aac", "-b:a", "192k", "-shortest", out_mp4]
    r = subprocess.run(cmd, capture_output=True, **recorder._no_window())
    if r.returncode != 0 or not os.path.exists(out_mp4):
        raise recorder.RecordingError("曲名表示の動画を作れませんでした: "
                                      + r.stderr.decode("utf-8", "replace")[-300:])
    return out_mp4


def concat_videos(parts, out_mp4, work_dir, title="", preset="veryfast"):
    """parts を順に1本へ。まず作り直さずにつなぎ、だめなら作り直してつなぐ。"""
    lst = os.path.join(work_dir, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '%s'\n" % p.replace("\\", "/").replace("'", "'\\''"))
    meta = ["-metadata", "title=%s" % " ".join(str(title).split())] if title else []
    exe = recorder._ffmpeg_exe()
    cmd = [exe, "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", lst,
           "-c", "copy", "-movflags", "+faststart"] + meta + [out_mp4]
    r = subprocess.run(cmd, capture_output=True, **recorder._no_window())
    if r.returncode == 0 and os.path.exists(out_mp4):
        return out_mp4
    cmd = [exe, "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", lst,
           "-c:v", "libx264", "-preset", preset, "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"] + meta + [out_mp4]
    r = subprocess.run(cmd, capture_output=True, **recorder._no_window())
    if r.returncode != 0 or not os.path.exists(out_mp4):
        raise recorder.RecordingError("つなげませんでした: "
                                      + r.stderr.decode("utf-8", "replace")[-300:])
    return out_mp4


class PlaylistRecorder(QObject):
    """範囲の曲を順に書き出し、まとめて/曲ごと/両方を作る。

    items: [{"path", "course"}]。quality: record_dialog.QUALITY_PRESETS の値。
    進み具合は progress(何曲目 1〜, 全部, 全体の割合 0〜1)、終わりは
    finished({"combined": 道 or "", "songs": [道], "failed": [(道, 理由)],
    "cancelled": bool, "out_dir": 道})。"""

    progress = Signal(int, int, float)
    finished = Signal(object)

    def __init__(self, cfg, items, out_dir, *, mode="both", quality=None,
                 title="", card_sec=CARD_SEC, parent=None):
        super().__init__(parent)
        from neotja.dialogs.record_dialog import DEFAULT_QUALITY, QUALITY_PRESETS
        self.cfg = cfg
        self.items = [dict(i) for i in items]
        self.out_dir = out_dir
        self.mode = mode if mode in MODES else "both"
        self.quality = dict(quality or QUALITY_PRESETS[DEFAULT_QUALITY][1])
        self.title = str(title or "")
        self.card_sec = float(card_sec)
        self._index = -1
        self._rec = None
        self._widget = None
        self._task = None
        self._token = None
        self._cancel = False
        self._running = False
        self._batch = 1
        self._parts = []          # まとめて用: [曲名表示, 曲, 曲名表示, 曲 …]
        self.songs = []           # 書き出した曲の動画
        self.failed = []
        self.combined = ""
        self._work = None
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._tick)
        from neotja.tja_analyzer import TJACourseAnalyzer
        self.analyzer = TJACourseAnalyzer(cfg)

    # ------------------------------------------------------------------
    def is_running(self):
        return self._running

    def start(self):
        if self._running:
            return
        os.makedirs(self.out_dir, exist_ok=True)
        self._work = tempfile.mkdtemp(prefix="neotja-plrec-")
        self._running = True
        self._cancel = False
        self._index = -1
        self._next()

    def cancel(self):
        """中止。書きかけの1本は捨てる。まとめての1本は作らない。"""
        if not self._running:
            return
        self._cancel = True
        self._timer.stop()
        if self._token is not None:
            self._token.cancel()
        if self._rec is not None:
            try:
                self._rec.abort()
            except Exception:  # noqa: BLE001
                pass
            self._rec = None
        self._done()

    # ------------------------------------------------------------------
    def _emit_progress(self):
        n = len(self.items)
        if not n:
            return
        frac = 0.0
        if self._rec is not None and self._rec.total_frames:
            frac = self._rec.frame / float(self._rec.total_frames)
        i = max(0, self._index)
        self.progress.emit(min(n, i + 1), n, min(1.0, (i + frac) / n))

    def _song_dir(self):
        """曲ごとの動画を置く所。まとめてだけなら作業用のフォルダ。"""
        return self._work if self.mode == "combined" else self.out_dir

    def _next(self):
        self._drop_widget()
        self._index += 1
        if self._cancel:
            return
        if self._index >= len(self.items):
            self._finish_all()
            return
        self._emit_progress()
        item = self.items[self._index]
        try:
            prep = self._prepare(item)
        except Exception as exc:  # noqa: BLE001
            prep = None
            self.failed.append((item.get("path", ""), "%s: %s" % (type(exc).__name__, exc)))
        if prep is None:
            QTimer.singleShot(0, self._next)
            return
        preview, offset, wave, end_sec, out_path, bg, card = prep
        if card:
            self._parts.append(card)
        self._out_path = out_path
        self._bg = bg
        self._widget = recorder.make_offline_widget(
            preview, offset, bool(self.cfg.get("se_text_enabled", True)))
        don, ka = settings_mod.effective_hit_sound_paths(self.cfg)
        q = self.quality
        self._token = recorder.CancelToken()
        token = self._token
        from neotja.dialogs.record_dialog import _Task
        task = _Task(
            lambda: recorder.prepare_recording(
                preview_data=preview, offset=offset, song_path=wave,
                start_sec=0.0, end_sec=end_sec, fps=int(q["fps"]),
                don_path=don, ka_path=ka,
                song_volume=float(self.cfg.get("preview_volume", 0.8)),
                sfx_volume=float(self.cfg.get("sfx_volume", 0.9)),
                hit_sounds=True, cancel=token),
            self, cancel=token.cancel, discard=lambda plan: plan.discard())
        task.ok.connect(self._on_prepared)
        task.ng.connect(self._on_prep_failed)
        self._task = task
        task.start()

    def _prepare(self, item):
        """1曲ぶんの譜面・音源・長さ・出力先・背景(と、まとめて用の曲名表示)。"""
        from neotja.player import song_lists
        from neotja.player.batch import BatchPage
        from neotja.player.core import read_text
        from neotja.preview_dock import parse_preview_headers
        path = item.get("path") or ""
        if not os.path.isfile(path):
            self.failed.append((path, "見つかりません"))
            return None
        content = read_text(path)
        info = song_lists.song_info(path)
        levels = dict(info.get("courses") or [])
        course = item.get("course")
        if course not in levels:
            from neotja.player.playlists import default_course
            course = default_course(info.get("courses") or [])
        preview = self.analyzer.build_preview_timeline(content, None, course, branch_level="M")
        if not preview.get("notes") and not preview.get("rolls"):
            self.failed.append((path, "音符がありません"))
            return None
        offset = float(parse_preview_headers(content).get("offset", 0.0) or 0.0)
        wave = BatchPage._find_wave(path, content)
        if not wave:
            self.failed.append((path, "音源(WAVE:)が見つかりません"))
            return None
        end_sec = recorder.full_record_end(preview, offset, recorder.probe_song_seconds(wave))
        if end_sec - recorder.record_tail_seconds(preview) <= 0.1:
            self.failed.append((path, "書き出す長さがありません"))
            return None
        title = info.get("title") or os.path.splitext(os.path.basename(path))[0]
        label = song_lists.COURSE_LABELS.get(course, course or "")
        stem = recorder.safe_file_stem("%02d_%s_%s" % (self._index + 1, title, label))
        out_path = unique_path(os.path.join(self._song_dir(), stem + ".mp4"))
        bg = song_background(self.cfg, path, preview, offset)
        card = ""
        if self.mode in ("combined", "both") and self.card_sec > 0:
            q = self.quality
            size = recorder.CANVAS_PRESETS.get(q.get("canvas", "720p")) or (1280, 720)
            png = os.path.join(self._work, "card_%02d.png" % self._index)
            render_card(self.cfg, path, course, levels.get(course), size, png)
            card = encode_card(png, os.path.join(self._work, "card_%02d.mp4" % self._index),
                               self.card_sec, q["fps"], preset=q.get("preset", "veryfast"))
        self._song_title = title
        return preview, offset, wave, end_sec, out_path, bg, card

    def _on_prep_failed(self, msg):
        self._task = None
        if self._cancel:
            return
        self.failed.append((self.items[self._index].get("path", ""), str(msg)))
        self._drop_last_card()
        QTimer.singleShot(0, self._next)

    def _drop_last_card(self):
        if self._parts and os.path.basename(self._parts[-1]).startswith("card_"):
            self._parts.pop()

    def _on_prepared(self, plan):
        self._task = None
        if self._cancel or self._widget is None:
            plan.discard()
            return
        q = self.quality
        try:
            self._rec = recorder.VideoRecording(
                self._widget, self._out_path, plan=plan, canvas=q["canvas"],
                supersample=q["supersample"], preset=q["preset"],
                background=self._bg, title=self._song_title)
        except Exception as exc:  # noqa: BLE001
            plan.discard()
            self.failed.append((self.items[self._index].get("path", ""),
                                "%s: %s" % (type(exc).__name__, exc)))
            self._drop_last_card()
            QTimer.singleShot(0, self._next)
            return
        self._batch = 1
        self._timer.start()

    def _tick(self):
        if self._rec is None or self._cancel:
            self._timer.stop()
            return
        t0 = time.perf_counter()
        try:
            more = self._rec.step(self._batch)
        except Exception as exc:  # noqa: BLE001
            self._timer.stop()
            try:
                self._rec.abort()
            except Exception:  # noqa: BLE001
                pass
            self._rec = None
            self.failed.append((self.items[self._index].get("path", ""),
                                "%s: %s" % (type(exc).__name__, exc)))
            self._drop_last_card()
            QTimer.singleShot(0, self._next)
            return
        dt = time.perf_counter() - t0
        if dt < 0.02:
            self._batch = min(self._batch + 1, 16)
        elif dt > 0.05:
            self._batch = max(1, self._batch - 1)
        self._emit_progress()
        if more:
            return
        self._timer.stop()
        rec, self._rec = self._rec, None
        try:
            path = rec.finish()
            self._parts.append(path)
            if self.mode != "combined":
                self.songs.append(path)
        except Exception as exc:  # noqa: BLE001
            self.failed.append((self.items[self._index].get("path", ""), str(exc)))
            self._drop_last_card()
        QTimer.singleShot(0, self._next)

    def _finish_all(self):
        """全部書けた。まとめての1本を作る(裏で)。"""
        self._drop_widget()
        songs_done = [p for p in self._parts if not os.path.basename(p).startswith("card_")]
        if self.mode in ("combined", "both") and songs_done:
            out = unique_path(os.path.join(
                self.out_dir, recorder.safe_file_stem(self.title, "playlist") + ".mp4"))
            parts = list(self._parts)
            work = self._work
            from neotja.dialogs.record_dialog import _Task
            task = _Task(lambda: concat_videos(parts, out, work, title=self.title,
                                               preset=self.quality.get("preset", "veryfast")),
                         self)
            task.ok.connect(self._on_combined)
            task.ng.connect(self._on_combine_failed)
            self._task = task
            self.progress.emit(len(self.items), len(self.items), 1.0)
            task.start()
            return
        self._done()

    def _on_combined(self, path):
        self._task = None
        self.combined = path
        self._done()

    def _on_combine_failed(self, msg):
        self._task = None
        self.failed.append(("", "まとめて: %s" % msg))
        self._done()

    def _done(self):
        self._timer.stop()
        self._drop_widget()
        task, self._task = self._task, None
        if task is not None:
            from neotja.dialogs.record_dialog import _join_task
            task.cancel()
            _join_task(task)
        if self._work:
            shutil.rmtree(self._work, ignore_errors=True)
            self._work = None
        if not self._running:
            return
        self._running = False
        self.finished.emit({"combined": self.combined, "songs": list(self.songs),
                            "failed": list(self.failed), "cancelled": self._cancel,
                            "out_dir": self.out_dir})

    def _drop_widget(self):
        if self._widget is not None:
            try:
                chara = getattr(self._widget, "_chara", None)
                if chara is not None:
                    chara.sprites.cancel_preload()
            except Exception:  # noqa: BLE001
                pass
            self._widget.deleteLater()
            self._widget = None
