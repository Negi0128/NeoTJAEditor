"""Player の「録画」。難易度選択の「録画」から、Editor と同じ録画の窓
(neotja/dialogs/record_dialog.py の RecordDialog)を開く。

書き出しの準備・本番・中止・後始末は RecordDialog のものをそのまま使う。
ここで作るのは、RecordDialog が「メインウィンドウ」に求めるものを Player の
譜面から用意する小さな入れ物(RecordContext)だけ。

- 譜面とコース: PlayerCore が再生と同じ組み立て(build_preview)で作ったもの。
- 最初のジャケット: 曲ごとの Player のジャケット(player_chart_art)。
  lyric_jackets の base として渡す。**set_jacket_base は持たせない** —
  録画の窓でジャケットを変えても、Editor の譜面ごとの .jacket.json は書かない。
- 最初の背景: 曲ごとの Player の背景(RecordDialog の bg_path)。
- それ以外(画質・保存先・背景の見た目の既定・書き出したときに覚える
  rec_bg_* など)は RecordDialog のふだんの動きのまま。
- 曲名表示(5 秒)は録画に入れない(計画書: 今回は追加しない)。
"""

import os

from neotja.lyric_jacket import LyricJackets
from neotja.player import song_lists


class RecordContext:
    """RecordDialog に「メインウィンドウ」として渡すもの。"""

    def __init__(self, config_data, current_file, preview_dock, jacket=""):
        self.config_data = config_data
        self.current_file = current_file
        self.preview_dock = preview_dock
        #: 基本ジャケットだけ(歌詞ごとの絵は持たない)。無ければ None
        #: (RecordDialog は共通の rec_bg_art を出す)。
        self.lyric_jackets = LyricJackets(jacket, {}) if jacket else None


def chart_art(cfg, path):
    """(ジャケット, 背景) の道。無ければ ""。表示の ON/OFF は見ない
    (暫定方針: 表示設定はまず再生時の表示に適用する)。"""
    def get(kind):
        try:
            p = song_lists.existing_art(cfg, path, kind)
        except Exception:  # noqa: BLE001
            p = ""
        return p if p and os.path.exists(p) else ""
    return get("jacket"), get("background")


def build_dialog(core, path, cfg, parent, dialog_cls=None):
    """PlayerCore に読み込んだ譜面から RecordDialog を作る(まだ見せない)。
    作れないとき(音源が無い等)は (None, 理由)。"""
    from neotja.player.core import read_text
    from neotja.preview_dock import parse_preview_headers
    pd = core.dock
    wave = pd.wave_path()
    if not wave or not os.path.exists(wave):
        return None, "音源(WAVE)が見つかりません。TJA の WAVE: を確認してください。"
    try:
        content = read_text(path)
    except OSError as exc:
        return None, "譜面を読めませんでした: %s" % exc
    preview = core.build_preview(content)
    out_dir = os.path.dirname(path)
    if not out_dir or not os.path.isdir(out_dir):
        out_dir = os.path.expanduser("~")
    jacket, bg = chart_art(cfg, path)
    ctx = RecordContext(cfg, path, pd, jacket)
    if dialog_cls is None:
        from neotja.dialogs.record_dialog import RecordDialog as dialog_cls
    dlg = dialog_cls(ctx, preview, pd.spin_offset.value(), wave,
                     pd.song_duration_seconds(), out_dir, parent=parent,
                     layout="game",
                     title=parse_preview_headers(content).get("title", ""),
                     bg_path=bg or None)
    return dlg, ""
