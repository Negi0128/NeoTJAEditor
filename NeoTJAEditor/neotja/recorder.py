"""NeoTJAPlayer(ゲームプレビュー)を動画ファイルとして書き出す。

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
import threading
import time

import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

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


def chart_end_seconds(preview_data, offset, fallback=0.0):
    """譜面の終わり(=最終小節が終わる時刻)を音源時刻で返す。

    bar_times は小節の**開始**時刻しか持っていないので、最後の小節線の BPM と
    その時点の #MEASURE から 1 小節の長さ(240/BPM × 拍子)を足して求める。

    これを録画の終わりに使う。音源は譜面が終わったあとも鳴っていることが多く
    (フェードアウトや後奏)、そこまで録ると何も起きない映像が延々と続く。
    求められないときは fallback(ふつうは曲の長さ)を返す。"""
    bars = (preview_data or {}).get("bar_times") or []
    if not bars:
        return fallback
    try:
        last_t = float(bars[-1][0])
        bpm = float(bars[-1][1]) if len(bars[-1]) > 1 and bars[-1][1] else 0.0
    except (TypeError, ValueError, IndexError):
        return fallback
    if bpm <= 0:
        return fallback
    num, den = 4.0, 4.0
    for entry in ((preview_data or {}).get("measure_changes") or []):
        try:
            t, n, d = float(entry[0]), float(entry[1]), float(entry[2])
        except (TypeError, ValueError, IndexError):
            continue
        if t <= last_t + 1e-6 and d:
            num, den = n, d
    bar_len = (240.0 / bpm) * (num / den) if den else (240.0 / bpm)
    return max(0.0, last_t + bar_len - float(offset))


def make_offline_widget(preview_data, offset, se_text_enabled=True):
    """録画専用の画面を1つ、画面外に組み立てる。

    返すのは本家レイアウトの GameScreenWidget(1280x720)。中にレーンが
    入っていて、スコア・コンボ・太鼓・魂ゲージ・判定文字もそのまま乗る。
    時刻の出し入れ(begin_offline_render / set_render_time /
    end_offline_render)は画面側がレーンへ流してくれるので、録画側の手順は
    レーン1枚を録っていたときと変わらない。

    利用者が今見ているウィジェットを使い回さないのは、録画中に表示が
    書き換わってしまうため(録画は再生位置を勝手に動かす)。"""
    from neotja.chart_preview_widget import ChartPreviewWidget
    from neotja.game_screen import GameScreenWidget

    data = preview_data or {}
    cp = ChartPreviewWidget()
    cp.set_se_text_enabled(bool(se_text_enabled))
    cp.set_preview_data(data)
    cp.set_offset(offset)
    gs = GameScreenWidget(cp, compact=False)
    gs.set_chart(data, data.get("course_key"))
    return gs


class _WaveRecordScreen(QWidget):
    """音声波形モードの見た目そのままで録画するための画面。

    上半分(1280x360)は compact のゲーム画面、下半分は音声波形モードの下画面
    (波形・譜面・命令の3段)。画面で見ているものと同じ構成にしてある。

    **画面のウィジェットを使い回さない**のは通常の録画と同じ理由で、書き出しは
    再生位置を勝手に動かすため(make_offline_widget の説明を参照)。

    高さを 720 にしているのは、実際の窓(692)と数十 px しか違わないうえ、
    720p の入れ物にぴったり収まって余白の付け足しが要らないため。差の 28px は
    波形の下の何も描かない場所に足されるだけなので見た目は変わらない。

    「合成」「OFFSET調整」ボタンは出さない。あれは触るためのものであって
    録りたい中身(波形・譜面・命令)ではない。
    """

    #: 上のゲーム画面。1280x360(compact)。
    GAME_H = 360
    #: 下画面での波形の位置と大きさ。画面(preview_dock._build_wave_page)の
    #: 余白 10/8 をそのまま写したもの。
    WAVE_RECT = (10, GAME_H + 8, 1260, 170)
    #: 背景。下画面の何も描かない場所を塗る色で、ffmpeg 側の余白と同じ。
    BG = "#0d1117"

    def __init__(self, game_screen, waveform, parent=None):
        super().__init__(parent)
        self.setFixedSize(1280, 720)
        self.setStyleSheet("background-color: %s;" % self.BG)
        self.game = game_screen
        self.game.setParent(self)
        self.game.setGeometry(0, 0, 1280, self.GAME_H)
        self.wave = waveform
        self.wave.setParent(self)
        self.wave.setGeometry(*self.WAVE_RECT)

    # ---- オフライン描画。GameScreenWidget と同じ約束 ----
    def begin_offline_render(self):
        self.game.begin_offline_render()

    def set_render_time(self, seconds):
        # 上のレーンと下の波形へ**同じ時刻**を配る。画面では
        # preview_dock._on_preview_frame が同じことをしている。
        self.game.set_render_time(seconds)
        # set_position ではなく smooth のほう。あちらは 30fps に間引く仕掛けが
        # 入っていて、1コマずつ時刻を与える書き出しとは相性が悪い。画面の
        # 音声波形モードも smooth 側で駆動している(preview_dock._on_preview_frame)。
        self.wave.set_position_smooth(seconds)

    def end_offline_render(self):
        self.game.end_offline_render()


def make_offline_wave_widget(preview_data, offset, mips=None,
                             se_text_enabled=True):
    """音声波形モードの見た目で録画するための画面を、画面外に1つ組み立てる。

    返すのは 1280x720 の _WaveRecordScreen。上が compact のゲーム画面、下が
    波形・譜面・命令の3段で、画面で見ているものと同じ配線・同じデータを流す。

    mips(WaveformMips)は曲の波形。無ければ波形の線だけが出ず、譜面と命令の帯は
    そのまま出る(音源が読めなかったときでも録画は成立させる)。
    """
    from neotja.chart_preview_widget import ChartPreviewWidget
    from neotja.game_screen import GameScreenWidget
    from neotja.waveform_data import bar_grid_clicks
    from neotja.waveform_widget import WaveformWidget

    data = preview_data or {}
    cp = ChartPreviewWidget()
    cp.set_se_text_enabled(bool(se_text_enabled))
    cp.set_preview_data(data)
    cp.set_offset(offset)
    # 画面の音声波形モードと同じ「軽量の描き方」にそろえる。あちらは
    # set_lite(True)+set_compact(True) で、どんちゃんも下の背景も出ない。
    gs = GameScreenWidget(cp, compact=True)
    gs.set_chart(data, data.get("course_key"))
    gs.set_lite(True)

    wf = WaveformWidget(force_dark=True)
    # 触るためのボタンは録画には要らない(録りたいのは波形・譜面・命令)。
    wf.btn_stereo.hide()
    wf.btn_offset.hide()
    if mips is not None:
        wf.set_mips(mips)
    wf.set_stereo_view(False)          # 画面の既定と同じ「合成」
    wf.set_follow_window(6.0)          # 画面の既定と同じ表示幅(秒)
    bars = data.get("bar_times") or []
    bpm = float(bars[0][1]) if bars and bars[0][1] else 120.0
    wf.set_beat_grid(bpm, offset, bar_grid_clicks(bars))
    wf.set_notes(list(data.get("notes", [])))
    wf.set_spans(list(data.get("rolls", [])),
                 list(data.get("balloons", [])),
                 list(data.get("kusudamas", [])))
    wf.set_commands(list(data.get("bpm_changes", [])),
                    list(data.get("scroll_changes", [])),
                    list(data.get("measure_changes", [])),
                    list(data.get("gogo_regions", [])))
    return _WaveRecordScreen(gs, wf)


class RecordingCancelled(Exception):
    """利用者が途中で中止した。書きかけの出力は呼び出し側が消す。"""


class RecordingError(Exception):
    """ffmpeg が失敗した / 曲が読めない など。"""


class CancelToken:
    """下ごしらえ(prepare_recording)を外から止めるための札。

    曲のデコードは ffmpeg を待つだけの時間で、3分の曲でも1〜2秒、長い曲や
    遅いディスクだともっとかかる。ここを **止められない** と、書き出しを
    始めた直後にウィンドウを閉じた人はデコードが終わるまで待たされる
    (GUI スレッドがワーカーの終わりを待つため画面ごと固まる)。

    そこで走っている ffmpeg のプロセスをこの札に預けてもらい、cancel() で
    まとめて kill する。押さえておくのは2点:

      - 別スレッドから呼ばれる(cancel() は GUI スレッド、attach/detach は
        ワーカースレッド)ので、中身はロックで守る。
      - cancel() が attach() より先に来ることがある。そのときは attach() が
        False を返し、呼び出し側がその場で殺す。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._proc = None

    @property
    def cancelled(self):
        with self._lock:
            return self._cancelled

    def cancel(self):
        """中止を申し込む。走っている子プロセスがあれば殺す。"""
        with self._lock:
            self._cancelled = True
            proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    def attach(self, proc):
        """子プロセスを預ける。既に中止されていれば False(呼び出し側が殺す)。"""
        with self._lock:
            if self._cancelled:
                return False
            self._proc = proc
            return True

    def detach(self):
        """預けた子プロセスを外す(終わったので殺す相手が居なくなった)。"""
        with self._lock:
            self._proc = None

    def raise_if_cancelled(self):
        if self.cancelled:
            raise RecordingCancelled()


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _no_window():
    """Windows で ffmpeg のコンソールが一瞬光るのを防ぐ。"""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def decode_song_pcm(path: str, sample_rate: int = SAMPLE_RATE,
                    cancel=None) -> np.ndarray:
    """曲を (N, 2) float32 へデコードする。読めなければ RecordingError。

    アプリ本体は QAudioDecoder で読んでいるが、こちらは ffmpeg を直接叩く。
    イベントループが要らず、同期で確実に読め、対応形式も広いため。

    cancel に CancelToken を渡すと、ここで走らせる ffmpeg をその札へ預ける。
    途中で cancel() されたら ffmpeg ごと殺され、RecordingCancelled を投げて
    すぐ戻る(subprocess.run で待ち込むと止める手立てが無くなるため、
    Popen + communicate で書いてある。読む中身は run と同じ)。"""
    if not path or not os.path.exists(path):
        return np.zeros((0, 2), dtype=np.float32)
    cmd = [
        _ffmpeg_exe(), "-v", "error", "-nostdin", "-i", path,
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ac", "2", "-ar", str(sample_rate), "-",
    ]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, **_no_window())
    except OSError as e:
        raise RecordingError(f"ffmpeg を起動できませんでした: {e}") from e
    if cancel is not None and not cancel.attach(p):
        # 預ける前に中止されていた。誰も殺してくれないので自分で。
        try:
            p.kill()
        except OSError:
            pass
    try:
        out, err = p.communicate()
    finally:
        if cancel is not None:
            cancel.detach()
    if cancel is not None:
        # 殺されたぶんの returncode を「読めなかった」と誤解しないよう、
        # 中止の判定を先に済ませる。
        cancel.raise_if_cancelled()
    if p.returncode != 0:
        msg = (err or b"").decode("utf-8", "replace").strip().splitlines()
        raise RecordingError("音源を読み込めませんでした: " + (msg[-1] if msg else "原因不明"))
    pcm = np.frombuffer(out, dtype=np.float32)
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
    # 風船は割れる時刻まで(表示と同じ切り詰め)。
    from neotja.tja_analyzer import balloon_pop_spans
    spd = data.get("roll_hit_speed", 45)
    notes += _roll_tick_notes(balloon_pop_spans(data.get("balloons", []), spd), bpm_index=2)
    notes += _roll_tick_notes(balloon_pop_spans(data.get("kusudamas", []), spd), bpm_index=2)

    pairs = sorted((t - offset, "don" if c in "13" else "ka") for t, c, _bpm in notes)
    return [p[0] for p in pairs], [p[1] for p in pairs]


def render_audio(song_pcm, hit_times, hit_kinds, *, start_sec, end_sec,
                 don_path="", ka_path="", song_volume=0.8, sfx_volume=0.9,
                 hit_sounds=True, sample_rate=SAMPLE_RATE,
                 cancel=None) -> np.ndarray:
    """[start_sec, end_sec) の音声を (N, 2) float32 で作る。

    実機の再生とまったく同じ MixerCore を、デバイスに繋がずに回すだけ。
    そのため打音の鳴る位置は実際に聞いているものと1サンプルも違わない。

    cancel(CancelToken)を渡すと、ブロックを1つ作るごとに中止を見る。
    長い曲だとここも数秒かかるので、デコードと同じく途中で降りられる。"""
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
        if cancel is not None:
            cancel.raise_if_cancelled()
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
    if (w, h) == (src_w, src_h):
        # 描画解像度と出力解像度が同じならスケール不要。フィルタグラフごと
        # 省くと lanczos のコストが完全に消える。
        return None
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={bg}")


def frame_pixel_size(widget, supersample=1.0):
    """録画で実際に描く画素数。VideoRecording と1pxも違わない丸め方をする。

    見積もり(FrameCostProbe)は「本番とまったく同じ大きさ・同じ
    devicePixelRatio」で測らないと意味がないので、丸めをここに集約して
    両方から呼ぶ。"""
    ss = max(1.0, float(supersample))
    pw = int(round(widget.width() * ss))
    ph = int(round(widget.height() * ss))
    return pw + (pw & 1), ph + (ph & 1), ss


def _new_frame_image(pw, ph, ss):
    """1コマぶんの描画先を作る。

    Format_RGB32 なのは速さのため。Qt のラスタエンジンが素で扱えるのは
    ARGB32_Premultiplied 系(= RGB32 と同じ B,G,R,X 並び)で、以前使っていた
    RGBA8888 は毎回 非プリマルチプライ経路へ落ちるぶん 2〜3割 遅かった
    (1920x1080 で 17.0ms/コマ → 13.4ms/コマ の実測)。しかも RGB32 は画面の
    バックストア(ARGB32_Premultiplied)と1bitも違わない絵になるので、
    エディタ上で見えているものと動画が完全に一致する。
    ffmpeg 側へは "bgra" として渡す(このメモリ配置そのまま)。

    描画側(GameScreenWidget / ChartPreviewWidget)は毎フレーム全面を塗る
    (WA_OpaquePaintEvent + paintEvent 冒頭の fillRect)ので、毎コマの
    fill() は不要。ただし ss が小数のとき端に 1px 塗り残る可能性があるので、
    最初の1回だけ塗って未初期化メモリを避ける。"""
    img = QImage(pw, ph, QImage.Format_RGB32)
    # devicePixelRatio を上げると、Qt は同じ論理座標のまま ss 倍の細かさで
    # 描いてくれる(円も文字も本当に高精細になる。単なる拡大ではない)。
    img.setDevicePixelRatio(ss)
    img.fill(0)
    return img


# 描画の前後にかかる、コマ数によらない分。曲のデコードと音声合成(曲の長さに
# ほぼ比例する)と、最後に ffmpeg がファイルを閉じきるまでの待ち。描画に比べれば
# 小さいが、短い曲だと無視できない。
SETUP_PER_SONG_SEC = 0.030
SETUP_FIXED_SEC = 2.5


def estimate_seconds(ms_per_frame, song_seconds, fps):
    """1コマの実測(ms)から、書き出し全体にかかる秒数を見積もる。

    ffmpeg のエンコードはこちらが描いているあいだ別プロセスで並走していて、
    veryfast なら常に描画のほうが遅い。だからエンコード時間そのものは足さない
    (足すと二重に数えることになる)。並走ぶん描画が重くなる分は、
    FrameCostProbe が実際に x264 を回しながら測っているので ms_per_frame に
    もう入っている。"""
    frames = max(0.0, float(song_seconds)) * float(fps)
    draw = frames * (max(0.0, float(ms_per_frame)) / 1000.0)
    setup = SETUP_FIXED_SEC + SETUP_PER_SONG_SEC * max(0.0, float(song_seconds))
    return draw + setup


class FrameCostProbe:
    """「この PC・この譜面・この画質で1コマ何ms か」をその場で測る。

    固定の係数表(以前の time_factor)を捨てたのは、1コマの重さが PC の速さだけ
    でなく譜面の密度・スキンの有無・supersample・解像度で何倍も動くため。
    書いた本人の PC で測った数字は他所ではまるで当たらない。

    **本番と同じ形で測る**のが肝。ここでは本番とまったく同じ大きさの QImage へ
    描き、しかも同じ設定の x264 を裏で回してそこへ流し込む(出力は -f null で
    捨てる)。これをしないと数字が半分以下に出てしまう: ふだんの描画は
    メモリ帯域で頭打ちになっていて、x264 が同じ帯域を食い始めた途端に 2〜3倍
    重くなる。しかもその倍率は解像度で変わる(1080p で約2.6倍、720p で約1.9倍)
    ので、係数で後から補正するのは無理だった。裏で本物を回してしまえば、
    パイプへの書き込み待ちも含めて丸ごと実測になる。

    UI を固めないよう step() で少しずつ進める。最初の数コマ(warmup)は必ず
    捨てる: フォントやスキンの初回読み込みで 100ms〜1秒かかるうえ、x264 も
    走り出すまで数コマかかる。
    """

    def __init__(self, widget, *, supersample=1.0, song_seconds=0.0,
                 warmup=8, samples=24, crf=18, preset="veryfast"):
        self.widget = widget
        self.warmup = max(1, int(warmup))
        self.samples = max(1, int(samples))
        pw, ph, ss = frame_pixel_size(widget, supersample)
        self._img = _new_frame_image(pw, ph, ss)
        # 測る時刻は曲の中ほどへ散らす。頭は音符が無くて軽く、そこだけ測ると
        # 見積もりが短く出てしまう。
        span = max(1.0, float(song_seconds))
        total = self.warmup + self.samples
        self._times = [span * (0.2 + 0.6 * i / max(1, total - 1))
                       for i in range(total)]
        self._i = 0
        self._elapsed = 0.0
        self._started = False
        self.done = False
        # 出力は -f null で捨てる。音声も多重化もしないが、x264 のエンコードは
        # 本番どおり行われるので CPU とメモリ帯域の食われ方は同じになる。
        cmd = [
            _ffmpeg_exe(), "-v", "error", "-y", "-nostdin",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{pw}x{ph}",
            "-thread_queue_size", "64", "-r", "60", "-i", "-",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-f", "null", "-",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **_no_window())
        except OSError:
            # ffmpeg が起動できなくても見積もりは出したい。描画だけの数字に
            # なるぶん短めに出るが、無いよりはるかにまし。
            self._proc = None

    def step(self, count=3) -> bool:
        """最大 count コマ測る。まだ続きがあれば True。"""
        if self.done:
            return False
        if not self._started:
            self.widget.begin_offline_render()
            self._started = True
        total = self.warmup + self.samples
        end = min(self._i + max(1, int(count)), total)
        while self._i < end:
            t0 = time.perf_counter()
            self.widget.set_render_time(self._times[self._i])
            self.widget.render(self._img)
            if self._proc is not None:
                try:
                    self._proc.stdin.write(self._img.constBits())
                except (BrokenPipeError, OSError):
                    self._proc = None
            if self._i >= self.warmup:
                self._elapsed += time.perf_counter() - t0
            self._i += 1
        if self._i >= total:
            self._teardown()
            return False
        return True

    def _teardown(self):
        self.done = True
        try:
            self.widget.end_offline_render()
        except RuntimeError:
            pass                      # ウィジェットが先に消えていた
        self._img = None
        if self._proc is not None:
            # 溜まっているぶんを最後まで encode させる必要はない。閉じてから
            # 少しだけ待ち、居座るようなら殺す(見積もりで待たされたら本末転倒)。
            try:
                self._proc.stdin.close()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None

    def cancel(self):
        if not self.done:
            self._teardown()

    @property
    def ms_per_frame(self) -> float:
        n = max(0, self._i - self.warmup)
        return (self._elapsed / n * 1000.0) if n else 0.0


class RecordingPlan:
    """録画の「下ごしらえ」だけを持つ入れ物。曲のデコードと音声の合成の結果。

    VideoRecording から切り出してあるのは、**この部分だけが Qt に一切
    触らない**ため。絵を描くのは GUI スレッドからしかできない(QWidget.render)
    が、曲のデコード(ffmpeg)と音声のオフライン合成(MixerCore)は純粋な
    計算なので、ワーカースレッドで先に済ませておける。3分の曲で 2秒ほど
    かかるところなので、ここを別スレッドへ追い出せると「書き出すボタンを
    押した瞬間にアプリが固まる」のが無くなる。

    出来上がりの音声は先に丸ごと作ってしまう。映像フレーム i と音声サンプルは
    どちらも同じ仮想時計から出るので、途中で何が起きても音ズレは発生しない。
    """

    def __init__(self, tmp_dir, audio_path, start_sec, end_sec, fps, total_frames,
                 mips=None):
        self.tmp_dir = tmp_dir
        self.audio_path = audio_path
        self.start_sec = start_sec
        self.end_sec = end_sec
        self.fps = fps
        self.total_frames = total_frames
        # 音声波形モードの録画で下画面に描く波形(WaveformMips)。要らない
        # ときは None。ここで作るのは、曲の PCM がこの時点にしか無いから
        # (下ごしらえが済むと捨ててしまう)。WaveformMips は Qt に触らない
        # ので、ワーカースレッドで作れる。
        self.mips = mips

    def discard(self):
        """使わずに捨てる(用意し終わる前に中止された等)。一時音声を消す。"""
        if self.tmp_dir:
            _cleanup(self.tmp_dir)
            self.tmp_dir = None


def prepare_recording(*, preview_data, offset, song_path, start_sec=0.0,
                      end_sec=None, fps=60, don_path="", ka_path="",
                      song_volume=0.8, sfx_volume=0.9, hit_sounds=True,
                      cancel=None, want_mips=False):
    """曲を読み、音声を作り、RecordingPlan を返す。**Qt には触らない**ので
    ワーカースレッドから呼んでよい(GUI スレッドから呼んでも同じ結果)。

    cancel(CancelToken)を渡すと途中で降りられる。降りるときは
    RecordingCancelled を投げ、**作りかけの一時ファイルは自分で片付ける**
    (中止されたということは、受け取って discard() してくれる相手がもう
    居ないため。数十MB の audio.f32 が %TEMP% に居座るのを防ぐ)。"""
    song = decode_song_pcm(song_path, cancel=cancel)
    song_sec = song.shape[0] / float(SAMPLE_RATE) if song.shape[0] else 0.0
    if end_sec is None:
        end_sec = song_sec
    start_sec = max(0.0, float(start_sec))
    end_sec = max(start_sec, float(end_sec))
    if end_sec - start_sec < 1.0 / fps:
        raise RecordingError("録画する範囲が短すぎます。")

    hit_times, hit_kinds = hit_schedule_from_preview(preview_data, offset)
    audio = render_audio(song, hit_times, hit_kinds, start_sec=start_sec,
                         end_sec=end_sec, don_path=don_path, ka_path=ka_path,
                         song_volume=song_volume, sfx_volume=sfx_volume,
                         hit_sounds=hit_sounds, cancel=cancel)
    tmp_dir = tempfile.mkdtemp(prefix="neotja_rec_")
    audio_path = os.path.join(tmp_dir, "audio.f32")
    with open(audio_path, "wb") as f:
        f.write(np.ascontiguousarray(audio, dtype=np.float32).tobytes())
    # 波形は曲の PCM からしか作れないので、捨てる前にここで作る。
    mips = None
    if want_mips:
        try:
            from neotja.waveform_data import WaveformMips
            mips = WaveformMips.build(song, SAMPLE_RATE)
        except Exception:  # noqa: BLE001
            # 波形が出ないだけで録画そのものは成立する。ここで諦めない。
            mips = None
    del audio, song
    plan = RecordingPlan(tmp_dir, audio_path, start_sec, end_sec, int(fps),
                         int(round((end_sec - start_sec) * fps)), mips=mips)
    if cancel is not None and cancel.cancelled:
        # 書き終えた直後に中止された。渡す先がもう居ないので自分で捨てる。
        plan.discard()
        raise RecordingCancelled()
    return plan


class VideoRecording:
    """少しずつ進められる録画セッション。

    **なぜ「少しずつ」なのか**: 絵を描くのは QWidget.render() で、これは Qt の
    決まりで GUI スレッドからしか呼べない(別スレッドから触ると壊れる)。かと
    いって全フレームを一息に描くと、その間ずっとアプリが無反応になる。そこで
    step() で数コマずつ描き、あいだにイベントループへ戻れるようにしてある。
    呼び出し側は QTimer で step() を回し、進捗表示と中止を面倒みる。

    音声は plan(RecordingPlan)として先に用意しておく。plan を渡さなければ
    ここで作る(GUI 無しの同期版・テスト用の従来どおりの呼び方)。
    """

    def __init__(self, widget, out_path, *, plan=None, preview_data=None,
                 offset=0.0, song_path="", start_sec=0.0, end_sec=None, fps=60,
                 canvas="720p", don_path="", ka_path="", song_volume=0.8,
                 sfx_volume=0.9, hit_sounds=True, crf=18, supersample=1,
                 preset="medium"):
        if plan is None:
            plan = prepare_recording(
                preview_data=preview_data, offset=offset, song_path=song_path,
                start_sec=start_sec, end_sec=end_sec, fps=fps,
                don_path=don_path, ka_path=ka_path, song_volume=song_volume,
                sfx_volume=sfx_volume, hit_sounds=hit_sounds)

        self.widget = widget
        self.out_path = out_path
        self.start_sec = plan.start_sec
        self.fps = plan.fps
        self.total_frames = plan.total_frames
        self.frame = 0
        self._done = False
        self._tmp_dir = plan.tmp_dir
        audio_path = plan.audio_path
        fps = plan.fps

        w, h = widget.width(), widget.height()
        if w <= 0 or h <= 0:
            _cleanup(self._tmp_dir)
            raise RecordingError("描画用ウィジェットの大きさが未設定です。")
        # レーンは 908px 幅しかないので、1080p などへ引き伸ばすとぼやける。
        # 描くときの解像度だけ ss 倍にして(=文字も円も細かく描き直される)、
        # ffmpeg 側で目的の大きさへ縮小する。レイアウトは論理座標のままなので
        # 見た目の配置は 1 倍のときと 1px も変わらない。
        # supersample は小数も許す。1080p を ss=1.5 (1280x720 -> 1920x1080) の
        # ように「出力と同じ画素数」で描ければ、ffmpeg 側の lanczos 縮小が丸ごと
        # 不要になり、パイプに流すデータ量も大きく減る(整数丸めだと 2 倍固定に
        # なってしまい、出力の4倍を描いて捨てていた)。
        pw, ph, ss = frame_pixel_size(widget, supersample)
        vf = _video_filter(CANVAS_PRESETS.get(canvas, CANVAS_PRESETS["720p"]), pw, ph)

        cmd = [
            _ffmpeg_exe(), "-v", "error", "-y", "-nostdin",
            # bgra = QImage.Format_RGB32 のメモリ配置そのまま(リトルエンディアンで
            # B,G,R,X の順)。下の QImage の説明を参照。
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{pw}x{ph}",
            # 入力キューを深くしておくと、こちらの書き込みとエンコードが
            # 重なりやすくなる(既定は浅くて待ちが生じる)。
            "-thread_queue_size", "64",
            "-r", str(fps), "-i", "-",
            "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "2", "-i", audio_path,
        ]
        if vf:
            cmd += ["-vf", vf]
        cmd += [
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
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

        self._img = _new_frame_image(pw, ph, ss)
        widget.begin_offline_render()

    def step(self, max_frames=8) -> bool:
        """最大 max_frames コマ進める。まだ続きがあれば True。

        ffmpeg 側が落ちたときはそこで打ち切り、finish() が理由を報告する。"""
        if self._done:
            return False
        end = min(self.frame + max(1, int(max_frames)), self.total_frames)
        while self.frame < end:
            self.widget.set_render_time(self.start_sec + self.frame / float(self.fps))
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

    def detach_widget(self):
        """描画用ウィジェットを offline モードから戻す。**GUI スレッド専用**。

        finish()/abort() から切り離してあるのは、そちらをワーカースレッドへ
        回せるようにするため。ffmpeg がファイルを閉じ切るのを待つ数百 ms は
        Qt に一切関係ないので裏でやれるが、ウィジェットに触るこの一行だけは
        GUI スレッドに残さなければならない。先にここを呼んでおけば、
        _teardown はスレッドの区別なく呼べる。"""
        if getattr(self, "_widget_detached", False):
            return
        self._widget_detached = True
        try:
            self.widget.end_offline_render()
        except RuntimeError:
            pass                      # ウィジェットが先に消えていた

    def _teardown(self):
        if getattr(self, "_torn", False):
            return b"", self._proc.returncode
        self._torn = True
        self._done = True
        self.detach_widget()
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
