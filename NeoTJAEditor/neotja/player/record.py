"""Player の「録画」。難易度選択の「録画」から、Editor と同じ録画の窓
(neotja/dialogs/record_dialog.py の RecordDialog)を開く。

書き出しの準備・本番・中止・後始末は RecordDialog のものをそのまま使う。
ここで作るのは、RecordDialog が「メインウィンドウ」に求めるものを Player の
譜面から用意する小さな入れ物(RecordContext)だけ。

- 譜面とコース: PlayerCore が再生と同じ組み立て(build_preview)で作ったもの。
- 最初のジャケット: 曲ごとの Player のジャケット(player_chart_art)。
  Editor と同じ曲別設定を使い、歌詞ごとのジャケットも保持する。
- 最初の背景: 曲ごとの Player の背景(RecordDialog の bg_path)。
- それ以外(画質・保存先・背景の見た目の既定・書き出したときに覚える
  rec_bg_* など)は既定を変更せず、背景の調整値は曲別に保存する。
- 曲名表示(5 秒)は録画に入れない(計画書: 今回は追加しない)。
"""

import os

from neotja.lyric_jacket import LyricJackets, jacket_segments
from neotja.player import song_lists


def matching_editor(parent, path):
    """同じ譜面を開いているEditorだけを返す。"""
    top = parent.window() if parent is not None else None
    if (top is not None and getattr(top, "current_file", "")
            and song_lists.path_key(top.current_file) == song_lists.path_key(path)
            and callable(getattr(top, "set_jacket_base", None))):
        return top
    return None


class RecordContext:
    """RecordDialog に渡す曲別設定と、既存LYRICジャケット。"""

    def __init__(self, config_data, current_file, preview_dock, jacket="", parent=None):
        self.config_data = config_data
        self.current_file = current_file
        self.preview_dock = preview_dock
        self._editor = matching_editor(parent, current_file)
        live = getattr(self._editor, "lyric_jackets", None)
        self.lyric_jackets = live.copy() if live is not None else LyricJackets.load(current_file)
        self._jacket_dirty = bool(getattr(self._editor, "_jacket_dirty", False))
        if not self._jacket_dirty:
            self.lyric_jackets.base = jacket

    def set_jacket_base(self, path):
        # diskはbaseだけ更新する。Editorの未保存lyricsを勝手に保存しない。
        from neotja.chart_art import set_chart_art
        set_chart_art(self.config_data, self.current_file, jacket=path)
        self.lyric_jackets.set_base(path)
        if self._editor is not None:
            self._editor.set_jacket_base(path)


def chart_art(cfg, path):
    """(ジャケット, 背景) の道。無ければ ""。表示の ON/OFF は見ない
    (暫定方針: 表示設定はまず再生時の表示に適用する)。"""
    def get(kind):
        try:
            p = song_lists.existing_art(cfg, path, kind, respect_hidden=False)
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
    ctx = RecordContext(cfg, path, pd, jacket, parent)
    if dialog_cls is None:
        from neotja.dialogs.record_dialog import RecordDialog as dialog_cls
    dlg = dialog_cls(ctx, preview, pd.spin_offset.value(), wave,
                     pd.song_duration_seconds(), out_dir, parent=parent,
                     layout="game",
                     title=parse_preview_headers(content).get("title", ""),
                     bg_path=bg or None, allow_bg_defaults=False)
    return dlg, ""


def build_art_dialog(core, path, cfg, parent, dialog_cls=None):
    """録画と同じCPU描画を借り、曲別背景の窓を作る。"""
    from neotja import recorder
    from neotja.chart_art import record_background
    from neotja.player.core import read_text
    from neotja.preview_dock import parse_preview_headers
    pd = core.dock
    content = read_text(path)
    preview = core.build_preview(content)
    offset = float(pd.spin_offset.value())
    from neotja.chart_art import jacket_hidden
    bg = record_background(cfg, path, recorder.bg_with_defaults(
        recorder.RecordBackground(), cfg.get("rec_bg_default")))
    ctx = RecordContext(cfg, path, pd, bg.art, parent)
    # 未保存の基本ジャケットも同曲Editorから引き継ぐ。
    editor = matching_editor(parent, path)
    if (editor is not None and getattr(editor, "_jacket_dirty", False)
            and getattr(editor, "lyric_jackets", None) is not None):
        bg.art = editor.lyric_jackets.base
    widget = recorder.make_offline_widget(preview, offset)
    widget.resize(1280, 720)

    def art_track(base):
        lyrics = preview.get("lyrics") or []
        use = LyricJackets(base, {k: v for k, v in ctx.lyric_jackets.lyrics.items()
                                 if os.path.exists(v)})
        if not lyrics or not use.lyrics:
            return []
        segs = jacket_segments([(float(t) - offset, text) for t, text in lyrics], use)
        return segs if len(segs) > 1 else []

    if dialog_cls is None:
        from neotja.dialogs.record_bg_dialog import RecordBackgroundDialog as dialog_cls
    try:
        duration = recorder.full_record_end(preview, offset, pd.song_duration_seconds())
        dlg = dialog_cls(parent, lambda: widget, max(0.1, duration), bg,
                         out_dir=os.path.dirname(path), song_path=pd.wave_path() or "",
                         canvas_wh=(1280, 720), art_track=art_track, chart_path=path,
                         file_title=parse_preview_headers(content).get("title", ""),
                         allow_defaults=False, context="player",
                         jacket_hidden=jacket_hidden(cfg, path))
    except Exception:
        widget.close()
        widget.deleteLater()
        raise
    # 窓を閉じてdeleteLaterすると、画面外の描画部品も一緒に破棄される。
    widget.setParent(dlg)
    widget.hide()
    dlg.setWindowTitle("背景・ジャケット")
    return dlg
