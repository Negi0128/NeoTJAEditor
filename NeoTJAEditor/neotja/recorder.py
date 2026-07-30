"""えぬいーさん次郎(ゲームプレビュー)を動画ファイルとして書き出す。

画面をリアルタイムに録画するのではなく、**1コマずつ時刻を与えて描き直す**。
音声も同じ時間軸でオフライン合成するので、

  - 描画が何ms かかろうと映像は必ず一定fpsで揃う(コマ落ちが起きない)
  - 映像フレーム i と音声サンプルが同じ仮想時計から出るので、原理的に
    音ズレ(ドリフト)が起きない
  - 実時間より速く書き出せる(実測: 3分の譜面で 30秒程度)

仕組み:
  1) 曲を ffmpeg でデコードして PCM に(imageio-ffmpeg 同梱の exe を使うので
     追加インストールは不要)
  2) MixerCore に曲+打音を渡し、デバイス無しで render() を回して音声を作る
     (MixerCore はもともと Qt・sounddevice に依存しない純粋ミキサー)
  3) ChartPreviewWidget を画面外に1つ用意し、begin_offline_render() で
     「外から与えた時刻で描く」モードにして、フレームごとに QImage へ描く
  4) 生フレームを ffmpeg の標準入力へ流し込み、音声と多重化して mp4 に

中間ファイル(PNG の山)は作らない。音声だけ一時ファイルを1つ使う。
"""

import os
import subprocess
import sys
import tempfile

import numpy as np
from PySide6.QtGui import QImage

from neotja.audio_engine import ensure_don_wav, ensure_ka_wav
from neotja.mixer_engine import MixerCore, _load_sfx, _load_sfx_or_none

SAMPLE_RATE = 48000
# ffmpeg へ渡すブロック長。音声合成の粒度で、大きすぎても速くならない。
_AUDIO_BLOCK = 4096

# 出力の入れ物。譜面レーンは 908x212 前後の横長なので、上下に余白を付けて
# 一般的な動画サイズに収める(はみ出さないよう縮小してから中央寄せ)。
CANVAS_PRESETS = {
    "native": None,          # レーンそのままの大きさ
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}


def probe_song_seconds(path: str) -> float:
    """曲の長さ(秒)を ffmpeg に聞く。分からなければ 0.0。

    プレビュー側の duration は音源の読み込みが終わるまで 0 のことがあり、
    それをそのまま範囲指定の上限にすると「何も選べない」状態になる。
    ここは全部デコードせずヘッダだけ見るので一瞬で返る。"""
    if not path or not os.path.exists(path):
        return 0.0
    try:
        p = subprocess.run([_ffmpeg_exe(), "-i", path], capture_output=True, **_no_window())
    except OSError:
        return 0.0
    for line in (p.stderr or b"").decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("Duration:"):
            continue
        stamp = line.split("Duration:", 1)[1].split(",")[0].strip()
        try:
            hh, mm, ss = stamp.split(":")
            return int(hh) * 3600 + int(mm) * 60 + float(ss)
        except ValueError:
            return 0.0
    return 0.0


def make_offline_widget(preview_data, offset, se_text_enabled=True):
    """録画専用の ChartPreviewWidget を画面外に1つ作る。

    実機のゲーム窓と同じ固定サイズ(LANE_WIDTH × widget_height)にする。
    レイアウトに載せない裸のウィジェットは幅が 0 のままなので、ここで明示的に
    大きさを与えないと真っ白な動画ができてしまう。

    利用者が今見ているウィジェットを使い回さないのは、録画中に表示が
    書き換わってしまうため(録画は再生位置を勝手に動かす)。"""
    from neotja.chart_preview_widget import ChartPreviewWidget

    cp = ChartPreviewWidget()
    cp.set_se_text_enabled(bool(se_text_enabled))
    cp.set_preview_data(preview_data or {})
    cp.set_offset(offset)
    cp.setFixedSize(int(ChartPreviewWidget.LANE_WIDTH), cp.widget_height())
    return cp


class RecordingCancelled(Exception):
    """利用者が途中で中止した。書きかけの出力は呼び出し側が消す。"""


class RecordingError(Exception):
    """ffmpeg が失敗した / 曲が読めない など。"""


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _no_window():
    """Windows で ffmpeg のコンソールが一瞬光るのを防ぐ。"""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def decode_song_pcm(path: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """曲を (N, 2) float32 へデコードする。読めなければ RecordingError。

    アプリ本体は QAudioDecoder で読んでいるが、こちらは ffmpeg を直接叩く。
    イベントループが要らず、同期で確実に読め、対応形式も広いため。"""
    if not path or not os.path.exists(path):
        return np.zeros((0, 2), dtype=np.float32)
    cmd = [
        _ffmpeg_exe(), "-v", "error", "-nostdin", "-i", path,
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ac", "2", "-ar", str(sample_rate), "-",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, **_no_window())
    except OSError as e:
        raise RecordingError(f"ffmpeg を起動できませんでした: {e}") from e
    if p.returncode != 0:
        msg = (p.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise RecordingError("音源を読み込めませんでした: " + (msg[-1] if msg else "原因不明"))
    pcm = np.frombuffer(p.stdout, dtype=np.float32)
    return pcm[: (pcm.size // 2) * 2].reshape(-1, 2).copy()


def hit_schedule_from_preview(preview_data: dict, offset: float):
    """プレビュー用タイムラインから打音のスケジュール(音声時間, 種別)を作る。

    アプリ本体(preview_dock.set_preview_data → hit_sounds.set_schedule)と
    まったく同じ組み立てをする。連打/風船/くす玉は等間隔の連打音へ展開し、
    面(1/3)はドン、それ以外はカッ。音声時間 = 譜面時間 - OFFSET。"""
    from neotja.preview_dock import _roll_tick_notes

    data = preview_data or {}
    notes = [(t, c, bpm) for t, c, bpm, _sc, _se in data.get("notes", [])]
    notes += _roll_tick_notes(data.get("rolls", []), bpm_index=3)
    notes += _roll_tick_notes(data.get("balloons", []), bpm_index=2)
    notes += _roll_tick_notes(data.get("kusudamas", []), bpm_index=2)

    pairs = sorted((t - offset, "don" if c in "13" else "ka") for t, c, _bpm in notes)
    return [p[0] for p in pairs], [p[1] for p in pairs]


def render_audio(song_pcm, hit_times, hit_kinds, *, start_sec, end_sec,
                 don_path="", ka_path="", song_volume=0.8, sfx_volume=0.9,
                 hit_sounds=True, sample_rate=SAMPLE_RATE) -> np.ndarray:
    """[start_sec, end_sec) の音声を (N, 2) float32 で作る。

    実機の再生とまったく同じ MixerCore を、デバイスに繋がずに回すだけ。
    そのため打音の鳴る位置は実際に聞いているものと1サンプルも違わない。"""
    core = MixerCore(sample_rate, max_block=_AUDIO_BLOCK)
    core.post(("song", np.ascontiguousarray(song_pcm, dtype=np.float32), sample_rate))
    core.post(("sfx", "don", _load_hit_pcm(don_path, ensure_don_wav, sample_rate)))
    core.post(("sfx", "ka", _load_hit_pcm(ka_path, ensure_ka_wav, sample_rate)))
    core.post(("hit_sched", list(hit_times), list(hit_kinds)))
    core.post(("hit_enabled", bool(hit_sounds)))
    core.post(("metro_enabled", False))
    core.post(("vol", "song", song_volume))
    core.post(("vol", "sfx", sfx_volume))
    # song コマンドが read_pos を 0 に戻すので、seek はそのあとで積む。
    core.post(("seek", int(round(start_sec * sample_rate))))
    core.post(("play",))

    total = max(0, int(round((end_sec - start_sec) * sample_rate)))
    out = np.zeros((total, 2), dtype=np.float32)
    done = 0
    while done < total:
        n = min(_AUDIO_BLOCK, total - done)
        out[done:done + n] = core.render(n)
        done += n
    return out


def _load_hit_pcm(path, synth_factory, sample_rate):
    """打音を読む。解決順は実機(_load_one_sfx)と同じ: 指定WAV → 合成音。"""
    if path and os.path.exists(path):
        pcm = _load_sfx_or_none(path, sample_rate)
        if pcm is not None:
            return pcm
    return _load_sfx(synth_factory(), sample_rate)


def _video_filter(canvas, src_w, src_h, bg="0x0d1117"):
    """出力サイズへ合わせるフィルタ。縦横比は保ったまま縮小し、余白は中央寄せ。
    yuv420p は幅・高さが偶数でないと使えないので、native でも偶数へ丸める。"""
    if canvas is None:
        w = src_w + (src_w & 1)
        h = src_h + (src_h & 1)
        if (w, h) == (src_w, src_h):
            return None
        return f"pad={w}:{h}:0:0:color={bg}"
    w, h = canvas
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={bg}")


class VideoRecording:
    """少しずつ進められる録画セッション。

    **なぜ「少しずつ」なのか**: 絵を描くのは QWidget.render() で、これは Qt の
    決まりで GUI スレッドからしか呼べない(別スレッドから触ると壊れる)。かと
    いって全フレームを一息に描くと、その間ずっとアプリが無反応になる。そこで
    step() で数コマずつ描き、あいだにイベントループへ戻れるようにしてある。
    呼び出し側は QTimer で step() を回し、進捗表示と中止を面倒みる。

    音声は先に丸ごと作ってしまう(オフライン合成は 3分でも 1秒程度)。
    映像フレーム i と音声サンプルはどちらも同じ仮想時計から出るので、
    途中で何が起きても音ズレは発生しない。
    """

    def __init__(self, widget, out_path, *, preview_data, offset, song_path,
                 start_sec=0.0, end_sec=None, fps=60, canvas="720p",
                 don_path="", ka_path="", song_volume=0.8, sfx_volume=0.9,
                 hit_sounds=True, crf=18):
        song = decode_song_pcm(song_path)
        song_sec = song.shape[0] / float(SAMPLE_RATE) if song.shape[0] else 0.0
        if end_sec is None:
            end_sec = song_sec
        start_sec = max(0.0, float(start_sec))
        end_sec = max(start_sec, float(end_sec))
        if end_sec - start_sec < 1.0 / fps:
            raise RecordingError("録画する範囲が短すぎます。")

        self.widget = widget
        self.out_path = out_path
        self.start_sec = start_sec
        self.fps = int(fps)
        self.total_frames = int(round((end_sec - start_sec) * fps))
        self.frame = 0
        self._done = False

        hit_times, hit_kinds = hit_schedule_from_preview(preview_data, offset)
        audio = render_audio(song, hit_times, hit_kinds, start_sec=start_sec,
                             end_sec=end_sec, don_path=don_path, ka_path=ka_path,
                             song_volume=song_volume, sfx_volume=sfx_volume,
                             hit_sounds=hit_sounds)
        self._tmp_dir = tempfile.mkdtemp(prefix="neotja_rec_")
        audio_path = os.path.join(self._tmp_dir, "audio.f32")
        with open(audio_path, "wb") as f:
            f.write(np.ascontiguousarray(audio, dtype=np.float32).tobytes())
        del audio, song

        w, h = widget.width(), widget.height()
        if w <= 0 or h <= 0:
            _cleanup(self._tmp_dir)
            raise RecordingError("描画用ウィジェットの大きさが未設定です。")
        vf = _video_filter(CANVAS_PRESETS.get(canvas, CANVAS_PRESETS["720p"]), w, h)

        cmd = [
            _ffmpeg_exe(), "-v", "error", "-y", "-nostdin",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
            "-r", str(fps), "-i", "-",
            "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "2", "-i", audio_path,
        ]
        if vf:
            cmd += ["-vf", vf]
        cmd += [
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            out_path,
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, **_no_window())
        except OSError as e:
            _cleanup(self._tmp_dir)
            raise RecordingError(f"ffmpeg を起動できませんでした: {e}") from e

        self._img = QImage(w, h, QImage.Format_RGBA8888)
        widget.begin_offline_render()

    def step(self, max_frames=8) -> bool:
        """最大 max_frames コマ進める。まだ続きがあれば True。

        ffmpeg 側が落ちたときはそこで打ち切り、finish() が理由を報告する。"""
        if self._done:
            return False
        end = min(self.frame + max(1, int(max_frames)), self.total_frames)
        while self.frame < end:
            self.widget.set_render_time(self.start_sec + self.frame / float(self.fps))
            self._img.fill(0)
            self.widget.render(self._img)
            try:
                self._proc.stdin.write(self._img.constBits())
            except (BrokenPipeError, OSError):
                self._done = True
                return False
            self.frame += 1
        if self.frame >= self.total_frames:
            self._done = True
            return False
        return True

    def finish(self) -> str:
        """書き出しを閉じる。失敗していれば RecordingError。"""
        err, code = self._teardown()
        if code != 0:
            _unlink(self.out_path)
            msg = (err or b"").decode("utf-8", "replace").strip().splitlines()
            raise RecordingError("動画の書き出しに失敗しました: " + (msg[-1] if msg else "原因不明"))
        return self.out_path

    def abort(self):
        """中止する。書きかけの出力は残さない。"""
        self._teardown()
        _unlink(self.out_path)

    def _teardown(self):
        if getattr(self, "_torn", False):
            return b"", self._proc.returncode
        self._torn = True
        self._done = True
        try:
            self.widget.end_offline_render()
        except RuntimeError:
            pass                      # ウィジェットが先に消えていた
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        err = self._proc.stderr.read()
        self._proc.wait()
        self._proc.stderr.close()
        _cleanup(self._tmp_dir)
        return err, self._proc.returncode


def record_chart_video(widget, out_path, progress_cb=None, cancel_cb=None, **kwargs):
    """VideoRecording を最後まで回すだけの同期版(テストや GUI 無しの用途)。

    GUI から使うときはこれではなく VideoRecording を QTimer で回すこと。
    ここは呼び出しから戻るまでイベントループが回らない。"""
    rec = VideoRecording(widget, out_path, **kwargs)
    report_every = max(1, rec.fps)
    try:
        while True:
            if cancel_cb is not None and cancel_cb():
                rec.abort()
                raise RecordingCancelled()
            before = rec.frame
            more = rec.step(report_every)
            if progress_cb is not None and rec.frame // report_every != before // report_every:
                progress_cb(rec.frame, rec.total_frames)
            if not more:
                break
    except RecordingCancelled:
        raise
    except BaseException:
        rec.abort()
        raise
    path = rec.finish()
    if progress_cb is not None:
        progress_cb(rec.total_frames, rec.total_frames)
    return path


def _cleanup(tmp_dir):
    try:
        for name in os.listdir(tmp_dir):
            _unlink(os.path.join(tmp_dir, name))
        os.rmdir(tmp_dir)
    except OSError:
        pass


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass
