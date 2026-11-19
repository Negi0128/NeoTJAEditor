"""Player の再生画面の背景・ジャケット(録画と同じ見え方)。

ゲーム画面(game_screen)の「下に敷くもの」(set_underlay)として働く。
ゲーム画面は背景を描かず(set_background_passthrough)、ここが先に
背景を敷き、その上に画面が載る。決まりは録画(recorder / record_bg_check)
と同じ:

- 背景は**上下を合わせて、余る左右は黒**。暗さ(dim)は黒を重ねる
  (掛け算と同じ: c * (1 - dim))。レーンの地の濃さは bg.lane。
- ジャケットは枠つきで焼いたもの(record_bg_check._baked_art)を、
  recorder.art_xy の場所(下背景の真ん中が 0,0)へ。背景があれば背景の上・
  画面の下、背景が無ければ画面の上(録画の背景なしの経路と同じ)。
- 動画は「曲の秒 + offset」のコマ(録画の -ss と同じ)。負なら黒。
  終わったら最後のコマで止める。

大きさの基準は通常プレイの 1280x720(論理)。詳細モードは画面が上半分
(compact)なので、同じ絵の上半分だけが見える(下背景が無いので
ジャケットは出ない)。

動画の読み方: QMediaPlayer + QVideoSink は 1080p の toImage が GUI の
スレッドで 1コマ約 7ms(実測)かかるので使わない。ffmpeg に**出す大きさへ
縮めて**吐かせ、パイプに1コマ揃ったときだけ(待たずに)読んで、再生画面の
時計(レーンの時刻)が来たコマを貼る。時計からコマを選ぶので、速度の寄せは要らない。
時計が大きく飛んだ(詳細モードのシーク・やり直し)ときだけ ffmpeg を
その位置から起こし直す。止めている間は読み手が詰まって ffmpeg も待つ。
"""
import os
import re
import subprocess
import threading
import time

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage

#: 基準の画面(論理)。ジャケットの置き場所・背景の高さはこれで決める。
BASE_W, BASE_H = 1280, 720
#: 動画を読む高さの上限(画素)。CPU の画面は貼るたびに縮めると重いので、
#: 出す大きさ(1080 まで)で読んで等倍で貼る。
MAX_DECODE_H = 1080
#: GPU の画面は貼るときに GPU が伸ばすので、720 で読む(1コマを GL へ
#: 上げる量とパイプを流れる量が半分以下になる。背景は暗くして敷くので
#: 見た目の差は小さい)。
MAX_DECODE_H_GPU = 720
#: 動画のコマ数の上限。60fps の素材も 30 にする(背景を毎秒 60 枚読むと、
#: 1080p60 の録画で再生画面の fps が 2割ほど余計に落ちた)。
MAX_FPS = 30.0
#: ffmpeg に使わせるスレッドの数。多いと GUI のスレッドと CPU を取り合って
#: 再生画面の fps が落ちる(4コア8スレッドで、既定の数では 1080p60 の
#: 素材で 350 → 250fps)。
DECODE_THREADS = 2
#: ffmpeg の -hwaccel。auto は使える GPU の復号(dxva2 / d3d11va など)を
#: 選び、使えなければ CPU で復号する。1080p60 の録画で ffmpeg の CPU が
#: 1コアの 63% → 35%(実測)。空なら使わない。
HWACCEL = "auto"
#: 時計がこれより先へ飛んだら、ffmpeg をその位置から起こし直す(秒)。
#: これより小さい遅れは、読み進めて(間のコマを捨てて)追いつく — ffmpeg は
#: 1080p でも実時間の 2倍以上で読めるので、起こし直すより早い。
SEEK_AHEAD_SEC = 3.0
#: 時計がこれより戻ったら起こし直す(秒)。
SEEK_BACK_SEC = 0.15
#: 起こし直してから最初のコマが届くまでは、先への飛びで起こし直さない
#: 時間(秒)。キーフレームの間隔が長い動画はシークに時間がかかるので、
#: 待たずに起こし直すと、いつまでも1コマも届かない(実測: 8秒間隔の
#: 1080p30 で 6回起こし直した)。
STARTUP_GRACE_SEC = 5.0
#: 大きさが変わってから読み直すまで待つ時間(秒)。窓を引きずる最中に
#: 何度も起こさないため。
RESIZE_SETTLE_SEC = 0.3

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*?: Video: (.*)")
_SIZE_RE = re.compile(r"(?<![\dx])(\d{2,5})x(\d{2,5})(?![\d])")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:fps|tbr)")


def video_time(song_sec, offset):
    """曲(音源)の秒 → 背景の動画の秒。録画の -ss(offset + 曲の秒)と同じ。"""
    return float(song_sec) + float(offset or 0.0)


def bg_draw_rect(src_w, src_h, base_w=BASE_W, base_h=BASE_H):
    """背景(src_w x src_h)を基準の画面へ置く矩形(論理)。

    録画と同じ「上下を合わせて、余る左右は黒」— 高さを base_h に合わせ、
    横は真ん中。横長すぎる素材ははみ出す(描くときに画面で切る)。"""
    if src_w <= 0 or src_h <= 0:
        return QRectF(0, 0, base_w, base_h)
    w = src_w * float(base_h) / float(src_h)
    return QRectF((base_w - w) / 2.0, 0.0, w, float(base_h))


def decode_size(src_w, src_h, want_h, cap=None):
    """読む大きさ(偶数)。出す高さ・元の高さ・上限の小さい方。"""
    h = int(max(16, min(int(want_h), int(src_h) if src_h else int(want_h),
                        MAX_DECODE_H if cap is None else int(cap))))
    h -= h & 1
    w = int(round(src_w * h / float(src_h))) if src_h else h * 16 // 9
    w += w & 1
    return max(2, w), max(2, h)


def probe_video(path, ffmpeg=None):
    """動画の (幅, 高さ, fps, 長さ秒)。読めなければ None。"""
    if ffmpeg is None:
        from neotja import recorder
        ffmpeg = recorder._ffmpeg_exe()
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", path],
                             capture_output=True, timeout=20,
                             creationflags=flags)
        text = (out.stderr or b"").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    return parse_probe(text)


def parse_probe(text):
    m = _VIDEO_RE.search(text or "")
    if not m:
        return None
    line = m.group(1)
    sz = _SIZE_RE.search(line)
    if not sz:
        return None
    w, h = int(sz.group(1)), int(sz.group(2))
    fps = 30.0
    f = _FPS_RE.search(line)
    if f:
        try:
            fps = float(f.group(1))
        except ValueError:
            pass
    if not (1.0 <= fps <= 240.0):
        fps = 30.0
    dur = None
    d = _DUR_RE.search(text)
    if d:
        dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    return w, h, fps, dur


class FrameReader:
    """ffmpeg を1つ起こして、start 秒から縮めたコマを順に読む係。

    コマ i の時刻は start + i / fps(fps フィルタで刻みを揃える)。

    **読むのは GUI のスレッドで、待たない。** パイプに1コマぶん溜まって
    いるときだけ読む(PeekNamedPipe で見てから)。別のスレッドで待ち受けて
    読むと、コマが届くたびに GIL を取り合い、再生画面の描画が 3 割ほど
    遅くなった(実測: GL で 387 → 262fps)。パイプの入れ物はコマ数枚ぶん
    なので、読まないあいだ(止めている間)は ffmpeg が書けずに待つ。"""

    #: パイプに溜めておけるコマの数。
    PIPE_FRAMES = 3

    def __init__(self, path, start, width, height, fps, ffmpeg=None, gpu=False):
        self.path = path
        #: GPU の画面へは RGBX(GL へそのまま上げられる並び)、CPU の画面へは
        #: RGB32(QPainter がそのまま写せる並び)で吐かせる。
        self.gpu = bool(gpu)
        self.start = max(0.0, float(start))
        self.width, self.height = int(width), int(height)
        self.fps = max(1.0, min(MAX_FPS, float(fps)))
        self.eof = False
        #: 読んだコマの数(次に読むコマの番号)。
        self.frames = 0
        #: 読んだ回数(計測用)。
        self.reads = 0
        self.started_at = time.monotonic()
        self._ffmpeg = ffmpeg
        self._size = self.width * self.height * 4
        # 入れ物は使い回す(毎コマ 数MB を確保して 0 で埋めない)。貼っている
        # 1枚・読みかけの1枚・この回に読み終えた1枚の3つで足りる。
        self._free = [bytearray(self._size) for _ in range(3)]
        self._shown = None            # 貼っているコマの入れ物
        self._fill_buf = None         # 読みかけの入れ物
        self._fill = 0
        self._proc = self._out = None
        self._peek = None
        try:
            self._proc, self._out = _spawn(self.command(), self._size * self.PIPE_FRAMES)
            self._peek = _peeker(self._out)
        except Exception:  # noqa: BLE001
            self.eof = True

    def command(self):
        ff = self._ffmpeg
        if ff is None:
            from neotja import recorder
            ff = recorder._ffmpeg_exe()
        cmd = [ff, "-v", "error", "-nostdin"]
        if self.start > 0.0:
            cmd += ["-ss", "%.3f" % self.start]
        if HWACCEL:
            cmd += ["-hwaccel", HWACCEL]
        cmd += ["-threads", str(DECODE_THREADS), "-i", self.path,
                "-an", "-sn", "-dn",
                # 先に間引いてから縮める(縮めるコマを減らす)。
                "-vf", "fps=%s,scale=%d:%d:flags=bilinear"
                % (("%.6f" % self.fps).rstrip("0").rstrip("."), self.width, self.height),
                "-pix_fmt", "rgb0" if self.gpu else "bgra",
                # 1コマを1回で書かせる(既定は 32KB ずつ)。
                "-avioflags", "direct",
                "-f", "rawvideo", "-"]
        return cmd

    def next_time(self):
        """次に読むコマの時刻。"""
        return self.start + self.frames / self.fps

    def _available(self):
        """いま待たずに読める量。読み終わり(ffmpeg が閉じた)なら -1。"""
        if self._out is None:
            return -1
        try:
            return self._peek()
        except (BrokenPipeError, OSError, ValueError):
            return -1

    def _read_some(self):
        """読みかけのコマへ、待たずに読めるぶんを足す。1コマ揃えば True。"""
        if self.eof:
            return False
        if self._fill_buf is None:
            self._fill_buf = self._free.pop() if self._free else bytearray(self._size)
            self._fill = 0
        need = self._size - self._fill
        avail = self._available()
        if avail < 0:
            self._finish()
            return False
        if avail <= 0:
            return False
        view = memoryview(self._fill_buf)[self._fill:self._fill + min(need, avail)]
        try:
            n = self._out.readinto(view)
        except (BlockingIOError, InterruptedError):
            n = None
        except (OSError, ValueError):
            self._finish()
            return False
        self.reads += 1
        if n is None:
            return False
        if n == 0:
            self._finish()
            return False
        self._fill += n
        return self._fill >= self._size

    def ready(self, t):
        """時刻 t までに出すコマが1枚まるごと届いているか(読まずに見る)。"""
        if self.eof or self.next_time() > t + 0.5 / self.fps:
            return False
        avail = self._available()
        return avail >= self._size - (self._fill if self._fill_buf is not None else 0)

    def take(self, t):
        """時刻 t までに出すべきコマのうち一番新しいもの (時刻, 絵, 入れ物)。
        届いていなければ None(待たない)。間のコマは読んで捨てる。"""
        half = 0.5 / self.fps
        got = None
        while not self.eof and self.next_time() <= t + half:
            if not self._read_some():
                break
            ft = self.next_time()
            buf, self._fill_buf, self._fill = self._fill_buf, None, 0
            self.frames += 1
            if got is not None:
                self._free.append(got[2])      # 飛ばしたコマの入れ物を戻す
            img = QImage(buf, self.width, self.height, self.width * 4,
                         QImage.Format_RGBX8888 if self.gpu
                         else QImage.Format_RGB32)
            got = (ft, img, buf)
        if got is not None:
            if self._shown is not None:
                self._free.append(self._shown)
            self._shown = got[2]
        return got

    def _finish(self):
        self.eof = True
        out, self._out = self._out, None
        if self._proc is not None:
            _kill(self._proc, out)

    def close(self):
        self._finish()


def _peeker(out):
    """パイプに溜まっている量を返す呼び出し(待たない)。"""
    if os.name == "nt":
        import _winapi
        import msvcrt
        handle = msvcrt.get_osfhandle(out.fileno())

        def peek():
            return int(_winapi.PeekNamedPipe(handle, 0)[0])
        return peek
    os.set_blocking(out.fileno(), False)
    return lambda: 1 << 30          # 読めるだけ読む(無ければ None が返る)


def _spawn(cmd, pipe_bytes):
    """ffmpeg を起こし、(プロセス, 読み口) を返す。

    Windows では**大きな入れ物のパイプ**を自分で作って渡す。既定のパイプ
    (4KB)だと1コマを何百回にも分けて読むことになり、そのたびに GIL を
    取り合って、GUI が忙しいと読みがほとんど進まない(実測: 1778x1000 で
    毎秒 0.3 コマ)。数コマぶんの入れ物なら1コマを1回で読める(毎秒 60 コマ)。"""
    # 画面の方を先に回してもらう(背景のコマが少し遅れても、取りに行く側が
    # 追いつく)。
    flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
             | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    if os.name == "nt":
        try:
            import _winapi
            import msvcrt
            cap = int(min(64 << 20, max(1 << 20, pipe_bytes)))
            r, w = _winapi.CreatePipe(None, cap)
            wfd = msvcrt.open_osfhandle(w, 0)
            try:
                proc = subprocess.Popen(cmd, stdout=wfd, stderr=subprocess.DEVNULL,
                                        stdin=subprocess.DEVNULL,
                                        creationflags=flags)
            except Exception:
                os.close(wfd)
                _winapi.CloseHandle(r)
                raise
            os.close(wfd)
            out = os.fdopen(msvcrt.open_osfhandle(r, os.O_RDONLY), "rb",
                            buffering=0)
            return proc, out
        except ImportError:
            pass
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, creationflags=flags,
                            bufsize=0)
    return proc, proc.stdout


def _kill(proc, out=None):
    try:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1.0)
    except Exception:  # noqa: BLE001
        pass
    for f in (out, getattr(proc, "stdout", None)):
        try:
            if f is not None:
                f.close()
        except Exception:  # noqa: BLE001
            pass


class ChartBackdrop(QObject):
    """再生画面の背景・ジャケット。ゲーム画面の set_underlay に渡す。

    clock() は (曲の秒, 流れているか) を返す呼び出し。曲の秒は音源の頭が
    0(助走は負)。"""

    #: 新しいコマが届いていないか見る間隔(止めている間の詳細モードの
    #: シークでも、届いたら描き直すため)。
    POLL_MS = 30

    def __init__(self, clock, parent=None):
        super().__init__(parent)
        self._clock = clock
        self.bg = None
        self.bg_path = ""
        self.art_path = ""
        self.is_video = False
        self._screen = None
        self._image_cache = None          # (鍵, QImage)
        self._art_cache = None            # (鍵, 矩形, QImage)
        # 動画
        self._info = None                 # (w, h, fps, dur)
        self._probing = False
        self._reader = None
        self._frame = None                # (時刻, QImage, buf)
        self._want_h = None
        self._want_since = 0.0
        self._gen = 0
        self._gpu = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)
        #: 試験・計測用: 起こした ffmpeg の数と、その位置。
        self.restarts = []

    # ------------------------------------------------------------------
    def configure(self, bg, show_bg=True, show_jacket=True):
        """bg は recorder.RecordBackground。表示の設定に合わせて絞る。"""
        from neotja.player.demo_video import is_video_path
        self.stop_video()
        self.bg = bg
        path = getattr(bg, "path", "") if (bg is not None and show_bg) else ""
        self.bg_path = path if path and os.path.isfile(path) else ""
        art = getattr(bg, "art", "") if (bg is not None and show_jacket) else ""
        self.art_path = art if art and os.path.isfile(art) else ""
        self.is_video = bool(self.bg_path) and is_video_path(self.bg_path)
        self._image_cache = None
        self._art_cache = None
        if self.is_video:
            self._start_probe()
        self._apply_passthrough()
        self._update_screen()

    def active(self):
        return bool(self.bg_path or self.art_path)

    def has_background(self):
        return bool(self.bg_path)

    def attach(self, screen):
        """ゲーム画面へ敷く。何も出すものが無ければ敷かない。"""
        if screen is not self._screen:
            self.detach()
        self._screen = screen
        self._gpu = bool(getattr(screen, "gpu_screen", False))
        if screen is None:
            return
        self._apply_passthrough()
        if self.active():
            screen.set_underlay(self)
            self._timer.start()
        else:
            screen.set_underlay(None)

    def detach(self):
        scr, self._screen = self._screen, None
        self._timer.stop()
        if scr is None:
            return
        try:
            if scr.underlay() is self:
                scr.set_underlay(None)
            scr.set_background_passthrough(False)
        except (RuntimeError, AttributeError):
            pass

    def stop(self):
        """離れるとき。動画を止め、画面から外す。"""
        self.detach()
        self.stop_video()

    def stop_video(self):
        self._gen += 1
        r, self._reader = self._reader, None
        if r is not None:
            r.close()
        self._frame = None

    def _apply_passthrough(self):
        scr = self._screen
        if scr is None:
            return
        try:
            if self.bg_path:
                scr.set_background_passthrough(True, getattr(self.bg, "lane", 1.0))
            else:
                scr.set_background_passthrough(False)
        except (RuntimeError, AttributeError):
            pass

    def _update_screen(self):
        scr = self._screen
        if scr is not None:
            try:
                scr.update()
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    def song_time(self):
        try:
            got = self._clock()
        except Exception:  # noqa: BLE001
            return 0.0, False
        if isinstance(got, tuple):
            return float(got[0]), bool(got[1])
        return float(got), False

    def video_now(self):
        """いま出すべき動画の秒。"""
        return video_time(self.song_time()[0], getattr(self.bg, "offset", 0.0))

    # ------------------------------------------------------------------
    # 動画
    def _start_probe(self):
        self._info = None
        self._probing = True
        gen = self._gen
        path = self.bg_path

        def run():
            info = probe_video(path)
            if gen == self._gen and path == self.bg_path:
                self._info = info
            self._probing = False

        threading.Thread(target=run, name="neotja-bgprobe", daemon=True).start()

    def info(self):
        return self._info

    def _start_reader(self, at, height, gpu=None):
        info = self._info
        if info is None:
            return
        if gpu is None:
            gpu = self._gpu
        w, h = decode_size(info[0], info[1], height, self._cap(gpu))
        dur = info[3]
        if dur:
            at = min(at, max(0.0, dur - 1.0 / info[2]))
        at = max(0.0, at)
        old, self._reader = self._reader, None
        if old is not None:
            old.close()
        if self._frame is not None:
            # 新しいコマが届くまでは今の絵を出しておく(時刻は忘れる)。
            self._frame = (None,) + tuple(self._frame[1:])
        self.restarts.append(round(at, 3))
        self._reader = FrameReader(self.bg_path, at, w, h,
                                   min(info[2], MAX_FPS), gpu=gpu)

    @staticmethod
    def _cap(gpu):
        return MAX_DECODE_H_GPU if gpu else MAX_DECODE_H

    def _needs_restart(self, vt):
        r = self._reader
        if r is None:
            return True
        cur = self._frame[0] if self._frame is not None else None
        head = cur if cur is not None else r.start
        if vt < r.start - SEEK_BACK_SEC:
            return True
        if cur is not None and vt < cur - SEEK_BACK_SEC:
            return True
        if vt > head + SEEK_AHEAD_SEC:
            if cur is None and time.monotonic() - r.started_at < STARTUP_GRACE_SEC:
                return False            # まだシークの途中
            return True
        return False

    def _video_frame(self, want_h):
        """いま貼るコマ(QImage)。まだ無ければ None。"""
        info = self._info
        if info is None:
            return None
        vt = self.video_now()
        dur = info[3]
        if vt < 0.0:
            # 曲の頭より前(録画は黒を足す所)。頭のコマを用意しておく。
            if self._reader is None or self._reader.start > 0.0:
                self._frame = None
                self._start_reader(0.0, want_h)
            return None
        # 大きさが変わった: 少し落ち着いてから読み直す(それまでは今のを伸ばす)。
        r = self._reader
        dh = decode_size(info[0], info[1], want_h, self._cap(self._gpu))[1]
        if r is not None and r.height != dh:
            if self._want_h != dh:
                self._want_h, self._want_since = dh, time.monotonic()
            elif time.monotonic() - self._want_since >= RESIZE_SETTLE_SEC:
                self._want_h = None
                self._start_reader(vt, want_h)
        if dur:
            # 終わったら最後のコマで止める(録画の tpad=stop_mode=clone)。
            vt = min(vt, max(0.0, dur - 0.5 / info[2]))
        if self._needs_restart(vt):
            self._start_reader(vt, want_h)
        got = self._reader.take(vt) if self._reader is not None else None
        if got is not None:
            self._frame = got
        return self._frame[1] if self._frame is not None else None

    def _poll(self):
        """届いたコマを、止めている間でも描き直してもらう。"""
        scr = self._screen
        if scr is None:
            return
        if not self.is_video or self._info is None:
            return
        if self.song_time()[1]:
            return                          # 流れている間は画面が自分で描き直す
        r = self._reader
        if r is None or r.ready(self.video_now()):
            self._update_screen()           # 調べ終わった直後・シークのあと

    # ------------------------------------------------------------------
    # 絵
    def _static_image(self, want_h):
        h = max(16, int(want_h))
        key = (self.bg_path, h)
        got = self._image_cache
        if got is not None and got[0] == key:
            return got[1]       # 1回だけ縮めて覚えておく
        src = QImage(self.bg_path)
        if src.isNull():
            self._image_cache = (key, None)
            return None
        img = src.scaledToHeight(h, Qt.SmoothTransformation)
        if img.format() != QImage.Format_RGB32:
            img = img.convertToFormat(QImage.Format_RGB32)
        self._image_cache = (key, img)
        return img

    @staticmethod
    def _scale_of(screen, p):
        try:
            k = float(p.deviceTransform().m11())
        except Exception:  # noqa: BLE001
            k = 1.0
        return max(0.1, k)

    @staticmethod
    def _screen_h(screen):
        try:
            return int(screen._screen_height())
        except Exception:  # noqa: BLE001
            return BASE_H

    def paint_under(self, screen, p):
        if not self.bg_path:
            return
        sh = self._screen_h(screen)
        k = self._scale_of(screen, p)
        want_h = int(round(BASE_H * k))
        p.setClipRect(QRectF(0, 0, BASE_W, sh))
        p.fillRect(QRectF(0, 0, BASE_W, sh), QColor(0, 0, 0))
        img = (self._video_frame(want_h) if self.is_video
               else self._static_image(want_h))
        if img is not None and not img.isNull():
            p.drawImage(bg_draw_rect(img.width(), img.height()), img)
        dim = float(getattr(self.bg, "dim", 0.0) or 0.0)
        if dim > 0.0:
            p.fillRect(QRectF(0, 0, BASE_W, sh),
                       QColor(0, 0, 0, int(round(255 * min(1.0, dim)))))
        self._paint_art(screen, p, sh, k)

    def paint_over(self, screen, p):
        if self.bg_path:
            return                          # 背景があれば下に置いてある
        self._paint_art(screen, p, self._screen_h(screen),
                        self._scale_of(screen, p))

    def art_rect(self, k=1.0):
        """ジャケットを置く矩形(論理)と焼いた絵。出さないなら (None, None)。

        録画と同じ: 1280x720 を k 倍した画素で焼き、recorder.art_xy の場所へ。"""
        if not self.art_path or self.bg is None:
            return None, None
        ck = (self.art_path, round(float(k), 4))
        got = self._art_cache
        if got is not None and got[0] == ck:
            return got[1], got[2]
        from neotja import recorder
        from neotja.dialogs.record_bg_check import _baked_art
        W, H = int(round(BASE_W * k)), int(round(BASE_H * k))
        img = _baked_art(self.bg, H, self.art_path)
        if img is None:
            return None, None
        side = img.width()
        x, y = recorder.art_xy(self.bg, W, H, side)
        rect = QRectF(x / k, y / k, side / k, img.height() / k)
        # 毎コマ焼き場所を引かない(大きさが変わったときだけ出し直す)。
        self._art_cache = (ck, rect, img)
        return rect, img

    def _paint_art(self, screen, p, sh, k):
        if sh < BASE_H:
            return                          # 詳細モード(上半分)には下背景が無い
        rect, img = self.art_rect(k)
        if rect is None:
            return
        p.setClipRect(QRectF(0, 0, BASE_W, sh))
        p.drawImage(rect, img)


def player_background(cfg, path):
    """Player の再生画面に出す背景(recorder.RecordBackground)。

    録画と同じ曲別の値(開始位置・暗さ・レーンの濃さ・ジャケットの大きさと
    場所)を使う。ジャケットは「表示しない」を守る。"""
    from neotja import recorder
    from neotja.chart_art import existing_art, record_background
    bg = record_background(cfg, path, recorder.bg_with_defaults(
        recorder.RecordBackground(), cfg.get("rec_bg_default")))
    bg.path = existing_art(cfg, path, "background")
    bg.art = existing_art(cfg, path, "jacket", respect_hidden=True)   # 再生画面だけ「表示しない」を守る
    bg.art_track = []
    return bg
