"""曲別素材の共有。ファイル書き込みはすべて一時フォルダ内。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from neotja.chart_art import (chart_art, has_chart_art, set_chart_art,
                              record_background, set_record_background)
from neotja.lyric_jacket import sidecar_path
from neotja.recorder import RecordBackground


class SharedArtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.chart = self.root / "song.tja"
        self.chart.write_text("TITLE:sample", encoding="utf-8")
        self.sidecar = Path(sidecar_path(self.chart))

    def test_base_update_preserves_lyrics_and_unknown_fields(self):
        original = {"version": 1, "base": "old.png",
                    "lyrics": {"歌詞": "lyric.png"}, "future": {"key": 7}}
        self.sidecar.write_text(json.dumps(original), encoding="utf-8")
        cfg = {"rec_bg_default": {"dim": .4}, "rec_bg_art": "global.png"}
        set_chart_art(cfg, str(self.chart), jacket=str(self.root / "new.png"))
        saved = json.loads(self.sidecar.read_text(encoding="utf-8"))
        self.assertEqual(saved, dict(original, base="new.png"))
        self.assertEqual(chart_art(cfg, str(self.chart))["jacket"], str(self.root / "new.png"))
        self.assertEqual(cfg["rec_bg_art"], "global.png")
        self.assertEqual(cfg["rec_bg_default"], {"dim": .4})

    def test_empty_is_explicit_and_other_song_is_untouched(self):
        cfg = {}
        fallback = RecordBackground("global.mp4", art="global.png", dim=.3)
        self.assertFalse(has_chart_art(cfg, str(self.chart), "jacket"))
        set_record_background(cfg, str(self.chart), RecordBackground())
        result = record_background(cfg, str(self.chart), fallback)
        self.assertEqual((result.path, result.art), ("", ""))
        self.assertTrue(has_chart_art(cfg, str(self.chart), "jacket"))
        other = record_background(cfg, str(self.root / "other.tja"), fallback)
        self.assertEqual((other.path, other.art), ("global.mp4", "global.png"))
        self.assertEqual((fallback.path, fallback.art, fallback.dim),
                         ("global.mp4", "global.png", .3))

    def test_editor_sidecar_wins_over_player_legacy_value(self):
        cfg = {}
        set_chart_art(cfg, str(self.chart), jacket="legacy.png", save_jacket=False)
        self.sidecar.write_text('{"base":"editor.png","lyrics":{}}', encoding="utf-8")
        self.assertEqual(chart_art(cfg, str(self.chart))["jacket"], str(self.root / "editor.png"))
        self.sidecar.write_text('{"base":"","lyrics":{}}', encoding="utf-8")
        self.assertEqual(chart_art(cfg, str(self.chart))["jacket"], "")

    def test_corrupt_sidecar_blocks_save_without_overwriting(self):
        self.sidecar.write_text("broken", encoding="utf-8")
        cfg = {"rec_bg_path": "global.mp4"}
        original = copy.deepcopy(cfg)
        with self.assertRaises(ValueError):
            set_chart_art(cfg, str(self.chart), jacket="new.png")
        self.assertEqual(cfg, original)
        self.assertEqual(self.sidecar.read_text(encoding="utf-8"), "broken")

    def test_roundtrip_appearance_without_persisting_dynamic_lyrics(self):
        cfg = {}
        bg = RecordBackground("video.mp4", art="cover.png", offset=3.2, dim=.2,
                              lane=.4, art_x=120, art_y=-80, art_edge=8,
                              fade_in=1.5, fade_out=2.0, fade_target="bg",
                              art_track=[(0, 1, "lyric.png")])
        set_record_background(cfg, str(self.chart), bg, save_jacket=False)
        result = record_background(cfg, str(self.chart))
        for key in RecordBackground.DEFAULT_KEYS + ("offset",):
            self.assertEqual(getattr(result, key), getattr(bg, key), key)
        self.assertEqual(result.art_track, [])
        self.assertFalse(self.sidecar.exists())
        set_chart_art(cfg, str(self.chart), background="changed.mp4")
        self.assertTrue(record_background(cfg, str(self.chart)).path.endswith("changed.mp4"))


if __name__ == "__main__":
    unittest.main()
