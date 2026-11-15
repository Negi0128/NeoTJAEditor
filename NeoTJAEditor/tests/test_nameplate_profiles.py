"""保存したネームプレートの一覧と、書き出し・読み込みの検査。"""
import base64
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage

from neotja import nameplate_profiles as np_mod
from neotja import settings as settings_mod


def _png_bytes(color="red") -> bytes:
    img = QImage(8, 4, QImage.Format_ARGB32)
    img.fill(QColor(color))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


class NameplateProfilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="np_profiles_"))
        self.dest = self.tmp / "NamePlate"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, **over):
        cfg = settings_mod.default_settings()
        cfg.update(over)
        return cfg

    # ---- 一覧の初期化 ----

    def test_ensure_creates_profile_from_flat_values(self):
        cfg = self._cfg(nameplate_name="ねぎ", nameplate_title="達人",
                        nameplate_name_size=20)
        np_mod.ensure_profiles(cfg)
        self.assertEqual(len(cfg["nameplate_profiles"]), 1)
        prof = cfg["nameplate_profiles"][0]
        self.assertEqual(prof["label"], "ねぎ")
        self.assertEqual(prof["nameplate_title"], "達人")
        self.assertEqual(prof["nameplate_name_size"], 20)
        self.assertEqual(cfg["nameplate_profile_index"], 0)

    def test_ensure_keeps_and_cleans_existing(self):
        cfg = self._cfg(nameplate_profiles=[
            {"label": "A", "nameplate_name_size": "99", "junk": 1},
            "壊れた行",
            {"label": "A"},
        ], nameplate_profile_index=7)
        np_mod.ensure_profiles(cfg)
        profs = cfg["nameplate_profiles"]
        self.assertEqual([p["label"] for p in profs], ["A", "A (2)"])
        self.assertEqual(profs[0]["nameplate_name_size"], 60)   # 幅で止める
        self.assertNotIn("junk", profs[0])
        self.assertEqual(cfg["nameplate_profile_index"], 1)

    def test_load_settings_migrates_old_file(self):
        path = self.tmp / "settings.json"
        path.write_text(json.dumps({"nameplate_name": "昔の名前",
                                    "master_volume": 0.0}), encoding="utf-8")
        saved = (settings_mod.settings_path, settings_mod._primary_settings_path,
                 settings_mod._fallback_settings_path, settings_mod._BASELINE)
        try:
            settings_mod.settings_path = lambda: path
            settings_mod._primary_settings_path = lambda: path
            settings_mod._fallback_settings_path = lambda: self.tmp / "fb.json"
            settings_mod._BASELINE = None
            cfg = settings_mod.load_settings()
        finally:
            (settings_mod.settings_path, settings_mod._primary_settings_path,
             settings_mod._fallback_settings_path, settings_mod._BASELINE) = saved
        self.assertEqual(cfg["nameplate_profiles"][0]["label"], "昔の名前")
        self.assertIn("nameplate_profiles", settings_mod._SETTINGS_KEYS)
        self.assertIn("nameplate_profile_index", settings_mod._SETTINGS_KEYS)

    def test_unique_label(self):
        self.assertEqual(np_mod.unique_label("x", ["x", "x (2)"]), "x (3)")
        self.assertEqual(np_mod.unique_label("y", ["x"]), "y")

    def test_sanitize_values(self):
        vals = np_mod.sanitize_values({
            "nameplate_name": "名" * 50,
            "nameplate_title_type": 9, "nameplate_dan": "宇宙段",
            "nameplate_dan_text_color": "pink", "nameplate_dan_dx": -999,
            "nameplate_title_size": True, "nameplate_name_dx": "3.6",
            "nameplate_title": "a\nb",
        })
        d = settings_mod.default_settings()
        self.assertEqual(vals["nameplate_name"], "名" * np_mod.NAME_MAX)
        self.assertEqual(vals["nameplate_title"], "a b")
        self.assertEqual(vals["nameplate_title_type"], d["nameplate_title_type"])
        self.assertEqual(vals["nameplate_dan"], d["nameplate_dan"])
        self.assertEqual(vals["nameplate_dan_text_color"],
                         d["nameplate_dan_text_color"])
        self.assertEqual(vals["nameplate_dan_dx"], -200)
        self.assertEqual(vals["nameplate_title_size"], d["nameplate_title_size"])
        self.assertEqual(vals["nameplate_name_dx"], 4)
        self.assertEqual(set(vals), set(np_mod.PLATE_KEYS))
        long = np_mod.sanitize_values({"nameplate_title": "称" * 100})
        self.assertEqual(len(long["nameplate_title"]), np_mod.TITLE_MAX)

    # ---- 書き出し → 読み込み ----

    def test_export_embeds_image_and_roundtrips(self):
        img = self.tmp / "帯.png"
        data = _png_bytes()
        img.write_bytes(data)
        prof = np_mod.profile_from_values("自分", {
            "nameplate_name": "ねぎ", "nameplate_title_image": str(img),
            "nameplate_dan": "十段"})
        out = np_mod.export_dict(prof)
        self.assertEqual(out["format"], np_mod.FORMAT_NAME)
        plate = out["plates"][0]
        self.assertNotIn("nameplate_title_image", plate)   # 手元の道は書かない
        self.assertEqual(plate["title_image"]["filename"], "帯.png")
        self.assertEqual(base64.b64decode(plate["title_image"]["data"]), data)

        text = np_mod.export_json(prof)
        added = np_mod.import_plates(text, ["自分"], self.dest)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["label"], "自分 (2)")      # 上書きしない
        self.assertEqual(added[0]["nameplate_dan"], "十段")
        stored = Path(added[0]["nameplate_title_image"])
        self.assertEqual(stored.parent, self.dest)
        self.assertEqual(stored.read_bytes(), data)
        # 同じ中身をもう一度読んでも、絵の複製は増えない。
        again = np_mod.import_plates(text, [], self.dest)
        self.assertEqual(Path(again[0]["nameplate_title_image"]), stored)

    def test_import_does_not_overwrite_different_image(self):
        self.dest.mkdir(parents=True)
        (self.dest / "a.png").write_bytes(_png_bytes("blue"))
        doc = {"format": np_mod.FORMAT_NAME, "version": 1, "plates": [{
            "label": "x", "title_image": {
                "filename": "../../a.png",
                "data": base64.b64encode(_png_bytes("red")).decode()}}]}
        added = np_mod.import_plates(json.dumps(doc), [], self.dest)
        stored = Path(added[0]["nameplate_title_image"])
        self.assertEqual(stored.parent, self.dest)          # 外へ出ない
        self.assertEqual(stored.name, "a (2).png")
        self.assertEqual((self.dest / "a.png").read_bytes(), _png_bytes("blue"))

    def test_import_avoids_reserved_names(self):
        doc = {"format": np_mod.FORMAT_NAME, "version": 1, "plates": [{
            "title_image": {"filename": "NamePlate.png",
                            "data": base64.b64encode(_png_bytes()).decode()}}]}
        added = np_mod.import_plates(doc, [], self.dest)
        self.assertEqual(Path(added[0]["nameplate_title_image"]).name,
                         "title_NamePlate.png")
        self.assertFalse((self.dest / "NamePlate.png").exists())

    def test_import_ignores_local_path_and_fills_defaults(self):
        doc = {"format": np_mod.FORMAT_NAME, "version": 1, "plates": [
            {"label": "A", "nameplate_title_image": "C:/secret/x.png",
             "unknown": [1, 2]},
            {"nameplate_name": "名無し"}]}
        added = np_mod.import_plates(doc, [], self.dest)
        self.assertEqual(added[0]["nameplate_title_image"], "")
        self.assertNotIn("unknown", added[0])
        self.assertEqual(added[1]["label"], "名無し")
        self.assertEqual(added[1]["nameplate_name_size"],
                         settings_mod.default_settings()["nameplate_name_size"])

    def test_bad_files_raise(self):
        bad = [
            b"not json",
            b"\xff\xfe\x00",
            json.dumps({"format": "other", "plates": [{}]}),
            json.dumps({"format": np_mod.FORMAT_NAME, "version": 99,
                        "plates": [{}]}),
            json.dumps({"format": np_mod.FORMAT_NAME, "version": 1, "plates": []}),
            json.dumps({"format": np_mod.FORMAT_NAME, "version": 1,
                        "plates": ["x"]}),
            json.dumps({"format": np_mod.FORMAT_NAME, "version": 1, "plates": [
                {"title_image": {"filename": "a.png", "data": "!!!"}}]}),
            json.dumps({"format": np_mod.FORMAT_NAME, "version": 1, "plates": [
                {"title_image": {"filename": "a.png",
                                 "data": base64.b64encode(b"not an image").decode()}}]}),
            json.dumps([1, 2, 3]),
        ]
        for data in bad:
            with self.subTest(data=data[:40]):
                with self.assertRaises(np_mod.NameplateImportError):
                    np_mod.import_plates(data, [], self.dest)
        self.assertFalse(self.dest.exists() and any(self.dest.iterdir()))

    def test_image_size_limit(self):
        big = base64.b64encode(b"\0" * (np_mod.IMAGE_MAX_BYTES + 1)).decode()
        doc = {"format": np_mod.FORMAT_NAME, "version": 1, "plates": [
            {"title_image": {"filename": "a.png", "data": big}}]}
        with self.assertRaises(np_mod.NameplateImportError):
            np_mod.parse_import(doc, is_image=lambda b: True)

    def test_safe_file_stem(self):
        self.assertEqual(np_mod.safe_file_stem('a/b:c*?'), "a_b_c__")
        self.assertEqual(np_mod.safe_file_stem("..."), "nameplate")


if __name__ == "__main__":
    unittest.main()
